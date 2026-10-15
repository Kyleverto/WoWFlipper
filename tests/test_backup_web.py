import copy
from wowflipper.web import create_app


def post(client, path, payload, token=None):
    if token is None:
        token=client.get('/api/state').json['csrf']
    return client.post(path,json=payload,headers={'X-CSRF-Token':token})


def test_backup_routes_preview_restore_and_old_session_is_rejected(tmp_path):
    app=create_app(tmp_path,testing=True)
    client=app.test_client(); old_tab=app.test_client()
    before=client.get('/api/state').json
    old_token=old_tab.get('/api/state').json['csrf']
    response=client.get('/api/backup')
    assert response.status_code==200
    assert 'attachment' in response.headers['Content-Disposition']
    backup=response.json
    assert post(client,'/api/backup/preview',{'backup':backup}).status_code==200
    assert post(client,'/api/settings',{'min_profit':7654321}).status_code==200
    result=post(client,'/api/backup/restore',{'backup':backup})
    assert result.status_code==200, result.json
    assert result.json['restored'] is True
    assert (tmp_path/'backups'/result.json['recovery_backup']).is_file()
    assert old_tab.post('/api/settings',json={'min_profit':111},headers={'X-CSRF-Token':old_token}).status_code==403
    state=client.get('/api/state').json
    assert state['data_epoch']!=before['data_epoch']
    assert state['settings']['min_profit']==before['settings']['min_profit']
    assert post(client,'/api/settings',{'min_profit':222}).status_code==200


def test_backup_restore_refuses_active_refresh_and_invalid_input(tmp_path):
    app=create_app(tmp_path,testing=True); client=app.test_client()
    original=client.get('/api/backup').json
    app.extensions['refresh'].status={'running':True,'sources':{}}
    assert post(client,'/api/backup/restore',{'backup':original}).status_code==409
    app.extensions['refresh'].status={'running':False,'sources':{}}
    bad=copy.deepcopy(original); bad['version']=999
    assert post(client,'/api/backup/restore',{'backup':bad}).status_code==400
    assert not (tmp_path/'backups').exists()
    assert client.get('/api/state').json['settings']==original['data']['settings']


def test_backup_routes_require_local_csrf_and_serialize_mutations(tmp_path):
    app=create_app(tmp_path,testing=True); client=app.test_client()
    token=client.get('/api/state').json['csrf']
    document=client.get('/api/backup').json
    assert client.post('/api/backup/restore',json={'backup':document}).status_code==403
    assert client.post('/api/backup/preview',json={'backup':document},headers={'X-CSRF-Token':token,'Origin':'https://invalid.example'}).status_code==403
    # Even a request whose cookie decoded before restore cannot mutate afterwards.
    lock=app.extensions['mutation_lock']
    with lock:
        app.extensions['repository'].cache_put('data_epoch','new-dataset')
    assert client.post('/api/settings',json={'min_profit':11},headers={'X-CSRF-Token':token}).status_code==403
    assert post(client,'/api/settings',{'min_profit':12}).status_code==200
