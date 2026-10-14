"""Revision-scoped auction analysis and a shared visible/export query."""
from dataclasses import asdict, replace
from datetime import datetime, timezone, timedelta
import json
from .repository import encode
from .models import is_fresh
from .engine import evaluate, exclusion_reason, rank, compare_destinations, index_destinations

def safe(value):
    return json.loads(json.dumps(value,default=encode))

class MarketQuery:
    def __init__(self,repo):
        # One bounded entry per repository, shared across all HTTP request threads.
        with repo._analysis_lock:
            while True:
                now=datetime.now(timezone.utc)
                revision=repo.analysis_revision()
                cached=repo._analysis_cache
                if cached and cached[0]==revision and cached[1]<=now<=cached[2]:
                    self.__dict__.update(cached[3])
                    self.now=now
                    return
                self._build(repo)
                if repo.analysis_revision()!=revision:
                    continue
                valid_until=self._valid_until()
                finished=datetime.now(timezone.utc)
                if finished<self.now or finished>valid_until:
                    continue
                repo._analysis_cache=(revision,self.now,valid_until,self.__dict__.copy())
                self.now=finished
                return

    def _valid_until(self):
        deadlines=[]
        times=[(s.source_time,self.settings.max_auction_age_hours) for s in self.snapshots.values() if s]
        if self.tsm:
            times.append((self.tsm.source_time,self.settings.max_tsm_age_hours))
            times.extend((v.updated_at,self.settings.max_tsm_age_hours) for v in self.values.values())
        for source_time,hours in times:
            if source_time is None: continue
            try:
                expiry=source_time+timedelta(hours=hours)
            except OverflowError:
                # No representable expiry; is_fresh retains its existing semantics.
                expiry=None
            if expiry is not None and expiry>=self.now: deadlines.append(expiry)
            # timestamp() also rejects timestamps more than five minutes ahead.
            try:
                eligible=source_time-timedelta(minutes=5)
            except OverflowError:
                continue
            if eligible>self.now: deadlines.append(eligible-timedelta(microseconds=1))
        return min(deadlines,default=datetime.max.replace(tzinfo=timezone.utc))

    def _build(self,repo):
        self.repo=repo; self.settings=repo.load_settings(); self.now=datetime.now(timezone.utc)
        self.realms=repo.cache_get('realms') or []
        self.mapping={r['slug']:r['house_id'] for r in self.realms}
        self.tsm=repo.latest('tsm:us',include_payload=False)
        self.values={v.item_id:v for v in self.tsm.records} if self.tsm else {}
        self.snapshots={}
        self.candidates={}; self.exclusions=[]
        selected=set(self.settings.buying_realms)|set(self.settings.selling_realms)
        for slug in selected:
            house=self.mapping.get(slug)
            if house is None: continue
            if house not in self.snapshots: self.snapshots[house]=repo.latest(f'auctions:us:{house}',include_payload=False)
        self.destination_snapshots={s:self.snapshots.get(self.mapping.get(s)) for s in self.settings.selling_realms}
        self.destination_index=index_destinations(self.destination_snapshots)
        buy_houses={self.mapping[s] for s in self.settings.buying_realms if s in self.mapping}
        valuation_fresh=bool(self.tsm and is_fresh(self.tsm.source_time,self.settings.max_tsm_age_hours,self.now))
        for house in sorted(buy_houses):
            snapshot=self.snapshots.get(house)
            if not snapshot: continue
            provenance=json.dumps({'auction_snapshot':snapshot.id,'auction_hash':snapshot.content_hash,'auction_source_time':str(snapshot.source_time),'valuation_snapshot':self.tsm.id if self.tsm else None,'valuation_hash':self.tsm.content_hash if self.tsm else None,'valuation_source_time':str(self.tsm.source_time) if self.tsm else None,'valuation_mode':self.tsm.mode if self.tsm else None,'mode':snapshot.mode},sort_keys=True)
            for auction in snapshot.records:
                auction=replace(auction,source_time=snapshot.source_time)
                value=self.values.get(auction.item_id)
                candidate=evaluate(auction,value,self.settings,self.now) if value and valuation_fresh else None
                key=(auction.region,house,auction.auction_id)
                if candidate:
                    self.candidates[key]=replace(candidate,auction=replace(auction,provenance=provenance))
                else: self.exclusions.append({'key':key,'item_id':auction.item_id,'name':value.name if value else f'Item {auction.item_id}','reason':('stale_or_unknown_valuation_source' if value and not valuation_fresh else exclusion_reason(auction,value,self.settings,self.now)) if value else 'missing-valuation'})
    def candidate(self,key): return self.candidates.get(tuple(key))
    def _ordered(self,args):
        query=args.get('q','').casefold().strip()
        candidates=[c for c in rank(self.candidates.values()) if query in c.valuation.name.casefold() or query in str(c.auction.item_id)]
        sort=args.get('sort','profit'); direction=args.get('direction','desc')
        sorts={'name':lambda c:c.valuation.name.casefold(),'cost':lambda c:c.auction.buyout,'profit':lambda c:c.profit,'roi':lambda c:c.roi,'quantity':lambda c:c.auction.quantity,'sale_rate':lambda c:c.valuation.sale_rate,'sold_per_day':lambda c:c.valuation.sold_per_day}
        if sort=='ranking': sort='profit'  # Legacy query compatibility.
        if sort not in sorts or direction not in ('asc','desc'): raise ValueError('Unknown sort')
        candidates.sort(key=sorts[sort],reverse=direction=='desc')
        return candidates
    @staticmethod
    def page_info(args,total):
        try:
            page=int(args.get('page',1)); size=min(100,int(args.get('page_size',100)))
        except (ValueError,TypeError): raise ValueError('Page and page size must be positive integers')
        if page<1 or size<1: raise ValueError('Page and page size must be positive integers')
        pages=max(1,(total+size-1)//size); page=min(page,pages)
        return {'total':total,'page':page,'page_size':size,'pages':pages}
    def page(self,args):
        candidates=self._ordered(args)
        info=self.page_info(args,len(candidates))
        start=(info['page']-1)*info['page_size']
        return {'rows':self._rows(candidates[start:start+info['page_size']]),
                'exclusion_total':len(self.exclusions),**info}
    def exclusions_page(self,args):
        info=self.page_info(args,len(self.exclusions)); start=(info['page']-1)*info['page_size']
        return {'rows':safe(self.exclusions[start:start+info['page_size']]),**info}
    def rows(self,args):
        return self.page(args)['rows']
    def _rows(self,candidates):
        rows=[]
        for candidate in candidates:
            row=safe(asdict(candidate))
            row['comparisons']=safe(compare_destinations(candidate,self.destination_snapshots,self.settings,self.now,self.destination_index))
            row['source_realms']=[r['slug'] for r in self.realms if r['house_id']==candidate.auction.house_id]
            row['mode']=self.snapshots[candidate.auction.house_id].mode
            if self.tsm.mode.startswith('offline') and not row['mode'].startswith('offline'): row['mode']='offline-mixed'
            for comparison in row['comparisons']:
                snapshot=self.destination_snapshots.get(comparison['realm'])
                comparison['mode']=snapshot.mode if snapshot else 'missing'
                comparison['content_hash']=snapshot.content_hash if snapshot else None
                comparison['source_key']=snapshot.source_key if snapshot else None
                comparison['fetched_at']=safe(snapshot.fetched_at) if snapshot else None
            rows.append(row)
        return rows
