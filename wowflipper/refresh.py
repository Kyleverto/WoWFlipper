"""One refresh worker with pollable status and per-house failure isolation."""
from dataclasses import replace
import json, threading
from concurrent.futures import ThreadPoolExecutor

class RefreshService:
    def __init__(self,repository,client):
        self.repository=repository; self.client=client
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
            realms=self.repository.cache_get('realms')
            if realms is None:
                realms=self.client.realms(); self.repository.cache_put('realms',realms)
            selected=set(settings.buying_realms)|set(settings.selling_realms)
            mapping={r['slug']:r['house_id'] for r in realms}
            unknown=selected-set(mapping)
            if unknown: raise ValueError('Selected realm was not discovered')
            tsm=self.repository.latest('tsm:us')
            # Fetch metadata only for priced, discounted plausible candidates.
            values={r.item_id:r for r in tsm.records} if tsm else {}
            for house in sorted({mapping[slug] for slug in selected}):
                key=f'auctions:us:{house}'
                try:
                    snapshot=self.client.auctions(house,self.repository.latest(key)); records=[]; enriched=0; skipped=0
                    for auction in snapshot.records:
                        valuation=values.get(auction.item_id); metadata=None
                        if valuation and auction.buyout <= int(valuation.avg_sale_price*auction.quantity*(1-settings.discount)):
                            cache_key=f'item:us:{auction.item_id}'; metadata=self.repository.cache_get(cache_key)
                            if metadata is None:
                                if enriched<100:
                                    metadata=self.client.item(auction.item_id); self.repository.cache_put(cache_key,metadata); enriched+=1
                                else: skipped+=1
                        variants=json.loads(auction.variant_key) if auction.variant_key else {}
                        simple=all(k in {'context','bonus_lists','modifiers'} and v in (0,[],None) for k,v in variants.items())
                        metadata={**(metadata or {}),'simple_identity':simple,'tradeability':(metadata or {}).get('tradeability')}
                        records.append(replace(auction,metadata=metadata,category=metadata.get('category','unknown')))
                    identity=self.repository.publish(replace(snapshot,records=tuple(records)))
                    self.status['sources'][key]={'state':'success','snapshot_id':identity,'metadata_fetched':enriched,'metadata_skipped':skipped}
                except Exception as exc:
                    # Never expose upstream response bodies, headers or credentials.
                    self.status['sources'][key]={'state':'failed','error':type(exc).__name__}
            return self.status
        finally:
            self.status['running']=False; self._lock.release()
