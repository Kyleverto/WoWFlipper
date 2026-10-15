from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import pytest
from wowflipper.models import Auction, Valuation, Snapshot
from wowflipper.identity import pet_details, market_key
from wowflipper.engine import evaluate, exclusion_reason, compare_destinations, index_destinations
from wowflipper.repository import Repository
from wowflipper.query import MarketQuery
from wowflipper.portfolio import Portfolio
from wowflipper.settings import Settings
from wowflipper.web import create_app

NOW = datetime.now(timezone.utc)-timedelta(seconds=5)

def pet(species=7, level=25, quality=3, breed=4, auction_id=1):
    raw={'pet_species_id':species,'pet_level':level,'pet_quality_id':quality,'pet_breed_id':breed,'modifiers':[{'type':6,'value':42}]}
    return Auction('us',1,auction_id,82800,1,1000000,json.dumps(raw),NOW,'battle-pet',{'tradeability':True})

def value(species=7):
    return Valuation(82800,'Pet '+str(species),10000000,Decimal('.2'),Decimal('2'),10000000,10000000,NOW,species)

def repository(tmp_path, auctions=None):
    repo=Repository(tmp_path)
    repo.save_settings(replace(Settings(),selling_realms=('agamaggan',),max_copies=2))
    repo.cache_put('realms',[{'slug':'agamaggan','house_id':1}])
    repo.publish(Snapshot('tsm:us',NOW,NOW,None,'tsm',(replace(value(),item_id=7,pet_species_id=None),)))
    repo.publish(Snapshot('tsm-pets:us',NOW,NOW,None,'tsm-pets',(value(),value(8))))
    repo.publish(Snapshot('auctions:us:1',NOW,NOW,None,'blizzard',tuple(auctions or [pet()])))
    return repo

@pytest.mark.parametrize('field,bad',[('pet_species_id',0),('pet_level',0),('pet_level',26),('pet_quality_id',6),('pet_breed_id',0),('pet_level',True)])
def test_rejects_invalid_raw_pet_identity(field,bad):
    auction=pet();raw=json.loads(auction.variant_key);raw[field]=bad
    assert pet_details(replace(auction,variant_key=json.dumps(raw))) is None

def test_duplicate_missing_unknown_pet_fields_rejected():
    a=pet()
    for variant in ('{"pet_species_id":7,"pet_species_id":8,"pet_level":25,"pet_quality_id":3,"pet_breed_id":4}', '{}', a.variant_key[:-1]+',"pet_unknown":1}'):
        assert pet_details(replace(a,variant_key=variant)) is None
    assert pet_details(replace(a,item_id=7)) is None
    assert market_key(a)=='pet:7:25:3:4'


def test_engine_species_collision_and_unresolved_tradeability():
    settings=Settings()
    assert evaluate(pet(),value(),settings,NOW)
    assert exclusion_reason(pet(),value(8),settings,NOW)=='valuation_identity_mismatch'
    assert exclusion_reason(pet(),replace(value(),pet_species_id=None),settings,NOW)=='valuation_identity_mismatch'
    assert exclusion_reason(replace(pet(),metadata={}),value(),settings,NOW)=='unsuitable_tradeability'
    assert evaluate(replace(pet(),variant_key='{}'),value(),settings,NOW,{'unit_price':100000000}) is None
    result=evaluate(pet(),value(),settings,NOW)
    assert 'species_aggregate_price_not_variant_specific' in result.risks
    assert 'no level-specific premium' in result.explanation


def test_destination_comparison_only_exact_variant():
    candidate=evaluate(pet(),value(),Settings(),NOW)
    a=replace(pet(auction_id=2),house_id=2)
    records=(a,replace(a,auction_id=6,metadata={}),replace(a,auction_id=7,metadata=None),replace(a,auction_id=3,variant_key=pet(level=1).variant_key),replace(a,auction_id=4,variant_key=pet(breed=5).variant_key),replace(a,auction_id=5,variant_key=pet(8).variant_key))
    snap=Snapshot('auctions:us:2',NOW,NOW,None,'blizzard',records)
    settings=replace(Settings(),selling_realms=('dest',))
    for index in (None,index_destinations({'dest':snap})):
        result=compare_destinations(candidate,{'dest':snap},settings,NOW,index)[0]
        assert result['listing_count']==1 and result['quantity']==1


def test_query_separate_species_values_provenance_and_overrides(tmp_path):
    repo=repository(tmp_path,[pet(),pet(8,auction_id=2)])
    repo.set_resale_override(82800,1)
    rows=MarketQuery(repo).rows({})
    assert len(rows)==2 and {r['valuation']['pet_species_id'] for r in rows}=={7,8}
    assert all(r['resale_override'] is None for r in rows)
    p=repo.latest('tsm-pets:us')
    assert all(json.loads(r['auction']['provenance'])['valuation_snapshot']==p.id for r in rows)
    override=repo.set_resale_override('pet:7:25:3:4',20000000)
    assert override['pet']['species_id']==7 and 'item_id' not in override
    rows=MarketQuery(repo).rows({})
    assert next(r for r in rows if r['pet']['species_id']==7)['resale_override']==override
    assert next(r for r in rows if r['pet']['species_id']==8)['resale_override'] is None
    assert len(MarketQuery(repo).rows({'category':'battle-pet'}))==2
    assert MarketQuery(repo).rows({'category':'decor'})==[]


def test_pet_snapshot_and_record_freshness_cache_deadline(tmp_path,monkeypatch):
    import wowflipper.query as module
    repo=repository(tmp_path)
    repo.save_settings(replace(repo.load_settings(),max_auction_age_hours=72))
    snap=repo.latest('tsm-pets:us')
    repo.publish(replace(snap,source_time=NOW-timedelta(hours=47)))
    current=[NOW+timedelta(hours=1)]
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None): return current[0]
    monkeypatch.setattr(module,'datetime',Clock)
    assert MarketQuery(repo).page({})['total']==1
    current[0]+=timedelta(microseconds=1)
    assert MarketQuery(repo).page({})['total']==0
    repo.publish(replace(snap,source_time=NOW,records=(replace(value(),updated_at=NOW-timedelta(hours=47)),)))
    assert MarketQuery(repo).page({})['total']==0
    current[0]-=timedelta(microseconds=1)
    assert MarketQuery(repo).page({})['total']==1


def test_portfolio_species_limits_and_changed_variant(tmp_path):
    repo=repository(tmp_path,[pet(),pet(8,auction_id=2),pet(level=1,auction_id=3)])
    repo.save_settings(replace(repo.load_settings(),max_copies=1))
    portfolio=Portfolio(repo,lambda key:MarketQuery(repo).candidate(key))
    portfolio.select(('us',1,1),destination='agamaggan',request_id='one')
    portfolio.select(('us',1,2),destination='agamaggan',request_id='two')
    with pytest.raises(ValueError,match='quantity limit'):
        portfolio.select(('us',1,3),destination='agamaggan',request_id='three')
    snap=repo.latest('auctions:us:1')
    repo.publish(replace(snap,records=(pet(level=1),pet(8,auction_id=2))))
    assert not portfolio.shopping()[0]['valid']
    with pytest.raises(ValueError,match='identity changed'):
        portfolio.record({'type':'purchase','key':['us',1,1]},'buy')


def test_current_override_revalidation_and_aba(tmp_path):
    repo=repository(tmp_path)
    portfolio=Portfolio(repo,lambda key:MarketQuery(repo).candidate(key))
    portfolio.select(('us',1,1),destination='agamaggan',request_id='one')
    repo.set_resale_override('pet:7:25:3:4',1)
    assert not portfolio.shopping()[0]['valid']
    repo.remove_resale_override('pet:7:25:3:4')
    assert portfolio.shopping()[0]['valid']
    bought=portfolio.record({'type':'purchase','key':['us',1,1]},'buy')
    assert bought['candidate']['auction']['variant_key']==pet().variant_key


def test_legacy_valuation_settings_and_override_api(tmp_path):
    repo=repository(tmp_path)
    assert Settings.from_dict({'categories':['toy']}).categories==('toy',)
    assert {'decor','battle-pet'}<=set(Settings().categories)
    with repo.connect() as db:
        row=db.execute("SELECT id,data FROM snapshots WHERE source='tsm:us'").fetchone()
        data=json.loads(row[1]);data['records'][0].pop('pet_species_id')
        db.execute('UPDATE snapshots SET data=? WHERE id=?',(json.dumps(data),row[0]))
    assert repo.latest('tsm:us').records[0].pet_species_id is None
    client=create_app(tmp_path,testing=True).test_client()
    csrf=client.get('/api/state').json['csrf'];headers={'X-CSRF-Token':csrf}
    assert client.post('/api/resale-overrides',json={'identity':'pet:7:25:3:4','unit_price':20000000},headers=headers).status_code==200
    assert client.post('/api/resale-overrides',json={'item_id':'pet:7:25:3:4','unit_price':1},headers=headers).status_code==400
    assert client.post('/api/resale-overrides/remove',json={'item_id':'pet:7:25:3:4'},headers=headers).status_code==400
    assert client.post('/api/resale-overrides',json={'identity':'item:7','unit_price':1},headers=headers).status_code==400
    assert client.post('/api/resale-overrides',json={'identity':'pet:7:25:3:4','item_id':7,'unit_price':1},headers=headers).status_code==400
    assert client.post('/api/resale-overrides/remove',json={'identity':'pet:7:25:3:4'},headers=headers).status_code==200
    export=client.get('/api/export?category=battle-pet').text
    assert 'Pet species ID' in export and 'pet:7:25:3:4' in export and 'no level-specific premium' in export
