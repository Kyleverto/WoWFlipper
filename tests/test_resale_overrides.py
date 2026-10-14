from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import csv, io
import pytest
from wowflipper.models import Auction, Valuation, Snapshot, MAX_SAFE_INTEGER
from wowflipper.engine import evaluate, exclusion_reason, compare_destinations
from wowflipper.query import MarketQuery
from wowflipper.repository import Repository
from wowflipper.portfolio import Portfolio
from wowflipper.web import create_app

@pytest.fixture
def market(tmp_path):
    repo=Repository(tmp_path)
    now=datetime.now(timezone.utc)-timedelta(minutes=1)
    repo.save_settings(replace(repo.load_settings(), min_profit=0, min_roi=Decimal(0), discount=Decimal(0), max_copies=5, buying_realms=('home','away'), selling_realms=('away',)))
    repo.cache_put('realms',[{'slug':'home','house_id':1},{'slug':'away','house_id':2}])
    v=Valuation(100,'Recipe',94990000,Decimal('.2'),Decimal(2),1,1,now)
    repo.publish(Snapshot('tsm:us',now,now,None,'tsm',(v,)))
    for house in (1,2):
        a=Auction('us',house,house,100,1,60000000,None,now,'recipe',{'simple_identity':True,'tradeability':True})
        repo.publish(Snapshot(f'auctions:us:{house}',now,now,None,'live',(a,replace(a,auction_id=house+10,buyout=61000000))))
    repo.save_settings(replace(repo.load_settings(),item_exposure=100000000))
    return repo,now

def test_lower_manual_resale_excludes_all_realms_and_clear_restores(market):
    repo,now=market
    query=MarketQuery(repo); originals=query.page({})['rows']; assert len(originals)==4
    override=repo.set_resale_override(100,56130000)
    for c in query.candidates.values():
        assert evaluate(c.auction,c.valuation,query.settings,now,override) is None
        assert exclusion_reason(c.auction,c.valuation,query.settings,now,override)=='discount_below_threshold'
    assert MarketQuery(repo).page({})['total']==0
    assert Repository(repo.path).load_resale_overrides()[100]==override
    assert repo.latest('tsm:us').records[0].avg_sale_price==94990000
    assert repo.latest('tsm:us').source_time==now
    repo.remove_resale_override(100)
    assert MarketQuery(repo).page({})['rows']==originals

def test_override_final_unit_factor_bypass_stacks_and_destination(market):
    repo,now=market
    repo.save_settings(replace(repo.load_settings(),resale_factor=Decimal('.1')))
    assert MarketQuery(repo).page({})['total']==0
    saved=repo.set_resale_override(100,100000000)
    q=MarketQuery(repo)
    assert q.page({})['total']==4
    for c in q.candidates.values():
        assert c.assumed_unit_resale==100000000
        assert c.resale_override==saved
        assert c.proceeds==95000000
        assert c.valuation.avg_sale_price==94990000
        result=compare_destinations(c,q.destination_snapshots,q.settings,now,q.destination_index)
        assert result[0]['assumed_unit_resale']==100000000
        assert 'competition_below_resale' in result[0]['risks']
        stack=evaluate(replace(c.auction,quantity=2),c.valuation,q.settings,now,saved)
        assert stack.proceeds==190000000
    snapshot=repo.latest('tsm:us')
    repo.publish(replace(snapshot,source_time=now-timedelta(hours=49)))
    assert MarketQuery(repo).page({})['total']==0

def test_override_mutation_during_build_retries_and_aba_revision(market,monkeypatch):
    repo,_=market
    initial=repo.analysis_revision();repo.set_resale_override(100,56130000);repo.remove_resale_override(100)
    assert repo.analysis_revision()!=initial
    original=repo.latest;changed=[]
    def latest(source,**kwargs):
        snap=original(source,**kwargs)
        if not changed:
            changed.append(True);Repository(repo.path).set_resale_override(100,56130000)
        return snap
    monkeypatch.setattr(repo,'latest',latest)
    assert MarketQuery(repo).page({})['total']==0
    assert MarketQuery(repo).page({})['total']==0

def test_purchase_revalidates_current_override_and_freezes_provenance(market):
    repo,_=market;q=MarketQuery(repo)
    # Deliberately stale provider proves transaction-local valuation revalidation.
    p=Portfolio(repo,lambda key:q.candidate(key))
    p.select(('us',1,1),destination='away',request_id='select')
    repo.set_resale_override(100,56130000)
    assert not p.shopping()[0]['valid']
    with pytest.raises(ValueError): p.record({'type':'purchase','key':['us',1,1]},'blocked')
    saved=repo.set_resale_override(100,100000000)
    assert p.shopping()[0]['candidate']['assumed_unit_resale']=='100000000'
    bought=p.record({'type':'purchase','key':['us',1,1]},'purchase')
    assert bought['candidate']['assumed_unit_resale']=='100000000'
    assert bought['candidate']['resale_override']==saved
    assert bought['candidate']['valuation']['avg_sale_price']==94990000
    repo.remove_resale_override(100);repo.set_resale_override(100,1)
    assert p.history()[0]==bought

def test_api_validation_csrf_persistence_csv_and_nonvisible_item(market):
    repo,_=market;client=create_app(repo.path,testing=True).test_client()
    csrf=client.get('/api/state').json['csrf'];headers={'X-CSRF-Token':csrf}
    assert client.post('/api/resale-overrides',json={'item_id':100,'unit_price':100000000}).status_code==403
    for value in (0,-1,True,1.5,MAX_SAFE_INTEGER+1,'NaN'):
        for field in ('item_id','unit_price'):
            data={'item_id':100,'unit_price':100000000,field:value}
            assert client.post('/api/resale-overrides',json=data,headers=headers).status_code==400
    assert client.post('/api/resale-overrides',json={'item_id':100,'unit_price':100000000,'unexpected':1},headers=headers).status_code==400
    saved=client.post('/api/resale-overrides',json={'item_id':100,'unit_price':100000000},headers=headers).json
    assert client.get('/api/resale-overrides').json==[saved]
    assert client.get('/api/state').json['resale_overrides']==[saved]
    rows=list(csv.DictReader(io.StringIO(client.get('/api/export').text)))
    assert len(rows)==4
    assert rows[0]['Assumed unit resale copper']=='100000000'
    assert rows[0]['Original TSM average sale price copper']=='94990000'
    assert rows[0]['Resale price source']=='manual_override'
    assert rows[0]['Override updated at']==saved['updated_at']
    assert client.post('/api/resale-overrides',json={'item_id':999,'unit_price':12345},headers=headers).status_code==200
    assert client.post('/api/resale-overrides/remove',json={'item_id':100},headers=headers).status_code==200
    assert repo.load_resale_overrides().keys()=={999}

@pytest.mark.parametrize('manual,expected',[(10000000,True),(1000000,False)])
def test_refresh_metadata_prefilter_uses_final_override(tmp_path,manual,expected):
    from wowflipper.refresh import RefreshService
    from wowflipper.settings import Settings
    repo=Repository(tmp_path);now=datetime.now(timezone.utc)
    v=Valuation(7,'Recipe',10000000,Decimal('.2'),Decimal(2),1,1,now)
    repo.publish(Snapshot('tsm:us',now,now,None,'fixture',(v,)))
    repo.set_resale_override(7,manual)
    class TSM:
        def region(self,previous): return previous
    class Client:
        def __init__(self): self.calls=[]
        def realms(self): return [{'slug':'agamaggan','house_id':1,'category':'United States','is_auction_realm':True}]
        def auctions(self,*args): return Snapshot('auctions:us:1',now,now,None,'fixture',(Auction('us',1,1,7,1,7000000,None),))
        def item(self,item): self.calls.append(item); return {'category':'recipe','tradeability':True}
    client=Client()
    # Low factor would reject even the high manual override if applied twice.
    service=RefreshService(repo,client,TSM())
    service.run(replace(Settings(),resale_factor=Decimal('.1')))
    assert bool(client.calls)==expected
    assert bool(MarketQuery(repo).rows({}))==expected
    assert repo.latest('tsm:us').records[0]==v
    service._worker.shutdown()
