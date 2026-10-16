import csv,io
from dataclasses import replace
from wowflipper.web import create_app
from wowflipper.demo import seed_demo


def post(client,path,data):
    return client.post(path,json=data,headers={'X-CSRF-Token':client.get('/api/state').json['csrf']})


def test_monitor_configuration_has_local_security_and_does_not_start_thread(tmp_path):
    app=create_app(tmp_path,testing=True); client=app.test_client()
    response=client.get('/api/monitor')
    assert response.status_code==200
    assert response.json['enabled'] is False and response.json['interval_seconds']==120
    assert client.post('/api/monitor',json={'enabled':True}).status_code==403
    assert post(client,'/api/monitor',{'enabled':True,'sound':True,'desktop':False}).status_code==200
    state=client.get('/api/state').json
    assert state['monitor']['enabled'] and state['settings']['auto_refresh']
    assert state['monitor']['sound']
    assert post(client,'/api/monitor',{'enabled':'yes'}).status_code==400
    assert post(client,'/api/monitor',{'interval_seconds':1}).status_code==400
    app.extensions['monitor'].stop()


def test_new_opportunity_api_and_export_share_new_only_view(tmp_path):
    seed_demo(tmp_path); app=create_app(tmp_path,testing=True); client=app.test_client()
    baseline=client.get('/api/updates').json
    assert baseline['baseline'] and baseline['new_count']==0
    repo=app.extensions['repository']; snapshot=repo.latest('auctions:us:1')
    item=next(a for a in snapshot.records if a.category=='toy')
    repo.publish(replace(snapshot,records=snapshot.records+(replace(item,auction_id=999),)))
    update=client.get('/api/updates',query_string={'after':baseline['cursor'],'categories':'toy'}).json
    assert update['new_count']==update['notify_count']==1
    query=f"?new_after={baseline['cursor']}&categories=toy"
    rows=client.get('/api/results'+query).json
    exported=list(csv.DictReader(io.StringIO(client.get('/api/export'+query).text)))
    assert rows['total']==1 and rows['rows'][0]['key']==['us',1,999]
    assert len(exported)==1 and exported[0]['Auction ID']=='999'
    assert client.get('/api/results?new_after=-1').status_code==400
    app.extensions['refresh'].status={'running':True,'sources':{}}
    assert client.get('/api/updates',query_string={'after':baseline['cursor']}).json['deferred']
    assert post(client,'/api/monitor',{'enabled':True}).status_code==400  # Demo mode remains offline.


def test_cli_owns_one_monitor_lifecycle_even_when_server_exits(monkeypatch,tmp_path):
    import sys
    import pytest
    import wowflipper.__main__ as cli
    calls=[]
    class Monitor:
        def start(self):calls.append('start')
        def stop(self):calls.append('stop')
    class App:
        extensions={'monitor':Monitor()}
        def run(self,**kwargs):
            calls.append('run'); raise KeyboardInterrupt
    monkeypatch.setattr(cli,'create_app',lambda path:App())
    monkeypatch.setattr(sys,'argv',['wowflipper','--data-dir',str(tmp_path)])
    with pytest.raises(KeyboardInterrupt):cli.main()
    assert calls==['start','run','stop']
