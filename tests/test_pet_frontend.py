"""Pet variants retain their identity through browser views and override actions."""
from test_resale_frontend import run_behavior

PET = r'''
const pet={...row,identity:'pet:39:25:3:4',pet:{species_id:39,level:25,quality:3,breed_id:4},auction:{...row.auction,item_id:82800,category:'battle-pet'},valuation:{...row.valuation,name:'Mechanical Squirrel',pet_species_id:39}};
'''


def test_pet_variant_labels_survive_archived_shopping_and_inventory():
    run_behavior(PET+r'''
context.items=[pet];vm.runInContext('rows=items;renderResults()',context);
for(const n of [controls(node('results'),'tr')[1],controls(node('results'),'tr')[2]]){assert.ok(text(n).includes('Mechanical Squirrel'));assert.ok(text(n).includes('Level 25'));assert.ok(text(n).includes('Rare'));assert.ok(text(n).includes('Breed 4'))}
assert.ok(text(controls(node('results'),'tr')[2]).includes('species aggregate'));assert.ok(text(controls(node('results'),'tr')[2]).includes('No automatic level 25 premium'));
const archived={...pet};delete archived.pet;delete archived.identity;archived.auction={...pet.auction,variant_key:JSON.stringify({pet_species_id:39,pet_level:25,pet_quality_id:3,pet_breed_id:4})};
context.items=[{key:['us',1,2],candidate:archived,cost:10000,quantity:1,destination:'home',valid:true}];const loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;
assert.ok(text(shoppingNodes()[0]).includes('Level 25'));assert.ok(text(shoppingNodes()[0]).includes('Breed 4'));
context.fixture.summary={inventory:[{id:11,key:['us',1,2],candidate:archived,cost:10000,remaining_cost:10000,remaining_quantity:1,destination:'home',status:'held'}]};vm.runInContext('renderInventory()',context);assert.ok(text(node('inventory')).includes('Level 25'));assert.ok(text(node('inventory')).includes('Rare'));
''')


def test_pet_override_drafts_and_requests_are_separate_from_cage_and_other_variant():
    run_behavior(PET+r'''
context.items=[pet,{...pet,identity:'pet:39:1:2:5',pet:{species_id:39,level:1,quality:2,breed_id:5},key:['us',1,3]},{...row,auction:{...row.auction,item_id:82800},key:['us',1,4]}];vm.runInContext('rows=items;renderResults();loadShopping=async()=>{};loadResults=async()=>{}',context);
let details=controls(node('results'),'tr').filter(n=>n.className==='details');let input=controls(details[0],'input')[0];input.value='7.1234';input.oninput();vm.runInContext('renderResults()',context);details=controls(node('results'),'tr').filter(n=>n.className==='details');assert.equal(controls(details[0],'input')[0].value,'7.1234');assert.ok(text(details[1]).includes('Level 1'));assert.ok(text(details[1]).includes('Uncommon'));assert.ok(text(details[1]).includes('Breed 5'));assert.equal(controls(details[1],'input')[0].value,'3');assert.equal(controls(details[2],'input')[0].value,'3');
const saving=button(details[0],'Save override').onclick();assert.deepEqual(requests[0].payload,{identity:'pet:39:25:3:4',unit_price:71234});requests[0].resolve({});await saving;
context.fixture.resale_overrides=[{identity:'pet:39:25:3:4',pet:pet.pet,unit_price:71234,updated_at:'2026-10-14T00:00:00Z'}];vm.runInContext('renderResaleOverrides()',context);assert.ok(text(node('resale-overrides')).includes('Level 25'));button(node('resale-overrides'),'Edit').onclick();assert.equal(node('override-item-id').value,'pet:39:25:3:4');node('override-unit-price').value='8.7654';const managerSave=node('resale-override-form').onsubmit({preventDefault(){}});assert.deepEqual(requests[1].payload,{identity:'pet:39:25:3:4',unit_price:87654});requests[1].resolve({});await managerSave;const clear=button(node('resale-overrides'),'Clear override').onclick();assert.deepEqual(requests[2].payload,{identity:'pet:39:25:3:4'});requests[2].resolve({});await clear;
''')


def test_category_query_is_retained_for_accepted_csv_and_resets_page():
    run_behavior(r'''
vm.runInContext(source.slice(source.indexOf('for (const id of ["sort",'),source.indexOf('let searchTimer;')),context);
const view=id=>controls(node('opportunity-categories'),'input').find(n=>n.id==='view-category-'+id);const checks=controls(node('opportunity-categories'),'input');assert.equal(checks.length,5);assert.ok(checks.every(n=>n.checked));
view('battle-pet').checked=false;vm.runInContext('resultsPage=4',context);view('battle-pet').onchange();
assert.equal(new URLSearchParams(requests[0].path.split('?')[1]).get('categories'),'recipe,mount,toy,decor');assert.equal(new URLSearchParams(requests[0].path.split('?')[1]).get('page'),'1');
assert.equal(node('export').attrs['aria-disabled'],'true');requests[0].resolve({rows:[row],page:1,page_size:100,total:1,pages:1,exclusion_total:0});await flush();assert.equal(new URLSearchParams(node('export').href.split('?')[1]).get('categories'),'recipe,mount,toy,decor');
for(const check of checks)check.checked=false;checks[0].onchange();assert.equal(new URLSearchParams(requests[1].path.split('?')[1]).get('categories'),'');requests[1].resolve({rows:[],page:1,page_size:100,total:0,pages:1,exclusion_total:0});await flush();assert.ok(text(node('results')).includes('Select at least one category'));
''')


def test_category_selection_storage_poll_and_drafts_are_independent():
    run_behavior(r'''
const stored=new Map([['opportunity-categories',JSON.stringify(['recipe','toy'])]]);context.sessionStorage={getItem:key=>stored.get(key)??null,setItem:(key,value)=>stored.set(key,value)};
vm.runInContext(source.slice(source.indexOf('for (const id of ["sort",'),source.indexOf('let searchTimer;')),context);
const view=id=>controls(node('opportunity-categories'),'input').find(n=>n.id==='view-category-'+id);assert.equal(view('recipe').checked,true);assert.equal(view('toy').checked,true);assert.equal(view('battle-pet').checked,false);
context.items=[row];vm.runInContext('rows=items;renderResults()',context);const input=controls(controls(node('results'),'tr')[2],'input')[0];input.value='8.7654';input.oninput();node('setting-capital').value='777';node('category-battle-pet').checked=true;
view('mount').checked=true;view('mount').onchange();assert.deepEqual(JSON.parse(stored.get('opportunity-categories')),['recipe','mount','toy']);requests[0].resolve({rows:[row],page:1,page_size:100,total:1,pages:1,exclusion_total:0});await flush();assert.equal(controls(controls(node('results'),'tr')[2],'input')[0].value,'8.7654');assert.equal(node('setting-capital').value,'777');assert.equal(node('category-battle-pet').checked,true);
vm.runInContext('renderCapital=()=>{};renderSources=()=>{};renderInventory=()=>{};renderLedger=()=>{};populateDestinations=()=>{};renderShoppingGroups=()=>{}',context);const polling=vm.runInContext('reloadState()',context);requests[1].resolve({...context.fixture,csrf:'token',mode:'local',refresh:{running:false},settings:{...context.fixture.settings,categories:['recipe','mount','toy','decor','battle-pet']}});await polling;assert.equal(view('battle-pet').checked,false);assert.equal(view('mount').checked,true);
vm.runInContext('initializeOpportunityCategories()',context);assert.equal(view('mount').checked,true);assert.equal(view('battle-pet').checked,false);
stored.set('opportunity-categories','[]');vm.runInContext('initializeOpportunityCategories()',context);assert.ok(controls(node('opportunity-categories'),'input').every(n=>!n.checked));assert.equal(vm.runInContext('params().get("categories")',context),'');
stored.set('opportunity-categories','not-json');vm.runInContext('initializeOpportunityCategories()',context);assert.ok(controls(node('opportunity-categories'),'input').every(n=>n.checked));
''')


def test_pet_override_retires_stale_result_and_shopping_reads():
    run_behavior(PET+r'''
context.items=[{key:['us',1,2],candidate:pet,cost:10000,quantity:1,destination:'home',valid:true}];let loading=vm.runInContext('loadShopping()',context);requests[0].resolve(context.items);await loading;const current=shoppingNodes()[0],paid=controls(current,'input')[0];paid.value='8.7654';
const before=vm.runInContext('loadShopping()',context),oldResults=vm.runInContext('loadResults()',context),saving=vm.runInContext('saveResaleOverride("pet:39:25:3:4","7")',context);assert.equal(button(current,'Record purchase').disabled,true);requests[3].reject(Error('lost response'));await assert.rejects(saving);requests[1].resolve(context.items);await before;requests[2].resolve({rows:[pet],page:1,page_size:100,total:1,pages:1,exclusion_total:0});await oldResults;assert.equal(vm.runInContext('rows.length',context),0);assert.equal(button(current,'Record purchase').disabled,true);assert.equal(paid.value,'8.7654');
''')


def test_settings_keeps_new_categories_disabled_for_existing_profile():
    run_behavior(r'''
Object.assign(context.fixture.settings,{categories:['toy'],capital:300000000,reserve:50000000,item_exposure:30000000,min_profit:1000000,extra_costs:0});context.fixture.history=[];context.fixture.realms=[];
vm.runInContext('renderRealmSelection=()=>{};renderSettings()',context);const categories=controls(node('filter-settings'),'input').filter(n=>n.id?.startsWith('category-'));assert.equal(categories.find(n=>n.id==='category-decor').checked,false);assert.equal(categories.find(n=>n.id==='category-battle-pet').checked,false);assert.equal(categories.find(n=>n.id==='category-toy').checked,true);
''')


def test_source_pending_classification_diagnostics_explain_retained_evidence():
    run_behavior(r'''
context.fixture.sources=[];context.fixture.refresh={running:false,sources:{'decor:us':{state:'success',pending:4},'tsm-pets:us':{state:'failed',error_code:'network_error'}}};vm.runInContext('renderSources()',context);assert.ok(text(node('sources')).includes('4 classification lookups pending'));assert.ok(text(node('sources')).includes('Battle pet regional valuations'));assert.ok(text(node('sources')).includes('could not be reached'));
''')


def test_archived_metadata_pet_and_legacy_cage_editor_do_not_share_drafts():
    run_behavior(PET+r'''
const archived={...pet};delete archived.pet;delete archived.identity;archived.auction={...pet.auction,metadata:{pet:pet.pet}};
context.items=[archived,{...row,key:['us',1,3],auction:{...row.auction,item_id:82800},resale_override:{item_id:82800,unit_price:90000},assumed_unit_resale:90000}];vm.runInContext('rows=items;renderResults()',context);let details=controls(node('results'),'tr').filter(n=>n.className==='details');assert.ok(text(details[0]).includes('Level 25'));assert.equal(controls(details[0],'input')[0].value,'3');assert.equal(controls(details[1],'input')[0].value,'9');const cage=controls(details[1],'input')[0];cage.value='12';cage.oninput();vm.runInContext('renderResults()',context);details=controls(node('results'),'tr').filter(n=>n.className==='details');assert.equal(controls(details[0],'input')[0].value,'3');assert.equal(controls(details[1],'input')[0].value,'12');
''')
