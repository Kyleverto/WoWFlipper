from dataclasses import replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import csv, io
import pytest
from wowflipper.models import Auction, Valuation, Snapshot
from wowflipper.settings import Settings
from wowflipper.repository import Repository
from wowflipper.refresh import RefreshService
from wowflipper.query import MarketQuery
from wowflipper.engine import evaluate, compare_destinations
from wowflipper.web import create_app

@pytest.mark.parametrize('buy_house,sell_house',[(1,2),(2,1)])
@pytest.mark.parametrize('cached',[False,True])
def test_refresh_shared_metadata_competition(tmp_path,buy_house,sell_house,cached):
    repo=Repository(tmp_path); now=datetime.now(timezone.utc)
    repo.publish(Snapshot('tsm:us',now,now,None,'fixture',(Valuation(7,'Recipe',10000000,Decimal('.2'),Decimal(2),1,1,now),)))
    if cached: repo.cache_timed('item:us:7',{'category':'recipe','tradeability':True})
    settings=replace(Settings(),selling_realms=('away',))
    class TSM:
        def region(self,previous): return previous
    class Client:
        calls=[]
        def realms(self): return [{'slug':'agamaggan','house_id':buy_house,'category':'United States','is_auction_realm':True},{'slug':'away','house_id':sell_house,'category':'United States','is_auction_realm':True}]
        def auctions(self,house,previous):
            price=7000000 if house==buy_house else 9000000
            return Snapshot(f'auctions:us:{house}',now,now,None,'fixture',tuple(Auction('us',house,i,7,1,price,None) for i in range(1,4)))
        def item(self,item): self.calls.append(item); return {'category':'recipe','tradeability':True}
    client=Client(); cache_calls=[]; original=repo.cache_fresh_many
    def cache(keys,age):
        keys=list(keys); cache_calls.extend(keys); return original(keys,age)
    repo.cache_fresh_many=cache
    RefreshService(repo,client,TSM()).run(settings)
    rows=MarketQuery(repo).rows({})
    assert rows[0]['comparisons'][0]['listing_count']==3
    assert rows[0]['comparisons'][0]['lowest_unit_ask']=='9000000'
    assert 'competition_below_resale' in rows[0]['comparisons'][0]['risks']
    assert client.calls==([] if cached else [7])
    assert cache_calls.count('item:us:7')==1

@pytest.mark.parametrize('price,expected',[(9000000,True),(10200001,False)])
def test_refresh_resale_factor_precise_prefilter(tmp_path,price,expected):
    repo=Repository(tmp_path); now=datetime.now(timezone.utc)
    repo.publish(Snapshot('tsm:us',now,now,None,'fixture',(Valuation(7,'Recipe',10000000,Decimal('.2'),Decimal(2),1,1,now),)))
    class TSM:
        def region(self,previous): return previous
    class Client:
        calls=[]
        def realms(self): return [{'slug':'agamaggan','house_id':1,'category':'United States','is_auction_realm':True}]
        def auctions(self,*args): return Snapshot('auctions:us:1',now,now,None,'fixture',(Auction('us',1,1,7,1,price,None),))
        def item(self,item): self.calls.append(item); return {'category':'recipe','tradeability':True}
    client=Client(); RefreshService(repo,client,TSM()).run(replace(Settings(),resale_factor=Decimal('1.2')))
    assert bool(client.calls)==expected
    assert bool(MarketQuery(repo).rows({}))==expected

def market_fixture(path):
    repo=Repository(path); now=datetime.now(timezone.utc)
    repo.save_settings(replace(Settings(),selling_realms=('home','sibling','stale','missing'),resale_factor=Decimal('1.2'),auction_cut=Decimal('.07'),extra_costs=123))
    repo.cache_put('realms',[{'slug':slug,'house_id':house,'name':slug} for slug,house in [('agamaggan',1),('home',2),('sibling',2),('stale',3),('missing',4)]])
    value=Valuation(7,'Recipe',10000000,Decimal('.2'),Decimal(2),1,1,now)
    repo.publish(Snapshot('tsm:us',now,now,None,'offline-import',(value,)))
    a=Auction('us',1,1,7,1,7000000,None,now,'recipe',{'simple_identity':True,'tradeability':True})
    records=tuple(replace(a,auction_id=i) for i in range(1,206))+tuple(replace(a,auction_id=1000+i,category='unknown') for i in range(251))
    repo.publish(Snapshot('auctions:us:1',now,now,None,'offline-import',records))
    repo.publish(Snapshot('auctions:us:2',now,now,None,'offline-import',(replace(a,house_id=2,buyout=9000000),)))
    repo.publish(Snapshot('auctions:us:3',now-timedelta(hours=4),now,None,'offline-import',()))
    return repo

def test_index_equivalence_and_no_full_scan(tmp_path):
    repo=market_fixture(tmp_path); market=MarketQuery(repo)
    candidate=next(iter(market.candidates.values()))
    snaps={s:market.snapshots[market.mapping[s]] for s in market.settings.selling_realms}
    from wowflipper.engine import index_destinations
    index=index_destinations(snaps)
    expected=compare_destinations(candidate,snaps,market.settings,market.now)
    class NoScan:
        def __iter__(self): raise AssertionError('Repeated full destination scan')
    guarded={s:replace(snap,records=NoScan()) if snap else None for s,snap in snaps.items()}
    assert compare_destinations(candidate,guarded,market.settings,market.now,index)==expected
    assert len(index['auctions:us:2'][7])==1

def test_result_and_lazy_exclusion_pages_and_export(tmp_path):
    market_fixture(tmp_path); client=create_app(tmp_path,testing=True).test_client()
    query='?q=Recipe&sort=cost&direction=asc&page=2&page_size=999'
    response=client.get('/api/results'+query).json
    assert len(response['rows'])==100 and response['total']==205
    assert response['page']==2 and response['page_size']==100 and response['pages']==3
    assert response['exclusion_total']==251 and 'exclusions' not in response
    excluded=client.get('/api/exclusions?page=3&page_size=100').json
    assert len(excluded['rows'])==51 and excluded['total']==251
    exported=list(csv.DictReader(io.StringIO(client.get('/api/export'+query).text)))
    assert [int(r['Auction ID']) for r in exported]==[r['auction']['auction_id'] for r in response['rows']]
    row=exported[0]
    assert row['Auction cut']=='0.07' and row['Extra costs copper']=='123' and row['Resale factor']=='1.2' and row['Minimum discount']=='0.15'
    assert all(word in row['Destination evidence'] for word in ['competition_below_resale','stale_destination_evidence','absent_destination_evidence','unverified_transfer_eligibility','offline-import','source_time','snapshot_id','content_hash'])
    assert client.get('/api/results?page=0').status_code==400

def test_refresh_known_metadata_without_plausible_buy_and_bounded_requests(tmp_path):
    repo=Repository(tmp_path); now=datetime.now(timezone.utc)
    values=tuple(Valuation(i,f'Item {i}',10000000,Decimal('.2'),Decimal(2),1,1,now) for i in range(1,105))
    repo.publish(Snapshot('tsm:us',now,now,None,'fixture',values))
    repo.cache_timed('item:us:105',{'category':'mount','tradeability':True})
    class TSM:
        def region(self,previous): return previous
    class Client:
        calls=[]
        def realms(self): return [{'slug':'agamaggan','house_id':1,'category':'United States','is_auction_realm':True}]
        def auctions(self,*args):
            records=tuple(Auction('us',1,i,i,1,7000000,None) for i in range(1,105))
            # Cached classification applies even without valuation or a discounted ask.
            return Snapshot('auctions:us:1',now,now,None,'fixture',records+(Auction('us',1,105,105,1,99999999,None),))
        def item(self,item): self.calls.append(item); return {'category':'recipe','tradeability':True}
    client=Client(); result=RefreshService(repo,client,TSM()).run(Settings())
    assert len(client.calls)==100 and len(set(client.calls))==100
    assert result['sources']['auctions:us:1']['metadata_skipped']==4
    assert repo.latest('auctions:us:1').records[-1].category=='mount'

def test_query_builds_destination_index_once_per_house_and_compares_only_page(tmp_path,monkeypatch):
    repo=market_fixture(tmp_path)
    import wowflipper.query as query
    original=query.index_destinations; calls=[]
    def index(snapshots):
        calls.append([s.source_key if s else None for s in snapshots.values()]); return original(snapshots)
    monkeypatch.setattr(query,'index_destinations',index)
    market=query.MarketQuery(repo)
    assert len(calls)==1
    comparisons=[]; compare=query.compare_destinations
    def indexed(*args):
        assert args[4] is market.destination_index
        comparisons.append(args[0].key); return compare(*args)
    monkeypatch.setattr(query,'compare_destinations',indexed)
    page=market.page({'page':'3'})
    assert len(page['rows'])==5 and len(comparisons)==5
