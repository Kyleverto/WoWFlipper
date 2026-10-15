"""Bounded, resumable discovery with synthetic transports only."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import Barrier, Lock
import pytest

from wowflipper.sources.blizzard import BlizzardClient
from wowflipper.sources.http import HTTPClient, Response, SourceError


def response(data,status=200):
    return Response(status,{},json.dumps(data).encode())


class Cache:
    def __init__(self):
        self.values={}; self.writes=[]
    def cache_get(self,key): return deepcopy(self.values.get(key))
    def cache_fresh(self,key,age): return self.cache_get('timed:'+key)
    def cache_put(self,key,value):
        self.values[key]=deepcopy(value); self.writes.append(key)
    def cache_timed(self,key,value): self.cache_put('timed:'+key,value)


def client(transport):
    result=BlizzardClient(HTTPClient(transport))
    result._token='fixture'; result._expires=10**20
    return result


def test_missing_detail_does_not_starve_later_ids_and_waits_for_index_refresh():
    cache=Cache(); attempted=[]; events=[]
    def transport(method,url,*args):
        if '/index?' in url: return response({'toys':[{'id':i} for i in range(1,5)]})
        identity=int(url.split('/toy/')[1].split('?')[0]); attempted.append(identity)
        return response({},404) if identity==2 else response({'item':{'id':identity+100}})
    adapter=client(transport)
    first=adapter.toy_items(cache,limit=2,progress=events.append)
    assert first=={'items':[101],'pending':2,'details_fetched':1,'details_attempted':2,'unavailable':1,'errors':1}
    second=adapter.toy_items(cache)
    assert second['items']==[101,103,104] and second['pending']==0
    assert adapter.toy_items(cache)['details_attempted']==0
    assert attempted.count(2)==1
    assert cache.cache_get('toys:us')['unavailable']['2']=={'error_code':'unavailable','http_status':404,'attempts':1}
    assert events[-1]['pending']==2 and events[-1]['unavailable']==1
    cache.values.pop('timed:toys:us:index')
    adapter.toy_items(cache)
    assert attempted.count(2)==2


def test_transient_failure_rotates_behind_untouched_work_and_keeps_safe_diagnostics():
    cache=Cache(); attempted=[]
    def transport(method,url,*args):
        if '/index?' in url: return response({'toys':[{'id':i} for i in range(1,5)]})
        identity=int(url.split('/toy/')[1].split('?')[0]); attempted.append(identity)
        if identity==1 and attempted.count(1)==1: raise ValueError('private upstream payload')
        return response({'item':{'id':identity+100}})
    adapter=client(transport)
    adapter.toy_items(cache,limit=2)
    assert cache.cache_get('toys:us')['remaining']==[3,4,1]
    assert 'private' not in json.dumps(cache.values)
    assert adapter.toy_items(cache,limit=2)['pending']==1
    result=adapter.toy_items(cache,limit=2)
    assert result['items']==[101,102,103,104] and result['pending']==0
    assert cache.cache_get('toys:us')['failures']=={}


def test_collectibles_use_four_workers_and_fifty_attempt_budget_with_batched_writes():
    cache=Cache(); barrier=Barrier(4); attempted=[]; lock=Lock(); events=[]
    def transport(method,url,*args):
        if '/index?' in url: return response({'decor_items':[{'id':i} for i in range(1,61)]})
        identity=int(url.split('/decor/')[1].split('?')[0])
        with lock: attempted.append(identity)
        if identity<=4: barrier.wait(timeout=5)
        return response({'items':{'id':identity+100}})
    result=client(transport).decor_items(cache,limit=500,progress=events.append)
    assert result['details_attempted']==50 and result['details_fetched']==50 and result['pending']==10
    assert len(attempted)==50 and len(events)==13
    assert len(cache.writes)==27  # index plus two compatibility writes per batch
    assert events[-1]['completed']==50 and events[-1]['total']==60


def test_realm_details_use_four_workers_and_keep_index_order():
    barrier=Barrier(4); events=[]
    def transport(method,url,*args):
        if '/connected-realm/index?' in url:
            return response({'connected_realms':[{'href':'https://us.api.blizzard.com/data/wow/connected-realm/1'}]})
        if '/realm/index?' in url: return response({'realms':[{'slug':str(i)} for i in range(8)]})
        identity=int(url.split('/realm/')[1].split('?')[0])
        if identity<4: barrier.wait(timeout=5)
        return response({'connected_realm':{'href':'https://us.api.blizzard.com/data/wow/connected-realm/1'},'category':'United States'})
    result=client(transport).realms(progress=events.append)
    assert [realm['slug'] for realm in result]==[str(i) for i in range(8)]
    assert all(realm['is_auction_realm'] for realm in result)
    assert events[-1]=={'completed':8,'total':8,'pending':0,'failed':0,'unavailable':0}


def test_concurrent_401_responses_rotate_token_once():
    barrier=Barrier(4); lock=Lock(); authentications=[]
    def transport(method,url,headers,*args):
        if method=='POST':
            with lock: authentications.append(True)
            return response({'access_token':'replacement','expires_in':3600})
        if headers['Authorization']=='Bearer fixture':
            barrier.wait(timeout=5)
            return response({},401)
        assert headers['Authorization']=='Bearer replacement'
        return response({'ok':True})
    adapter=client(transport); adapter.client_id='fixture'; adapter.client_secret='fixture'
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda i:adapter._get('/data/wow/item/'+str(i)),range(4)))
    assert len(authentications)==1 and all(result.status==200 for result in results)


def test_null_binding_remains_unknown_tradeability():
    assert client(lambda *args:response({'binding':None})).item(1)['tradeability'] is None


@pytest.mark.parametrize('code,status',[('auth_missing',None),('auth_denied',403),('rate_limited',429)])
def test_global_collection_failure_stops_after_current_batch_and_retains_progress(code,status):
    cache=Cache(); attempted=[]; events=[]
    def transport(method,url,*args):
        if '/index?' in url: return response({'toys':[{'id':i} for i in range(1,101)]})
        identity=int(url.split('/toy/')[1].split('?')[0]); attempted.append(identity)
        if identity==1: raise SourceError('private credentials upstream response',code=code,status=status)
        return response({'item':{'id':identity+100}})
    with pytest.raises(SourceError) as failure:
        client(transport).toy_items(cache,progress=events.append)
    assert failure.value.code==code and sorted(attempted)==[1,2,3,4]
    state=cache.cache_get('toys:us')
    assert state['items']==[102,103,104] and len(state['remaining'])==97
    assert state['remaining'][-1]==1 and state['failures']['1']['error_code']==code
    assert events[-1]['completed']==3 and events[-1]['total']==100 and events[-1]['pending']==97
    assert events[-1]['attempted']==4 and 'private' not in json.dumps(cache.values)
