"""Request-scoped indexing and a shared visible/export query."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from .repository import encode
from .models import is_fresh
from .engine import evaluate, exclusion_reason, rank, compare_destinations

def safe(value):
    return json.loads(json.dumps(value,default=encode))

class MarketQuery:
    def __init__(self,repo):
        self.repo=repo; self.settings=repo.load_settings(); self.now=datetime.now(timezone.utc)
        self.realms=repo.cache_get('realms') or []
        self.mapping={r['slug']:r['house_id'] for r in self.realms}
        self.tsm=repo.latest('tsm:us')
        self.values={v.item_id:v for v in self.tsm.records} if self.tsm else {}
        self.snapshots={}
        self.auctions={}
        self.candidates={}; self.exclusions=[]
        selected=set(self.settings.buying_realms)|set(self.settings.selling_realms)
        for slug in selected:
            house=self.mapping.get(slug)
            if house is None: continue
            if house not in self.snapshots: self.snapshots[house]=repo.latest(f'auctions:us:{house}')
        buy_houses={self.mapping[s] for s in self.settings.buying_realms if s in self.mapping}
        for house in buy_houses:
            snapshot=self.snapshots.get(house)
            if not snapshot: continue
            for auction in snapshot.records:
                provenance=json.dumps({'auction_snapshot':snapshot.id,'auction_hash':snapshot.content_hash,'auction_source_time':str(snapshot.source_time),'valuation_snapshot':self.tsm.id if self.tsm else None,'valuation_hash':self.tsm.content_hash if self.tsm else None,'valuation_source_time':str(self.tsm.source_time) if self.tsm else None,'valuation_mode':self.tsm.mode if self.tsm else None,'mode':snapshot.mode},sort_keys=True)
                auction=replace(auction,source_time=snapshot.source_time,provenance=provenance)
                value=self.values.get(auction.item_id)
                valuation_fresh=bool(self.tsm and is_fresh(self.tsm.source_time,self.settings.max_tsm_age_hours,self.now))
                candidate=evaluate(auction,value,self.settings,self.now) if value and valuation_fresh else None
                key=(auction.region,house,auction.auction_id)
                self.auctions[key]=auction
                if candidate: self.candidates[key]=candidate
                else: self.exclusions.append({'key':key,'item_id':auction.item_id,'name':value.name if value else f'Item {auction.item_id}','reason':('stale_or_unknown_valuation_source' if value and not valuation_fresh else exclusion_reason(auction,value,self.settings,self.now)) if value else 'missing-valuation'})
    def candidate(self,key): return self.candidates.get(tuple(key))
    def rows(self,args):
        strategy=args.get('strategy','stronger-demand')
        if strategy not in ('stronger-demand','longer-term'): raise ValueError('Unknown ranking')
        query=args.get('q','').casefold().strip()
        candidates=[c for c in rank(self.candidates.values(),strategy) if query in c.valuation.name.casefold() or query in str(c.auction.item_id)]
        sort=args.get('sort','ranking'); direction=args.get('direction','desc')
        sorts={'name':lambda c:c.valuation.name.casefold(),'cost':lambda c:c.auction.buyout,'profit':lambda c:c.profit,'roi':lambda c:c.roi,'quantity':lambda c:c.auction.quantity,'sale_rate':lambda c:c.valuation.sale_rate,'sold_per_day':lambda c:c.valuation.sold_per_day}
        if sort!='ranking':
            if sort not in sorts or direction not in ('asc','desc'): raise ValueError('Unknown sort')
            candidates.sort(key=sorts[sort],reverse=direction=='desc')
        destination_snapshots={s:self.snapshots.get(self.mapping.get(s)) for s in self.settings.selling_realms}
        rows=[]
        for candidate in candidates:
            row=safe(asdict(candidate))
            row['comparisons']=safe(compare_destinations(candidate,destination_snapshots,self.settings,self.now))
            row['source_realms']=[r['slug'] for r in self.realms if r['house_id']==candidate.auction.house_id]
            row['mode']=self.snapshots[candidate.auction.house_id].mode
            if self.tsm.mode.startswith('offline') and not row['mode'].startswith('offline'): row['mode']='offline-mixed'
            for comparison in row['comparisons']:
                snapshot=destination_snapshots.get(comparison['realm'])
                comparison['mode']=snapshot.mode if snapshot else 'missing'
            rows.append(row)
        return rows
