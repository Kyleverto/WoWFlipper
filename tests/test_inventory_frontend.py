"""Inventory controls exercise real handlers with the shared DOM fixture."""
from test_destination_frontend import run_behavior

SETUP = r'''
context.fixture.summary={inventory:[{id:11,key:['us',17,2],candidate:row,cost:50000,remaining_cost:30000,remaining_quantity:3,destination:'home',status:'held',recorded_at:'2026-10-01T12:00:00Z'}]};
context.fixture.realms=[{slug:'buy',name:'Source Realm',house_id:17}];
vm.runInContext('renderInventory()',context);
const lotRow=()=>controls(node('inventory'),'tr')[1];
'''

def test_inventory_search_filters_and_drafts_survive_poll():
    run_behavior(SETUP+r'''
const original=lotRow(),inputs=controls(original,'input'),selects=controls(original,'select');
assert.ok(text(original).includes('#11'));assert.ok(text(original).includes('Source Realm'));assert.ok(text(original).includes('3'));assert.ok(text(original).includes('5.00g'));
inputs[1].value='12.3456';selects[0].value='listed';selects[0].onchange();
vm.runInContext('renderInventory()',context);assert.equal(lotRow(),original);assert.equal(inputs[1].value,'12.3456');assert.equal(selects[0].value,'listed');
node('inventory-search').value='missing';vm.runInContext('renderInventory()',context);assert.equal(original.hidden,true);
node('inventory-search').value='7';node('inventory-status').value='held';vm.runInContext('renderInventory()',context);assert.equal(original.hidden,false);
node('inventory-destination').value='away';vm.runInContext('renderInventory()',context);assert.equal(original.hidden,true);
''')

def test_inventory_update_and_unknown_retry_use_original_payload():
    run_behavior(SETUP+r'''
const current=lotRow(),selects=controls(current,'select');selects[0].value='listed';selects[0].onchange();
const saving=button(current,'Save status').onclick();assert.equal(requests[0].payload.event.type,'inventory-update');assert.equal(requests[0].payload.event.purchase_id,11);assert.equal(requests[0].payload.event.status,'listed');
button(current,'Save status').onclick();assert.equal(requests.length,1);requests[0].reject(Error('offline'));await saving;
assert.equal(selects[0].disabled,true);assert.equal(selects[1].disabled,true);assert.equal(button(current,'Save destination').disabled,true);
selects[0].value='held';const retry=button(current,'Save status').onclick();assert.equal(requests[1].payload.event.status,'listed');assert.equal(requests[1].payload.request_id,requests[0].payload.request_id);requests[1].resolve({});await retry;
assert.equal(selects[0].disabled,false);
''')

def test_inventory_partial_sale_requires_net_amount_and_full_sale_disappears():
    run_behavior(SETUP+r'''
const current=lotRow(),form=controls(current,'form')[0],inputs=controls(form,'input'),record=button(form,'Record outcome');
assert.equal(inputs[0].value,'3');await submit(form,record);assert.equal(requests.length,0);
inputs[0].value='2';inputs[1].value='9.8765';const selling=submit(form,record);assert.equal(requests[0].payload.event.purchase_id,11);assert.equal(requests[0].payload.event.quantity,2);assert.equal(requests[0].payload.event.net_proceeds,98765);
requests[0].resolve({});await selling;context.fixture.summary.inventory[0].remaining_quantity=1;vm.runInContext('renderInventory()',context);assert.equal(inputs[0].value,'1');assert.equal(inputs[1].value,'');
inputs[1].value='4';const full=submit(form,record);requests[1].resolve({});await full;context.fixture.summary.inventory=[];vm.runInContext('renderInventory()',context);assert.equal(controls(node('inventory'),'tr').length,1);assert.ok(text(node('inventory-empty')).includes('No held inventory'));
''')


def test_inventory_stale_state_read_cannot_restore_confirmed_update():
    run_behavior(SETUP+r'''
context.fixture.csrf='token';context.fixture.mode='offline-demo';context.fixture.refresh={running:false};
vm.runInContext('renderCapital=renderSources=renderLedger=populateDestinations=renderShoppingGroups=()=>{};loadState=reloadState',context);
const oldRead=vm.runInContext('reloadState()',context),current=lotRow(),picker=controls(current,'select')[0];picker.value='ready';picker.onchange();
const saving=button(current,'Save status').onclick();requests[1].resolve({});await flush();
requests[2].resolve({...context.fixture,summary:{inventory:[{...context.fixture.summary.inventory[0],status:'ready'}]}});await saving;
requests[0].resolve({...context.fixture,summary:{inventory:[{...context.fixture.summary.inventory[0],status:'held'}]}});await oldRead;
assert.equal(vm.runInContext('inventoryRows.get(11).lot.status',context),'ready');assert.equal(picker.value,'ready');
''')


def test_inventory_unconfirmed_sale_remains_retryable_when_lot_absent():
    run_behavior(SETUP+r'''
const current=lotRow(),form=controls(current,'form')[0],inputs=controls(form,'input'),record=button(form,'Record outcome');inputs[1].value='3.4567';
const selling=submit(form,record);requests[0].reject(Error('offline'));await selling;
context.fixture.summary.inventory=[];vm.runInContext('renderInventory()',context);assert.equal(lotRow(),current);assert.equal(record.disabled,false);assert.equal(inputs[1].disabled,true);
inputs[1].value='900';const retry=submit(form,record);assert.equal(requests[1].payload.event.net_proceeds,34567);assert.equal(requests[1].payload.request_id,requests[0].payload.request_id);requests[1].resolve({});await retry;
assert.equal(controls(node('inventory'),'tr').length,1);
''')


def test_inventory_reload_restores_pending_values_and_destination_retry():
    run_behavior(SETUP+r'''
vm.runInContext('pending.set("inventory:11",{id:"saved-id",fingerprint:JSON.stringify(["/api/ledger",{event:{type:"inventory-update",purchase_id:11,destination:"away"}}])});renderInventory()',context);
const current=lotRow(),picker=controls(current,'select')[1];assert.equal(picker.value,'away');assert.equal(picker.disabled,true);assert.equal(button(current,'Save status').disabled,true);
const saving=button(current,'Save destination').onclick();assert.equal(requests[0].payload.request_id,'saved-id');assert.equal(requests[0].payload.event.destination,'away');requests[0].resolve({});await saving;
assert.equal(picker.disabled,false);
''')


def test_inventory_reload_recovers_full_sale_retry_without_held_lot():
    run_behavior(r'''
context.fixture.summary={inventory:[]};
vm.runInContext('pending.set("inventory:11",{id:"sale-id",fingerprint:JSON.stringify(["/api/ledger",{event:{type:"sale",purchase_id:11,quantity:3,net_proceeds:90000}}])});renderInventory()',context);
const retryButton=button(node('inventory'),'Retry saved action');assert.ok(retryButton,'Reload needs a retry even when the sold lot is absent');
const retry=retryButton.onclick();assert.equal(requests[0].payload.request_id,'sale-id');assert.equal(requests[0].payload.event.quantity,3);assert.equal(requests[0].payload.event.net_proceeds,90000);
retryButton.onclick();assert.equal(requests.length,1);requests[0].resolve({});await retry;assert.equal(controls(node('inventory'),'tr').length,1);
''')


def test_inventory_update_correction_preserves_untouched_legacy_destination():
    run_behavior(r'''
Object.getPrototypeOf(node('message')).getAttribute=function(k){return this.attrs[k]};
context.fixture.history=[{id:11,type:'purchase',candidate:row},{id:21,type:'inventory-update',purchase_id:11,status:'listed',destination:'removed'}];
node('correction-target').value='21';node('correction-action').value='replace';
vm.runInContext(source.slice(source.indexOf('function renderCorrection('),source.indexOf('$("settings-form").onsubmit')),context);
vm.runInContext('renderCorrection()',context);
const fields=controls(node('correction-fields'),'select'),status=fields.find(n=>n.id==='correct-status'),destination=fields.find(n=>n.id==='correct-destination');assert.equal(destination.value,'removed');status.value='ready';
node('correction-form').onsubmit({preventDefault(){},submitter:new Node('button')});await flush();
assert.equal(requests[0].payload.event.type,'correction');assert.equal(requests[0].payload.event.replacement.status,'ready');assert.equal(requests[0].payload.event.replacement.purchase_id,11);assert.ok(!Object.hasOwn(requests[0].payload.event.replacement,'destination'));requests[0].resolve({});await flush();
''')


def test_inventory_confirmed_status_survives_failed_state_refresh():
    run_behavior(SETUP+r'''
vm.runInContext('loadState=async()=>{throw Error("refresh unavailable")}',context);
const current=lotRow(),status=controls(current,'select')[0];status.value='listed';status.onchange();const saving=button(current,'Save status').onclick();requests[0].resolve({});await saving;
assert.equal(status.value,'listed');assert.equal(vm.runInContext('state.summary.inventory[0].status',context),'listed');assert.equal(vm.runInContext('pending.has("inventory:11")',context),false);
''')


def test_inventory_confirmed_sale_blocks_stale_units_after_failed_refresh():
    run_behavior(SETUP+r'''
vm.runInContext('loadState=async()=>{throw Error("refresh unavailable")}',context);
const current=lotRow(),form=controls(current,'form')[0],amount=controls(form,'input')[1],record=button(form,'Record outcome');amount.value='10';const selling=submit(form,record);requests[0].resolve({});await selling;
assert.equal(record.disabled,true);assert.equal(button(current,'Save status').disabled,true);assert.ok(text(current).includes('Reload'));await submit(form,record);assert.equal(requests.length,1);assert.equal(vm.runInContext('pending.has("inventory:11")',context),false);
''')


def test_inventory_pending_startup_uses_inventory_retry_only():
    run_behavior(r'''
context.fixture.summary={inventory:[]};vm.runInContext('loadResults=loadShopping=outcomeFields=async()=>{};pending.set("inventory:11",{id:"sale-id",fingerprint:JSON.stringify(["/api/ledger",{event:{type:"sale",purchase_id:11,quantity:1,net_proceeds:10000}}])});pending.set("outcome",{id:"fee-id",fingerprint:JSON.stringify(["/api/ledger",{event:{type:"fee",amount:10000}}])})',context);
const startupStart=source.indexOf('(async () => {',source.indexOf('$("correction-fields").onchange'));vm.runInContext(source.slice(startupStart,source.indexOf('let refreshPollRunning',startupStart)),context);await flush();
assert.ok(text(node('message')).includes('Inventory'));assert.deepEqual(controls(node('message'),'button').map(n=>n.textContent),['Retry unconfirmed fee']);
''')
