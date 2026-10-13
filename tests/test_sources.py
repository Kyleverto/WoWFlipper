import pytest
from wowflipper.sources.http import HTTPClient, Response, Deferred

def test_retry_after_defers():
    calls=[]
    def transport(*a,**k):
        calls.append(a)
        return Response(429, {'Retry-After':'120'}, b'')
    with pytest.raises(Deferred): HTTPClient(transport=transport).request('GET','https://us.api.blizzard.com/test')
    assert len(calls)==1

def test_allowlist():
    with pytest.raises(ValueError): HTTPClient().request('GET','http://us.api.blizzard.com/test')

from datetime import datetime, timezone
from dataclasses import replace
import json
from wowflipper.sources.blizzard import BlizzardClient
from wowflipper.models import Snapshot, Auction
from wowflipper.refresh import RefreshService
from wowflipper.repository import Repository
from wowflipper.settings import Settings

def response(data,headers=None,status=200): return Response(status,headers or {},json.dumps(data).encode())

def test_token_expiry_and_single_401_renewal():
    sequence=[response({'access_token':'a','expires_in':100}),Response(401,{},b''),response({'access_token':'b','expires_in':100}),response({'id':2}),response({'access_token':'c','expires_in':100}),response({'id':2})]
    calls=[]; now=[0]
    def transport(*args): calls.append(args); return sequence.pop(0)
    client=BlizzardClient(HTTPClient(transport), 'id','secret',clock=lambda:now[0])
    client.item(2); now[0]=101; client.item(2)
    assert [c[0] for c in calls]==['POST','GET','POST','GET','POST','GET']

def test_unknown_timestamp_and_304_preserves_market_time():
    sequence=[response({'auctions':[{'id':1,'item':{'id':2},'quantity':1,'buyout':10}]}),Response(304,{},b''),Response(200,{},b'{')]
    client=BlizzardClient(HTTPClient(lambda *a:sequence.pop(0)))
    client._token='fixture'; client._expires=10**20
    first=client.auctions(1); assert first.source_time is None
    second=client.auctions(1,first); assert second.source_time is None and second.records==first.records
    with pytest.raises(ValueError): client.auctions(1)

class UnavailableTSM:
    def region(self,previous=None): raise ValueError('fixture unavailable')

def test_deduplicated_houses_partial_failure(tmp_path):
    repo=Repository(tmp_path); now=datetime.now(timezone.utc)
    old=Snapshot('auctions:us:2',None,now,None,'blizzard',())
    old_id=repo.publish(old)
    class Client:
        calls=[]
        def realms(self): return [{'slug':'agamaggan','house_id':1,'category':'United States','is_auction_realm':True},{'slug':'other','house_id':1,'category':'United States','is_auction_realm':True},{'slug':'third','house_id':2,'category':'United States','is_auction_realm':True}]
        def auctions(self,house,previous=None):
            self.calls.append(house)
            if house==2: raise ValueError('fixture')
            return Snapshot('auctions:us:1',None,now,None,'blizzard',())
    client=Client(); service=RefreshService(repo,client,UnavailableTSM())
    result=service.run(Settings.from_dict({'buying_realms':['agamaggan','other'],'selling_realms':['third']}))
    assert client.calls==[1,2]
    assert result['sources']['auctions:us:2']['state']=='failed'
    assert repo.latest('auctions:us:2').id==old_id
    assert repo.latest('auctions:us:2').fetched_at==now

def test_overlap_rejected(tmp_path):
    service=RefreshService(Repository(tmp_path),None)
    service._lock.acquire()
    with pytest.raises(RuntimeError): service.run(Settings())
    service._lock.release()

def test_metadata_cache_and_simple_identity(tmp_path):
    from wowflipper.models import Valuation
    from decimal import Decimal
    repo=Repository(tmp_path); now=datetime.now(timezone.utc)
    repo.publish(Snapshot('tsm:us',now,now,None,'csv',(Valuation(2,'fixture',100,Decimal('.1'),Decimal('1'),100,100,now),)))
    class Client:
        items=[]
        def realms(self): return [{'slug':'agamaggan','house_id':1,'category':'United States','is_auction_realm':True}]
        def auctions(self,*a): return Snapshot('auctions:us:1',now,now,None,'blizzard',(Auction('us',1,1,2,1,10,'{"context":0,"bonus_lists":[]}'),Auction('us',1,2,3,1,10,None)))
        def item(self,item): self.items.append(item); return {'category':'recipe','tradeability':True}
    client=Client()
    settings=replace(Settings(),min_profit=0,min_roi=Decimal(0))
    RefreshService(repo,client,UnavailableTSM()).run(settings); RefreshService(Repository(tmp_path),client,UnavailableTSM()).run(settings)
    assert client.items==[2]
    records=repo.latest('auctions:us:1').records
    assert records[0].metadata['simple_identity'] is True
    assert records[1].category=='unknown'

@pytest.mark.parametrize('entry',[{'item':{'id':2},'quantity':1,'buyout':10},{'id':1,'item':{'id':2},'quantity':0,'buyout':10},{'id':1,'item':{'id':2},'quantity':1,'buyout':-1}])
def test_malformed_auction_rejects_snapshot(entry):
    client=BlizzardClient(HTTPClient(lambda *a:response({'auctions':[entry]})))
    client._token='fixture'; client._expires=10**20
    with pytest.raises(ValueError): client.auctions(1)

def test_valid_bid_only_is_omitted():
    client=BlizzardClient(HTTPClient(lambda *a:response({'auctions':[{'id':1,'item':{'id':2},'quantity':1,'bid':5}]})))
    client._token='fixture'; client._expires=10**20
    assert client.auctions(1).records==()

def test_tsm_download_and_304():
    from wowflipper.sources.tsm import TSMClient
    raw=b'itemId,name,marketValue,historical,avgSalePrice,saleRate,soldPerDay,updatedAt\n2,fixture,10,10,10,0.1,1,2020-01-01T00:00:00Z\n'
    sequence=[Response(200,{'Last-Modified':'Wed, 01 Jan 2020 00:00:00 GMT'},raw),Response(304,{},b'')]
    client=TSMClient(HTTPClient(lambda *a:sequence.pop(0)))
    first=client.region(); second=client.region(first)
    assert first.source_key=='tsm:us' and first.raw_payload==raw
    assert second.source_time==first.source_time

def test_discovery_error_is_pollable(tmp_path):
    class Client:
        def realms(self): raise ValueError('secret must not escape')
    class TSM:
        def region(self,previous): raise ValueError('fixture')
    result=RefreshService(Repository(tmp_path),Client(),TSM()).run(Settings())
    assert result['sources']['realms:us']=={'state':'failed','error':'ValueError','error_code':'invalid_schema'}
    assert result['running'] is False

def test_toy_ids_map_through_details_and_cache(tmp_path):
    repo=Repository(tmp_path)
    sequence=[response({'toys':[{'id':7},{'id':8}]}),response({'id':7,'item':{'id':123}}),response({'id':8,'item':{'id':456}})]
    client=BlizzardClient(HTTPClient(lambda *a:sequence.pop(0)))
    client._token='fixture'; client._expires=10**20
    state=client.toy_items(repo,limit=1)
    assert state['items']==[123] and state['pending']==1
    state=client.toy_items(repo,limit=1)
    assert state['items']==[123,456] and state['pending']==0
    assert client.toy_items(repo,limit=1)==state


def test_tsm_precedes_auctions_and_toy_classification(tmp_path):
    from decimal import Decimal
    from wowflipper.models import Valuation
    repo=Repository(tmp_path); now=datetime.now(timezone.utc); calls=[]
    class TSM:
        def region(self,previous):
            calls.append('tsm')
            return Snapshot('tsm:us',now,now,None,'tsm',(Valuation(123,'fixture',100,Decimal('.1'),Decimal('1'),100,100,now),))
    class Client:
        def realms(self): calls.append('realms'); return [{'slug':'agamaggan','house_id':1,'category':'United States','is_auction_realm':True}]
        def toy_items(self,repo): return {'items':[123],'pending':1}
        def auctions(self,*args):
            calls.append('auctions')
            return Snapshot('auctions:us:1',now,now,None,'blizzard',(Auction('us',1,1,123,1,10,None),))
        def item(self,item): calls.append('item'); return {'category':'unknown','tradeability':True}
    result=RefreshService(repo,Client(),TSM()).run(replace(Settings(),min_profit=0,min_roi=Decimal(0)))
    assert calls==['tsm','realms','auctions','item']
    assert result['sources']['tsm:us']['state']=='success'
    assert result['sources']['toys:us']['state']=='pending'
    assert repo.latest('auctions:us:1').records[0].category=='toy'

def test_tsm_invalid_download_preserves_cache(tmp_path):
    from wowflipper.sources.tsm import TSMClient
    from decimal import Decimal
    from wowflipper.models import Valuation
    repo=Repository(tmp_path); now=datetime.now(timezone.utc)
    identity=repo.publish(Snapshot('tsm:us',now,now,None,'tsm',(Valuation(2,'fixture',100,Decimal('.1'),Decimal('1'),100,100,now),)))
    class Client:
        def realms(self): raise ValueError('fixture')
    result=RefreshService(repo,Client(),TSMClient(HTTPClient(lambda *a:Response(200,{},b'truncated')))).run(Settings())
    assert result['sources']['tsm:us']['state']=='failed'
    assert repo.latest('tsm:us').id==identity

def test_tsm_download_accepts_valid_rows_with_blank_name_exclusions():
    from wowflipper.sources.tsm import TSMClient
    raw=b'itemId,name,marketValue,historical,avgSalePrice,saleRate,soldPerDay,updatedAt\n2,fixture,10,10,10,0.1,1,2020-01-01T00:00:00Z\n3,,10,10,10,0.1,1,2020-01-01T00:00:00Z\n'
    snapshot=TSMClient(HTTPClient(lambda *a:Response(200,{},raw))).region()
    assert [record.item_id for record in snapshot.records]==[2]
    assert snapshot.source_metadata=={'accepted_rows':1,'rejected_rows':1}
    assert snapshot.records[0].updated_at==snapshot.source_time
