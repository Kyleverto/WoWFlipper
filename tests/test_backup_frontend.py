"""Backups preview before replacement and old tabs abandon stale retry intents."""
from test_destination_frontend import run_behavior

SETUP=r'''
context.fixture.refresh={running:false};context.fixture.sources=[];context.fixture.summary={};context.fixture.history=[];context.fixture.mode='live';context.fixture.realms=[];
let reloads=0;context.location={reload:()=>reloads++};
vm.runInContext('initializeBackupControls()',context);
'''


def test_backup_preview_then_explicit_restore_uses_same_document():
    run_behavior(SETUP+r'''
const document={format:'test-backup',events:[{id:1}]};
node('backup-file').files=[{size:100,text:async()=>JSON.stringify(document)}];
const preview=node('backup-preview').onclick();await flush();assert.equal(requests[0].path,'/api/backup/preview');assert.deepEqual(requests[0].payload.backup,document);assert.equal(node('backup-restore').disabled,true);
requests[0].resolve({created_at:'2026-10-15',mode:'live',counts:{events:1,selections:2,inventory:3,requests:4,overrides:5},cash:50000});await preview;
assert.ok(text(node('backup-summary')).includes('3 inventory'));assert.ok(text(node('backup-summary')).includes('5 overrides'));assert.equal(node('backup-restore').disabled,false);
const restore=node('backup-restore').onclick();assert.equal(requests[1].path,'/api/backup/restore');assert.deepEqual(requests[1].payload.backup,document);assert.equal(node('backup-restore').disabled,true);requests[1].resolve({restored:true,recovery_backup:'recovery.json'});await restore;assert.equal(reloads,1);
''')


def test_backup_rejects_large_file_and_blocks_restore_for_unsafe_state():
    run_behavior(SETUP+r'''
node('backup-file').files=[{size:16*1024*1024+1,text:async()=>{throw Error('must not read')}}];await node('backup-preview').onclick();assert.equal(requests.length,0);assert.ok(text(node('backup-summary')).includes('16 MiB'));
node('backup-file').files=[{size:2,text:async()=>'{bad'}];await node('backup-preview').onclick();assert.equal(requests.length,0);assert.equal(node('backup-restore').disabled,true);
node('backup-file').files=[{size:2,text:async()=>'{}'}];const preview=node('backup-preview').onclick();await flush();requests[0].resolve({counts:{}});await preview;
context.fixture.refresh.running=true;vm.runInContext('syncBackupControls()',context);assert.equal(node('backup-restore').disabled,true);await node('backup-restore').onclick();assert.equal(requests.length,1);
context.fixture.refresh.running=false;vm.runInContext('pending.set("outcome",{});syncBackupControls()',context);assert.equal(node('backup-restore').disabled,true);
vm.runInContext('pending.clear()',context);const writing=vm.runInContext('api("/api/settings",{})',context);assert.equal(node('backup-restore').disabled,true);requests[1].resolve({});await writing;assert.equal(node('backup-restore').disabled,false);
node('backup-file').onchange();assert.equal(node('backup-restore').disabled,true);
''')


def test_changed_data_epoch_clears_retries_and_does_not_apply_old_state():
    run_behavior(SETUP+r'''
context.fixture.data_epoch='old';vm.runInContext('knownDataEpoch="old";pending.set("inventory:1",{id:"stale"});renderCapital=renderSources=renderInventory=renderLedger=populateDestinations=renderShoppingGroups=()=>{}',context);
const loading=vm.runInContext('reloadState()',context);requests[0].resolve({...context.fixture,data_epoch:'new',csrf:'new-token'});await loading;
assert.equal(reloads,1);assert.equal(vm.runInContext('pending.size',context),0);assert.equal(vm.runInContext('state.data_epoch',context),'old');assert.ok(vm.runInContext('stateGeneration',context)>0);assert.ok(vm.runInContext('shoppingSequence',context)>0);assert.ok(vm.runInContext('resultsSequence',context)>0);
''')


def test_first_epoch_keeps_retries_and_remembers_epoch_across_reload():
    run_behavior(SETUP+r'''
const saved=new Map();context.sessionStorage.setItem=(key,value)=>saved.set(key,value);
vm.runInContext('knownDataEpoch=null;pending.set("inventory:1",{id:"keep"});renderCapital=renderSources=renderInventory=renderLedger=populateDestinations=renderShoppingGroups=()=>{}',context);
const loading=vm.runInContext('reloadState()',context);requests[0].resolve({...context.fixture,data_epoch:'first',csrf:'token'});await loading;
assert.equal(reloads,0);assert.equal(vm.runInContext('pending.size',context),1);assert.equal(saved.get('personal-data-epoch'),'first');
''')


def test_epoch_change_rejects_late_write_result_and_blocks_new_actions():
    run_behavior(SETUP+r'''
vm.runInContext('knownDataEpoch="old"',context);
const writing=vm.runInContext('api("/api/settings",{})',context).catch(error=>error);
context.nextEpoch={data_epoch:'new'};vm.runInContext('acceptDataEpoch(nextEpoch)',context);
requests[0].resolve({saved:true});const error=await writing;assert.ok(error.message.includes('Personal data changed'));assert.equal(reloads,1);
await assert.rejects(vm.runInContext('api("/api/ledger",{})',context),/Personal data changed/);assert.equal(requests.length,1);
''')


def test_restore_recovery_notice_survives_reload_and_is_consumed_once():
    run_behavior(SETUP+r'''
const stored=new Map();context.sessionStorage.setItem=(key,value)=>stored.set(key,value);context.sessionStorage.getItem=key=>stored.get(key)||null;
node('backup-file').files=[{size:2,text:async()=>'{}'}];const preview=node('backup-preview').onclick();await flush();requests[0].resolve({counts:{}});await preview;
const restoring=node('backup-restore').onclick();requests[1].resolve({restored:true,recovery_backup:'personal-recovery-test.json'});await restoring;
assert.ok(stored.get('restore-completion-notice').includes('personal-recovery-test.json'));
// A new page uses the same session storage and displays only safe text.
vm.runInContext('epochReloading=false;showRestoreNotice()',context);assert.ok(text(node('message')).includes('personal-recovery-test.json'));
node('message').textContent='later message';vm.runInContext('showRestoreNotice()',context);assert.equal(node('message').textContent,'later message');
''')
