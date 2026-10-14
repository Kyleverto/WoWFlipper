"""Actual frontend handlers exercised with a small DOM and transport fixture."""
from pathlib import Path
import shutil
import subprocess
import pytest

HARNESS = r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node {
 constructor(tag='div',text=''){this.tag=tag;this.textContent=text;this.children=[];this.value='';this.attrs={};this.options=this.children;this.disabled=false;}
 append(...nodes){for(const n of nodes){if(n.parentNode)n.parentNode.removeChild(n);this.children.push(n);n.parentNode=this}}
 removeChild(n){const i=this.children.indexOf(n);if(i<0)throw Error('not a child');this.children.splice(i,1);n.parentNode=null;return n}
 insertBefore(n,before){if(n.parentNode)n.parentNode.removeChild(n);const i=before===null?this.children.length:this.children.indexOf(before);if(i<0)throw Error('reference is not a child');this.children.splice(i,0,n);n.parentNode=this;return n}
 replaceChildren(...nodes){for(const n of [...this.children])this.removeChild(n);this.append(...nodes)}
 setAttribute(k,v){this.attrs[k]=v}
 querySelectorAll(selector){let found=[];for(const child of this.children){if(selector.split(',').some(s=>s.startsWith('.') ? child.className===s.slice(1) : child.tag===s))found.push(child);if(child.querySelectorAll)found.push(...child.querySelectorAll(selector))}return found}
}
const elements=new Map();function node(id){if(!elements.has(id))elements.set(id,new Node());return elements.get(id)}
const requests=[];let uuid=0;
const context={console,URLSearchParams,crypto:{randomUUID:()=>`id-${++uuid}`},sessionStorage:{getItem:()=>null,setItem:()=>{}},document:{getElementById:node,createElement:tag=>new Node(tag),createTextNode:text=>new Node('text',text)},Option:function(text,value){const n=new Node('option',text);n.value=String(value);return n},fetch:(path,opts)=>new Promise((resolve,reject)=>requests.push({path,payload:opts?.body?JSON.parse(opts.body):undefined,resolve:body=>resolve({ok:true,json:async()=>body}),fail:body=>resolve({ok:false,status:400,json:async()=>body}),reject}))};
vm.createContext(context);const source=fs.readFileSync('wowflipper/static/app.js','utf8');vm.runInContext(source.slice(0,source.indexOf('$("realm-search").oninput')),context);
context.fixture={settings:{selling_realms:['home','away'],resale_factor:'1'}};vm.runInContext('state=fixture;csrf="token";const reloadState=loadState;loadState=async()=>{}',context);node('destination').value='home';
const row={key:['us',1,2],auction:{item_id:7,quantity:1,buyout:10000,category:'toy'},valuation:{name:'Item',avg_sale_price:30000,market_value:30000,historical:30000,sale_rate:1,sold_per_day:1},source_realms:['buy'],mode:'offline-demo',roi:1,profit:10000,proceeds:20000,risks:[],comparisons:[{realm:'home',mode:'offline-demo',listing_count:1,quantity:1,lowest_unit_ask:20000,assumed_unit_resale:30000,risks:[]}]};
const shoppingNodes=()=>node('shopping-items').querySelectorAll('.shopping-row');
const text=n=>[n.textContent,...n.children.map(text)].join(' '),controls=(root,tag)=>root.querySelectorAll(tag),button=(root,label)=>controls(root,'button').find(n=>n.textContent===label),flush=()=>new Promise(resolve=>setImmediate(resolve)),submit=(form,buy)=>form.onsubmit({preventDefault(){},submitter:buy});
'''

def run_behavior(script):
    subprocess.run(['node','-e',HARNESS+'\n(async()=>{\n'+script+'\n})().catch(e=>{console.error(e);process.exitCode=1});'],check=True,cwd=Path(__file__).resolve().parents[1])

pytestmark=pytest.mark.skipif(shutil.which('node') is None,reason='Node is required for frontend behavior tests')

def test_opportunity_destination_is_local_and_compare_choice_is_shared_with_main_action():
    run_behavior(r'''
context.items=[row,{...row,key:['us',1,3]}];vm.runInContext('rows=items;renderResults()',context);
const tableRows=controls(node('results'),'tr'),first=tableRows[1],details=tableRows[2],second=tableRows[3],secondDetails=tableRows[4];
assert.equal(controls(first,'select').length,0);assert.equal(controls(second,'select').length,0);assert.deepEqual(controls(first,'button').map(n=>n.textContent),['Compare','Select listing']);
assert.equal(details.hidden,true);button(first,'Compare').onclick();assert.equal(details.hidden,false);
const picker=controls(details,'select')[0];assert.ok(picker,'Compare needs a destination selector');assert.equal(picker.value,'home');assert.equal(controls(secondDetails,'select')[0].value,'home');
picker.value='away';picker.onchange();assert.equal(node('destination').value,'home');assert.equal(controls(secondDetails,'select')[0].value,'home');vm.runInContext('loadShopping=async()=>{}',context);
const mainSelection=button(first,'Select listing').onclick();assert.equal(requests[0].path,'/api/shopping');assert.equal(requests[0].payload.destination,'away');requests[0].resolve({});await mainSelection;
picker.value='home';picker.onchange();const comparisonSelection=button(details,'Select listing').onclick();assert.equal(requests[1].payload.destination,'home');requests[1].resolve({});await comparisonSelection;
const defaultSelection=button(second,'Select listing').onclick();assert.equal(requests[2].payload.destination,'home');requests[2].resolve({});await defaultSelection;
''')

def test_shopping_save_blocks_purchase_and_preserves_paid_draft_on_retry():
    run_behavior(r'''
context.items=[{key:['us',1,2],candidate:row,cost:10000,quantity:1,destination:'home',valid:true}];const loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
const shoppingRow=shoppingNodes()[0],select=controls(shoppingRow,'select')[0];assert.ok(select,'Shopping selection needs a destination selector');
const form=controls(shoppingRow,'form')[0],paid=controls(form,'input')[0],buy=button(form,'Record purchase'),save=button(shoppingRow,'Save destination');
paid.value='1.2345';select.value='away';select.onchange();assert.equal(buy.disabled,true);assert.equal(save.disabled,false);submit(form,buy);await flush();assert.equal(requests.length,1);
const saving=save.onclick();assert.equal(buy.disabled,true);assert.equal(select.disabled,true);assert.equal(requests[1].path,'/api/shopping/destination');assert.equal(requests[1].payload.destination,'away');assert.deepEqual(requests[1].payload.key,['us',1,2]);submit(form,buy);await flush();assert.equal(requests.length,2);
requests[1].reject(Error('connection lost'));await saving;assert.equal(select.value,'away');assert.equal(paid.value,'1.2345');assert.equal(buy.disabled,true);assert.equal(save.disabled,false);assert.ok(text(shoppingRow).includes('not confirmed'));
const retry=save.onclick();assert.equal(requests[2].payload.request_id,requests[1].payload.request_id);requests[2].resolve({...context.items[0],destination:'away'});await flush();assert.equal(requests[3].path,'/api/shopping');requests[3].resolve([{...context.items[0],destination:'away'}]);await retry;
assert.equal(paid.value,'1.2345');assert.equal(shoppingNodes()[0],shoppingRow);assert.equal(buy.disabled,false);assert.equal(save.disabled,true);assert.ok(text(shoppingRow).includes('Saved destination: away'));
submit(form,buy);await flush();assert.equal(requests[4].path,'/api/ledger');assert.equal(requests[4].payload.event.cost,12345);requests[4].resolve({});await flush();requests[5].resolve([]);await flush();
''')

def test_missing_saved_destination_can_be_repaired_in_place():
    run_behavior(r'''
context.items=[{key:['us',1,2],candidate:row,cost:10000,quantity:1,destination:'removed',valid:false,validation_error:'Destination unavailable'}];const loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
const shoppingRow=shoppingNodes()[0],select=controls(shoppingRow,'select')[0];assert.ok(select);assert.equal(select.value,'removed');assert.ok(select.options.find(o=>o.value==='removed').disabled);assert.equal(select.disabled,false);
select.value='away';select.onchange();const saving=button(shoppingRow,'Save destination').onclick();requests[1].resolve({...context.items[0],destination:'away'});await flush();requests[2].resolve([{...context.items[0],destination:'away',valid:true,validation_error:null}]);await saving;assert.equal(button(shoppingRow,'Record purchase').disabled,false);
''')


def test_unconfirmed_destination_retry_survives_reload_and_keeps_original_request_id():
    run_behavior(r''' 
context.items=[{key:['us',1,2],candidate:row,cost:10000,quantity:1,destination:'home',valid:true}];
vm.runInContext('pending.set("destination:us:1:2", {fingerprint: JSON.stringify(["/api/shopping/destination", {key:["us",1,2],destination:"away"}]), id:"original-id"})',context);
const loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
const shoppingRow=shoppingNodes()[0], select=controls(shoppingRow,'select')[0];assert.equal(select.value,'away');assert.equal(select.disabled,true);assert.equal(button(shoppingRow,'Record purchase').disabled,true);
const saving=button(shoppingRow,'Save destination').onclick();assert.equal(requests[1].payload.destination,'away');assert.equal(requests[1].payload.request_id,'original-id');requests[1].resolve({...context.items[0],destination:'away'});await flush();requests[2].resolve([{...context.items[0],destination:'away'}]);await saving;
assert.equal(button(shoppingRow,'Record purchase').disabled,false);
''')


def test_eligibility_refresh_failure_requires_confirmation_without_losing_draft():
    run_behavior(r'''
context.items=[{key:['us',1,2],candidate:row,cost:10000,quantity:1,destination:'home',valid:true}];const loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
const shoppingRow=shoppingNodes()[0],select=controls(shoppingRow,'select')[0],form=controls(shoppingRow,'form')[0],paid=controls(form,'input')[0],buy=button(form,'Record purchase'),save=button(shoppingRow,'Save destination');
paid.value='9.8765';select.value='away';select.onchange();const saving=save.onclick();requests[1].resolve({...context.items[0],destination:'away'});await flush();requests[2].reject(Error('offline'));await saving;
assert.equal(paid.value,'9.8765');assert.equal(select.value,'away');assert.equal(buy.disabled,true);assert.equal(save.disabled,false);assert.ok(text(shoppingRow).includes('eligibility not confirmed'));submit(form,buy);await flush();assert.equal(requests.length,3);
''')


def test_no_configured_realms_disables_opportunity_select_actions():
    run_behavior(r'''
vm.runInContext('state.settings.selling_realms=[]',context);node('destination').value='';context.items=[row];vm.runInContext('rows=items;renderResults()',context);
assert.equal(controls(controls(node('results'),'tr')[1],'select').length,0);assert.equal(controls(node('results'),'select').length,1);for(const select of controls(node('results'),'select')){assert.equal(select.disabled,true);assert.ok(select.options[0].textContent.includes('Settings'));}
for(const select of controls(node('results'),'button').filter(n=>n.textContent==='Select listing'))assert.equal(select.disabled,true);
''')


def test_opportunity_unconfirmed_local_choice_survives_rerender():
    run_behavior(r'''
context.items=[row];vm.runInContext('rows=items;renderResults()',context);let first=controls(node('results'),'tr')[1],details=controls(node('results'),'tr')[2],picker=controls(details,'select')[0];assert.equal(controls(first,'select').length,0);picker.value='away';picker.onchange();const selected=button(first,'Select listing').onclick();requests[0].reject(Error('offline'));await selected;
vm.runInContext('renderResults()',context);first=controls(node('results'),'tr')[1];details=controls(node('results'),'tr')[2];assert.equal(controls(first,'select').length,0);picker=controls(details,'select')[0];assert.equal(picker.value,'away');vm.runInContext('loadShopping=async()=>{}',context);button(first,'Compare').onclick();const retry=button(details,'Select listing').onclick();assert.equal(requests[1].payload.destination,'away');assert.equal(requests[1].payload.request_id,requests[0].payload.request_id);requests[1].resolve({});await retry;
''')


def test_shopping_dirty_and_paid_drafts_survive_reload():
    run_behavior(r'''
context.items=[{key:['us',1,2],candidate:row,cost:10000,quantity:1,destination:'home',valid:true}];let loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
let current=shoppingNodes()[0],select=controls(current,'select')[0],paid=controls(current,'input')[0];paid.value='4.5678';select.value='away';select.onchange();loading=vm.runInContext('loadShopping()',context);requests[1].resolve(context.items);await loading;
current=shoppingNodes()[0];assert.equal(controls(current,'select')[0].value,'away');assert.equal(controls(current,'input')[0].value,'4.5678');assert.equal(button(current,'Record purchase').disabled,true);
''')


def test_shopping_reload_during_save_retains_blocked_row_and_paid_draft():
    run_behavior(r'''
context.items=[{key:['us',1,2],candidate:row,cost:10000,quantity:1,destination:'home',valid:true}];let loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
const original=shoppingNodes()[0],select=controls(original,'select')[0];controls(original,'input')[0].value='4.5678';select.value='away';select.onchange();const saving=button(original,'Save destination').onclick();loading=vm.runInContext('loadShopping()',context);requests[2].resolve(context.items);await loading;
let current=shoppingNodes()[0];assert.equal(button(current,'Record purchase').disabled,true);assert.equal(controls(current,'select')[0].value,'away');assert.equal(controls(current,'input')[0].value,'4.5678');
requests[1].resolve({...context.items[0],destination:'away'});await flush();requests[3].resolve([{...context.items[0],destination:'away'}]);await saving;current=shoppingNodes()[0];assert.equal(controls(current,'input')[0].value,'4.5678');assert.equal(button(current,'Record purchase').disabled,false);assert.ok(text(current).includes('Saved destination: away'));
''')


def test_old_shopping_response_does_not_restore_removed_rows_or_drafts():
    run_behavior(r'''
context.items=[{key:['us',1,2],candidate:row,cost:10000,quantity:1,destination:'home',valid:true}];let loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
const original=shoppingNodes()[0];controls(original,'input')[0].value='4.5678';const oldLoad=vm.runInContext('loadShopping()',context),newLoad=vm.runInContext('loadShopping()',context);requests[2].resolve([]);await newLoad;requests[1].resolve(context.items);await oldLoad;assert.ok(text(node('shopping-items')).includes('No selected listings'));
loading=vm.runInContext('loadShopping()',context);requests[3].resolve(context.items);await loading;assert.equal(controls(node('shopping-items'),'input')[0].value,'1');
''')


def test_unconfirmed_purchase_blocks_retargeting_and_preserves_retry_cost():
    run_behavior(r'''
context.items=[{key:['us',1,2],candidate:row,cost:10000,quantity:1,destination:'home',valid:true}];const loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
const current=shoppingNodes()[0],paid=controls(current,'input')[0],buy=button(current,'Record purchase'),select=controls(current,'select')[0];paid.value='4.5678';const buying=submit(controls(current,'form')[0],buy);requests[1].reject(Error('offline'));await buying;assert.equal(select.disabled,true);assert.equal(paid.disabled,true);assert.equal(button(current,'Save destination').disabled,true);
const reloading=vm.runInContext('loadShopping()',context);requests[2].resolve(context.items);await reloading;assert.equal(controls(node('shopping-items'),'input')[0].value,'4.5678');assert.equal(controls(node('shopping-items'),'select')[0].disabled,true);
const retry=submit(controls(current,'form')[0],buy);assert.equal(requests[3].payload.request_id,requests[1].payload.request_id);assert.equal(requests[3].payload.event.cost,45678);requests[3].resolve({});await flush();requests[4].resolve([]);await retry;
''')


def test_shopping_reload_while_eligibility_get_pending_updates_current_row():
    run_behavior(r'''
context.items=[{key:['us',1,2],candidate:row,cost:10000,quantity:1,destination:'home',valid:true}];let loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
const original=shoppingNodes()[0],select=controls(original,'select')[0];controls(original,'input')[0].value='4.5678';select.value='away';select.onchange();const saving=button(original,'Save destination').onclick();requests[1].resolve({...context.items[0],destination:'away'});await flush();loading=vm.runInContext('loadShopping()',context);requests[3].resolve([{...context.items[0],destination:'away'}]);await loading;
let current=shoppingNodes()[0];assert.equal(button(current,'Record purchase').disabled,true);assert.equal(controls(current,'input')[0].value,'4.5678');requests[2].resolve([{...context.items[0],destination:'away'}]);await saving;current=shoppingNodes()[0];assert.equal(button(current,'Record purchase').disabled,false);assert.ok(text(current).includes('Saved destination: away'));
''')


def test_shopping_settings_refresh_preserves_draft_and_updates_configured_choices():
    run_behavior(r'''
context.items=[{key:['us',1,2],candidate:row,cost:10000,quantity:1,destination:'home',valid:true}];let loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
let current=shoppingNodes()[0];controls(current,'input')[0].value='4.5678';vm.runInContext('state.settings.selling_realms=["away", "new"]',context);loading=vm.runInContext('loadShopping()',context);requests[1].resolve(context.items);await loading;
current=shoppingNodes()[0];const select=controls(current,'select')[0];assert.equal(select.value,'home');assert.ok(select.options.find(o=>o.value==='home').disabled);assert.ok(select.options.some(o=>o.value==='new'));assert.equal(button(current,'Record purchase').disabled,true);assert.equal(controls(current,'input')[0].value,'4.5678');
''')


def test_shopping_response_started_during_save_cannot_revert_confirmed_destination():
    run_behavior(r'''
context.items=[{key:['us',1,2],candidate:row,cost:10000,quantity:1,destination:'home',valid:true}];let loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
const current=shoppingNodes()[0],select=controls(current,'select')[0];select.value='away';select.onchange();const saving=button(current,'Save destination').onclick();loading=vm.runInContext('loadShopping()',context);requests[1].resolve({...context.items[0],destination:'away'});await flush();requests[3].resolve([{...context.items[0],destination:'away'}]);await saving;
requests[2].resolve([{...context.items[0],destination:'home'}]);await loading;assert.equal(button(current,'Record purchase').disabled,false);assert.ok(text(current).includes('Saved destination: away'));
''')


def test_removed_realm_pending_retries_can_replay_or_clear_confirmed_failure():
    run_behavior(r'''
context.items=[row];vm.runInContext('state.settings.selling_realms=["home"];pending.set("select:us:1:2",{fingerprint:JSON.stringify(["/api/shopping",{key:["us",1,2],destination:"away"}]),id:"selection-id"});rows=items;renderResults()',context);
const first=controls(node('results'),'tr')[1],details=controls(node('results'),'tr')[2],selection=button(details,'Select listing');assert.equal(controls(first,'select').length,0);button(first,'Compare').onclick();assert.equal(selection.disabled,false);vm.runInContext('loadState=async()=>{}',context);const selecting=selection.onclick();assert.equal(requests[0].payload.destination,'away');requests[0].fail({error:'Realm unavailable'});await selecting;assert.equal(controls(details,'select')[0].disabled,false);
context.items=[{key:['us',1,2],candidate:row,cost:10000,quantity:1,destination:'home',valid:true}];vm.runInContext('pending.set("destination:us:1:2",{fingerprint:JSON.stringify(["/api/shopping/destination",{key:["us",1,2],destination:"away"}]),id:"destination-id"})',context);const loading=vm.runInContext('loadShopping()',context);requests[1].resolve(context.items);await loading;
const current=shoppingNodes()[0],save=button(current,'Save destination'),picker=controls(current,'select')[0];assert.equal(save.disabled,false);const saving=save.onclick();assert.equal(requests[2].payload.destination,'away');requests[2].fail({error:'Realm unavailable'});await saving;assert.equal(picker.disabled,false);assert.equal(button(current,'Record purchase').disabled,true);picker.value='home';picker.onchange();assert.equal(button(current,'Record purchase').disabled,false);
''')


def test_removed_realm_pending_destination_success_can_be_repaired():
    run_behavior(r'''
context.items=[{key:['us',1,2],candidate:row,cost:10000,quantity:1,destination:'home',valid:true}];vm.runInContext('state.settings.selling_realms=["home"];pending.set("destination:us:1:2",{fingerprint:JSON.stringify(["/api/shopping/destination",{key:["us",1,2],destination:"away"}]),id:"destination-id"})',context);const loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
const current=shoppingNodes()[0],save=button(current,'Save destination'),picker=controls(current,'select')[0];const saving=save.onclick();requests[1].resolve({...context.items[0],destination:'away'});await flush();requests[2].resolve([{...context.items[0],destination:'away',valid:false,validation_error:'Realm unavailable'}]);await saving;assert.equal(picker.disabled,false);assert.equal(button(current,'Record purchase').disabled,true);picker.value='home';picker.onchange();assert.equal(save.disabled,false);
''')


def test_shopping_buy_realms_use_source_house_and_refresh_without_losing_drafts():
    run_behavior(r'''
context.items=[{key:['us',1,2],candidate:{...row,auction:{...row.auction,house_id:17}},cost:10000,quantity:1,destination:'home',valid:true},{key:['us',18,3],candidate:row,cost:10000,quantity:1,destination:'home',valid:true},{key:['us',99,4],candidate:row,cost:10000,quantity:1,destination:'home',valid:true}];
context.fixture.realms=[{slug:'home',name:'Selling Home',house_id:1},{slug:'first',name:'First Source',house_id:17},{slug:'sibling',name:'Connected Sibling',house_id:17},{slug:'legacy-source',house_id:18}];
context.fixture.settings.buying_realms=['first'];context.fixture.refresh={running:false};
let loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
const original=shoppingNodes()[0],legacy=shoppingNodes()[1],unmapped=shoppingNodes()[2];
const sourceLine=n=>controls(n,'p').find(p=>p.textContent.startsWith('Buy on:')).textContent;
assert.equal(sourceLine(original),'Buy on: First Source, Connected Sibling • Connected auction house 17');
assert.ok(text(original).includes('Saved destination: home'));assert.ok(!sourceLine(original).includes('Selling Home'));
assert.equal(sourceLine(legacy),'Buy on: legacy-source • Connected auction house 18');
assert.equal(sourceLine(unmapped),'Buy on: Realm mapping unavailable • Connected auction house 99');
const paid=controls(original,'input')[0],select=controls(original,'select')[0];paid.value='4.5678';select.value='away';select.onchange();
context.fixture.realms=[{slug:'new-source',name:'Updated Source',house_id:17}];
vm.runInContext('renderCapital=renderSources=renderLedger=populateDestinations=()=>{};currentTab="shopping"',context);
loading=vm.runInContext('reloadState()',context);requests[1].resolve(context.fixture);await loading;
assert.equal(sourceLine(original),'Buy on: Updated Source • Connected auction house 17');
loading=vm.runInContext('loadShopping()',context);requests[2].resolve(context.items);await loading;
assert.equal(shoppingNodes()[0],original);assert.equal(paid.value,'4.5678');assert.equal(select.value,'away');assert.equal(button(original,'Record purchase').disabled,true);
assert.equal(sourceLine(legacy),'Buy on: Realm mapping unavailable • Connected auction house 18');
''')
