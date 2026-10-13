from dataclasses import replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from wowflipper.models import Auction, Valuation, Snapshot
from wowflipper.settings import Settings
from wowflipper.engine import evaluate, exclusion_reason, rank, compare_destinations
NOW=datetime(2026,10,12,tzinfo=timezone.utc)
A=Auction('us',1,42,100,1,850_000_000,None,NOW,'recipe',{'simple_identity':True,'tradeability':True})
V=Valuation(100,'Recipe',1_000_000_000,Decimal('.2'),Decimal('2'),1,1,NOW)
S=replace(Settings(),capital=3_000_000_000,item_exposure=2_000_000_000,stronger_demand_budget=2_000_000_000,longer_term_budget=500_000_000)
def test_profit_after_cut():
 c=evaluate(A,V,S,NOW)
 assert (c.proceeds,c.profit)==(950_000_000,100_000_000)
def test_stack_boundary_rounding():
 s=replace(S,min_profit=0,min_roi=Decimal(0),max_copies=3)
 v=replace(V,avg_sale_price=101)
 assert evaluate(replace(A,quantity=2,buyout=171),v,s,NOW).proceeds==191
 assert evaluate(replace(A,quantity=2,buyout=172),v,s,NOW) is None
 assert evaluate(replace(A,buyout=85),replace(v,avg_sale_price=100),s,NOW)
 assert evaluate(replace(A,buyout=86),replace(v,avg_sale_price=100),s,NOW) is None
@pytest.mark.parametrize('changes,reason',[({'source_time':None},'auction'),({'source_time':NOW-timedelta(hours=3)},'auction'),({'metadata':None},'metadata'),({'category':None},'category'),({'category':'gear'},'gear'),({'category':'pet'},'pet'),({'metadata':{'simple_identity':False}},'variant'),({'metadata':{'simple_identity':True,'tradeability':False}},'tradeability')])
def test_exclusions(changes,reason):
 a=replace(A,**changes)
 assert evaluate(a,V,S,NOW) is None
 assert reason in exclusion_reason(a,V,S,NOW)
def test_invalid_valuation():
 for v in (replace(V,updated_at=None),replace(V,avg_sale_price=0),replace(V,sale_rate=Decimal('NaN')),replace(V,item_id=101)):
  assert evaluate(A,v,S,NOW) is None
def test_risk_cost_budget():
 c=evaluate(replace(A,metadata={'simple_identity':True}),V,replace(S,extra_costs=5),NOW)
 assert c.profit==99_999_995
 assert 'unverified_tradeability' in c.risks
 assert evaluate(A,V,Settings(),NOW) is None
 assert evaluate(replace(A,quantity=2),V,S,NOW) is None
 assert evaluate(A,V,replace(S,min_profit=100_000_001),NOW) is None
def test_rank():
 c=evaluate(A,V,S,NOW)
 d=replace(c,key=('us',1,43),auction=replace(A,auction_id=43),valuation=replace(V,sold_per_day=Decimal(3)),profit=90_000_000)
 assert rank([d,c,c],'stronger-demand')==[d,c]
 assert rank([d,c],'longer-term')==[c,d]
 assert rank([c,d],'longer-term')==rank([d,c],'longer-term')
 with pytest.raises(ValueError): rank([c],'bad')
def test_destinations():
 c=evaluate(A,V,S,NOW)
 s=replace(S,selling_realms=('home','away','missing','stale'))
 make=lambda source,records,time=NOW: Snapshot(source,time,NOW,None,'live',tuple(records))
 other=replace(A,auction_id=43,buyout=900_000_000)
 rows=compare_destinations(c,{'home':make('auctions:us:1',[A,other]),'away':make('auctions:us:2',[replace(other,house_id=2,metadata={'simple_identity':False})]),'stale':make('auctions:us:3',[other],NOW-timedelta(hours=3))},s,now=NOW)
 assert rows[0]['quantity']==1
 assert rows[0]['lowest_unit_ask']==Decimal(900_000_000)
 assert 'competition_below_resale' in rows[0]['risks']
 assert rows[1]['quantity']==0
 assert 'unverified_transfer_eligibility' in rows[1]['risks']
 assert 'absent_destination_evidence' in rows[2]['risks']
 assert 'stale_destination_evidence' in rows[3]['risks']
def test_decimal_magnitude():
 c=evaluate(A,V,replace(S,resale_factor=Decimal('1e40')),NOW)
 assert c.proceeds==95*10**47

def test_variant_identity_is_conservative():
 assert evaluate(replace(A,variant_key='{"context":0,"bonus_lists":[],"modifiers":[]}'),V,S,NOW)
 assert evaluate(replace(A,variant_key='{"bonus_lists":[123]}'),V,S,NOW) is None
 assert evaluate(replace(A,variant_key='{"pet_species_id":5}'),V,S,NOW) is None

def test_stale_valuation_excludes_actionable_candidate():
 stale=replace(V,updated_at=NOW-timedelta(hours=S.max_tsm_age_hours+1))
 assert evaluate(A,stale,S,NOW) is None
 assert 'valuation' in exclusion_reason(A,stale,S,NOW)
