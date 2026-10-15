"""One refresh worker with pollable status and per-house failure isolation."""
from dataclasses import replace
import json, threading
from datetime import datetime, timezone
from .engine import exclusion_reason
from .models import is_fresh
from .identity import pet_details, override_key
from .realms import auction_realm
from .sources.http import source_diagnostic
from concurrent.futures import ThreadPoolExecutor

class RefreshService:
    def __init__(self,repository,client,tsm_client=None):
        from .sources.tsm import TSMClient
        self.repository=repository; self.client=client
        self.tsm_client=tsm_client or TSMClient()
        self._lock=threading.Lock(); self._worker=ThreadPoolExecutor(max_workers=1)
        self.status={'running':False,'sources':{}}
    def start(self,settings):
        if not self._lock.acquire(blocking=False): raise RuntimeError('Refresh already running')
        self.status={'running':True,'sources':{}}
        return self._worker.submit(self._run,settings)
    def run(self,settings):
        if not self._lock.acquire(blocking=False): raise RuntimeError('Refresh already running')
        self.status={'running':True,'sources':{}}
        return self._run(settings)
    def _run(self,settings):
        try:
            self.repository.save_settings(settings)
            try:
                snapshot=self.tsm_client.region(self.repository.latest('tsm:us'))
                identity=self.repository.publish(snapshot)
                self.status['sources']['tsm:us']={'state':'success','snapshot_id':identity,**(snapshot.source_metadata or {})}
                if self.repository.maintenance_warning: self.status['sources']['tsm:us']['maintenance_warning']=self.repository.maintenance_warning
            except Exception as exc:
                self.status['sources']['tsm:us']={'state':'failed',**source_diagnostic(exc)}
            if 'battle-pet' in settings.categories:
                try:
                    snapshot=self.tsm_client.pets(self.repository.latest('tsm-pets:us'))
                    identity=self.repository.publish(snapshot)
                    self.status['sources']['tsm-pets:us']={'state':'success','snapshot_id':identity,**(snapshot.source_metadata or {})}
                    if self.repository.maintenance_warning: self.status['sources']['tsm-pets:us']['maintenance_warning']=self.repository.maintenance_warning
                except Exception as exc:
                    self.status['sources']['tsm-pets:us']={'state':'failed',**source_diagnostic(exc)}
            selected=set(settings.buying_realms)|set(settings.selling_realms)
            cached_realms=self.repository.cache_get('realms') or []
            realms=cached_realms
            try:
                fresh=self.repository.cache_fresh('realms',86400)
                if fresh and all('category' in realm and 'house_id' in realm for realm in fresh) and any(not isinstance(realm.get('is_auction_realm'),bool) for realm in fresh) and hasattr(self.client,'connected_realm_ids'):
                    # Legacy cached details already establish category and house.
                    # Upgrade them using one authoritative index request.
                    houses=self.client.connected_realm_ids()
                    realms=[{**realm,'is_auction_realm':realm.get('house_id') in houses} for realm in fresh]
                    self.repository.cache_put('realms',realms)
                    self.repository.cache_timed('realms',realms)
                elif not fresh or any('category' not in realm or not isinstance(realm.get('is_auction_realm'),bool) for realm in fresh):
                    realms=self.client.realms()
                    self.repository.cache_put('realms',realms)
                    self.repository.cache_timed('realms',realms)
                else: realms=fresh
                self.status['sources']['realms:us']={'state':'success'}
            except Exception as exc:
                # Failed discovery never rewrites known eligibility or settings.
                # Previously verified houses can continue using the retained cache.
                realms=cached_realms
                self.status['sources']['realms:us']={'state':'failed',**source_diagnostic(exc)}
                if realms: self.status['sources']['realms:us']['using_cached_realms']=True
            discovered={r['slug']:r for r in realms}
            mapping={}
            for slug in sorted(selected):
                realm=discovered.get(slug)
                house=realm.get('house_id') if realm else None
                if realm and auction_realm(realm) and isinstance(house,int) and house>0:
                    mapping[slug]=house
                    continue
                code='realm_unavailable' if realm is None else 'unsupported_realm' if realm.get('is_auction_realm') is False else 'verification_pending'
                key=f'auctions:us:{house}' if house else f'realm:us:{slug}'
                status=self.status['sources'].setdefault(key,{'state':'skipped','error_code':code,'realms':[]})
                status['realms'].append(slug)
            collections={}
            for category,method,key in [('toy','toy_items','toys:us'),('decor','decor_items','decor:us')]:
                legacy=self.repository.cache_get('timed:'+key) or {}
                cached=self.repository.cache_get(key) or legacy.get('value') or {}
                state={'items':cached.get('items',[]),'pending':len(cached.get('remaining',[]))}
                if category in settings.categories and hasattr(self.client,method):
                    try:
                        state=getattr(self.client,method)(self.repository)
                        self.status['sources'][key]={'state':'pending' if state['pending'] else 'success',
                            'pending':state['pending'],'known_items':len(state['items']),
                            **({'details_fetched':state['details_fetched']} if 'details_fetched' in state else {})}
                    except Exception as exc:
                        retained=self.repository.cache_get(key) or self.repository.cache_fresh(key,7*86400) or cached
                        state={'items':retained.get('items',[]),'pending':len(retained.get('remaining',[]))}
                        self.status['sources'][key]={'state':'failed',**source_diagnostic(exc),
                            'using_cached_items':bool(state['items']),'known_items':len(state['items']),'pending':state['pending']}
                        if category=='decor':
                            self.status['sources'][key]['details_fetched']=max(0,len(retained.get('resolved',{}))-len(cached.get('resolved',{})))
                collections[category]=set(state['items'])
            tsm=self.repository.latest('tsm:us')
            # Fetch each house once, then discover buy identities before enriching any
            # destination. Shared item metadata is independent of ask and house order.
            values={r.item_id:r for r in tsm.records} if tsm else {}
            pet_snapshot=self.repository.latest('tsm-pets:us')
            pet_values={r.pet_species_id:r for r in pet_snapshot.records if r.pet_species_id is not None} if pet_snapshot else {}
            snapshots={}; counts={}
            buy_houses={mapping[slug] for slug in settings.buying_realms if slug in mapping}
            for house in sorted(set(mapping.values())):
                key=f'auctions:us:{house}'
                try:
                    snapshots[house]=self.client.auctions(house,self.repository.latest(key))
                    counts[house]={'metadata_fetched':0,'metadata_skipped':0,'pet_metadata_failed':0,'pet_metadata_unknown':0}
                except Exception as exc:
                    self.status['sources'][key]={'state':'failed',**source_diagnostic(exc)}
            metadata_by_item={}
            def metadata_identity(auction):
                details=pet_details(auction)
                if details: return ('pet',details['species_id'])
                return ('item',auction.item_id)
            def cache_key(identity):
                kind,value=identity
                return f'{kind}:us:{value}'
            for snapshot in snapshots.values():
                for auction in snapshot.records:
                    item=metadata_identity(auction)
                    if item not in metadata_by_item:
                        metadata_by_item[item]=self.repository.cache_fresh(cache_key(item),7*86400)
            now=datetime.now(timezone.utc); attempted=set(); pending_by_house={}
            resale_overrides=self.repository.load_resale_overrides()
            valuation_fresh=bool(tsm and is_fresh(tsm.source_time,settings.max_tsm_age_hours,now))
            pet_valuation_fresh=bool(pet_snapshot and is_fresh(pet_snapshot.source_time,settings.max_tsm_age_hours,now))
            for house in sorted(buy_houses & snapshots.keys()):
                snapshot=snapshots[house]
                try:
                    pending=set()
                    for auction in snapshot.records:
                        item=metadata_identity(auction); details=pet_details(auction)
                        pet=item[0]=='pet'
                        if metadata_by_item[item] is not None or item in attempted: continue
                        # The cage item has no item-wide regional price or identity.
                        if auction.item_id==82800 and not pet: continue
                        if pet and 'battle-pet' not in settings.categories: continue
                        valuation=pet_values.get(item[1]) if pet else values.get(item[1])
                        fresh=pet_valuation_fresh if pet else valuation_fresh
                        plausible=replace(auction,source_time=snapshot.source_time,
                            category='battle-pet' if pet else next((category for category in settings.categories if category != 'battle-pet'),'unknown'),
                            metadata={'simple_identity':True,'tradeability':True,**({'pet':details} if pet else {})})
                        override=resale_overrides.get(override_key(auction))
                        if not fresh or not valuation or exclusion_reason(plausible,valuation,settings,now,override): continue
                        if counts[house]['metadata_fetched']>=100:
                            pending.add(item); continue
                        attempted.add(item)
                        if pet:
                            counts[house]['metadata_fetched']+=1
                            try:
                                metadata=self.client.pet(item[1])
                                if not isinstance(metadata,dict) or not isinstance(metadata.get('is_tradable'),bool) or not isinstance(metadata.get('is_battlepet'),bool):
                                    raise ValueError('Invalid pet metadata')
                                metadata={**metadata,'category':'battle-pet','tradeability':metadata['is_tradable'] and metadata['is_battlepet']}
                            except Exception:
                                counts[house]['pet_metadata_failed']+=1
                                continue
                        else:
                            metadata=self.client.item(item[1])
                            counts[house]['metadata_fetched']+=1
                        key=cache_key(item)
                        self.repository.cache_put(key,metadata); self.repository.cache_timed(key,metadata)
                        metadata_by_item[item]=metadata
                    pending_by_house[house]=pending
                except Exception as exc:
                    self.status['sources'][f'auctions:us:{house}']={'state':'failed',**source_diagnostic(exc)}
                    del snapshots[house]
            for house,pending in pending_by_house.items():
                counts[house]['metadata_skipped']=sum(metadata_by_item[item] is None for item in pending)
            for item,metadata in metadata_by_item.items():
                if metadata is not None and item[0]=='item':
                    for category in ('toy','decor'):
                        if item[1] in collections[category]:
                            metadata_by_item[item]={**metadata,'category':category}
                    if metadata.get('item_class',{}).get('id') == 20 and metadata.get('item_subclass',{}).get('id') == 0:
                        metadata_by_item[item]={**metadata,'category':'decor'}
            for house,snapshot in snapshots.items():
                key=f'auctions:us:{house}'
                try:
                    records=[]
                    for auction in snapshot.records:
                        item=metadata_identity(auction)
                        metadata=metadata_by_item[item] or {}
                        details=pet_details(auction)
                        if details:
                            verified=isinstance(metadata.get('is_tradable'),bool) and isinstance(metadata.get('is_battlepet'),bool)
                            if not verified: counts[house]['pet_metadata_unknown']+=1
                            pet={**details,**({'name':metadata['name']} if metadata.get('name') else {})}
                            metadata={**metadata,'simple_identity':False,'pet':pet,
                                'tradeability':metadata.get('is_tradable') is True and metadata.get('is_battlepet') is True}
                            category='battle-pet' if verified else 'unknown'
                            if not verified: metadata['exclusion_reason']='unknown_pet_metadata'
                        else:
                            variants=json.loads(auction.variant_key) if auction.variant_key else {}
                            simple=isinstance(variants,dict) and all(k in {'context','bonus_lists','modifiers'} and v in (0,[],None) for k,v in variants.items())
                            metadata={**metadata,'simple_identity':simple,'tradeability':metadata.get('tradeability')}
                            category=metadata.get('category','unknown') if auction.item_id != 82800 else 'unknown'
                        records.append(replace(auction,metadata=metadata,category=category))
                    identity=self.repository.publish(replace(snapshot,records=tuple(records)))
                    self.status['sources'][key]={'state':'success','snapshot_id':identity,**counts[house]}
                    if self.repository.maintenance_warning: self.status['sources'][key]['maintenance_warning']=self.repository.maintenance_warning
                except Exception as exc:
                    # Never expose upstream response bodies, headers or credentials.
                    self.status['sources'][key]={'state':'failed',**source_diagnostic(exc)}
            return self.status
        finally:
            self.status['running']=False; self._lock.release()
