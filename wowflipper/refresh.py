"""One refresh worker with pollable status and per-house failure isolation."""
from dataclasses import replace
import copy, inspect, json, pickle, tempfile, threading, time, uuid
from collections.abc import MutableMapping
from pathlib import Path
from itertools import islice
from datetime import datetime, timezone
from .engine import exclusion_reason
from .models import is_fresh
from .identity import pet_details, override_key, simple_item_identity, discovery_item_identity
from .realms import auction_realm
from .sources.http import SourceError, source_diagnostic
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

class _SnapshotSpool(MutableMapping):
    """Private refresh-owned scratch files; only our own snapshots are unpickled.

    Download workers stage their result before completing, so futures never retain
    a region's worth of auction records. Reads materialize one house at a time.
    """
    def __init__(self,directory):
        self.directory=Path(directory)
        self.files={}
    def __getitem__(self,house):
        with self.files[house].open('rb') as stream:return pickle.load(stream)
    def __setitem__(self,house,snapshot):
        path=self.directory/f'{house}.snapshot'
        with path.open('wb') as stream:pickle.dump(snapshot,stream,protocol=pickle.HIGHEST_PROTOCOL)
        self.files[house]=path
    def __delitem__(self,house):self.files.pop(house).unlink()
    def __iter__(self):return iter(self.files)
    def __len__(self):return len(self.files)


class RefreshService:
    def __init__(self,repository,client,tsm_client=None):
        from .sources.tsm import TSMClient
        self.repository=repository; self.client=client
        self.tsm_client=tsm_client or TSMClient()
        self._lock=threading.Lock(); self._worker=ThreadPoolExecutor(max_workers=1)
        self.status={'running':False,'sources':{}}
        self._started=None; self._phase_started=None
    def status_snapshot(self):
        # Copy each mutable mapping before iteration; fetch workers replace source
        # entries while the web worker polls. Never expose those live mappings.
        status=self.status.copy()
        status['sources']={key:copy.deepcopy(value.copy()) for key,value in status['sources'].copy().items()}
        status['phase_seconds']=status.get('phase_seconds',{}).copy()
        status['changed_sources']=status.get('changed_sources',[])[:]
        if self._started is not None and status.get('running'):
            status['elapsed_seconds']=round(time.monotonic()-self._started,3)
            if self._phase_started is not None:
                status['phase_seconds'][status['phase']]=round(time.monotonic()-self._phase_started,3)
        return status
    def _begin(self):
        self._started=time.monotonic(); self._phase_started=None
        self.status={'running':True,'sources':{},'started_at':datetime.now(timezone.utc).isoformat(),
            'run_id':str(uuid.uuid4()),'completed_at':None,'changed_sources':[],
            'elapsed_seconds':0,'completed':0,'total':0,'phase_seconds':{}}
        self._phase('valuations')
    def _phase(self,name,total=0):
        now=time.monotonic()
        if self._phase_started is not None:
            self.status['phase_seconds']={**self.status['phase_seconds'],self.status['phase']:round(now-self._phase_started,3)}
        self._phase_started=now
        self.status.update(phase=name,completed=0,total=total)
    def _call_progress(self,method,*args,progress):
        parameters=inspect.signature(method).parameters
        if 'progress' in parameters or any(p.kind==inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
            return method(*args,progress=progress)
        return method(*args)
    def _discover_collection(self,method,key,max_batches=50):
        fetched=attempted=0; previous=None
        def progress(update):
            self.status['sources'][key]={**self.status['sources'].get(key,{}),**update,'state':'running'}
        for batch in range(max_batches):
            state=self._call_progress(method,self.repository,progress=progress)
            fetched+=state.get('details_fetched',0); attempted+=state.get('details_attempted',0)
            observed=self.status['sources'].get(key,{})
            marker=(state['pending'],len(state['items']),state.get('failed',observed.get('failed',0)),state.get('unavailable',0))
            self.status['sources'][key]={**observed,'state':'running',**{k:v for k,v in state.items() if k!='items'},
                'known_items':len(state['items']),'details_fetched':fetched,'details_attempted':attempted,'batches':batch+1}
            if not state['pending'] or state.get('actionable_pending')==0 or marker==previous: break
            previous=marker
        partial=bool(self.status['sources'][key].get('failed') or state.get('unavailable') or state.get('errors'))
        self.status['sources'][key].update(state='partial' if partial else 'pending' if state['pending'] else 'success')
        return state
    @staticmethod
    def _same_source(snapshot,previous):
        return previous is not None and all(getattr(snapshot,key)==getattr(previous,key)
            for key in ('source_key','source_time','http_last_modified','mode','records','source_id','source_metadata'))
    def start(self,settings,changed_only=False):
        if not self._lock.acquire(blocking=False): raise RuntimeError('Refresh already running')
        self._begin()
        return self._worker.submit(self._run,settings,changed_only)
    def run(self,settings,changed_only=False):
        if not self._lock.acquire(blocking=False): raise RuntimeError('Refresh already running')
        self._begin()
        return self._run(settings,changed_only)
    def _run(self,settings,changed_only=False):
        staging=None
        try:
            staging=tempfile.TemporaryDirectory(prefix='refresh-',dir=self.repository.path)
            snapshots=_SnapshotSpool(staging.name)
            self.repository.save_settings(settings)
            valuation_changed=False; valuations={}
            def refresh_valuation(key,method_name):
                nonlocal valuation_changed
                previous=self.repository.latest(key,include_payload=False)
                valuations[key]=previous
                snapshot=getattr(self.tsm_client,method_name)(previous)
                changed=not self._same_source(snapshot,previous)
                if changed_only and not changed:
                    identity=previous.id; state='unchanged'
                else:
                    identity=self.repository.publish(snapshot); state='success'
                valuations[key]=snapshot
                if changed:
                    valuation_changed=True; self.status['changed_sources'].append(key)
                self.status['sources'][key]={'state':state,'snapshot_id':identity,**(snapshot.source_metadata or {})}
                if self.repository.maintenance_warning: self.status['sources'][key]['maintenance_warning']=self.repository.maintenance_warning
            try:
                refresh_valuation('tsm:us','region')
            except Exception as exc:
                self.status['sources']['tsm:us']={'state':'failed',**source_diagnostic(exc)}
            if 'battle-pet' in settings.categories:
                try:
                    refresh_valuation('tsm-pets:us','pets')
                except Exception as exc:
                    self.status['sources']['tsm-pets:us']={'state':'failed',**source_diagnostic(exc)}
            self._phase('realms')
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
                    def realm_progress(update): self.status['sources']['realms:us']={'state':'running',**update}
                    realms=self._call_progress(self.client.realms,progress=realm_progress)
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
            self._phase('collections',sum(category in settings.categories for category in ('toy','decor')))
            collections={}; collection_cache={}
            for category,method,key in [('toy','toy_items','toys:us'),('decor','decor_items','decor:us')]:
                legacy=self.repository.cache_get('timed:'+key) or {}
                cached=self.repository.cache_get(key) or legacy.get('value') or {}
                collection_cache[category]=cached
                state={'items':cached.get('items',[]),'pending':len(cached.get('remaining',[]))}
                if category=='decor' and category in settings.categories:
                    # Item class 20/subclass 0 establishes decor directly. A full
                    # decor catalog crawl adds no required evidence to those rows.
                    # Retain existing catalog membership for older cached metadata.
                    self.status['sources'][key]={'state':'success','classification':'item_metadata',
                        'known_items':len(state['items'])}
                elif changed_only and category in settings.categories:
                    self.status['sources'][key]={'state':'unchanged','known_items':len(state['items']),
                        'pending':state['pending'],'classification':'cached_membership'}
                elif category in settings.categories and hasattr(self.client,method):
                    try:
                        state=self._discover_collection(getattr(self.client,method),key)
                    except Exception as exc:
                        retained=self.repository.cache_get(key) or self.repository.cache_fresh(key,7*86400) or cached
                        state={'items':retained.get('items',[]),'pending':len(retained.get('remaining',[]))}
                        self.status['sources'][key]={**self.status['sources'].get(key,{}),'state':'failed',**source_diagnostic(exc),
                            'using_cached_items':bool(state['items']),'known_items':len(state['items']),'pending':state['pending']}
                        if category=='decor':
                            self.status['sources'][key]['details_fetched']=max(0,len(retained.get('resolved',{}))-len(cached.get('resolved',{})))
                collections[category]=set(state['items'])
                if category in settings.categories: self.status['completed']+=1
            tsm=valuations.get('tsm:us')
            # Fetch each house once, then discover buy identities before enriching any
            # destination. Shared item metadata is independent of ask and house order.
            pet_snapshot=valuations.get('tsm-pets:us')
            counts={}; unchanged={}
            buy_houses={mapping[slug] for slug in settings.buying_realms if slug in mapping}
            houses=sorted(set(mapping.values()))
            analysis_settings=settings.to_dict()
            for preference in ('auto_refresh','sound_alerts','desktop_alerts'): analysis_settings.pop(preference,None)
            configuration=json.dumps({'settings':analysis_settings,'mapping':mapping,'overrides':self.repository.cache_get('resale_overrides')},
                sort_keys=True,separators=(',',':'))
            configuration_changed=configuration!=self.repository.cache_get('refresh:configuration')
            self._phase('auction_fetch',len(houses))
            for house in houses: self.status['sources'][f'auctions:us:{house}']={'state':'queued','phase':'auction_fetch'}
            def fetch(house):
                key=f'auctions:us:{house}'
                self.status['sources'][key]={'state':'running','phase':'auction_fetch'}
                if changed_only and hasattr(self.client,'auction_update'):
                    previous=self.repository.latest_provenance(key)
                    snapshot=self.client.auction_update(house,previous.get('http_last_modified') if previous else None)
                    if snapshot is None:return False
                    snapshots[house]=snapshot
                    return True
                previous=self.repository.latest(key,include_payload=False)
                snapshot=self.client.auctions(house,previous)
                if changed_only and self._same_source(snapshot,previous):return False
                snapshots[house]=snapshot
                return True
            with ThreadPoolExecutor(max_workers=3) as pool:
                queued=iter(houses); stop_error=None
                futures={pool.submit(fetch,house):house for house in islice(queued,3)}
                while futures:
                    done,_=wait(futures,return_when=FIRST_COMPLETED)
                    for future in done:
                        house=futures.pop(future); key=f'auctions:us:{house}'
                        try:
                            changed=future.result()
                            if not changed:
                                unchanged[house]=True
                                self.status['sources'][key]={'state':'unchanged'}
                            else:
                                self.status['sources'][key]={'state':'running','phase':'metadata'}
                        except Exception as exc:
                            self.status['sources'][key]={'state':'failed',**source_diagnostic(exc)}
                            if isinstance(exc,SourceError) and exc.code in {'auth_missing','auth_denied','rate_limited'}:
                                stop_error=stop_error or exc
                        self.status['completed']+=1
                    if stop_error is None:
                        for house in islice(queued,3-len(futures)): futures[pool.submit(fetch,house)]=house
                if stop_error is not None:
                    for house in queued:
                        self.status['sources'][f'auctions:us:{house}']={'state':'skipped','phase':'auction_fetch',
                            'deferred':True,**source_diagnostic(stop_error)}
                        self.status['completed']+=1
            if changed_only:
                if not snapshots and not valuation_changed and not configuration_changed:
                    return self.status
                if 'toy' in settings.categories and hasattr(self.client,'toy_items'):
                    # Changed automatic checks advance a single bounded catalog
                    # batch. The all-304 path above does no classification work.
                    self._phase('collections',1)
                    self.status['sources']['toys:us']={'state':'running'}
                    try:
                        state=self._discover_collection(self.client.toy_items,'toys:us',max_batches=1)
                    except Exception as exc:
                        retained=self.repository.cache_get('toys:us') or self.repository.cache_fresh('toys:us',7*86400) or collection_cache['toy']
                        state={'items':retained.get('items',[]),'pending':len(retained.get('remaining',[]))}
                        self.status['sources']['toys:us']={**self.status['sources'].get('toys:us',{}),'state':'failed',
                            **source_diagnostic(exc),'using_cached_items':bool(state['items']),
                            'known_items':len(state['items']),'pending':state['pending']}
                    collections['toy']=set(state['items'])
                    self.status['completed']=1
                # A changed buy identity can alter destination classification and
                # competition. Load retained selected houses only when work exists.
                for house in unchanged:
                    previous=self.repository.latest(f'auctions:us:{house}',include_payload=False)
                    if previous is not None: snapshots[house]=previous
            counts={house:{'metadata_fetched':0,'metadata_skipped':0,'metadata_failed':0,'pet_metadata_failed':0,'pet_metadata_unknown':0} for house in snapshots}
            values={r.item_id:r for r in tsm.records} if tsm else {}
            pet_values={r.pet_species_id:r for r in pet_snapshot.records if r.pet_species_id is not None} if pet_snapshot else {}
            self._phase('metadata',len(buy_houses & snapshots.keys()))
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
                        metadata_by_item[item]=None
            cached=self.repository.cache_fresh_many((cache_key(item) for item in metadata_by_item),7*86400)
            metadata_by_item={item:cached[cache_key(item)] for item in metadata_by_item}
            now=datetime.now(timezone.utc); attempted=set(); pending_by_house={}; metadata_jobs=[]
            planned_by_house=dict.fromkeys(snapshots,0)
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
                        if not pet and not discovery_item_identity(auction,settings.categories): continue
                        # Category is not known until the item lookup. Check the
                        # supported raw shape above, then run only the financial
                        # prefilter here. Publication validates the original raw
                        # identity against the category returned by item details.
                        plausible=replace(auction,variant_key=auction.variant_key if pet else None,source_time=snapshot.source_time,
                            category='battle-pet' if pet else next((category for category in settings.categories if category != 'battle-pet'),'unknown'),
                            metadata={'simple_identity':True,'tradeability':True,**({'pet':details} if pet else {})})
                        override=resale_overrides.get(override_key(auction))
                        if not fresh or not valuation or exclusion_reason(plausible,valuation,settings,now,override): continue
                        if planned_by_house[house]>=100:
                            pending.add(item); continue
                        attempted.add(item)
                        planned_by_house[house]+=1
                        metadata_jobs.append((item,house))
                    pending_by_house[house]=pending
                except Exception as exc:
                    self.status['sources'][f'auctions:us:{house}']={'state':'failed',**source_diagnostic(exc)}
                    del snapshots[house]
                self.status['completed']+=1
            # Eligibility and house budgets are decided in a stable order above.
            # Only network requests run in parallel; persistence stays on this
            # coordinator thread and at most four futures exist at a time.
            self.status.update(completed=0,total=len(metadata_jobs))
            def fetch_metadata(item):
                if item[0]=='item': return self.client.item(item[1])
                metadata=self.client.pet(item[1])
                if not isinstance(metadata,dict) or not isinstance(metadata.get('is_tradable'),bool) or not isinstance(metadata.get('is_battlepet'),bool):
                    raise ValueError('Invalid pet metadata')
                return {**metadata,'category':'battle-pet','tradeability':metadata['is_tradable'] and metadata['is_battlepet']}
            with ThreadPoolExecutor(max_workers=4) as pool:
                jobs=iter(metadata_jobs)
                stop_error=None
                def submit_job(job):
                    item,house=job
                    counts[house]['metadata_fetched']+=1
                    return pool.submit(fetch_metadata,item)
                futures={submit_job(job):job for job in islice(jobs,4)}
                while futures:
                    done,_=wait(futures,return_when=FIRST_COMPLETED)
                    for future in done:
                        item,house=futures.pop(future)
                        try: metadata=future.result()
                        except Exception as exc:
                            counts[house]['pet_metadata_failed' if item[0]=='pet' else 'metadata_failed']+=1
                            if isinstance(exc,SourceError) and exc.code in {'auth_missing','auth_denied','rate_limited'}:
                                stop_error=stop_error or exc
                                counts[house]['metadata_error_code']=exc.code
                        else:
                            try:
                                key=cache_key(item)
                                self.repository.cache_put(key,metadata); self.repository.cache_timed(key,metadata)
                                metadata_by_item[item]=metadata
                            except Exception as exc:
                                self.status['sources'][f'auctions:us:{house}']={'state':'failed',**source_diagnostic(exc)}
                                snapshots.pop(house,None)
                        self.status['completed']+=1
                    if stop_error is None:
                        for job in islice(jobs,4-len(futures)): futures[submit_job(job)]=job
                if stop_error is not None:
                    for item,house in jobs:
                        counts[house]['metadata_deferred']=counts[house].get('metadata_deferred',0)+1
                        counts[house]['metadata_error_code']=stop_error.code
            for house,pending in pending_by_house.items():
                counts[house]['metadata_skipped']=sum(metadata_by_item[item] is None for item in pending)
            for item,metadata in metadata_by_item.items():
                if metadata is not None and item[0]=='item':
                    for category in ('toy','decor'):
                        if item[1] in collections[category]:
                            metadata_by_item[item]={**metadata,'category':category}
                    if metadata.get('item_class',{}).get('id') == 20 and metadata.get('item_subclass',{}).get('id') == 0:
                        metadata_by_item[item]={**metadata,'category':'decor'}
            self._phase('publication',len(snapshots))
            for house in sorted(snapshots):
                snapshot=snapshots[house]
                key=f'auctions:us:{house}'
                self.status['sources'][key]={'state':'running','phase':'publication',**counts[house]}
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
                            category=metadata.get('category','unknown') if auction.item_id != 82800 else 'unknown'
                            simple=simple_item_identity(auction,category)
                            metadata={**metadata,'simple_identity':simple,'tradeability':metadata.get('tradeability')}
                        records.append(replace(auction,metadata=metadata,category=category))
                    normalized=replace(snapshot,records=tuple(records))
                    previous=self.repository.latest(key,include_payload=False) if changed_only else None
                    if changed_only and self._same_source(normalized,previous):
                        identity=previous.id; state='unchanged'
                    else:
                        identity=self.repository.publish(normalized); state='success'
                        self.status['changed_sources'].append(key)
                    self.status['sources'][key]={'state':state,'snapshot_id':identity,**counts[house]}
                    if self.repository.maintenance_warning: self.status['sources'][key]['maintenance_warning']=self.repository.maintenance_warning
                except Exception as exc:
                    # Never expose upstream response bodies, headers or credentials.
                    self.status['sources'][key]={'state':'failed',**source_diagnostic(exc)}
                self.status['completed']+=1
            if all(self.status['sources'][f'auctions:us:{house}']['state'] in {'success','unchanged'} for house in houses):
                self.repository.cache_put('refresh:configuration',configuration)
            return self.status
        finally:
            completed=self.status.get('completed',0); total=self.status.get('total',0)
            self._phase('complete',total)
            self.status.update(completed=completed,running=False,elapsed_seconds=round(time.monotonic()-self._started,3),
                completed_at=datetime.now(timezone.utc).isoformat())
            self._lock.release()
            if staging is not None:staging.cleanup()
