"""Resale overrides through the actual browser handlers."""
from test_destination_frontend import HARNESS
from pathlib import Path
import subprocess


def run_behavior(script):
    harness = HARNESS.replace("setAttribute(k,v){this.attrs[k]=v}", "setAttribute(k,v){this.attrs[k]=v} removeAttribute(k){delete this.attrs[k]}")
    harness = harness.replace("const context={console,", "const context={console,AbortController,")
    subprocess.run(['node', '-e', harness + '\n(async()=>{\n' + script + '\n})().catch(e=>{console.error(e);process.exitCode=1});'], check=True, cwd=Path(__file__).resolve().parents[1])


def test_effective_resale_and_verification_keep_raw_source_separate():
    run_behavior(r'''
context.items=[{...row,assumed_unit_resale:'12345',resale_override:{item_id:7,unit_price:12345,updated_at:'2026-10-14T01:00:00Z'}}];vm.runInContext('state.settings.resale_factor="0.5";rows=items;renderResults()',context);
const first=controls(node('results'),'tr')[1],details=controls(node('results'),'tr')[2];assert.equal(first.children[4].textContent,'1.2345g');assert.ok(text(first).includes('Manual resale'));
assert.ok(text(details).includes('Raw TSM average sale price: 3.00g'));assert.ok(text(details).includes('Global resale factor: 50%'));assert.ok(text(details).includes('Effective unit resale: 1.2345g'));assert.ok(text(details).includes('2026-10-14T01:00:00Z'));assert.ok(button(details,'Save override'));assert.ok(button(details,'Clear override'));
''')


def test_compare_save_clear_copper_refresh_all_rows_and_ignore_old_response():
    run_behavior(r'''
context.items=[row];vm.runInContext('rows=items;renderResults();loadShopping=async()=>{}',context);
const details=controls(node('results'),'tr')[2],input=controls(details,'input')[0];input.value='1.2345';
const stale=vm.runInContext('loadResults()',context);const saving=button(details,'Save override').onclick();assert.equal(requests[1].path,'/api/resale-overrides');assert.deepEqual(requests[1].payload,{item_id:7,unit_price:12345});requests[1].resolve({});await flush();
assert.ok(requests[0]);requests[0].resolve({rows:[row],page:1,page_size:100,total:1,pages:1,exclusion_total:0});await stale;assert.equal(vm.runInContext('rows.length',context),0);
const updated={...row,assumed_unit_resale:'12345',resale_override:{item_id:7,unit_price:12345,updated_at:'2026-10-14T01:00:00Z'}};requests[2].resolve({rows:[updated,{...updated,key:['us',2,3]}],page:1,page_size:100,total:2,pages:1,exclusion_total:0});await saving;
for(const tr of controls(node('results'),'tr').slice(1).filter(n=>n.children.length>1))assert.equal(tr.children[4].textContent,'1.2345g');
const clearing=button(controls(node('results'),'tr')[2],'Clear override').onclick();assert.equal(requests[3].path,'/api/resale-overrides/remove');assert.deepEqual(requests[3].payload,{item_id:7});requests[3].resolve({});await flush();requests[4].resolve({rows:[row],page:1,page_size:100,total:1,pages:1,exclusion_total:0});await clearing;assert.equal(controls(node('results'),'tr')[1].children[4].textContent,'3.00g');
''')


def test_settings_manager_repairs_excluded_items_and_validates_money():
    run_behavior(r'''
vm.runInContext('state.resale_overrides=[{item_id:999,unit_price:25000,updated_at:"2026-10-14T01:00:00Z"}];renderResaleOverrides();loadResults=async()=>{};loadShopping=async()=>{}',context);
assert.ok(text(node('resale-overrides')).includes('999'));button(node('resale-overrides'),'Edit').onclick();assert.equal(node('override-item-id').value,'999');assert.equal(node('override-unit-price').value,'2.5');
const form=node('resale-override-form');for(const bad of ['-1','1.00001','1e3','900719925475']){node('override-unit-price').value=bad;await form.onsubmit({preventDefault(){}});assert.equal(requests.length,0)}
node('override-item-id').value='1.5';node('override-unit-price').value='2';await form.onsubmit({preventDefault(){}});assert.equal(requests.length,0);
node('override-item-id').value='999';node('override-unit-price').value='10.0001';const saving=form.onsubmit({preventDefault(){}});assert.deepEqual(requests[0].payload,{item_id:999,unit_price:100001});requests[0].resolve({});await saving;
const clearing=button(node('resale-overrides'),'Clear override').onclick();assert.equal(requests[1].path,'/api/resale-overrides/remove');requests[1].resolve({});await clearing;
''')


def test_override_failure_preserves_editor_and_unrelated_settings_drafts():
    run_behavior(r'''
context.items=[row];vm.runInContext('rows=items;renderResults()',context);const details=controls(node('results'),'tr')[2],input=controls(details,'input')[0];input.value='2.3456';node('setting-capital').value='777';node('override-item-id').value='888';node('override-unit-price').value='9';
const saving=button(details,'Save override').onclick();assert.equal(input.disabled,true);requests[0].fail({error:'Cannot save'});await saving;assert.equal(input.value,'2.3456');assert.equal(input.disabled,false);assert.equal(node('setting-capital').value,'777');assert.equal(node('override-item-id').value,'888');assert.equal(node('override-unit-price').value,'9');assert.ok(text(details).includes('Cannot save'));assert.equal(requests.length,1);vm.runInContext('rows=items;renderResults()',context);assert.equal(controls(controls(node('results'),'tr')[2],'input')[0].value,'2.3456');
''')


def test_saved_override_blocks_unconfirmed_shopping_eligibility_without_losing_paid_draft():
    run_behavior(r'''
context.items=[{key:['us',1,2],candidate:row,cost:10000,quantity:1,destination:'home',valid:true}];const loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
const current=shoppingNodes()[0],paid=controls(current,'input')[0];paid.value='9.8765';vm.runInContext('loadResults=async()=>{}',context);
const saving=vm.runInContext('saveResaleOverride(7,"2")',context);requests[1].resolve({});await flush();assert.equal(button(current,'Record purchase').disabled,true);requests[2].reject(Error('offline'));await assert.rejects(saving);assert.equal(paid.value,'9.8765');assert.equal(button(current,'Record purchase').disabled,true);
const retry=vm.runInContext('loadShopping()',context);requests[3].resolve(context.items);await retry;assert.equal(paid.value,'9.8765');assert.equal(button(current,'Record purchase').disabled,false);
''')


def test_override_connection_error_does_not_claim_a_preserved_request_id():
    run_behavior(r'''
context.items=[row];vm.runInContext('rows=items;renderResults()',context);const details=controls(node('results'),'tr')[2];const saving=button(details,'Save override').onclick();requests[0].reject(Error('offline'));await saving;assert.ok(text(details).includes('Retry Save or Clear'));assert.ok(!text(details).includes('request ID'));
''')


def test_override_pending_and_lost_response_block_old_shopping_eligibility():
    run_behavior(r'''
context.items=[{key:['us',1,2],candidate:row,cost:10000,quantity:1,destination:'home',valid:true}];let loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
const current=shoppingNodes()[0],paid=controls(current,'input')[0];paid.value='9.8765';context.rowsFixture=[row];vm.runInContext('rows=rowsFixture;renderResults()',context);
const before=vm.runInContext('loadShopping()',context),saving=vm.runInContext('saveResaleOverride(7,"2")',context);assert.equal(button(current,'Record purchase').disabled,true);assert.equal(vm.runInContext('rows.length',context),0);
const during=vm.runInContext('loadShopping()',context);requests[3].resolve(context.items);await during;assert.equal(button(current,'Record purchase').disabled,true);
requests[2].reject(Error('response lost after commit'));await assert.rejects(saving);requests[1].resolve(context.items);await before;assert.equal(button(current,'Record purchase').disabled,true);assert.equal(paid.value,'9.8765');
loading=vm.runInContext('loadShopping()',context);requests[4].resolve([{...context.items[0],valid:false,validation_error:'New resale excludes this item'}]);await loading;assert.equal(button(current,'Record purchase').disabled,true);assert.ok(text(current).includes('New resale excludes this item'));assert.equal(paid.value,'9.8765');
''')


def test_destination_eligibility_response_from_before_override_cannot_unlock():
    run_behavior(r'''
context.items=[{key:['us',1,2],candidate:row,cost:10000,quantity:1,destination:'home',valid:true}];let loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
const current=shoppingNodes()[0],select=controls(current,'select')[0];select.value='away';select.onchange();const destSaving=button(current,'Save destination').onclick();requests[1].resolve({...context.items[0],destination:'away'});await flush();
const override=vm.runInContext('saveResaleOverride(7,"2")',context);assert.equal(button(current,'Record purchase').disabled,true);requests[3].fail({error:'Known rejection'});await assert.rejects(override);requests[2].resolve([{...context.items[0],destination:'away',valid:true}]);await destSaving;assert.equal(button(current,'Record purchase').disabled,true);
loading=vm.runInContext('loadShopping()',context);requests[4].resolve([{...context.items[0],destination:'away',valid:true}]);await loading;assert.equal(button(current,'Record purchase').disabled,true);const confirm=button(current,'Save destination').onclick();requests[5].resolve({...context.items[0],destination:'away'});await flush();requests[6].resolve([{...context.items[0],destination:'away',valid:true}]);await confirm;assert.equal(button(current,'Record purchase').disabled,false);
''')
