from pathlib import Path
import pytest
from wowflipper.web import create_app

@pytest.fixture
def client(tmp_path):
    app=create_app(tmp_path, testing=True)
    return app.test_client()

def token(client):
    return client.get('/api/state').json['csrf']

def post(client,path,data):
    return client.post(path,json=data,headers={'X-CSRF-Token':token(client)})

def test_external_origin_cannot_mutate(client):
    assert client.post('/api/settings',json={},headers={'Origin':'https://example.invalid'}).status_code==403

def test_host_and_csrf(client):
    assert client.get('/api/state',headers={'Host':'evil.invalid'}).status_code==403
    assert client.post('/api/settings',json={}).status_code==403
    assert client.post('/api/settings',json={},headers={'Origin':'http://localhost:9999','X-CSRF-Token':token(client)}).status_code==403

def test_settings_validation_and_secrets(client,monkeypatch):
    monkeypatch.setenv('BLIZZARD_CLIENT_SECRET','never-print-this')
    assert 'never-print-this' not in client.get('/api/state').text
    assert post(client,'/api/settings',{'capital':1}).status_code==400
    assert post(client,'/api/settings',{'unknown':2}).status_code==400
    client.application.extensions['repository'].cache_put('realms',[{'slug':'agamaggan','house_id':1,'category':'United States','is_auction_realm':True}])
    assert post(client,'/api/settings',{'selling_realms':['agamaggan']}).status_code==200

def test_demo_workflow_and_export(tmp_path):
    from wowflipper.demo import seed_demo
    seed_demo(tmp_path)
    client=create_app(tmp_path,testing=True).test_client()
    state=client.get('/api/state').json
    assert state['mode']=='offline-demo'
    rows=client.get('/api/results').json['rows']; assert rows
    row=next(row for row in rows if row['auction']['quantity']>1)
    selected=post(client,'/api/shopping',{'key':row['key'],'strategy':'stronger-demand','destination':'agamaggan','request_id':'select1'})
    assert selected.status_code==200
    purchase={'event':{'type':'purchase','key':row['key'],'cost':row['auction']['buyout']},'request_id':'buy1'}
    bought=post(client,'/api/ledger',purchase); assert bought.status_code==200
    assert post(client,'/api/ledger',purchase).json==bought.json
    pid=bought.json['id']
    sold=post(client,'/api/ledger',{'event':{'type':'sale','purchase_id':pid,'quantity':1,'net_proceeds':2000000},'request_id':'sale1'})
    assert sold.status_code==200
    assert post(client,'/api/ledger',{'event':{'type':'correction','target_id':sold.json['id'],'replacement':{'net_proceeds':1900000}},'request_id':'correct1'}).status_code==200
    assert post(client,'/api/settings',{'capital':400000000}).status_code==400
    assert create_app(tmp_path,testing=True).test_client().get('/api/state').json['summary']['inventory']
    assert client.get('/api/results?q=no-such-item').json['rows']==[]
    assert len(client.get('/api/export?q=no-such-item').text.splitlines())==1
    assert 'Offline' in client.get('/api/export').text

def test_formula_and_script_text(tmp_path):
    from wowflipper.demo import seed_demo
    from wowflipper.repository import Repository
    from dataclasses import replace
    seed_demo(tmp_path); repo=Repository(tmp_path); snap=repo.latest('tsm:us')
    repo.publish(replace(snap,records=tuple(replace(v,name='=HYPERLINK("evil")<script>') for v in snap.records)))
    client=create_app(tmp_path,testing=True).test_client()
    assert client.get('/api/results').json['rows'][0]['valuation']['name'].startswith('=')
    assert "'=HYPERLINK" in client.get('/api/export').text
    assert '<script>' not in client.get('/').text

def test_import_validation(client):
    assert post(client,'/api/import',{'kind':'auctions','text':'{}','house_id':1}).status_code==400
    assert post(client,'/api/import',{'kind':'bad','text':''}).status_code==400

def test_missing_import_time_excluded_and_current_candidate_recomputed(tmp_path):
    from wowflipper.demo import seed_demo
    from wowflipper.repository import Repository
    from dataclasses import replace
    seed_demo(tmp_path); repo=Repository(tmp_path)
    client=create_app(tmp_path,testing=True).test_client()
    row=client.get('/api/results').json['rows'][0]
    assert post(client,'/api/shopping',{'key':row['key'],'strategy':'stronger-demand','destination':'agamaggan','request_id':'x'}).status_code==200
    snap=repo.latest('tsm:us'); repo.publish(replace(snap,source_time=None,mode='offline-import'))
    assert client.get('/api/results').json['rows']==[]
    assert not client.get('/api/shopping').json[0]['valid']
    assert post(client,'/api/ledger',{'event':{'type':'purchase','key':row['key']},'request_id':'buy-stale'}).status_code==400

def test_html_and_limits(client):
    assert client.get('/').status_code==200
    assert 'Content-Security-Policy' in client.get('/').headers
    assert post(client,'/api/settings',{'discount':'2'}).status_code==400
    assert client.post('/api/settings',data='x'*17000000,headers={'X-CSRF-Token':token(client),'Content-Type':'application/json'}).status_code==413

def test_export_matches_query(tmp_path):
    import csv,io
    from wowflipper.demo import seed_demo
    seed_demo(tmp_path); client=create_app(tmp_path,testing=True).test_client()
    query='?q=Pattern&strategy=longer-term&sort=profit&direction=asc'
    rows=client.get('/api/results'+query).json['rows']
    exported=list(csv.DictReader(io.StringIO(client.get('/api/export'+query).text)))
    assert [r['valuation']['name'] for r in rows]==[r['Item'] for r in exported]
    assert all(r['Snapshot provenance'] and r['Auction source time'] and r['Assumptions and risks'] for r in exported)


def test_shopping_strategy_optional_and_legacy_queries_match_default(tmp_path):
 import csv,io
 from wowflipper.demo import seed_demo
 seed_demo(tmp_path); client=create_app(tmp_path,testing=True).test_client()
 rows=client.get('/api/results').json['rows']
 assert [int(r['profit']) for r in rows]==sorted((int(r['profit']) for r in rows),reverse=True)
 for strategy in ('stronger-demand','longer-term'):
  assert client.get('/api/results?strategy='+strategy).json['rows']==rows
 exported=list(csv.DictReader(io.StringIO(client.get('/api/export').text)))
 assert [r['valuation']['name'] for r in rows]==[r['Item'] for r in exported]
 selected=post(client,'/api/shopping',{'key':rows[0]['key'],'destination':'agamaggan','request_id':'general'})
 assert selected.status_code==200
 assert selected.json['strategy']=='general'


def test_inventory_update_ledger_guard_retry_and_persisted_state(tmp_path):
    from wowflipper.demo import seed_demo
    seed_demo(tmp_path)
    client = create_app(tmp_path, testing=True).test_client()
    row = client.get('/api/results').json['rows'][0]
    assert post(client, '/api/shopping', {
        'key': row['key'], 'destination': 'agamaggan', 'request_id': 'select-inventory',
    }).status_code == 200
    purchase = post(client, '/api/ledger', {
        'event': {'type': 'purchase', 'key': row['key']}, 'request_id': 'buy-inventory',
    }).json
    payload = {'event': {'type': 'inventory-update', 'purchase_id': purchase['id'],
                         'status': 'listed', 'destination': 'agamaggan'},
               'request_id': 'update-inventory'}
    assert client.post('/api/ledger', json=payload).status_code == 403
    assert len(client.get('/api/ledger').json) == 1
    updated = post(client, '/api/ledger', payload)
    assert updated.status_code == 200
    assert post(client, '/api/ledger', payload).json == updated.json
    changed = dict(payload, event=dict(payload['event'], status='ready'))
    assert post(client, '/api/ledger', changed).status_code == 400
    restored = create_app(tmp_path, testing=True).test_client().get('/api/state').json
    assert len(restored['history']) == 2
    assert restored['history'][0] == purchase
    lot = restored['summary']['inventory'][0]
    assert (lot['status'], lot['destination']) == ('listed', 'agamaggan')
    assert lot['recorded_at'] == purchase['recorded_at']
    assert (lot['key'], lot['cost'], lot['remaining_cost']) == (row['key'], purchase['cost'], purchase['cost'])
