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
    responses=[{'realms':[{'slug':slug,'name':slug}]},detail]
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
            return [{'slug':'agamaggan','house_id':1,'category':'United States'}]
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
const realms=[{slug:'us',name:'US',category:'United States'},
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
const context={console,$,el:()=>new Node(),document:{createTextNode:text=>({text})},state:{realms:[{slug:'us',category:'United States'},{slug:'oce',category:'Oceanic'}],settings:{buying_realms:['oce'],selling_realms:[]}}};
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
context.state.realms.push({slug:'new',category:'United States'});
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
context.api=async()=>context.state; context.currentTab='settings';
context.renderCapital=context.renderSources=context.renderLedger=context.populateDestinations=()=>{};
context.renderSettings=()=>{throw Error('must not reset the full settings form')};
context.state.mode='live';context.state.refresh={running:false};
vm.runInContext(source.slice(source.indexOf('async function loadState('),source.indexOf('function renderCapital(')),context);
context.state.realms.push({slug:'updated',category:'United States'});
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
