"""Refresh status drives the evidence panel without replacing user controls."""
from test_destination_frontend import run_behavior


def test_refresh_summary_distinguishes_work_pending_and_unavailable():
    run_behavior(r'''
context.fixture.sources=[];context.fixture.realms=[];
context.fixture.refresh={running:true,phase:'auctions',elapsed_seconds:137,completed:2,total:4,sources:{
'auctions:us:1':{state:'success'},'auctions:us:2':{state:'failed',error_code:'network_error'},
'auctions:us:3':{state:'running',phase:'fetching'},'auctions:us:4':{state:'queued'},
 'toys:us':{state:'pending',pending:50,unavailable:3,details_attempted:12,details_fetched:9,known_items:90}}};
vm.runInContext('renderSources()',context);
const summary=node('refresh-summary');assert.ok(text(summary).includes('Auction houses'));assert.ok(text(summary).includes('2m 17s'));assert.ok(text(summary).includes('2 of 4 houses finalized'));assert.equal(summary.attrs['aria-live'],'polite');
const evidence=text(node('sources'));assert.ok(evidence.includes('50 classification lookups pending'));assert.ok(evidence.includes('3 unavailable'));assert.ok(evidence.includes('12 detail lookups attempted'));assert.ok(evidence.includes('90 known items'));assert.ok(evidence.includes('queued'));assert.ok(evidence.includes('Check the connection'));
assert.ok(!evidence.includes('Metadata limit reached'));
assert.equal(controls(node('sources'),'details').length,1);assert.ok(text(controls(node('sources'),'summary')[0]).includes('1 completed auction house'));
''')


def test_status_poll_only_updates_sources_and_preserves_controls():
    run_behavior(r'''
context.fixture.sources=[];context.fixture.realms=[];context.fixture.refresh={running:true};
context.fixture.mode='live';let reloads=0;context.reloaded=()=>reloads++;
vm.runInContext('loadState=async()=>reloaded();',context);
const draft=node('inventory-search');draft.value='my typed draft';context.document.activeElement=draft;
vm.runInContext(source.slice(source.indexOf('let refreshPollRunning = false;')).replace('setInterval(async () => {','async function runRefreshPoll() {').replace('}, 2000);','}'),context);
const polling=vm.runInContext('runRefreshPoll()',context);assert.equal(requests[0].path,'/api/refresh/status');
requests[0].resolve({running:true,phase:'classification',elapsed_seconds:90,sources:{}});await polling;
assert.equal(reloads,0);assert.equal(draft.value,'my typed draft');assert.equal(context.document.activeElement,draft);assert.ok(text(node('refresh-summary')).includes('Classification'));
''')


def test_published_house_metadata_failure_stays_visible_with_safe_guidance():
    run_behavior(r'''
context.fixture.sources=[];context.fixture.realms=[];context.fixture.refresh={running:false,phase:'complete',sources:{'auctions:us:1':{state:'success',metadata_error_code:'auth_missing',metadata_deferred:20,metadata_failed:1,error:'secret-token'}}};
vm.runInContext('renderSources()',context);
assert.ok(text(node('sources')).includes('20 item detail lookups deferred'));assert.ok(text(node('sources')).includes('credentials are not configured'));assert.ok(!text(node('sources')).includes('secret-token'));assert.equal(controls(node('sources'),'details').length,0);
''')
