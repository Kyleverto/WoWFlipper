"""Observed mount/decor listing shapes must reach classification before identity checks."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
import pytest
from wowflipper.models import Snapshot, Valuation, normalize_auction
from wowflipper.settings import Settings
from wowflipper.repository import Repository
from wowflipper.refresh import RefreshService
from wowflipper.query import MarketQuery
from wowflipper.portfolio import Portfolio
from wowflipper.sources.blizzard import BlizzardClient
from wowflipper.sources.http import HTTPClient, Response


@pytest.mark.parametrize('cached',[False,True])
def test_refresh_collectible_metadata_variants_survive_comparison_purchase_and_backup(tmp_path,cached):
    from wowflipper.backup import BackupService
    repo=Repository(tmp_path); now=datetime.now(timezone.utc)
    items={67151:('mount',15,5),242948:('decor',20,0)}
    metadata={item:{'id':item,'name':category,'category':category,'item_class':{'id':cls},'item_subclass':{'id':sub},'binding':{'type':'ON_USE'},'tradeability':True}
              for item,(category,cls,sub) in items.items()}
    if cached:
        for item,detail in metadata.items(): repo.cache_timed(f'item:us:{item}',dict(detail,category='unknown' if item==242948 else detail['category']))
    values=tuple(Valuation(item,category,30000000,Decimal('.1'),Decimal('1'),30000000,30000000,now) for item,(category,*_) in items.items())
    repo.publish(Snapshot('tsm:us',now,now,None,'fixture',values))
    settings=replace(Settings(),categories=('recipe','mount','decor'),selling_realms=('away',),item_exposure=100000000)
    calls=[]
    def transport(method,url,*args):
        item=int(url.split('/item/')[1].split('?')[0]); calls.append(item)
        return Response(200,{},json.dumps(metadata[item]).encode())
    adapter=BlizzardClient(HTTPClient(transport)); adapter._token='fixture';adapter._expires=10**20
    originals={}
    class Client:
        def realms(self): return [{'slug':'agamaggan','house_id':1,'category':'United States','is_auction_realm':True},{'slug':'away','house_id':2,'category':'United States','is_auction_realm':True}]
        def item(self,item): return adapter.item(item)
        def auctions(self,house,previous):
            rows=[]
            for item in items:
                mods=[{'type':28,'value':872 if house==1 else 391}]
                if item==242948: mods += [{'type':39,'value':56138},{'type':40,'value':6348},{'type':42,'value':300 if house==1 else 150}]
                raw={'id':item+house,'item':{'id':item,'context':13 if item==242948 else 0,'modifiers':mods},'quantity':1,'buyout':10000000 if house==1 else 20000000}
                auction=normalize_auction(raw,house,'us'); originals[(house,item)]=auction.variant_key; rows.append(auction)
            return Snapshot(f'auctions:us:{house}',now,now,None,'fixture',tuple(rows),raw_payload=json.dumps({'fixture':house}).encode())
    class TSM:
        def region(self,previous): return previous
    status=RefreshService(repo,Client(),TSM()).run(settings)
    assert status['sources']['auctions:us:1']['state']=='success'
    assert sorted(calls)==([] if cached else sorted(items))
    query=MarketQuery(repo); rows=query.rows({})
    assert {row['auction']['category'] for row in rows}=={'mount','decor'}
    for row in rows:
        item=row['auction']['item_id']
        assert row['auction']['variant_key']==originals[(1,item)]
        assert row['comparisons'][0]['listing_count']==1
        assert row['comparisons'][0]['lowest_unit_ask']=='20000000'
    portfolio=Portfolio(repo,lambda key:query.candidate(key))
    for row in rows:
        item=row['auction']['item_id']; portfolio.select(row['key'],destination='away',request_id=f'select-{item}')
        result=portfolio.record({'type':'purchase','key':row['key']},f'buy-{item}')
        assert result['candidate']['auction']['variant_key']==originals[(1,item)]
    document=BackupService(repo).export()
    assert BackupService(repo).preview(document)['counts']['inventory']==2
    assert {event['candidate']['auction']['variant_key'] for event in document['data']['portfolio']['events']}=={originals[(1,item)] for item in items}


def test_discovery_does_not_accept_quality_or_meaningful_variants(tmp_path):
    repo=Repository(tmp_path); now=datetime.now(timezone.utc)
    repo.publish(Snapshot('tsm:us',now,now,None,'fixture',tuple(Valuation(i,str(i),10000000,Decimal('.1'),Decimal(1),1,1,now) for i in range(1,6))))
    variants=[{'modifiers':[{'type':38,'value':3}]},{'bonus_lists':[123]}, {'pet_species_id':39}, {'modifiers':[{'type':999,'value':1}]},{'context':True}]
    class Client:
        calls=[]
        def realms(self): return [{'slug':'agamaggan','house_id':1,'category':'United States','is_auction_realm':True}]
        def auctions(self,house,previous):
            rows=[normalize_auction({'id':i,'item':{'id':i,**variant},'quantity':1,'buyout':1000000},1,'us') for i,variant in enumerate(variants,1)]
            return Snapshot('auctions:us:1',now,now,None,'fixture',tuple(rows))
        def item(self,item): self.calls.append(item); return {'category':'decor','tradeability':True}
    class TSM:
        def region(self,previous): return previous
    client=Client(); RefreshService(repo,client,TSM()).run(replace(Settings(),categories=('mount','decor')))
    assert client.calls==[]
    assert MarketQuery(repo).rows({})==[]
