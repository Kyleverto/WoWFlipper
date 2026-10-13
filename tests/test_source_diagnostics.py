import pytest
from wowflipper.sources import http

@pytest.mark.parametrize('status,code', [(401,'auth_denied'),(403,'auth_denied'),(404,'unavailable'),(429,'rate_limited'),(500,'upstream_error'),(503,'upstream_error')])
def test_http_status_diagnostics_contain_only_safe_fields(status, code):
    assert http.source_diagnostic(http.http_status_error(status)) == {'error':'SourceError','error_code':code,'http_status':status}

def test_unknown_exception_details_never_escape():
    assert http.source_diagnostic(ValueError('https://private/?token=secret')) == {'error':'ValueError','error_code':'invalid_schema'}
    assert http.source_diagnostic(RuntimeError('Authorization: secret')) == {'error':'RuntimeError','error_code':'source_error'}

def test_structured_error_rejects_untrusted_codes_and_status():
    error = http.SourceError('token=secret', code='secret-url', status='token=secret')
    assert http.source_diagnostic(error) == {'error':'SourceError','error_code':'source_error'}

@pytest.mark.parametrize('status,headers,expected_type,code',[(429,{},http.SourceError,'rate_limited'),(503,{},http.SourceError,'upstream_error'),(429,{'Retry-After':'120'},http.Deferred,'rate_limited'),(503,{'Retry-After':'invalid secret'},http.Deferred,'upstream_error')])
def test_transport_retries_preserve_safe_status(status, headers, expected_type, code):
    client = http.HTTPClient(lambda *args: http.Response(status,headers,b'secret'),sleep=lambda delay: None)
    with pytest.raises(expected_type) as caught:
        client.request('GET','https://us.api.blizzard.com/test?token=secret')
    assert http.source_diagnostic(caught.value) == {'error':expected_type.__name__,'error_code':code,'http_status':status}

def test_transport_network_failure_has_safe_code():
    def failed(*args): raise OSError('secret-token')
    client = http.HTTPClient(failed,sleep=lambda delay: None)
    with pytest.raises(http.SourceError) as caught:
        client.request('GET','https://us.api.blizzard.com/test')
    assert http.source_diagnostic(caught.value) == {'error':'SourceError','error_code':'network_error'}

def test_refresh_retains_safe_auth_status_without_secret(tmp_path):
    from wowflipper.refresh import RefreshService
    from wowflipper.repository import Repository
    from wowflipper.settings import Settings
    class Client:
        def realms(self): raise http.SourceError('Authorization: secret',code='auth_denied',status=403)
    class TSM:
        def region(self,previous): raise ValueError('token=secret')
    result=RefreshService(Repository(tmp_path),Client(),TSM()).run(Settings())
    assert result['sources']['realms:us'] == {'state':'failed','error':'SourceError','error_code':'auth_denied','http_status':403}
    assert 'secret' not in str(result)

def test_tsm_unavailable_retains_safe_http_status():
    from wowflipper.sources.tsm import TSMClient
    client=TSMClient(http.HTTPClient(lambda *args:http.Response(404,{},b'secret')))
    with pytest.raises(http.SourceError) as caught: client.region()
    assert http.source_diagnostic(caught.value) == {'error':'SourceError','error_code':'unavailable','http_status':404}

def test_source_status_renderer_uses_fixed_safe_messages():
    import subprocess
    from pathlib import Path
    script=Path('wowflipper/static/app.js').read_text()
    block=script[script.index('function renderSources()'):script.index('function populateDestinations()')]
    program='''const target={lines:[],replaceChildren(){this.lines=[]},append(node){this.lines.push(node.text)}};
const $=()=>target; const el=(tag,text)=>({text});
let state={sources:[],realms:[{slug:'area-52',name:'Area 52',house_id:1}],refresh:{running:false,sources:{'auctions:us:1':{state:'failed',error_code:'unavailable',http_status:404,error:'secret-token'}}}};
'''+block+'''
renderSources();
if(!target.lines.some(line=>line.includes('Area 52') && line.includes('unavailable')))throw Error(JSON.stringify(target.lines));
if(target.lines.some(line=>line.includes('secret-token') || line.includes('Configure credentials')))throw Error('Unsafe or misleading message');
state.refresh.sources={'realms:us':{state:'failed',error_code:'auth_missing'}};renderSources();
if(!target.lines.some(line=>line.includes('credentials are not configured')))throw Error('Missing auth guidance');
state.refresh.sources={'realm:us:test':{state:'skipped',error_code:'unsupported_realm'}};renderSources();
if(!target.lines.some(line=>line.includes('active United States realm')))throw Error('Missing skip guidance');
'''
    result=subprocess.run(['node','-e',program],text=True,capture_output=True)
    assert result.returncode==0,result.stderr
