"""Record liveness and bounded exclusion retention, without RSS timing thresholds."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import gc
from pathlib import Path
import weakref

from wowflipper.models import Auction, Snapshot, Valuation
from wowflipper.query import MarketQuery
from wowflipper.repository import Repository


def generated_repo(tmp_path,monkeypatch,check_previous=False):
    repo=Repository(tmp_path)
    repo.save_settings(replace(repo.load_settings(),buying_realms=('home','second','third'),selling_realms=('destination','sibling')))
    repo.cache_put('realms',[{'slug':slug,'house_id':house} for slug,house in [('home',1),('second',2),('third',3),('destination',4),('sibling',4)]])
    now=datetime.now(timezone.utc)
    value=Valuation(2,'Recipe',10000000,Decimal('.2'),Decimal(2),10000000,10000000,now)
    records={};snapshots={};reads=[]
    def latest(source,**kwargs):
        assert kwargs.get('include_payload') is False
        if source=='tsm:us':return Snapshot(source,now,now,None,'fixture',(value,),id=10)
        if source=='tsm-pets:us':return None
        house=int(source.rsplit(':',1)[1]);reads.append(house)
        if check_previous:
            for prior in (h for h in records if h in (1,2,3)):
                assert snapshots[prior]() is None,'Prior buying-only Snapshot retained into next house load'
                assert all(record() is None for record in records[prior]),'Prior buying-only raw records retained'
        auction=Auction('us',house,1,2,1,1000000,None,now,'recipe',{'simple_identity':True,'tradeability':True})
        rows=(auction,)+tuple(replace(auction,auction_id=i+2,item_id=999) for i in range(200))
        records[house]=[weakref.ref(record) for record in rows]
        snapshot=Snapshot(source,now,now,None,'fixture',rows,id=house)
        snapshots[house]=weakref.ref(snapshot)
        return snapshot
    monkeypatch.setattr(repo,'latest',latest)
    return repo,records,reads


def test_buying_only_snapshots_and_raw_records_die_before_next_house_load(tmp_path,monkeypatch):
    repo,records,reads=generated_repo(tmp_path,monkeypatch,check_previous=True)
    query=MarketQuery(repo)
    assert sorted(reads)==[1,2,3,4]
    assert len(query.candidates)==3 and len(query.exclusions)==600
    assert all(query.snapshots[house].records==() for house in (1,2,3))
    assert len(query.snapshots[4].records)==1
    assert query.destination_snapshots['destination'] is query.destination_snapshots['sibling']
    row=query.rows({})[0]
    assert row['mode']=='fixture' and row['comparisons'][0]['listing_count']==1


def test_exclusions_are_disk_backed_ordered_paged_and_thread_safe(tmp_path,monkeypatch):
    repo,records,reads=generated_repo(tmp_path,monkeypatch)
    query=MarketQuery(repo)
    assert not isinstance(query.exclusions,list),'Exclusion dicts must not be retained in a Python list'
    assert len(query.exclusions)==600
    assert query.exclusions[0]=={'key':('us',1,2),'item_id':999,'name':'Item 999','reason':'missing-valuation'}
    assert query.exclusions[-1]['key']==('us',3,201)
    assert [row['key'] for row in query.exclusions[198:203]]==[('us',1,200),('us',1,201),('us',2,2),('us',2,3),('us',2,4)]
    assert [row['key'] for row in query.exclusions[3:0:-1]]==[('us',1,5),('us',1,4),('us',1,3)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        pages=list(pool.map(lambda page:query.exclusions_page({'page':str(page),'page_size':'100'}),range(1,7)))
    assert all(page['total']==600 for page in pages)
    assert [row for page in pages for row in page['rows']]==query.exclusions_page({'page_size':'100'})['rows']+[
        {**row,'key':list(row['key'])} for row in query.exclusions[100:]]


def test_old_query_spool_lives_through_cache_replacement_then_cleans_up(tmp_path,monkeypatch):
    repo,records,reads=generated_repo(tmp_path,monkeypatch)
    original=MarketQuery(repo);shared=MarketQuery(repo)
    assert shared.exclusions is original.exclusions
    path=Path(original.exclusions.path);assert path.exists()
    repo.save_settings(replace(repo.load_settings(),min_profit=20000000))
    replacement=MarketQuery(repo)
    assert replacement.exclusions is not original.exclusions
    assert len(original.exclusions)==600 and path.exists()
    assert original.exclusions_page({'page':'6'})['rows'][-1]['key']==['us',3,201]
    del original,shared;gc.collect()
    assert not path.exists()
    replacement_path=Path(replacement.exclusions.path)
    repo._analysis_cache=None;del replacement;gc.collect()
    assert not replacement_path.exists()


def test_all_buy_sell_houses_keep_only_relevant_records_and_build_one_shared_index(tmp_path,monkeypatch):
    import wowflipper.query as module
    repo,records,reads=generated_repo(tmp_path,monkeypatch)
    repo.save_settings(replace(repo.load_settings(),selling_realms=('home','second','third')))
    index_calls=[];original_index=module.index_destinations
    def index(snapshots):
        index_calls.append(True);return original_index(snapshots)
    monkeypatch.setattr(module,'index_destinations',index)
    query=MarketQuery(repo)
    assert reads==[1,2,3,1,2,3]  # Intentional second pass instead of retaining all houses.
    assert index_calls==[True]
    assert all(len(snapshot.records)==1 for snapshot in query.snapshots.values())
    assert all(sum(record() is not None for record in refs)==1 for refs in records.values())
    assert all(len(query.destination_index[f'auctions:us:{house}'][2])==1 for house in (1,2,3))


def test_destination_retains_all_relevant_asks_for_existing_engine_checks(tmp_path,monkeypatch):
    from wowflipper.engine import compare_destinations
    repo,records,reads=generated_repo(tmp_path,monkeypatch)
    original=repo.latest;full_destination=[]
    def latest(source,**kwargs):
        snapshot=original(source,**kwargs)
        if source=='auctions:us:4':
            first=snapshot.records[0]
            rows=(first,replace(first,auction_id=900,metadata={'simple_identity':False,'tradeability':True}),
                  replace(first,auction_id=901,metadata={'simple_identity':True,'tradeability':False}),
                  replace(first,auction_id=902,variant_key='{"bonus_lists":[123]}'),
                  replace(first,auction_id=903,buyout=100000000))
            snapshot=replace(snapshot,records=rows+snapshot.records[1:]);full_destination.append(snapshot)
        return snapshot
    monkeypatch.setattr(repo,'latest',latest)
    query=MarketQuery(repo);candidate=next(iter(query.candidates.values()))
    retained=query.destination_snapshots['destination']
    assert {auction.auction_id for auction in retained.records}=={1,900,901,902,903}
    full={slug:full_destination[0] for slug in ('destination','sibling')}
    expected=compare_destinations(candidate,full,query.settings,query.now)
    actual=compare_destinations(candidate,query.destination_snapshots,query.settings,query.now,query.destination_index)
    assert actual==expected and actual[0]['listing_count']==2
