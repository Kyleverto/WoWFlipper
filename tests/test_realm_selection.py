import json
import pytest
from wowflipper.sources.blizzard import BlizzardClient
from wowflipper.sources.http import HTTPClient, Response

@pytest.mark.parametrize('slug,category,locale,timezone,is_us', [
    ('tichondrius','United States','enUS','America/Los_Angeles',True),
    ('frostmourne','Oceanic','enUS','Australia/Melbourne',False),
    ('azralon','Brazil','ptBR','America/Sao_Paulo',False),
    ('ragnaros','Latin America','esMX','America/Chicago',False),
    ('unknown',None,'enUS','America/New_York',False),
])
def test_realm_details_preserve_authoritative_category(slug,category,locale,timezone,is_us):
    detail={'slug':slug,'name':slug,'category':category,'locale':locale,'timezone':timezone,
            'connected_realm':{'href':'https://us.api.blizzard.com/data/wow/connected-realm/11?namespace=dynamic-us'}}
    responses=[{'connected_realms':[{'href':'https://us.api.blizzard.com/data/wow/connected-realm/11?namespace=dynamic-us'}]}, {'realms':[{'slug':slug,'name':slug}]},detail]
    client=BlizzardClient(HTTPClient(lambda *a: Response(200,{},json.dumps(responses.pop(0)).encode())))
    client._token='fixture'; client._expires=10**20
    realm=client.realms()[0]
    assert realm['category']==category
    assert realm['locale']==locale
    assert realm['timezone']==timezone
    assert realm['is_us']==is_us
    assert realm['house_id']==11

from datetime import datetime, timezone
from wowflipper.repository import Repository
from wowflipper.refresh import RefreshService
from wowflipper.settings import Settings
from wowflipper.models import Snapshot

def test_fresh_legacy_realm_cache_is_rediscovered(tmp_path):
    repo=Repository(tmp_path)
    repo.cache_timed('realms',[{'slug':'agamaggan','house_id':1}])
    class TSM:
        def region(self,previous=None): raise ValueError('offline')
    class Client:
        calls=0
        def realms(self):
            self.calls+=1
            return [{'slug':'agamaggan','house_id':1,'category':'United States','is_auction_realm':True}]
        def auctions(self,house,previous=None):
            return Snapshot('auctions:us:1',None,datetime.now(timezone.utc),None,'blizzard',())
    client=Client()
    service=RefreshService(repo,client,TSM())
    service.run(Settings())
    assert client.calls==1
    assert repo.cache_get('realms')[0]['category']=='United States'
    service.run(Settings())
    assert client.calls==1

import subprocess
import shutil
from pathlib import Path

@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for frontend behavior tests")
def test_selector_preserves_unknown_and_nonus_selections_and_bounds_bulk_actions():
    script='''
const assert=require('node:assert/strict');
const {realmOptions, bulkBuy}=require('./wowflipper/static/realm-selection.js');
const realms=[{slug:'us',name:'US',category:'United States',is_auction_realm:true},
 {slug:'oce',name:'Oceania',category:'Oceanic'},
 {slug:'unknown',name:'Unknown'}];
const settings={buying_realms:['oce','missing'],selling_realms:['unknown']};
const options=realmOptions(realms,settings);
assert.deepEqual(options.map(r=>r.slug),['us','oce','unknown','missing']);
assert.equal(options.find(r=>r.slug==='missing').legacy,true);
assert.deepEqual(bulkBuy(realms,settings.buying_realms),['oce','missing','us']);
assert.deepEqual(bulkBuy(realms,settings.buying_realms,true),[]);
assert.deepEqual(settings.selling_realms,['unknown']);
'''
    subprocess.run(['node','-e',script],check=True,cwd=Path(__file__).resolve().parents[1])

@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for frontend behavior tests")
def test_results_loading_cancels_old_requests_deduplicates_and_blocks_stale_export():
    script=r'''
const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const elements=new Map();
function node(id) { if(!elements.has(id)) elements.set(id,{value:'',textContent:'',hidden:false,disabled:false,attrs:{},setAttribute(k,v){this.attrs[k]=v},removeAttribute(k){delete this.attrs[k]},replaceChildren(){this.cleared=true}}); return elements.get(id); }
let requests=[];
const context={console,AbortController,URLSearchParams, document:{getElementById:node}, sessionStorage:{getItem:()=>null}, Map,JSON};
vm.createContext(context);
let source=fs.readFileSync('wowflipper/static/app.js','utf8');
source=source.slice(0,source.indexOf('function renderResults()'));
vm.runInContext(source,context);
vm.runInContext(`api=(path,data,options)=>new Promise((resolve,reject)=>{
 requests.push({path,resolve,reject,signal:options?.signal});
}); renderResults=()=>{};`,context);
context.requests=requests;
const first=vm.runInContext('loadResults()',context);
const same=vm.runInContext('loadResults()',context);
assert.equal(requests.length,1);
assert.equal(node('export').attrs['aria-disabled'],'true');
assert.ok(node('results-status').textContent.includes('Loading'));
node('search').value='new';
const second=vm.runInContext('loadResults()',context);
assert.equal(requests.length,2);
assert.equal(requests[0].signal.aborted,true);
requests[0].resolve({rows:[{old:true}],page:1,page_size:100,total:1,pages:1,exclusion_total:0});
requests[1].resolve({rows:[],page:1,page_size:100,total:0,pages:1,exclusion_total:0});
Promise.all([first,same,second]).then(()=>{
 assert.equal(node('export').attrs['aria-disabled'],'false');
 assert.ok(node('export').href.includes('q=new'));
 assert.ok(node('results-status').textContent.includes('Loaded'));
 node('search').value='failed'; const failure=vm.runInContext('loadResults()',context);
 requests[2].reject(new Error('fixture failure'));
 return failure.catch(()=>{assert.equal(node('export').attrs['aria-disabled'],'true');assert.ok(node('results-status').textContent.includes('failed'));});
}).catch(error=>{console.error(error);process.exitCode=1});
'''
    subprocess.run(['node','-e',script],check=True,cwd=Path(__file__).resolve().parents[1])

@pytest.mark.skipif(shutil.which('node') is None, reason='Node is required for frontend behavior tests')
def test_realm_refresh_preserves_drafts_and_search_and_blocks_new_legacy_memberships():
    script=r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node {
 constructor(){this.children=[];this.dataset={};this.value='';this.checked=false;this.disabled=false;}
 append(...nodes){this.children.push(...nodes)}
 replaceChildren(...nodes){this.children=nodes}
 querySelectorAll(selector){let result=[]; const membership=selector.match(/data-membership=['"]?(buying|selling)/)?.[1];
 function visit(node){for(const child of node.children || []) {if(child.dataset?.membership && (!membership || child.dataset.membership===membership) && (!selector.includes(':checked') || child.checked)) result.push(child); visit(child);}} visit(this); return result;}
}
const elements=new Map();const $=id=>{if(!elements.has(id))elements.set(id,new Node());return elements.get(id)};
const context={console,$,el:()=>new Node(),document:{createTextNode:text=>({text})},state:{realms:[{slug:'us',category:'United States',is_auction_realm:true},{slug:'oce',category:'Oceanic'}],settings:{buying_realms:['oce'],selling_realms:[]}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('wowflipper/static/realm-selection.js','utf8'),context);
const source=fs.readFileSync('wowflipper/static/app.js','utf8');
vm.runInContext(source.slice(source.indexOf('function renderRealmSelection('),source.indexOf('$("realm-search").oninput')),context);
vm.runInContext('renderRealmSelection()',context);
const input=(slug,type)=>$('realm-membership').querySelectorAll('input').find(n=>n.dataset.realm===slug&&n.dataset.membership===type);
assert.equal(input('oce','buying').disabled,false);
assert.equal(input('oce','selling').disabled,true);
input('us','buying').checked=true; input('oce','buying').checked=false;
$('realm-search').value='us';
context.state.realms.push({slug:'new',category:'United States',is_auction_realm:true});
vm.runInContext('renderRealmSelection(true)',context);
assert.equal(input('us','buying').checked,true);
assert.equal(input('oce','buying').checked,false);
assert.equal(input('oce','buying').disabled,false);
assert.equal(input('oce','selling').disabled,true);
assert.equal(input('new','buying').checked,false);
assert.equal($('realm-search').value,'us');
assert.equal($('realm-select-all').disabled,false);
assert.equal($('realm-membership').children.find(n=>n.dataset.search.startsWith('new')).hidden,true);
// Exercise the actual state polling path: choices update while Settings is open.
context.api=async()=>context.state; context.currentTab='settings';context.shoppingRows=new Map();context.inventoryRows=new Map();context.stateSequence=0;context.stateGeneration=0;
context.acceptDataEpoch=()=>true;context.syncBackupControls=()=>{};
context.renderInventory=context.renderCapital=context.renderSources=context.renderLedger=context.populateDestinations=context.renderResaleOverrides=context.renderShoppingGroups=()=>{};
context.renderSettings=()=>{throw Error('must not reset the full settings form')};
context.state.mode='live';context.state.refresh={running:false};
vm.runInContext(source.slice(source.indexOf('async function loadState('),source.indexOf('function renderCapital(')),context);
context.state.realms.push({slug:'updated',category:'United States',is_auction_realm:true});
vm.runInContext('loadState()',context).then(()=>{
assert.ok(input('updated','buying'));assert.equal(input('us','buying').checked,true);assert.equal($('realm-search').value,'us');
}).catch(error=>{console.error(error);process.exitCode=1});
'''
    subprocess.run(['node','-e',script],check=True,cwd=Path(__file__).resolve().parents[1])

@pytest.mark.skipif(shutil.which('node') is None, reason='Node is required for frontend behavior tests')
def test_force_reload_after_data_change_retires_same_query_pending_result():
    script=r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements=new Map();
function node(id){if(!elements.has(id))elements.set(id,{value:'',attrs:{},textContent:'',setAttribute(k,v){this.attrs[k]=v},removeAttribute(k){delete this.attrs[k]},replaceChildren(){}});return elements.get(id)}
const context={AbortController,URLSearchParams,document:{getElementById:node},sessionStorage:{getItem:()=>null},requests:[]};
vm.createContext(context);
const source=fs.readFileSync('wowflipper/static/app.js','utf8');
vm.runInContext(source.slice(0,source.indexOf('function renderResults()')),context);
vm.runInContext('api=(path,data,options)=>new Promise(resolve=>requests.push({resolve,signal:options.signal}));renderResults=()=>{}',context);
const old=vm.runInContext('loadResults()',context);
const fresh=vm.runInContext('loadResults(true)',context);
assert.equal(context.requests.length,2);
assert.equal(context.requests[0].signal.aborted,true);
context.requests[1].resolve({rows:[],page:1,page_size:100,total:0,pages:1,exclusion_total:0});
fresh.then(()=>{
assert.ok(node('results-status').textContent.includes('Loaded 0'));
context.requests[0].resolve({rows:[{stale:true}],page:1,page_size:100,total:1,pages:1,exclusion_total:0});
return old;
}).then(()=>{
assert.ok(node('results-status').textContent.includes('Loaded 0'));
assert.equal(vm.runInContext('rows.length',context),0);
assert.equal(node('export').attrs['aria-disabled'],'false');
}).catch(error=>{console.error(error);process.exitCode=1});
'''
    subprocess.run(['node','-e',script],check=True,cwd=Path(__file__).resolve().parents[1])


def test_connected_realm_index_excludes_internal_realms_without_name_heuristics():
    realms=[{'slug':'normal','name':'Normal'}, {'slug':'account','name':'US1A Account Realm'},
            {'slug':'inst','name':'US1A1-INST'}, {'slug':'odd-name','name':'Account Realm Player'}]
    houses={'normal':11,'account':1436,'inst':3017,'odd-name':12}
    def transport(method,url,*args):
        path=url.split('?')[0]
        if path.endswith('/connected-realm/index'):
            payload={'connected_realms':[{'href':f'https://us.api.blizzard.com/data/wow/connected-realm/{house}?namespace=dynamic-us'} for house in (11,12)]}
        elif path.endswith('/realm/index'): payload={'realms':realms}
        else:
            slug=path.rsplit('/',1)[-1]
            payload={'category':'United States','is_tournament':False,'connected_realm':{'href':f'https://us.api.blizzard.com/data/wow/connected-realm/{houses[slug]}?namespace=dynamic-us'}}
        return Response(200,{},json.dumps(payload).encode())
    client=BlizzardClient(HTTPClient(transport));client._token='fixture';client._expires=10**20
    result=client.realms()
    assert [(r['slug'],r.get('is_auction_realm')) for r in result]==[('normal',True),('account',False),('inst',False),('odd-name',True)]


def test_refresh_retains_and_reports_unsupported_and_missing_memberships(tmp_path):
    from dataclasses import replace
    repo=Repository(tmp_path)
    class Client:
        def realms(self): return [{'slug':'agamaggan','house_id':11,'category':'United States','is_auction_realm':True},
                                  {'slug':'internal','house_id':1436,'category':'United States','is_auction_realm':False}]
        def auctions(self,house,previous=None):
            assert house==11
            return Snapshot('auctions:us:11',None,datetime.now(timezone.utc),None,'blizzard',())
    class TSM:
        def region(self,previous=None): raise ValueError('offline')
    settings=replace(Settings(),buying_realms=('agamaggan','internal','missing'))
    status=RefreshService(repo,Client(),TSM()).run(settings)
    assert status['sources'].get('auctions:us:11',{}).get('state')=='success'
    assert status['sources'].get('auctions:us:1436',{}).get('error_code')=='unsupported_realm'
    assert status['sources'].get('realm:us:missing',{}).get('error_code')=='realm_unavailable'
    assert repo.load_settings().buying_realms==settings.buying_realms


def test_legacy_cache_index_failure_does_not_classify_or_drop_realms(tmp_path):
    repo=Repository(tmp_path)
    legacy=[{'slug':'agamaggan','house_id':11,'category':'United States'}]
    repo.cache_put('realms',legacy)
    repo.cache_timed('realms',legacy)
    class Client:
        def realms(self): raise ValueError('index unavailable')
        def auctions(self,*args): raise AssertionError('unverified house must be reported, not queried')
    class TSM:
        def region(self,previous=None): raise ValueError('offline')
    status=RefreshService(repo,Client(),TSM()).run(Settings())
    assert status['sources'].get('auctions:us:11',{}).get('error_code')=='verification_pending'
    assert repo.cache_get('realms')==legacy
    assert repo.load_settings().buying_realms==('agamaggan',)


def test_bulk_choices_exclude_internal_and_unverified_us_realms():
    script=r'''
const assert=require('node:assert/strict');
const {realmOptions,bulkBuy}=require('./wowflipper/static/realm-selection.js');
const realms=[{slug:'player',category:'United States',is_auction_realm:true},
 {slug:'internal',category:'United States',is_auction_realm:false},
 {slug:'unverified',category:'United States'},
 {slug:'oce',category:'Oceanic',is_auction_realm:true}];
assert.deepEqual(bulkBuy(realms,['internal']),['internal','player']);
const options=realmOptions(realms,{buying_realms:['internal'],selling_realms:[]});
assert.deepEqual(options.map(r=>[r.slug,r.legacy]),[['player',false],['internal',true]]);
'''
    subprocess.run(['node','-e',script],check=True,cwd=Path(__file__).resolve().parents[1])


@pytest.mark.parametrize('payload',[{'connected_realms':[]},{'connected_realms':[{}]},
    {'connected_realms':[{'href':'https://us.api.blizzard.com/data/wow/realm/11'}]},
    {'connected_realms':[{'href':'https://example.invalid/data/wow/connected-realm/11'}]}])
def test_invalid_connected_index_preserves_previously_verified_cache(tmp_path,payload):
    repo=Repository(tmp_path)
    verified=[{'slug':'agamaggan','house_id':11,'category':'United States','is_auction_realm':True}]
    repo.cache_put('realms',verified)
    client=BlizzardClient(HTTPClient(lambda *args:Response(200,{},json.dumps(payload).encode())))
    client._token='fixture';client._expires=10**20
    # Discovery failure keeps the previous classification and usable auction source.
    client.auctions=lambda house,previous=None:Snapshot('auctions:us:11',None,datetime.now(timezone.utc),None,'blizzard',())
    class TSM:
        def region(self,previous=None): raise ValueError('offline')
    status=RefreshService(repo,client,TSM()).run(Settings())
    assert status['sources']['realms:us']['state']=='failed'
    assert status['sources']['realms:us']['using_cached_realms'] is True
    assert status['sources']['auctions:us:11']['state']=='success'
    assert repo.cache_get('realms')==verified
    assert repo.load_settings().buying_realms==('agamaggan',)


def test_fresh_classified_cache_migrates_with_one_index_fetch(tmp_path):
    from dataclasses import replace
    repo=Repository(tmp_path)
    legacy=[{'slug':'agamaggan','house_id':11,'category':'United States'},
            {'slug':'internal','house_id':1436,'category':'United States'}]
    repo.cache_put('realms',legacy);repo.cache_timed('realms',legacy)
    class Client:
        index_calls=0
        def connected_realm_ids(self): self.index_calls+=1;return {11}
        def realms(self): raise AssertionError('complete details already cached')
        def auctions(self,house,previous=None):
            assert house==11
            return Snapshot('auctions:us:11',None,datetime.now(timezone.utc),None,'blizzard',())
    class TSM:
        def region(self,previous=None): raise ValueError('offline')
    client=Client();service=RefreshService(repo,client,TSM())
    settings=replace(Settings(),buying_realms=('agamaggan','internal'))
    result=service.run(settings)
    assert result['sources']['auctions:us:11']['state']=='success'
    assert result['sources']['auctions:us:1436']['error_code']=='unsupported_realm'
    assert [r['is_auction_realm'] for r in repo.cache_get('realms')]==[True,False]
    service.run(settings)
    assert client.index_calls==1
    assert repo.load_settings().buying_realms==settings.buying_realms


def test_empty_realm_catalog_keeps_last_good_cache(tmp_path):
    repo=Repository(tmp_path)
    verified=[{'slug':'agamaggan','house_id':11,'category':'United States','is_auction_realm':True}]
    repo.cache_put('realms',verified)
    def transport(method,url,*args):
        data={'connected_realms':[{'href':'https://us.api.blizzard.com/data/wow/connected-realm/11?namespace=dynamic-us'}]} if '/connected-realm/index?' in url else {'realms':[]}
        return Response(200,{},json.dumps(data).encode())
    client=BlizzardClient(HTTPClient(transport));client._token='fixture';client._expires=10**20
    client.auctions=lambda house,previous=None:Snapshot('auctions:us:11',None,datetime.now(timezone.utc),None,'blizzard',())
    class TSM:
        def region(self,previous=None): raise ValueError('offline')
    result=RefreshService(repo,client,TSM()).run(Settings())
    assert result['sources']['realms:us']['state']=='failed'
    assert result['sources']['auctions:us:11']['state']=='success'
    assert repo.cache_get('realms')==verified
