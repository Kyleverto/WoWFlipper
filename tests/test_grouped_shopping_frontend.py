"""Buying-house grouping through actual shopping controllers and DOM."""
from test_destination_frontend import run_behavior


def test_groups_use_buying_house_siblings_and_exact_quoted_totals():
    run_behavior(r''' 
context.fixture.realms=[{slug:'home',name:'Selling Home',house_id:90},{slug:'alpha',name:'Alpha',house_id:17},{slug:'sibling',name:'Sibling',house_id:17},{slug:'beta',name:'Beta',house_id:18}];
const selection=(key,house,quantity,cost,destination)=>({key,candidate:{...row,auction:{...row.auction,house_id:house}},quantity,cost,destination,valid:true});
context.items=[selection(['us',18,1],undefined,3,20001,'home'),selection(['us',99,2],17,2,10002,'away'),selection(['us',17,3],17,4,30003,'home'),selection(['us',44,4],undefined,1,40004,'home')];
let loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
const groups=node('shopping-items').children;assert.equal(groups.length,3);
assert.ok(text(groups[0]).includes('Connected auction house 17'));assert.ok(text(groups[0]).includes('Alpha, Sibling'));assert.ok(!text(groups[0]).includes('Selling Home'));
assert.equal(controls(groups[0],'strong').length,2);assert.ok(text(groups[0]).includes('2 listings'));assert.ok(text(groups[0]).includes('6 units'));assert.ok(text(groups[0]).includes('4.0005g quoted'));
assert.ok(text(groups[1]).includes('Connected auction house 18'));assert.ok(text(groups[1]).includes('1 listing'));assert.ok(text(groups[1]).includes('3 units'));assert.ok(text(groups[1]).includes('2.0001g quoted'));
assert.ok(text(groups[2]).includes('Realm mapping unavailable'));assert.ok(text(groups[2]).includes('Connected auction house 44'));
// A destination belongs to a sale plan, so editing it must leave the buying group alone.
const first=shoppingNodes()[0],picker=controls(first,'select')[0];picker.value='home';picker.onchange();assert.equal(node('shopping-items').children[0],groups[0]);assert.equal(controls(groups[0],'strong').length,2);
''')


def test_group_and_row_identity_drafts_and_names_survive_state_and_list_refresh():
    run_behavior(r'''
context.fixture.realms=[{slug:'alpha',name:'Alpha',house_id:17}];context.fixture.refresh={running:false};
context.items=[{key:['us',17,2],candidate:{...row,auction:{...row.auction,house_id:17}},quantity:2,cost:12345,destination:'home',valid:true}];
let loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
const group=node('shopping-items').children[0],original=shoppingNodes()[0],controller=vm.runInContext('shoppingRows.get(JSON.stringify(["us",17,2]))',context),paid=controls(original,'input')[0],picker=controls(original,'select')[0];paid.value='9.8765';picker.value='away';picker.onchange();
context.fixture.realms=[{slug:'alpha',name:'Renamed Alpha',house_id:17},{slug:'sibling',name:'New Sibling',house_id:17}];vm.runInContext('renderCapital=renderSources=renderLedger=populateDestinations=()=>{};currentTab="shopping"',context);
loading=vm.runInContext('reloadState()',context);requests[1].resolve(context.fixture);await loading;
assert.equal(node('shopping-items').children[0],group);assert.ok(text(group).includes('Renamed Alpha, New Sibling'));assert.equal(shoppingNodes()[0],original);
loading=vm.runInContext('loadShopping()',context);requests[2].resolve([{...context.items[0],cost:23456,quantity:3,destination:'home'}]);await loading;
assert.equal(node('shopping-items').children[0],group);assert.equal(shoppingNodes()[0],original);assert.equal(vm.runInContext('shoppingRows.get(JSON.stringify(["us",17,2]))',context),controller);assert.equal(controls(original,'input')[0],paid);assert.equal(paid.value,'9.8765');assert.equal(picker.value,'away');assert.equal(button(original,'Record purchase').disabled,true);assert.ok(text(group).includes('3 units'));assert.ok(text(group).includes('2.3456g quoted'));
''')


def test_state_poll_and_unchanged_refresh_do_not_detach_groups_or_rows():
    run_behavior(r'''
context.fixture.refresh={running:true};context.fixture.realms=[{name:'Alpha',house_id:17}];
context.items=[{key:['us',17,2],candidate:row,quantity:1,cost:10000,destination:'home',valid:true}];
let loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
const container=node('shopping-items'),group=container.children[0],list=group.children[2],original=shoppingNodes()[0],paid=controls(original,'input')[0];paid.value='9.8765';
let detaches=0;for(const parent of [container,list])for(const method of ['replaceChildren','removeChild','insertBefore']){const originalMethod=parent[method];parent[method]=function(...args){detaches++;return originalMethod.apply(this,args)}}
context.fixture.realms=[{name:'Renamed Alpha',house_id:17}];vm.runInContext('renderCapital=renderSources=renderLedger=populateDestinations=()=>{};currentTab="shopping"',context);
loading=vm.runInContext('reloadState()',context);requests[1].resolve(context.fixture);await loading;
assert.equal(detaches,0,'State polling must not detach focused shopping controls');assert.ok(text(group).includes('Renamed Alpha'));
loading=vm.runInContext('loadShopping()',context);requests[2].resolve(context.items);await loading;
assert.equal(detaches,0,'An unchanged shopping refresh must not detach rows or sections');assert.equal(shoppingNodes()[0],original);assert.equal(paid.value,'9.8765');
''')


def test_membership_changes_preserve_unaffected_rows_and_remove_empty_groups():
    run_behavior(r'''
const selection=(house,id)=>({key:['us',house,id],candidate:row,quantity:1,cost:10000,destination:'home',valid:true});context.items=[selection(17,1),selection(17,2),selection(18,3)];
let loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
const container=node('shopping-items'),firstGroup=container.children[0],otherGroup=container.children[1],list=firstGroup.children[2],first=shoppingNodes()[0],second=shoppingNodes()[1];
let firstRemoved=0;const remove=list.removeChild;list.removeChild=function(n){if(n===first)firstRemoved++;return remove.call(this,n)};
loading=vm.runInContext('loadShopping()',context);requests[1].resolve([context.items[0],selection(17,4),context.items[1]]);await loading;
assert.equal(firstRemoved,0,'Adding a sibling listing must not detach an unaffected row');assert.equal(container.children.length,1);assert.equal(container.children[0],firstGroup);assert.equal(otherGroup.parentNode,null);assert.equal(list.children[0],first);assert.equal(list.children[2],second);
loading=vm.runInContext('loadShopping()',context);requests[2].resolve([]);await loading;assert.ok(text(container).includes('No selected listings'));assert.equal(vm.runInContext('shoppingGroups.size',context),0);
''')
