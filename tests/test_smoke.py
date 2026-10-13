import importlib.util
from pathlib import Path
from datetime import datetime, timezone
from wowflipper.models import Snapshot
from wowflipper.sources.http import SourceError

spec=importlib.util.spec_from_file_location('smoke_live',Path(__file__).parents[1]/'scripts/smoke_live.py')
smoke=importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

def test_missing_credentials_no_network(monkeypatch,capsys):
 monkeypatch.delenv('BLIZZARD_CLIENT_ID',raising=False)
 monkeypatch.delenv('BLIZZARD_CLIENT_SECRET',raising=False)
 monkeypatch.setattr(smoke.TSMClient,'region',lambda self: (_ for _ in ()).throw(AssertionError('network')))
 assert smoke.main([])==2
 assert '.env is not loaded' in capsys.readouterr().out

def test_tsm_only_summary(monkeypatch,capsys):
 now=datetime.now(timezone.utc)
 snap=Snapshot('tsm:us',now,now,None,'tsm',(),source_metadata={'accepted_rows':20,'rejected_rows':2})
 monkeypatch.setattr(smoke.TSMClient,'region',lambda self:snap)
 assert smoke.main(['--tsm-only'])==0
 assert '"accepted_rows": 20' in capsys.readouterr().out

def test_failure_hides_upstream_details(monkeypatch,capsys):
 def fail(self): raise SourceError('SECRET upstream body')
 monkeypatch.setattr(smoke.TSMClient,'region',fail)
 assert smoke.main(['--tsm-only'])==1
 assert 'SECRET' not in capsys.readouterr().out

def test_full_smoke_reads_configured_database_without_mutating_it(monkeypatch,tmp_path,capsys):
 from dataclasses import replace
 from wowflipper.repository import Repository
 repo=Repository(tmp_path)
 settings=replace(repo.load_settings(),buying_realms=('area-52',),selling_realms=('agamaggan',))
 repo.save_settings(settings)
 before=(tmp_path/'market.sqlite3').read_bytes()
 monkeypatch.setenv('BLIZZARD_CLIENT_ID','test-id')
 monkeypatch.setenv('BLIZZARD_CLIENT_SECRET','test-secret')
 now=datetime.now(timezone.utc)
 snap=Snapshot('tsm:us',now,now,None,'tsm',(),source_metadata={'accepted_rows':0,'rejected_rows':0})
 monkeypatch.setattr(smoke.TSMClient,'region',lambda self:snap)
 seen=[]
 class FakeRefresh:
  def __init__(self,repo,client,tsm):
   self.repo=repo
   self._worker=self
  def run(self,settings):
   seen.append(settings)
   self.repo.save_settings(settings)
   self.repo.publish(snap)
   return {'running':False,'sources':{'tsm:us':{'state':'success'}}}
  def shutdown(self,wait): pass
 monkeypatch.setattr(smoke,'RefreshService',FakeRefresh)
 assert smoke.main(['--data-dir',str(tmp_path)])==0
 assert seen==[settings]
 assert '"qualifying_rows": 0' in capsys.readouterr().out
 assert (tmp_path/'market.sqlite3').read_bytes()==before
