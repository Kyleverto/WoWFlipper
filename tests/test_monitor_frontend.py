"""Automatic updates preserve the accepted view and alert only after a quiet baseline."""
from test_destination_frontend import run_behavior

SETUP=r'''
const shared=new Map();context.localStorage={getItem:key=>shared.get(key)||null,setItem:(key,value)=>shared.set(key,String(value))};
context.fixture.refresh={running:false,sources:{}};context.fixture.sources=[];context.fixture.realms=[];context.fixture.data_epoch='dataset';context.fixture.mode='live';
vm.runInContext('knownDataEpoch="dataset";monitorAcceptedQuery="q=mount&categories=mount&sort=profit&direction=desc&page=1&page_size=100";initializeMonitorControls()',context);
const monitor={enabled:true,sound:false,desktop:false,interval_seconds:120,data_epoch:'dataset',refresh:{running:false,sources:{}}};
const respondPoll=async(data)=>{const start=requests.length;const work=vm.runInContext('pollMonitor()',context);requests[start].resolve(monitor);await flush();requests[start+1].resolve({data_epoch:'dataset',preview:[],...data});await work;};
'''


def test_monitor_baseline_and_background_updates_preserve_rows_focus_and_search_drafts():
    run_behavior(SETUP+r'''
const original=new Node('article','kept rows');node('results').append(original);const draft=node('search');draft.value='unsubmitted draft';context.document.activeElement=draft;let reloads=0;context.reloaded=()=>reloads++;vm.runInContext('loadState=async()=>reloaded();loadResults=async()=>reloaded()',context);
await respondPoll({baseline:true,cursor:10,new_count:0,notify_count:0,updated:false});assert.equal(node('updates-banner').hidden,true);
await respondPoll({baseline:false,cursor:12,new_count:2,notify_count:2,updated:true});assert.ok(text(node('updates-count')).includes('2 new'));assert.equal(node('updates-banner').hidden,false);assert.equal(node('results').children[0],original);assert.equal(context.document.activeElement,draft);assert.equal(draft.value,'unsubmitted draft');assert.equal(reloads,0);
const params=new URLSearchParams(requests[3].path.split('?')[1]);assert.equal(params.get('q'),'mount');assert.equal(params.get('categories'),'mount');assert.equal(params.get('after'),'10');
await respondPoll({deferred:true,baseline:false,cursor:12,new_count:0,updated:false});assert.equal(node('updates-banner').hidden,false);assert.ok(text(node('updates-count')).includes('2 new'));
''')


def test_show_new_and_all_use_accepted_query_keep_export_filter_and_ack_after_success():
    run_behavior(SETUP+r'''
Node.prototype.removeAttribute=function(key){delete this.attrs[key]};context.AbortController=AbortController;
vm.runInContext('renderResults=pageControls=()=>{};loadResults=async(force,accepted)=>performResults(accepted,resultsSequence)',context);
await respondPoll({baseline:true,cursor:10,new_count:0});await respondPoll({baseline:false,cursor:13,new_count:3,updated:true});node('search').value='draft must not become query';
const showing=node('show-new').onclick();await flush();let request=requests[4];let params=new URLSearchParams(request.path.split('?')[1]);assert.equal(params.get('new_after'),'10');assert.equal(params.get('q'),'mount');assert.equal(vm.runInContext('monitorViewCursor',context),10);
request.resolve({rows:[],total:0,page:1,page_size:100,exclusion_total:0});await showing;assert.equal(vm.runInContext('monitorViewCursor',context),13);assert.equal(new URLSearchParams(node('export').href.split('?')[1]).get('new_after'),'10');
const all=node('all-opportunities').onclick();await flush();request=requests[5];assert.equal(new URLSearchParams(request.path.split('?')[1]).has('new_after'),false);request.resolve({rows:[],total:0,page:1,page_size:100,exclusion_total:0});await all;
''')


def test_notification_cursor_is_claimed_once_across_tabs_and_quiet_baseline():
    run_behavior(SETUP+r'''
let alerts=0;context.Notification=function(){alerts++};context.Notification.permission='granted';monitor.desktop=true;
await respondPoll({baseline:true,cursor:10,new_count:0,notify_count:50});assert.equal(alerts,0);
await respondPoll({baseline:false,cursor:11,new_count:1,notify_count:1,updated:true,preview:[{name:'New mount',key:['us',1,2]}]});assert.equal(alerts,1);
await respondPoll({baseline:false,cursor:11,new_count:1,notify_count:1,updated:true});assert.equal(alerts,1);
// Simulate another tab claiming a later cursor before this response arrives.
shared.set('notified-opportunities:dataset','13');await respondPoll({baseline:false,cursor:13,new_count:2,notify_count:2,updated:true});assert.equal(alerts,1);
''')


def test_controls_permission_denial_and_unavailable_sound_are_actionable():
    run_behavior(SETUP+r'''
context.Notification={permission:'default',requestPermission:async()=> 'denied'};node('monitor-desktop').checked=true;
const changing=node('monitor-desktop').onchange();await flush();assert.equal(requests.length,1);assert.equal(requests[0].path,'/api/monitor');assert.equal(requests[0].payload.desktop,false);requests[0].resolve({...monitor,desktop:false});await changing;assert.ok(text(node('monitor-alert-status')).includes('denied'));assert.equal(node('monitor-desktop').checked,false);
await node('test-alert').onclick();assert.ok(text(node('monitor-alert-status')).includes('Sound is unavailable'));
''')


def test_zero_new_update_still_offers_user_requested_list_refresh():
    run_behavior(SETUP+r'''
await respondPoll({baseline:true,cursor:1,new_count:0});await respondPoll({baseline:false,cursor:2,new_count:0,updated:true});assert.equal(node('updates-banner').hidden,false);assert.equal(node('show-new').hidden,true);assert.equal(node('update-list').hidden,false);assert.ok(text(node('updates-count')).includes('Market data updated'));
''')


def test_sound_requires_gesture_then_plays_once_for_new_matching_results():
    run_behavior(SETUP+r'''
let starts=0,resumes=0;context.AudioContext=function(){this.state='suspended';this.currentTime=0;this.destination={};this.resume=async()=>{resumes++;this.state='running'};this.createOscillator=()=>({frequency:{setValueAtTime(){}},connect(){},start(){starts++},stop(){}});this.createGain=()=>({gain:{setValueAtTime(){},exponentialRampToValueAtTime(){}},connect(){}})};
monitor.sound=true;await respondPoll({baseline:true,cursor:1,notify_count:100});assert.equal(starts,0);assert.equal(resumes,0);
await node('test-alert').onclick();assert.equal(starts,1);assert.equal(resumes,1);
await respondPoll({baseline:false,cursor:2,notify_count:1,new_count:1,updated:true});assert.equal(starts,2);
await respondPoll({baseline:false,cursor:2,notify_count:1,new_count:1,updated:true});assert.equal(starts,2);
''')


def test_old_feed_response_cannot_restore_banner_after_user_acknowledges_update():
    run_behavior(SETUP+r'''
await respondPoll({baseline:true,cursor:10});await respondPoll({baseline:false,cursor:12,new_count:2,updated:true});
const polling=vm.runInContext('pollMonitor()',context);requests[4].resolve(monitor);await flush();
vm.runInContext('loadResults=async(force,accepted)=>{monitorAcceptedQuery=accepted}',context);await node('update-list').onclick();assert.equal(node('updates-banner').hidden,true);
requests[5].resolve({cursor:12,data_epoch:'dataset',new_count:2,notify_count:0,updated:true});await polling;assert.equal(node('updates-banner').hidden,true);
''')


def test_monitor_response_started_before_toggle_cannot_revert_saved_preferences():
    run_behavior(SETUP+r'''
const polling=vm.runInContext('pollMonitor()',context);node('monitor-enabled').checked=true;
const configuring=node('monitor-enabled').onchange();requests[1].resolve({...monitor,enabled:true});await configuring;
requests[0].resolve({...monitor,enabled:false});await flush();requests[2].resolve({data_epoch:'dataset',baseline:true,cursor:1,new_count:0});await polling;
assert.equal(node('monitor-enabled').checked,true);
''')


def test_update_list_preserves_new_only_slice_until_all_is_explicitly_selected():
    run_behavior(SETUP+r'''
vm.runInContext('newOnlyAfter=5;monitorAcceptedQuery+="&new_after=5";monitorViewCursor=10;monitorCurrentCursor=12;loadResults=async(force,accepted)=>{monitorAcceptedQuery=accepted}',context);
await node('update-list').onclick();assert.equal(new URLSearchParams(vm.runInContext('monitorAcceptedQuery',context)).get('new_after'),'5');
await node('all-opportunities').onclick();assert.equal(new URLSearchParams(vm.runInContext('monitorAcceptedQuery',context)).has('new_after'),false);
''')


def test_steady_poll_in_new_only_view_keeps_all_opportunities_reachable():
    run_behavior(SETUP+r'''
await respondPoll({baseline:true,cursor:10});await respondPoll({baseline:false,cursor:12,new_count:2,updated:true});
vm.runInContext('loadResults=async(force,accepted)=>{monitorAcceptedQuery=accepted}',context);await node('show-new').onclick();
await respondPoll({baseline:false,cursor:12,new_count:0,notify_count:0,updated:false});
assert.equal(node('updates-banner').hidden,false);assert.equal(node('all-opportunities').hidden,false);assert.ok(text(node('updates-count')).includes('Showing new opportunities'));
await node('all-opportunities').onclick();assert.equal(node('updates-banner').hidden,true);
''')


def test_unmatched_or_silent_tab_does_not_consume_shared_notification_cursor():
    run_behavior(SETUP+r'''
await respondPoll({baseline:true,cursor:10});assert.equal(shared.has('notified-opportunities:dataset'),false);
monitor.desktop=true;context.Notification=function(){};context.Notification.permission='granted';
await respondPoll({baseline:false,cursor:11,new_count:0,notify_count:0,updated:true});assert.equal(shared.has('notified-opportunities:dataset'),false);
monitor.desktop=false;await respondPoll({baseline:false,cursor:12,new_count:1,notify_count:1,updated:true});assert.equal(shared.has('notified-opportunities:dataset'),false);
assert.equal(vm.runInContext('monitorStoredCursor()',context),12);
// Another tab with a matching view and enabled desktop alerts may still claim it.
vm.runInContext('monitorMemoryNotified=10',context);monitor.desktop=true;let alerts=0;context.Notification=function(){alerts++};context.Notification.permission='granted';
await respondPoll({baseline:false,cursor:12,new_count:1,notify_count:1,updated:true});assert.equal(alerts,1);assert.equal(shared.get('notified-opportunities:dataset'),'12');
''')


def test_unchanged_sources_are_finalized_and_compact_after_conditional_checks():
    run_behavior(SETUP+r'''
context.fixture.refresh={running:false,phase:'complete',sources:{'auctions:us:1':{state:'unchanged'},'auctions:us:2':{state:'success'}}};vm.runInContext('renderSources()',context);
assert.ok(text(node('refresh-summary')).includes('2 of 2 houses finalized'));assert.equal(controls(node('sources'),'details').length,1);assert.ok(text(controls(node('sources'),'summary')[0]).includes('2 completed auction houses'));
''')
