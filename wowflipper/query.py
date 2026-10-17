"""Revision-scoped auction analysis and a shared visible/export query."""
from dataclasses import asdict, replace
from datetime import datetime, timezone, timedelta
import json
from .repository import encode
from .query_exclusions import ExclusionStore
from .models import Auction, is_fresh
from .identity import pet_details, market_key, override_key
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
                self.revision=revision
                repo._analysis_cache=(revision,self.now,valid_until,self.__dict__.copy())
                self.now=finished
                return

    def _valid_until(self):
        deadlines=[]
        times=[(s.source_time,self.settings.max_auction_age_hours) for s in self.snapshots.values() if s]
        for snapshot in (self.tsm,self.pet_tsm):
            if snapshot: times.append((snapshot.source_time,self.settings.max_tsm_age_hours))
        times.extend((v.updated_at,self.settings.max_tsm_age_hours) for v in (*self.values.values(),*self.pet_values.values()))
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
        self.resale_overrides=repo.load_resale_overrides()
        self.realms=repo.cache_get('realms') or []
        self.mapping={r['slug']:r['house_id'] for r in self.realms}
        self.tsm=repo.latest('tsm:us',include_payload=False)
        self.pet_tsm=repo.latest('tsm-pets:us',include_payload=False)
        self.values={v.item_id:v for v in self.tsm.records if v.pet_species_id is None} if self.tsm else {}
        self.pet_values={v.pet_species_id:v for v in self.pet_tsm.records if v.pet_species_id is not None} if self.pet_tsm else {}
        self.snapshots={}
        self.candidates={}; self.exclusions=ExclusionStore()
        buy_houses={self.mapping[s] for s in self.settings.buying_realms if s in self.mapping}
        sell_houses={self.mapping[s] for s in self.settings.selling_realms if s in self.mapping}
        # Analyze one buying house at a time. Keep only its small provenance
        # header and eligible candidates; raw rejected listings can be released
        # before the next repository read.
        try:
            for house in sorted(buy_houses):
                snapshot=repo.latest(f'auctions:us:{house}',include_payload=False)
                self.snapshots[house]=replace(snapshot,records=()) if snapshot else None
                if not snapshot: continue
                provenance_by_kind={}
                fresh_by_kind={}
                for kind,valuation_snapshot in ((False,self.tsm),(True,self.pet_tsm)):
                    fresh_by_kind[kind]=bool(valuation_snapshot and is_fresh(valuation_snapshot.source_time,self.settings.max_tsm_age_hours,self.now))
                    provenance_by_kind[kind]=json.dumps({'auction_snapshot':snapshot.id,'auction_hash':snapshot.content_hash,'auction_source_time':str(snapshot.source_time),'valuation_snapshot':valuation_snapshot.id if valuation_snapshot else None,'valuation_hash':valuation_snapshot.content_hash if valuation_snapshot else None,'valuation_source_time':str(valuation_snapshot.source_time) if valuation_snapshot else None,'valuation_mode':valuation_snapshot.mode if valuation_snapshot else None,'mode':snapshot.mode},sort_keys=True)
                for auction in snapshot.records:
                    pet=pet_details(auction)
                    valuation_fresh=fresh_by_kind[bool(pet)]
                    provenance=provenance_by_kind[bool(pet)]
                    auction=replace(auction,source_time=snapshot.source_time)
                    value=self.pet_values.get(pet['species_id']) if pet else self.values.get(auction.item_id)
                    candidate=evaluate(auction,value,self.settings,self.now,self.resale_overrides.get(override_key(auction))) if value and valuation_fresh else None
                    key=(auction.region,house,auction.auction_id)
                    if candidate:
                        self.candidates[key]=replace(candidate,auction=replace(auction,provenance=provenance))
                    else: self.exclusions.append({'key':key,'item_id':auction.item_id,'name':value.name if value else f'Item {auction.item_id}','reason':('stale_or_unknown_valuation_source' if value and not valuation_fresh else exclusion_reason(auction,value,self.settings,self.now,self.resale_overrides.get(override_key(auction)))) if value else 'missing-valuation'})
                del snapshot
        finally:
            self.exclusions.seal()
        # Candidate identities are now complete across all buying houses. A
        # second pass retains every destination listing of those exact identities,
        # including expensive/unknown/untradeable asks for the existing comparison
        # checks to decide. Overlapping buy/sell houses are deliberately re-read
        # instead of retaining the entire selling universe during analysis.
        relevant={override_key(candidate.auction) for candidate in self.candidates.values()}
        for house in sorted(sell_houses):
            snapshot=repo.latest(f'auctions:us:{house}',include_payload=False)
            self.snapshots[house]=replace(snapshot,records=tuple(auction for auction in snapshot.records if isinstance(auction,Auction) and override_key(auction) in relevant)) if snapshot else None
            del snapshot
        self.destination_snapshots={s:self.snapshots.get(self.mapping.get(s)) for s in self.settings.selling_realms}
        self.destination_index=index_destinations(self.destination_snapshots)
    def candidate(self,key): return self.candidates.get(tuple(key))
    def _ordered(self,args):
        query=args.get('q','').casefold().strip()
        candidates=[c for c in rank(self.candidates.values()) if query in c.valuation.name.casefold() or query in str(c.auction.item_id)]
        if 'new_after' in args:
            from .opportunity_updates import keys_since
            keys=keys_since(self.repo,args['new_after'])
            candidates=[candidate for candidate in candidates if candidate.key in keys]
        if 'categories' in args:
            selected=set(args['categories'].split(',')) if args['categories'] else set()
            if not selected <= {'recipe','mount','toy','decor','battle-pet'}:
                raise ValueError('Unknown category')
            candidates=[c for c in candidates if c.auction.category in selected]
        else:
            category=args.get('category','')
            if category and category != 'all': candidates=[c for c in candidates if c.auction.category==category]
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
            row['identity']=market_key(candidate.auction)
            row['pet']=pet_details(candidate.auction)
            row['comparisons']=safe(compare_destinations(candidate,self.destination_snapshots,self.settings,self.now,self.destination_index))
            row['source_realms']=[r['slug'] for r in self.realms if r['house_id']==candidate.auction.house_id]
            row['mode']=self.snapshots[candidate.auction.house_id].mode
            valuation_snapshot=self.pet_tsm if row['pet'] else self.tsm
            if valuation_snapshot.mode.startswith('offline') and not row['mode'].startswith('offline'): row['mode']='offline-mixed'
            for comparison in row['comparisons']:
                snapshot=self.destination_snapshots.get(comparison['realm'])
                comparison['mode']=snapshot.mode if snapshot else 'missing'
                comparison['content_hash']=snapshot.content_hash if snapshot else None
                comparison['source_key']=snapshot.source_key if snapshot else None
                comparison['fetched_at']=safe(snapshot.fetched_at) if snapshot else None
            rows.append(row)
        return rows
