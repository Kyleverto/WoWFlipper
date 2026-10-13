"""One refresh worker with pollable status and per-house failure isolation."""
from dataclasses import replace
import json, threading
from datetime import datetime, timezone
from .engine import exclusion_reason
from .models import is_fresh
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
                self.status['sources']['tsm:us']={'state':'failed','error':type(exc).__name__}
            try:
                realms=self.repository.cache_fresh('realms',86400)
                if realms is None or any('category' not in realm for realm in realms):
                    realms=self.client.realms(); self.repository.cache_put('realms',realms); self.repository.cache_timed('realms',realms)
                selected=set(settings.buying_realms)|set(settings.selling_realms)
                mapping={r['slug']:r['house_id'] for r in realms}
                if selected-set(mapping): raise ValueError('Selected realm was not discovered')
                self.status['sources']['realms:us']={'state':'success'}
            except Exception as exc:
                self.status['sources']['realms:us']={'state':'failed','error':type(exc).__name__}
                return self.status
            toys={'items':[],'pending':None}
            if 'toy' in settings.categories and hasattr(self.client,'toy_items'):
                try:
                    toys=self.client.toy_items(self.repository)
                    self.status['sources']['toys:us']={'state':'pending' if toys['pending'] else 'success','pending':toys['pending']}
                except Exception as exc:
                    self.status['sources']['toys:us']={'state':'failed','error':type(exc).__name__}
            tsm=self.repository.latest('tsm:us')
            # Fetch each house once, then discover buy identities before enriching any
            # destination. Shared item metadata is independent of ask and house order.
            values={r.item_id:r for r in tsm.records} if tsm else {}
            snapshots={}; counts={}
            buy_houses={mapping[slug] for slug in settings.buying_realms}
            for house in sorted({mapping[slug] for slug in selected}):
                key=f'auctions:us:{house}'
                try:
                    snapshots[house]=self.client.auctions(house,self.repository.latest(key))
                    counts[house]={'metadata_fetched':0,'metadata_skipped':0}
                except Exception as exc:
                    self.status['sources'][key]={'state':'failed','error':type(exc).__name__}
            metadata_by_item={}
            for snapshot in snapshots.values():
                for auction in snapshot.records:
                    if auction.item_id not in metadata_by_item:
                        metadata_by_item[auction.item_id]=self.repository.cache_fresh(f'item:us:{auction.item_id}',7*86400)
            now=datetime.now(timezone.utc); attempted=set(); pending_by_house={}
            valuation_fresh=bool(tsm and is_fresh(tsm.source_time,settings.max_tsm_age_hours,now))
            for house in sorted(buy_houses & snapshots.keys()):
                snapshot=snapshots[house]
                try:
                    pending=set()
                    for auction in snapshot.records:
                        item=auction.item_id; valuation=values.get(item)
                        if metadata_by_item[item] is not None or item in attempted: continue
                        # Use the engine's exact Decimal cutoff, demand, costs and
                        # budgets, assuming only the unknown classification is eligible.
                        plausible=replace(auction,source_time=snapshot.source_time,
                            category=next(iter(settings.categories),'unknown'),
                            metadata={'simple_identity':True,'tradeability':True})
                        if not valuation_fresh or not valuation or exclusion_reason(plausible,valuation,settings,now): continue
                        if counts[house]['metadata_fetched']>=100:
                            pending.add(item); continue
                        attempted.add(item)
                        metadata=self.client.item(item)
                        cache_key=f'item:us:{item}'
                        self.repository.cache_put(cache_key,metadata); self.repository.cache_timed(cache_key,metadata)
                        metadata_by_item[item]=metadata; counts[house]['metadata_fetched']+=1
                    pending_by_house[house]=pending
                except Exception as exc:
                    self.status['sources'][f'auctions:us:{house}']={'state':'failed','error':type(exc).__name__}
                    del snapshots[house]
            for house,pending in pending_by_house.items():
                counts[house]['metadata_skipped']=sum(metadata_by_item[item] is None for item in pending)
            toy_ids=set(toys['items'])
            for item,metadata in metadata_by_item.items():
                if metadata is not None and item in toy_ids:
                    metadata_by_item[item]={**metadata,'category':'toy'}
            for house,snapshot in snapshots.items():
                key=f'auctions:us:{house}'
                try:
                    records=[]
                    for auction in snapshot.records:
                        metadata=metadata_by_item[auction.item_id] or {}
                        variants=json.loads(auction.variant_key) if auction.variant_key else {}
                        simple=all(k in {'context','bonus_lists','modifiers'} and v in (0,[],None) for k,v in variants.items())
                        metadata={**metadata,'simple_identity':simple,'tradeability':metadata.get('tradeability')}
                        records.append(replace(auction,metadata=metadata,category=metadata.get('category','unknown')))
                    identity=self.repository.publish(replace(snapshot,records=tuple(records)))
                    self.status['sources'][key]={'state':'success','snapshot_id':identity,**counts[house]}
                    if self.repository.maintenance_warning: self.status['sources'][key]['maintenance_warning']=self.repository.maintenance_warning
                except Exception as exc:
                    # Never expose upstream response bodies, headers or credentials.
                    self.status['sources'][key]={'state':'failed','error':type(exc).__name__}
            return self.status
        finally:
            self.status['running']=False; self._lock.release()
