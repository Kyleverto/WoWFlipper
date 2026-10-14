from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from wowflipper.repository import Repository
from wowflipper.models import Auction, Valuation, Candidate
from wowflipper.portfolio import Portfolio

@pytest.fixture
def portfolio(tmp_path):
    repo=Repository(tmp_path)
    repo.save_settings(replace(repo.load_settings(),selling_realms=('agamaggan',),max_copies=10))
    a=Auction('us',1,1,100,3,1000001,None,datetime.now(timezone.utc),'toy',{'simple_identity':True,'tradeability':True})
    v=Valuation(100,'Toy',2000000,Decimal('.1'),Decimal('1'),2000000,2000000,datetime.now(timezone.utc))
    c=Candidate(('us',1,1),a,v,6000000,4999999,Decimal('4'),('agamaggan',),(),'ok')
    return Portfolio(repo,lambda key:c if tuple(key)==c.key else None)

def buy(p):
    p.select(('us',1,1),'stronger-demand','agamaggan','s')
    return p.record({'type':'purchase','key':['us',1,1]},'p')

def test_idempotency(portfolio):
    first=buy(portfolio)
    assert portfolio.record({'type':'purchase','key':['us',1,1]},'p')==first
    assert portfolio.summary()['cash']==300000000-1000001
    with pytest.raises(ValueError): portfolio.record({'type':'fee','amount':1},'p')

def test_partial_cost_and_correction(portfolio):
    purchase=buy(portfolio)
    portfolio.record({'type':'sale','purchase_id':purchase['id'],'quantity':1,'net_proceeds':500000},'sale')
    assert portfolio.summary()['inventory'][0]['remaining_cost']==666668
    with pytest.raises(ValueError): portfolio.record({'type':'correction','target_id':purchase['id'],'replacement':None},'bad')
    assert len(portfolio.history())==2
    portfolio.record({'type':'sale','purchase_id':purchase['id'],'quantity':2,'net_proceeds':1000000},'sale2')
    assert portfolio.summary()['inventory']==[]
    assert portfolio.summary()['realized_profit']==499999

def test_stale(portfolio):
    portfolio.select(('us',1,1),'stronger-demand','agamaggan','s')
    portfolio.candidate_provider=lambda key:None
    assert not portfolio.shopping()[0]['valid']
    with pytest.raises(ValueError): portfolio.record({'type':'purchase','key':['us',1,1]},'p')

def test_adjustment_note(portfolio):
    with pytest.raises(ValueError): portfolio.record({'type':'balance-adjustment','amount':-1},'a')
    portfolio.record({'type':'balance-adjustment','amount':-1,'note':'Bank correction'},'b')
    assert portfolio.summary()['cash']==299999999

def test_expiration_fee_and_oversale(portfolio):
    purchase=buy(portfolio)
    for kind in ('expiration','cancellation','rejection'):
        portfolio.record({'type':kind,'purchase_id':purchase['id']},kind)
    assert portfolio.summary()['inventory_cost']==1000001
    portfolio.record({'type':'fee','purchase_id':purchase['id'],'amount':100},'fee')
    assert portfolio.summary()['cash']==298999899
    assert portfolio.summary()['realized_profit']==-100
    with pytest.raises(ValueError):
        portfolio.record({'type':'sale','purchase_id':purchase['id'],'quantity':4,'net_proceeds':1},'oversale')
    assert len(portfolio.history())==5

def test_correction_replay_and_undo(portfolio):
    purchase=buy(portfolio)
    sale=portfolio.record({'type':'sale','purchase_id':purchase['id'],'quantity':1,'net_proceeds':500000},'sale')
    portfolio.record({'type':'correction','target_id':sale['id'],'replacement':{'net_proceeds':600000}},'correct')
    assert portfolio.summary()['cash']==299599999
    portfolio.record({'type':'correction','target_id':sale['id'],'replacement':None},'undo')
    assert portfolio.summary()['inventory_cost']==1000001
    assert portfolio.summary()['cash']==298999999

def test_cross_instance_concurrent_idempotency(portfolio):
    from concurrent.futures import ThreadPoolExecutor
    portfolio.select(('us',1,1),'stronger-demand','agamaggan','s')
    other=Portfolio(portfolio.repository,portfolio.candidate_provider)
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda p:p.record({'type':'purchase','key':['us',1,1]},'same'),[portfolio,other]))
    assert results[0]==results[1]
    assert len(portfolio.history())==1

def test_duplicate_identity_other_destination(portfolio):
    repo=portfolio.repository
    repo.save_settings(replace(repo.load_settings(),selling_realms=('agamaggan','area-52')))
    portfolio.select(('us',1,1),'stronger-demand','agamaggan','s')
    with pytest.raises(ValueError): portfolio.select(('us',1,1),'longer-term','area-52','duplicate')
    portfolio.remove(('us',1,1),'remove')
    portfolio.select(('us',1,1),'longer-term','area-52','s2')
    portfolio.record({'type':'purchase','key':['us',1,1]},'p')
    with pytest.raises(ValueError): portfolio.select(('us',1,1),'stronger-demand','agamaggan','duplicate2')

def test_held_quantity_exposure_and_reserve(portfolio):
    p=portfolio
    first=p.candidate_provider(('us',1,1))
    second=replace(first,key=('us',1,2),auction=replace(first.auction,auction_id=2))
    p.candidate_provider=lambda key: first if key==first.key else second if key==second.key else None
    buy(p)
    settings=p.repository.load_settings()
    for changes, reason in [({'max_copies':5},'quantity'),({'item_exposure':1500000},'exposure')]:
        p.repository.save_settings(replace(settings,**changes))
        with pytest.raises(ValueError,match=reason): p.select(second.key,'stronger-demand','agamaggan',reason)
    p.repository.save_settings(settings)
    p.record({'type':'balance-adjustment','amount':-248999999,'note':'Moved cash'},'adjust')
    with pytest.raises(ValueError,match='reserve'): p.select(second.key,'stronger-demand','agamaggan','reserve')

def test_settings_reduction_flags_shopping_and_preserves_holdings(portfolio):
    p=portfolio
    p.select(('us',1,1),'stronger-demand','agamaggan','s')
    p.repository.save_settings(replace(p.repository.load_settings(),max_copies=2))
    assert not p.shopping()[0]['valid']
    p.repository.save_settings(replace(p.repository.load_settings(),max_copies=10))
    p.record({'type':'purchase','key':['us',1,1]},'p')
    p.repository.save_settings(replace(p.repository.load_settings(),item_exposure=1000000))
    assert p.summary()['limits_error']
    assert p.summary()['inventory_cost']==1000001

def test_base_frozen_after_record_and_provenance(portfolio):
    p=portfolio
    buy(p)
    p.repository.save_settings(replace(p.repository.load_settings(),capital=400000000))
    assert p.summary()['initial_capital']==300000000
    assert p.history()[0]['candidate']['auction']['item_id']==100
    assert p.history()[0]['candidate']['valuation']['name']=='Toy'

def test_actual_purchase_preserves_quote_and_rechecks_limits(portfolio):
    p=portfolio
    p.select(('us',1,1),'stronger-demand','agamaggan','s')
    with pytest.raises(ValueError,match='exposure'):
        p.record({'type':'purchase','key':['us',1,1],'cost':30000001},'bad')
    purchase=p.record({'type':'purchase','key':['us',1,1],'cost':1200000},'actual')
    assert purchase['quoted_cost']==1000001
    assert purchase['candidate']['auction']['buyout']==1000001
    assert p.summary()['cash']==298800000

def test_correction_schema_and_dependent_quantity(portfolio):
    p=portfolio
    purchase=buy(p)
    p.record({'type':'sale','purchase_id':purchase['id'],'quantity':2,'net_proceeds':1},'sale')
    for replacement in ({'quantity':1},{'type':'fee'},{'item_id':999},{'cost':0}):
        with pytest.raises(ValueError):
            p.record({'type':'correction','target_id':purchase['id'],'replacement':replacement},str(replacement))
    assert len(p.history())==2
    assert p.summary()['inventory'][0]['remaining_quantity']==1

def test_unconfigured_destination(portfolio):
    portfolio.repository.save_settings(replace(portfolio.repository.load_settings(),selling_realms=()))
    with pytest.raises(ValueError,match='configured'):
        portfolio.select(('us',1,1),'stronger-demand','agamaggan','s')

def test_successive_corrections_and_restore_after_void(portfolio):
 p=portfolio
 purchase=buy(p)
 sale=p.record({'type':'sale','purchase_id':purchase['id'],'quantity':1,'net_proceeds':500000},'sale')
 p.record({'type':'correction','target_id':sale['id'],'replacement':{'net_proceeds':600000}},'first')
 p.record({'type':'correction','target_id':sale['id'],'replacement':{'quantity':2}},'second')
 assert p.summary()['cash']==299599999
 assert p.summary()['inventory'][0]['remaining_quantity']==1
 p.record({'type':'correction','target_id':sale['id'],'replacement':None},'void')
 assert p.summary()['inventory'][0]['remaining_quantity']==3
 p.record({'type':'correction','target_id':sale['id'],'replacement':{'net_proceeds':700000}},'restore')
 assert p.summary()['cash']==299699999
 assert p.summary()['inventory'][0]['remaining_quantity']==2
 assert len(p.history())==6


def test_exhausted_legacy_allocations_allow_selection_purchase_and_corrections(portfolio):
 p=portfolio
 first=p.candidate_provider(('us',1,1))
 second=replace(first,key=('us',1,2),auction=replace(first.auction,auction_id=2))
 p.candidate_provider=lambda key: first if key==first.key else second if key==second.key else None
 purchase=buy(p)
 original=p.history()[0]
 p.select(second.key,'longer-term','agamaggan','legacy-selection')
 p.repository.save_settings(replace(p.repository.load_settings(),stronger_demand_budget=0,longer_term_budget=0))
 assert p.shopping()[0]['valid']
 assert p.summary()['limits_error'] is None
 assert 'allocations' not in p.summary()
 p.record({'type':'purchase','key':list(second.key)},'second-purchase')
 p.record({'type':'correction','target_id':purchase['id'],'replacement':{'strategy':'longer-term','cost':900000}},'legacy-correction')
 assert p.history()[0]==original
 assert p.summary()['cash']==298099999
 assert p.summary()['inventory'][0]['strategy']=='longer-term'


def test_single_budget_selection_defaults_to_general(portfolio):
 selected=portfolio.select(('us',1,1),destination='agamaggan',request_id='general-selection')
 assert selected['strategy']=='general'
 purchase=portfolio.record({'type':'purchase','key':['us',1,1]},'general-purchase')
 portfolio.record({'type':'correction','target_id':purchase['id'],'replacement':{'cost':900000}},'general-correction')
 assert portfolio.summary()['inventory_cost']==900000
