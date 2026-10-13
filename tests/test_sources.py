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

def test_deduplicated_houses_partial_failure(tmp_path):
    repo=Repository(tmp_path); now=datetime.now(timezone.utc)
    old=Snapshot('auctions:us:2',None,now,None,'blizzard',())
    old_id=repo.publish(old)
    class Client:
        calls=[]
        def realms(self): return [{'slug':'agamaggan','house_id':1},{'slug':'other','house_id':1},{'slug':'third','house_id':2}]
        def auctions(self,house,previous=None):
            self.calls.append(house)
            if house==2: raise ValueError('fixture')
            return Snapshot('auctions:us:1',None,now,None,'blizzard',())
    client=Client(); service=RefreshService(repo,client)
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
        def realms(self): return [{'slug':'agamaggan','house_id':1}]
        def auctions(self,*a): return Snapshot('auctions:us:1',None,now,None,'blizzard',(Auction('us',1,1,2,1,10,'{"context":0,"bonus_lists":[]}'),Auction('us',1,2,3,1,10,None)))
        def item(self,item): self.items.append(item); return {'category':'recipe','tradeability':True}
    client=Client()
    RefreshService(repo,client).run(Settings()); RefreshService(Repository(tmp_path),client).run(Settings())
    assert client.items==[2]
    records=repo.latest('auctions:us:1').records
    assert records[0].metadata['simple_identity'] is True
    assert records[1].category=='unknown'
