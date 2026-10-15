from wowflipper.web import create_app


def test_state_and_status_handlers_use_coordinator_snapshots(tmp_path, monkeypatch):
    app=create_app(tmp_path,testing=True)
    coordinator=app.extensions['refresh']
    coordinator.status={'running':False,'sources':{'unsafe-live-map':{}}}
    calls=[]
    snapshot={'running':True,'phase':'classification','started_at':'2026-10-15T12:00:00+00:00','elapsed_seconds':123.5,'completed':2,'total':4,'sources':{'toys:us':{'state':'pending','pending':50,'unavailable':3}}}
    def read():
        calls.append(True)
        return snapshot
    monkeypatch.setattr(coordinator,'status_snapshot',read)
    client=app.test_client()
    assert client.get('/api/state').json['refresh']==snapshot
    assert client.get('/api/refresh/status').json==snapshot
    assert len(calls)==2
