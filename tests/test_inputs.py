from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from wowflipper.sources.tsm import parse_region, parse_region_report
from wowflipper.models import normalize_auction, is_fresh
from wowflipper.settings import Settings

HEADER = 'itemId,name,marketValue,historical,avgSalePrice,saleRate,soldPerDay,updatedAt\n'
STAMP = datetime.now(timezone.utc).isoformat()
ROW = f'123,Synthetic,1200000000,1100000000,1000000000,0.025,0.05,{STAMP}\n'
CSV_FIXTURE = HEADER + ROW

def test_regional_copper_and_demand_are_preserved():
    row = parse_region(CSV_FIXTURE)[0]
    assert row.avg_sale_price == 1_000_000_000
    assert row.sale_rate == Decimal('0.025')
    assert row.sold_per_day == Decimal('0.05')
    assert row.updated_at.utcoffset().total_seconds() == 0

@pytest.mark.parametrize('old,new', [('0.025','NaN'),('0.025','Infinity'),('0.05','-0.05'),('1000000000','1.5'),('123','0'),(STAMP,'garbage'),(STAMP,datetime.now(timezone.utc).replace(tzinfo=None).isoformat()),(STAMP,(datetime.now(timezone.utc)+timedelta(minutes=6)).isoformat())])
def test_invalid_rows_counted(old,new):
    result = parse_region_report(HEADER + ROW.replace(old,new))
    assert result.records == []
    assert result.rejected_rows == 1

def test_headers_required_extra_allowed():
    with pytest.raises(ValueError): parse_region(CSV_FIXTURE.replace('avgSalePrice','other'))
    assert len(parse_region(HEADER.rstrip() + ',extra\n' + ROW.rstrip() + ',ok\n')) == 1

def test_conflicting_duplicates_exclude_all_rows():
    result = parse_region_report(CSV_FIXTURE + ROW.replace('1000000000','2000000000'))
    assert result.records == []
    assert result.rejected_rows == 2
    assert len(parse_region(CSV_FIXTURE + ROW)) == 1

@pytest.mark.parametrize('field,value', [('id',True),('buyout',-1),('buyout',1.5),('quantity',0),('quantity',False),('item',{'id':True})])
def test_bad_auction_rejected(field,value):
    data = {'id':1,'item':{'id':123},'quantity':2,'buyout':30000}
    data[field] = value
    assert normalize_auction(data, 4, 'us') is None

def test_auction_total_and_variant_preserved():
    auction = normalize_auction({'id':1,'item':{'id':123,'bonus_lists':[9]},'quantity':2,'buyout':30000},4,'us')
    assert auction.buyout == 30000
    assert auction.quantity == 2
    assert auction.variant_key is not None
    assert normalize_auction({'id':1,'item':{'id':123},'quantity':2},4,'us') is None

def test_unknown_stale_and_future_age_excluded():
    now = datetime.now(timezone.utc)
    assert not is_fresh(None,48,now)
    assert not is_fresh(now-timedelta(hours=49),48,now)
    assert not is_fresh(now+timedelta(minutes=6),48,now)
    assert is_fresh(now-timedelta(hours=1),48,now)

@pytest.mark.parametrize('data', [{'capital':1},{'min_roi':'NaN'},{'discount':'1.1'},{'auction_cut':'-0.1'},{'max_copies':True},{'max_tsm_age_hours':0},{'extra_costs':-1},{'capital':1.5},{'unknown':1}])
def test_settings_reject_invalid(data):
    with pytest.raises(ValueError): Settings.from_dict(data)

def test_settings_defaults_and_serialization():
    settings = Settings.from_dict({})
    assert settings.capital == 300_000_000
    assert settings.min_profit == 1_000_000
    assert settings.min_roi == Decimal('0.10')
    assert settings.min_sale_rate == settings.min_sold_per_day == Decimal('0.01')
    assert settings.max_tsm_age_hours == 48 and settings.max_auction_age_hours == 2
    assert settings.max_copies == 1 and settings.extra_costs == 0
    assert Settings.from_dict(settings.to_dict()) == settings


def test_legacy_settings_allocation_totals_are_inert_and_preserved():
 settings=Settings.from_dict({'capital':60_000_000,'reserve':10_000_000,
                             'stronger_demand_budget':200_000_000,'longer_term_budget':50_000_000})
 assert settings.capital==60_000_000
 assert settings.to_dict()['stronger_demand_budget']==200_000_000
 assert settings.to_dict()['longer_term_budget']==50_000_000


def test_reserve_cannot_exceed_capital():
 with pytest.raises(ValueError):
  Settings.from_dict({'reserve':300_000_001})
