"""Browser behavior of the single opportunities list without legacy ranking DOM."""
from pathlib import Path
import shutil
import subprocess
import pytest

@pytest.mark.skipif(shutil.which('node') is None, reason='Node is required for frontend behavior tests')
def test_single_opportunity_query_loading_sort_and_export_without_ranking():
    script = r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements=new Map();
function node(id){
 if(id==='ranking') return null;
 if(!elements.has(id)) elements.set(id,{value:'',attrs:{},textContent:'',setAttribute(k,v){this.attrs[k]=v},removeAttribute(k){delete this.attrs[k]},replaceChildren(){}});
 return elements.get(id);
}
node('sort').value='profit';node('direction').value='desc';
const context={console,AbortController,URLSearchParams,document:{getElementById:node},sessionStorage:{getItem:()=>null},requests:[]};
vm.createContext(context);
const source=fs.readFileSync('wowflipper/static/app.js','utf8');
vm.runInContext(source.slice(0,source.indexOf('function renderResults()')),context);
vm.runInContext('api=(path,data,options)=>new Promise((resolve,reject)=>requests.push({path,resolve,reject,signal:options.signal}));renderResults=()=>{}',context);
const first=vm.runInContext('loadResults()',context);
assert.equal(context.requests.length,1);
assert.equal(node('export').attrs['aria-disabled'],'true');
assert.ok(node('results-status').textContent.includes('Loading'));
assert.ok(context.requests[0].path.includes('sort=profit&direction=desc'));
assert.ok(!context.requests[0].path.includes('strategy='));
node('sort').value='sale_rate';node('direction').value='asc';
const second=vm.runInContext('loadResults()',context);
assert.equal(context.requests[0].signal.aborted,true);
context.requests[1].resolve({rows:[],page:1,page_size:100,total:0,pages:1,exclusion_total:0});
second.then(()=>{
 assert.ok(node('export').href.includes('sort=sale_rate&direction=asc'));
 assert.ok(!node('export').href.includes('strategy='));
 assert.equal(node('export').attrs['aria-disabled'],'false');
 context.requests[0].resolve({rows:[{stale:true}],page:1,page_size:100,total:1,pages:1,exclusion_total:0});
 return first;
}).then(()=>{
 assert.equal(vm.runInContext('rows.length',context),0);
 node('search').value='failed';const failure=vm.runInContext('loadResults()',context);
 context.requests[2].reject(new Error('fixture failure'));
 return failure.catch(()=>{assert.equal(node('export').attrs['aria-disabled'],'true');assert.ok(node('results-status').textContent.includes('failed'));});
}).catch(error=>{console.error(error);process.exitCode=1});
'''
    subprocess.run(['node', '-e', script], check=True, cwd=Path(__file__).resolve().parents[1])

@pytest.mark.skipif(shutil.which('node') is None, reason='Node is required for frontend behavior tests')
def test_legacy_shopping_and_history_render_and_correct_without_strategy_controls():
    script = r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements=new Map();
class Node {
 constructor(tag='div',text=''){this.tag=tag;this.textContent=text;this.children=[];this.value='';this.attrs={};this.options=this.children;}
 append(...nodes){this.children.push(...nodes)}
 replaceChildren(...nodes){this.children.splice(0,this.children.length,...nodes)}
 setAttribute(k,v){this.attrs[k]=v}
 querySelectorAll(selector){const found=[];function visit(n){for(const child of n.children||[]){if(selector.split(',').includes(child.tag))found.push(child);visit(child)}}visit(this);return found}
}
function node(id){if(id==='ranking')return null;if(!elements.has(id))elements.set(id,new Node());return elements.get(id)}
const context={console,document:{getElementById:node,createElement:tag=>new Node(tag)},sessionStorage:{getItem:()=>null},Option:function(text,value){const n=new Node('option',text);n.value=String(value);return n}};
vm.createContext(context);
const source=fs.readFileSync('wowflipper/static/app.js','utf8');
vm.runInContext(source.slice(0,source.indexOf('async function loadState(')),context);
vm.runInContext(source.slice(source.indexOf('async function loadShopping('),source.indexOf('function field(')),context);
vm.runInContext(source.slice(source.indexOf('function field('),source.indexOf('function renderSettings(')),context);
vm.runInContext(source.slice(source.indexOf('function renderCorrection('),source.indexOf('$("event-form").onsubmit')),context);
const candidate={valuation:{name:'Legacy item'}};
const purchase={id:1,type:'purchase',strategy:'longer-term',destination:'agamaggan',quantity:1,cost:10000,candidate};
context.fixture={history:[purchase,{id:2,type:'correction',target_id:1,replacement:{...purchase,strategy:'stronger-demand',cost:12000}}],settings:{selling_realms:['agamaggan']},summary:{inventory:[{...purchase,remaining_quantity:1,remaining_cost:12000}]}};
vm.runInContext('state=fixture',context);
node('correction-target').value='1';node('correction-action').value='replace';
vm.runInContext('renderLedger()',context);
const text=n=>[n.textContent,...n.children.map(text)].join(' ');
assert.ok(text(node('inventory')).includes('agamaggan'));
assert.ok(!text(node('inventory')).includes('longer-term'));
assert.ok(!text(node('history')).includes('strategy'));
assert.ok(!text(node('history')).includes('stronger-demand'));
const controls=node('correction-fields').querySelectorAll('input,select');
assert.ok(controls.some(n=>n.id==='correct-cost'&&n.value==='1.2'));
assert.ok(controls.some(n=>n.id==='correct-destination'));
assert.ok(!controls.some(n=>n.id==='correct-strategy'));
assert.equal(purchase.strategy,'longer-term');
context.items=[{...purchase,key:['us',1,2],valid:true}];context.api=async()=>context.items;
vm.runInContext('loadShopping()',context).then(()=>{
assert.ok(text(node('shopping-items')).includes('Legacy item'));
assert.ok(!text(node('shopping-items')).includes('longer-term'));
assert.ok(node('shopping-items').children[0].children.some(n=>n.tag==='form'));
}).catch(error=>{console.error(error);process.exitCode=1});
'''
    subprocess.run(['node', '-e', script], check=True, cwd=Path(__file__).resolve().parents[1])
