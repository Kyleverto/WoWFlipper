from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
import pytest

from wowflipper.sources.blizzard import BlizzardClient
from wowflipper.sources.tsm import TSMClient, parse_pet_report
from wowflipper.sources.http import HTTPClient, Response
from wowflipper.models import Snapshot, Valuation, normalize_auction
from wowflipper.refresh import RefreshService
from wowflipper.repository import Repository
from wowflipper.settings import Settings


def response(data): return Response(200,{},json.dumps(data).encode())
def blizzard(transport):
    client=BlizzardClient(HTTPClient(transport))
    client._token='fixture'; client._expires=10**20
    return client


def test_decor_details_link_plural_object_and_incremental_cache(tmp_path):
    repo=Repository(tmp_path); calls=[]
    def transport(method,url,*args):
        calls.append(url)
        if '/index?' in url:
            return response({'decor_items':[{'id':533,'name':'Sturdy Wooden Interior Pillar'},{'id':534}]})
        return response({'id':533,'items':{'id':245392}}) if '/533?' in url else response({'id':534,'items':{'id':245393}})
    client=blizzard(transport)
    assert client.decor_items(repo,limit=1)=={'items':[245392],'pending':1,'details_fetched':1,'details_attempted':1,'unavailable':0,'errors':0}
    assert client.decor_items(repo,limit=1)=={'items':[245392,245393],'pending':0,'details_fetched':1,'details_attempted':1,'unavailable':0,'errors':0}
    assert client.decor_items(repo)=={'items':[245392,245393],'pending':0,'details_fetched':0,'details_attempted':0,'unavailable':0,'errors':0}
    assert len(calls)==3 and all('namespace=static-us' in url for url in calls)


def test_decor_discovery_bounded_and_retains_progress_after_failure(tmp_path):
    repo=Repository(tmp_path); calls=[]
    def transport(method,url,*args):
        calls.append(url)
        if '/index?' in url: return response({'decor_items':[{'id':i} for i in range(1,55)]})
        identity=int(url.split('/decor/')[1].split('?')[0])
        if identity==52: raise ValueError('fixture secret')
        return response({'id':identity,'items':{'id':identity+1000}})
    client=blizzard(transport)
    first=client.decor_items(repo,limit=500)
    assert first['details_fetched']==50 and first['pending']==4
    second=client.decor_items(repo)
    assert second['details_fetched']==3 and second['errors']==1 and second['pending']==1
    state=repo.cache_get('decor:us')
    assert len(state['items'])==53 and state['remaining']==[52]
    assert state['failures']['52']=={'error_code':'invalid_schema','attempts':1}
    assert len(calls)==55


@pytest.mark.parametrize('data,expected',[
    ({'item_class':{'id':20},'item_subclass':{'id':0}},'decor'),
    ({'item_class':{'id':15},'item_subclass':{'id':0},'name':'Decor Pet Toy'},'unknown'),
])
def test_decor_classification_uses_authoritative_class(data,expected):
    assert blizzard(lambda *args:response(data)).item(245392)['category']==expected


def test_pet_csv_keeps_species_separate_and_rejects_conflicts():
    header='petSpeciesId,name,marketValue,historical,avgSalePrice,saleRate,soldPerDay,updatedAt\n'
    def row(species,name='Pet',price=10): return f'{species},{name},10,10,{price},0.1,1,2020-01-01T00:00:00Z\n'
    report=parse_pet_report(header+row(39)+row(40)+row(39,price=11)+row(0)+row(41,name='')+row(42)+'43,broken\n')
    assert [(v.item_id,v.pet_species_id) for v in report.records]==[(82800,40),(82800,42)]
    assert report.rejected_rows==5
    with pytest.raises(ValueError): parse_pet_report(header.replace('petSpeciesId','itemId')+row(39))


def test_pet_tsm_snapshot_raw_payload_304_and_warnings():
    raw=b'petSpeciesId,name,marketValue,historical,avgSalePrice,saleRate,soldPerDay,updatedAt\n39,Pet,10,10,10,0.1,1,2020-01-01T00:00:00Z\n40,,10,10,10,0.1,1,2020-01-01T00:00:00Z\n'
    responses=[Response(200,{'Last-Modified':'fixture'},raw),Response(304,{},b'')]; calls=[]
    def transport(*args): calls.append(args); return responses.pop(0)
    client=TSMClient(HTTPClient(transport)); first=client.pets(); second=client.pets(first)
    assert first.source_key=='tsm-pets:us' and first.raw_payload==raw
    assert first.source_metadata=={'accepted_rows':1,'rejected_rows':1}
    assert second.source_time==first.source_time and second.records==first.records
    assert calls[0][1].endswith('/region/pets.csv')
    assert calls[1][2]=={'If-Modified-Since':'fixture'}


def test_pet_metadata_uses_species_endpoint_and_strict_booleans():
    calls=[]
    def transport(method,url,*args):
        calls.append(url)
        return response({'id':39,'name':'Chicken','is_tradable':True,'is_battlepet':True})
    assert blizzard(transport).pet(39)['tradeability'] is True
    assert '/pet/39?' in calls[0]
    with pytest.raises(ValueError): blizzard(lambda *args:response({'id':39,'name':'Chicken','is_tradable':'true','is_battlepet':True})).pet(39)


def fixture(tmp_path,species=(39,40),missing=False):
    repo=Repository(tmp_path); now=datetime.now(timezone.utc)
    itemvalue=Valuation(245392,'Pillar',10000000,Decimal('.2'),Decimal(2),1,1,now)
    petvalues=tuple(Valuation(82800,f'Pet {s}',10000000,Decimal('.2'),Decimal(2),1,1,now,pet_species_id=s) for s in species)
    repo.publish(Snapshot('tsm:us',now,now,None,'fixture',(itemvalue,)))
    repo.publish(Snapshot('tsm-pets:us',now,now,None,'fixture',petvalues))
    class TSM:
        calls=[]
        def region(self,previous): return previous
        def pets(self,previous): self.calls.append('pets'); raise ValueError('retained fresh snapshot')
    class Client:
        calls=[]
        def realms(self): return [{'slug':'agamaggan','house_id':1,'category':'United States','is_auction_realm':True},{'slug':'away','house_id':2,'category':'United States','is_auction_realm':True}]
        def auctions(self,house,previous):
            raw=[{'id':i+1,'item':{'id':82800,'pet_species_id':s,'pet_level':1,'pet_quality_id':3,'pet_breed_id':3},'quantity':1,'buyout':1000000} for i,s in enumerate(species)]
            raw+= [{'id':999,'item':{'id':245392},'quantity':1,'buyout':1000000}]
            return Snapshot(f'auctions:us:{house}',now,now,None,'fixture',tuple(normalize_auction(a,house,'us') for a in raw))
        def pet(self,species):
            self.calls.append(('pet',species))
            if missing: raise ValueError('private failure')
            return {'id':species,'name':f'Pet {species}','is_tradable':species!=40,'is_battlepet':True}
        def item(self,item): self.calls.append(('item',item)); return {'category':'decor','tradeability':True}
    return repo,Client(),TSM()


def test_refresh_species_cache_shared_destination_and_source_failure(tmp_path):
    repo,client,tsm=fixture(tmp_path)
    settings=replace(Settings(),categories=('decor','battle-pet'),selling_realms=('away',))
    result=RefreshService(repo,client,tsm).run(settings)
    assert result['sources']['tsm-pets:us']['state']=='failed'
    assert sorted(client.calls)==sorted([('pet',39),('pet',40),('item',245392)])
    assert repo.cache_fresh('pet:us:39',100)['name']=='Pet 39'
    assert repo.cache_fresh('item:us:82800',100) is None
    for house in (1,2):
        rows=repo.latest(f'auctions:us:{house}').records
        assert rows[0].category=='battle-pet' and rows[0].metadata['pet']['species_id']==39
        assert rows[0].metadata['tradeability'] is True
        assert rows[1].metadata['tradeability'] is False
        assert rows[2].category=='decor'
    RefreshService(repo,client,tsm).run(settings)
    assert len(client.calls)==3


def test_refresh_unknown_pet_is_explicit_and_does_not_fail_house(tmp_path):
    repo,client,tsm=fixture(tmp_path,missing=True)
    settings=replace(Settings(),categories=('decor','battle-pet'))
    result=RefreshService(repo,client,tsm).run(settings)
    status=result['sources']['auctions:us:1']
    assert status['state']=='success' and status['pet_metadata_unknown']==2 and status['pet_metadata_failed']==2
    assert repo.latest('auctions:us:1').records[0].metadata['exclusion_reason']=='unknown_pet_metadata'
    assert 'private' not in json.dumps(result)


def test_pet_metadata_bound_is_by_species_not_listing(tmp_path):
    repo,client,tsm=fixture(tmp_path,species=tuple(range(1,104)))
    settings=replace(Settings(),categories=('decor','battle-pet'))
    result=RefreshService(repo,client,tsm).run(settings)
    assert len(client.calls)==100
    assert result['sources']['auctions:us:1']['metadata_skipped']==4
    assert result['sources']['auctions:us:1']['pet_metadata_unknown']==3


def test_toy_discovery_failure_keeps_toy_membership_and_cached_decor_fallback(tmp_path):
    repo,client,tsm=fixture(tmp_path)
    repo.cache_put('decor:us',{'items':[245392],'remaining':[534]})
    repo.cache_put('toys:us',{'items':[245392],'remaining':[]})
    def fail(*args): raise ValueError('private')
    client.decor_items=fail; client.toy_items=fail
    client.item=lambda item:{'category':'unknown','tradeability':True}
    settings=replace(Settings(),categories=('decor','toy'))
    result=RefreshService(repo,client,tsm).run(settings)
    assert tsm.calls==[]
    assert result['sources']['decor:us']=={'state':'success','classification':'item_metadata','known_items':1}
    assert result['sources']['toys:us']['using_cached_items'] is True
    assert repo.latest('auctions:us:1').records[-1].category=='decor'


def test_cached_unknown_housing_classifies_without_waiting_for_index(tmp_path):
    repo,client,tsm=fixture(tmp_path)
    repo.cache_timed('item:us:245392',{'category':'unknown','item_class':{'id':20},'item_subclass':{'id':0},'tradeability':True})
    client.decor_items=lambda repo:{'items':[],'pending':1000,'details_fetched':0,'details_attempted':0,'unavailable':0,'errors':0}
    RefreshService(repo,client,tsm).run(replace(Settings(),categories=('decor',)))
    assert client.calls==[]
    assert repo.latest('auctions:us:1').records[-1].category=='decor'


def test_exact_pet_override_can_trigger_initial_metadata_enrichment(tmp_path):
    repo,client,tsm=fixture(tmp_path)
    snapshot=repo.latest('tsm-pets:us')
    repo.publish(replace(snapshot,records=tuple(replace(v,avg_sale_price=10) for v in snapshot.records)))
    repo.set_resale_override('pet:39:1:3:3',10000000)
    RefreshService(repo,client,tsm).run(replace(Settings(),categories=('battle-pet',)))
    assert client.calls==[('pet',39)]
    assert repo.latest('auctions:us:1').records[0].category=='battle-pet'
    assert repo.latest('auctions:us:1').records[1].category=='unknown'


def test_pet_csv_offline_import_and_separate_coverage_demo(tmp_path):
    from wowflipper.demo import import_snapshot, seed_demo, seed_coverage_demo
    seed_demo(tmp_path)
    repo=Repository(tmp_path)
    assert len(repo.latest('auctions:us:1').records)==3
    seed_coverage_demo(tmp_path)
    assert len(repo.latest('auctions:us:1').records)==7
    assert {v.pet_species_id for v in repo.latest('tsm-pets:us').records}=={39,40}
    now=datetime.now(timezone.utc).isoformat()
    text='petSpeciesId,name,marketValue,historical,avgSalePrice,saleRate,soldPerDay,updatedAt\n39,Chicken,10,10,10,0.1,1,2020-01-01T00:00:00Z\n'
    result=import_snapshot(repo,{'kind':'tsm-pets','text':text,'source_time':now})
    assert result['source']=='tsm-pets:us' and result['records']==1
    assert repo.latest('tsm-pets:us').records[0].pet_species_id==39


def test_expired_legacy_toy_membership_survives_index_failure(tmp_path):
    repo,client,tsm=fixture(tmp_path)
    repo.cache_put('timed:toys:us',{'cached_at':0,'value':{'items':[245392],'remaining':[9]}})
    client.toy_items=lambda repo:(_ for _ in ()).throw(ValueError('fixture'))
    client.item=lambda item:{'category':'unknown','tradeability':True}
    result=RefreshService(repo,client,tsm).run(replace(Settings(),categories=('toy',)))
    assert result['sources']['toys:us']['using_cached_items'] is True
    assert repo.latest('auctions:us:1').records[-1].category=='toy'


def test_expired_legacy_toy_progress_survives_successful_new_index(tmp_path):
    repo=Repository(tmp_path)
    repo.cache_put('timed:toys:us',{'cached_at':0,'value':{'items':[123],'remaining':[8]}})
    sequence=[response({'toys':[{'id':8}]}),response({'id':8,'item':{'id':456}})]
    result=blizzard(lambda *args:sequence.pop(0)).toy_items(repo)
    assert result=={'items':[123,456],'pending':0,'details_fetched':1,'details_attempted':1,'unavailable':0,'errors':0}
