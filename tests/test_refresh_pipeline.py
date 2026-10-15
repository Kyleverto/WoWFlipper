"""Bounded refresh work and status without network or production databases."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
import threading
import pytest

from wowflipper.models import Auction, Snapshot, Valuation
from wowflipper.refresh import RefreshService
from wowflipper.repository import Repository
from wowflipper.settings import Settings


class RetainedTSM:
    def region(self,previous): return previous


def prepared(tmp_path,count=1,items=()):
    repo=Repository(tmp_path); now=datetime.now(timezone.utc)
    repo.publish(Snapshot('tsm:us',now,now,None,'fixture',tuple(
        Valuation(item,str(item),10000000,Decimal('.5'),Decimal('5'),10000000,10000000,now) for item in items)))
    realms=[{'slug':f'house-{house}','house_id':house,'category':'United States','is_auction_realm':True}
        for house in range(1,count+1)]
    repo.cache_timed('realms',realms)
    repo.cache_put('realms',realms)
    settings=replace(Settings(),buying_realms=tuple(r['slug'] for r in realms),selling_realms=(),categories=('recipe',))
    return repo,settings,now


def test_bulk_cache_one_connection_chunked_and_freshness(tmp_path,monkeypatch):
    repo=Repository(tmp_path)
    repo.cache_put('timed:good',{'cached_at':990,'value':{'name':'fresh'}})
    repo.cache_put('timed:boundary',{'cached_at':980,'value':2})
    repo.cache_put('timed:bad',[])
    repo.cache_put('timed:future',{'cached_at':1010,'value':4})
    monkeypatch.setattr('wowflipper.repository.time.time',lambda:1000)
    calls=[]; original=repo.connect
    def connect(): calls.append(1); return original()
    monkeypatch.setattr(repo,'connect',connect)
    keys=['good','boundary','bad','future']+[f'absent-{i}' for i in range(1800)]
    result=repo.cache_fresh_many(keys+['good'],20)
    assert len(calls)==1 and len(result)==len(keys)
    assert result['good']=={'name':'fresh'} and result['future']==4
    assert result['boundary'] is None and result['bad'] is None and result['absent-1799'] is None
    assert repo.cache_fresh_many([],20)=={}
    assert len(calls)==1


def test_fetch_concurrency_bounded_status_visible_and_failure_retains_snapshot(tmp_path):
    repo,settings,now=prepared(tmp_path,5)
    previous=repo.publish(Snapshot('auctions:us:2',now,now,None,'fixture',()))
    gate=threading.Event(); started=threading.Event(); lock=threading.Lock()
    active=0; peak=0; entered=[]
    class Client:
        def auctions(self,house,previous):
            nonlocal active,peak
            with lock:
                active+=1; peak=max(peak,active); entered.append(house)
                if active==3: started.set()
            assert gate.wait(5)
            with lock: active-=1
            if house==2: raise ValueError('private response body')
            return Snapshot(f'auctions:us:{house}',now,now,None,'fixture',())
    service=RefreshService(repo,Client(),RetainedTSM())
    task=service.start(settings)
    try:
        assert started.wait(5)
        for _ in range(30):
            progress=service.status_snapshot(); json.dumps(progress)
            assert progress['running'] and progress['phase']=='auction_fetch'
            assert progress['total']==5 and progress['completed']==0
            states=[s['state'] for key,s in progress['sources'].items() if key.startswith('auctions:')]
            assert states.count('running')==3 and states.count('queued')==2
        progress['sources'].clear()
        assert len(service.status_snapshot()['sources'])>=5
    finally: gate.set()
    result=task.result(5)
    assert peak==3 and sorted(entered)==[1,2,3,4,5]
    assert result['sources']['auctions:us:2']['state']=='failed'
    assert repo.latest('auctions:us:2').id==previous
    assert all(result['sources'][f'auctions:us:{h}']['state']=='success' for h in (1,3,4,5))
    assert result['phase']=='complete' and result['elapsed_seconds']>=0
    assert {'auction_fetch','metadata','publication'}<=result['phase_seconds'].keys()
    assert 'private' not in json.dumps(result)


def test_metadata_bulk_dedup_failure_isolation_and_no_raw_reload(tmp_path,monkeypatch):
    repo,settings,now=prepared(tmp_path,2,items=(2,3))
    original=repo.latest; latest_calls=[]; bulk_calls=[]; original_bulk=repo.cache_fresh_many
    def latest(key,**kwargs):
        latest_calls.append(kwargs.get('include_payload'))
        return original(key,**kwargs)
    def bulk(keys,age):
        keys=list(keys); bulk_calls.append(keys)
        return original_bulk(keys,age)
    monkeypatch.setattr(repo,'latest',latest); monkeypatch.setattr(repo,'cache_fresh_many',bulk)
    class Client:
        calls=[]
        def auctions(self,house,previous):
            return Snapshot(f'auctions:us:{house}',now,now,None,'fixture',tuple(
                Auction('us',house,item,item,1,1000,None) for item in (2,3)))
        def item(self,item):
            self.calls.append(item)
            if item==2: raise ValueError('private 404')
            return {'category':'recipe','tradeability':True}
    client=Client(); result=RefreshService(repo,client,RetainedTSM()).run(settings)
    assert sorted(client.calls)==[2,3] and len(bulk_calls)==1
    assert set(bulk_calls[0])=={'item:us:2','item:us:3'}
    assert all(value is False for value in latest_calls)
    assert result['sources']['auctions:us:1']['metadata_failed']==1
    for house in (1,2):
        assert result['sources'][f'auctions:us:{house}']['state']=='success'
        records=original(f'auctions:us:{house}').records
        assert records[0].category=='unknown' and records[1].category=='recipe'


def test_collection_drains_multiple_batches_and_progress_callback(tmp_path):
    repo,settings,now=prepared(tmp_path)
    settings=replace(settings,categories=('toy',))
    class Client:
        calls=0
        def toy_items(self,repository,progress=None):
            self.calls+=1
            progress({'completed':self.calls*50,'total':150,'pending':150-self.calls*50})
            observed=service.status_snapshot()['sources']['toys:us']
            assert observed['state']=='running' and observed['completed']==self.calls*50
            return {'items':list(range(self.calls*50)),'pending':150-self.calls*50,'details_fetched':50,'details_attempted':50}
        def auctions(self,house,previous): return Snapshot(f'auctions:us:{house}',now,now,None,'fixture',())
    client=Client(); service=RefreshService(repo,client,RetainedTSM())
    result=service.run(settings)
    source=result['sources']['toys:us']
    assert client.calls==3 and source['state']=='success'
    assert source['known_items']==150 and source['details_fetched']==150 and source['details_attempted']==150


def test_collection_no_progress_stops_and_partial_failures_visible(tmp_path):
    repo,settings,now=prepared(tmp_path)
    settings=replace(settings,categories=('toy',))
    class Client:
        calls=0
        def toy_items(self,repository):
            self.calls+=1
            return {'items':[2],'pending':1,'failed':1,'unavailable':2,'details_attempted':1}
        def auctions(self,house,previous): return Snapshot(f'auctions:us:{house}',now,now,None,'fixture',())
    client=Client(); result=RefreshService(repo,client,RetainedTSM()).run(settings)
    assert client.calls==2
    assert result['sources']['toys:us']['state']=='partial'
    assert result['sources']['toys:us']['unavailable']==2


def test_collection_batch_budget_is_bounded(tmp_path):
    repo,settings,now=prepared(tmp_path)
    class Client:
        calls=0
        def toy_items(self,repository):
            self.calls+=1
            return {'items':list(range(self.calls)),'pending':10000-self.calls,'details_attempted':50}
        def auctions(self,house,previous): return Snapshot(f'auctions:us:{house}',now,now,None,'fixture',())
    client=Client(); result=RefreshService(repo,client,RetainedTSM()).run(replace(settings,categories=('toy',)))
    assert client.calls==50 and result['sources']['toys:us']['state']=='pending'


def test_real_collection_adapter_finishes_healthy_index_in_one_refresh(tmp_path):
    from wowflipper.sources.blizzard import BlizzardClient
    from wowflipper.sources.http import HTTPClient, Response
    repo,settings,now=prepared(tmp_path)
    calls=[]
    def transport(method,url,*args):
        calls.append(url)
        if '/toy/index?' in url: data={'toys':[{'id':i} for i in range(1,136)]}
        elif '/toy/' in url:
            identity=int(url.split('/toy/')[1].split('?')[0]); data={'item':{'id':1000+identity}}
        elif '/auctions?' in url: data={'auctions':[]}
        else: raise AssertionError(url)
        return Response(200,{},json.dumps(data).encode())
    client=BlizzardClient(HTTPClient(transport)); client._token='fixture'; client._expires=10**20
    service=RefreshService(repo,client,RetainedTSM())
    result=service.run(replace(settings,categories=('toy',)))
    source=result['sources']['toys:us']
    assert source['state']=='success' and source['known_items']==135 and source['pending']==0
    assert source['details_attempted']==135 and source['details_fetched']==135 and source['batches']==3
    assert source['completed']==135 and source['total']==135
    assert len([url for url in calls if '/toy/' in url and '/index?' not in url])==135


@pytest.mark.parametrize('cached',[False,True])
def test_decor_uses_item_class_without_catalog_crawl(tmp_path,cached):
    repo,settings,now=prepared(tmp_path,items=(2,))
    metadata={'category':'unknown','item_class':{'id':20},'item_subclass':{'id':0},'tradeability':True}
    repo.cache_put('decor:us',{'items':[],'remaining':list(range(1,2501))})
    if cached: repo.cache_timed('item:us:2',metadata)
    class Client:
        calls=[]
        def decor_items(self,*args,**kwargs): raise AssertionError('Catalog crawl must not block auction refresh')
        def item(self,item): self.calls.append(item); return metadata
        def auctions(self,house,previous):
            return Snapshot(f'auctions:us:{house}',now,now,None,'fixture',(Auction('us',house,1,2,1,1000,None),))
    client=Client()
    result=RefreshService(repo,client,RetainedTSM()).run(replace(settings,categories=('decor',)))
    assert client.calls==([] if cached else [2])
    assert repo.latest('auctions:us:1').records[0].category=='decor'
    assert result['sources']['decor:us']=={'state':'success','classification':'item_metadata','known_items':0}
    assert len(repo.cache_get('decor:us')['remaining'])==2500


def test_decor_cached_membership_fallback_and_toy_drain_coexist(tmp_path):
    repo,settings,now=prepared(tmp_path,items=(2,))
    repo.cache_put('decor:us',{'items':[2],'remaining':[100]})
    class Client:
        toy_calls=0; decor_calls=0
        def decor_items(self,*args,**kwargs): self.decor_calls+=1; raise AssertionError('Unexpected catalog request')
        def toy_items(self,repository):
            self.toy_calls+=1
            return {'items':list(range(1000,1000+self.toy_calls*50)),'pending':100-self.toy_calls*50}
        def item(self,item): return {'category':'unknown','tradeability':True}
        def auctions(self,house,previous):
            return Snapshot(f'auctions:us:{house}',now,now,None,'fixture',(Auction('us',house,1,2,1,1000,None),))
    client=Client(); result=RefreshService(repo,client,RetainedTSM()).run(replace(settings,categories=('decor','toy')))
    assert client.toy_calls==2 and client.decor_calls==0
    assert result['sources']['toys:us']['state']=='success'
    assert result['sources']['decor:us']['known_items']==1
    assert repo.latest('auctions:us:1').records[0].category=='decor'


def test_metadata_four_workers_preserve_house_budgets_dedup_and_serial_cache_writes(tmp_path,monkeypatch):
    repo,settings,now=prepared(tmp_path,2,items=range(1,206))
    gate=threading.Event(); started=threading.Event(); lock=threading.Lock()
    active=peak=0; calls=[]; network_threads=set(); cache_threads=set()
    original=repo.cache_put
    def cache_put(key,value):
        if key.startswith(('item:','timed:item:')): cache_threads.add(threading.get_ident())
        return original(key,value)
    monkeypatch.setattr(repo,'cache_put',cache_put)
    class Client:
        def auctions(self,house,previous):
            identities=range(1,104) if house==1 else range(1,207)
            return Snapshot(f'auctions:us:{house}',now,now,None,'fixture',tuple(
                Auction('us',house,item,item,1,1000,None) for item in identities))
        def item(self,item):
            nonlocal active,peak
            with lock:
                calls.append(item); active+=1; peak=max(peak,active); network_threads.add(threading.get_ident())
                if active==4: started.set()
            assert gate.wait(5)
            with lock: active-=1
            if item==1: raise ValueError('private missing metadata')
            return {'category':'recipe','tradeability':True}
    service=RefreshService(repo,Client(),RetainedTSM()); task=service.start(settings)
    try:
        assert started.wait(5)
        status=service.status_snapshot()
        assert status['phase']=='metadata' and status['total']==200 and status['completed']==0
        assert len(calls)==4
    finally: gate.set()
    result=task.result(10)
    assert peak==4 and len(calls)==200 and len(set(calls))==200
    assert set(calls)==set(range(1,201)) and 206 not in calls
    assert len(cache_threads)==1 and cache_threads.isdisjoint(network_threads)
    first=result['sources']['auctions:us:1']; second=result['sources']['auctions:us:2']
    assert first['metadata_fetched']==100 and second['metadata_fetched']==100
    assert first['metadata_failed']==1 and second['metadata_failed']==0
    assert first['metadata_skipped']==0 and second['metadata_skipped']==5
    assert first['state']==second['state']=='success'
    for house in (1,2):
        rows=repo.latest(f'auctions:us:{house}').records
        assert rows[0].category=='unknown' and rows[1].category=='recipe'


def test_conditional_auction_refresh_reuses_original_raw_payload(tmp_path,monkeypatch):
    from wowflipper.sources.blizzard import BlizzardClient
    from wowflipper.sources.http import HTTPClient, Response
    repo,settings,now=prepared(tmp_path)
    raw=b'{"auctions": [], "original": "upstream archive"}'
    identity=repo.publish(Snapshot('auctions:us:1',now,now,'modified','fixture',(),raw_payload=raw))
    original=repo.latest('auctions:us:1'); archive=repo.payloads/(original.content_hash+'.json')
    client=BlizzardClient(HTTPClient(lambda *args:Response(304,{},b'')))
    client._token='fixture'; client._expires=10**20
    # Neither normalized previous-load nor republishing a 304 should read raw bytes.
    original_read=type(archive).read_bytes
    def read_bytes(path):
        if path==archive: raise AssertionError('Raw payload reloaded during refresh')
        return original_read(path)
    with monkeypatch.context() as patch:
        patch.setattr(type(archive),'read_bytes',read_bytes)
        result=RefreshService(repo,client,RetainedTSM()).run(settings)
    latest=repo.latest('auctions:us:1')
    assert result['sources']['auctions:us:1']['state']=='success' and latest.id>identity
    assert latest.content_hash==original.content_hash and latest.raw_payload==raw


@pytest.mark.parametrize('content_hash',['a'*64,'../untrusted/path','z'*64])
def test_missing_or_invalid_payload_reference_falls_back_to_normalized_archive(tmp_path,content_hash):
    repo,settings,now=prepared(tmp_path)
    snapshot=Snapshot('auctions:us:1',now,now,None,'fixture',(),content_hash=content_hash)
    repo.publish(snapshot)
    latest=repo.latest('auctions:us:1')
    assert latest.content_hash!=content_hash
    assert json.loads(latest.raw_payload)['source_key']=='auctions:us:1'


def test_global_catalog_failure_preserves_public_progress_and_continues_auction_refresh(tmp_path):
    from wowflipper.sources.blizzard import BlizzardClient
    from wowflipper.sources.http import HTTPClient, Response, SourceError
    repo,settings,now=prepared(tmp_path); attempted=[]
    def transport(method,url,*args):
        if '/toy/index?' in url: data={'toys':[{'id':i} for i in range(1,101)]}
        elif '/toy/' in url:
            identity=int(url.split('/toy/')[1].split('?')[0]); attempted.append(identity)
            if identity==1: raise SourceError('private rate limit response',code='rate_limited',status=429)
            data={'item':{'id':1000+identity}}
        elif '/auctions?' in url: data={'auctions':[]}
        else: raise AssertionError(url)
        return Response(200,{},json.dumps(data).encode())
    client=BlizzardClient(HTTPClient(transport)); client._token='fixture'; client._expires=10**20
    result=RefreshService(repo,client,RetainedTSM()).run(replace(settings,categories=('toy',)))
    source=result['sources']['toys:us']
    assert sorted(attempted)==[1,2,3,4]
    assert source['state']=='failed' and source['error_code']=='rate_limited' and source['http_status']==429
    assert source['completed']==source['known_items']==3 and source['total']==100
    assert source['pending']==97 and source['attempted']==4 and source['using_cached_items'] is True
    assert result['sources']['auctions:us:1']['state']=='success'
    assert 'private' not in json.dumps(result)


@pytest.mark.parametrize('code,status',[('auth_missing',None),('auth_denied',403),('rate_limited',429)])
def test_global_metadata_failure_defers_unstarted_jobs_preserves_cached_rows_and_actual_counts(tmp_path,code,status):
    from wowflipper.sources.http import SourceError
    repo,settings,now=prepared(tmp_path,2,items=range(1,206))
    repo.cache_timed('item:us:1',{'category':'recipe','tradeability':True})
    barrier=threading.Barrier(4); calls=[]
    class Client:
        def auctions(self,house,previous):
            return Snapshot(f'auctions:us:{house}',now,now,None,'fixture',tuple(
                Auction('us',house,item,item,1,1000,None) for item in range(1,206)))
        def item(self,item):
            calls.append(item); barrier.wait(timeout=5)
            raise SourceError('private upstream authentication failure',code=code,status=status)
    result=RefreshService(repo,Client(),RetainedTSM()).run(settings)
    assert sorted(calls)==[2,3,4,5]
    first=result['sources']['auctions:us:1']; second=result['sources']['auctions:us:2']
    assert first['metadata_fetched']==4 and second['metadata_fetched']==0
    assert first['metadata_failed']==4 and second['metadata_failed']==0
    assert first['metadata_deferred']==96 and second['metadata_deferred']==100
    assert first['metadata_error_code']==second['metadata_error_code']==code
    assert first['state']==second['state']=='success'
    for house in (1,2):
        rows=repo.latest(f'auctions:us:{house}').records
        assert rows[0].category=='recipe' and rows[1].category=='unknown'
    assert 'private' not in json.dumps(result)


@pytest.mark.parametrize('code,status',[('auth_missing',None),('auth_denied',403),('rate_limited',429)])
def test_global_house_failure_defers_queued_houses_and_retains_all_previous_snapshots(tmp_path,code,status):
    from wowflipper.sources.http import SourceError
    repo,settings,now=prepared(tmp_path,7)
    previous={house:repo.publish(Snapshot(f'auctions:us:{house}',now,now,None,'fixture',())) for house in range(1,8)}
    barrier=threading.Barrier(3); calls=[]
    class Client:
        def auctions(self,house,previous):
            calls.append(house); barrier.wait(timeout=5)
            raise SourceError('private global response',code=code,status=status)
    result=RefreshService(repo,Client(),RetainedTSM()).run(settings)
    assert sorted(calls)==[1,2,3]
    for house in range(1,8):
        source=result['sources'][f'auctions:us:{house}']
        assert source['state']==('failed' if house<=3 else 'skipped') and source['error_code']==code
        if house>3: assert source['deferred'] is True
        assert repo.latest(f'auctions:us:{house}').id==previous[house]
    assert not result['running'] and 'private' not in json.dumps(result)
