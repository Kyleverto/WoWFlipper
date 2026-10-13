from datetime import datetime, timezone
from dataclasses import replace
import pytest
from wowflipper.models import Snapshot, Auction
from wowflipper.repository import Repository

def test_failed_refresh_preserves_success(tmp_path):
    repo=Repository(tmp_path)
    valid=Snapshot('auctions:us:1',None,datetime.now(timezone.utc),None,'blizzard',(Auction('us',1,1,2,1,10,None),),raw_payload=b'{}')
    first=repo.publish(valid)
    with pytest.raises(ValueError): repo.publish(replace(valid,records=('broken',)))
    assert repo.latest(valid.source_key).id==first

def test_retention_and_reload(tmp_path):
    repo=Repository(tmp_path)
    for n in range(3): repo.publish(Snapshot('x',None,datetime.now(timezone.utc),None,'blizzard',(),raw_payload=str(n).encode()))
    assert len(list((tmp_path/'payloads').glob('*.json')))==2
    assert Repository(tmp_path).latest('x').raw_payload==b'2'


def test_interrupted_publication_preserves_previous(tmp_path,monkeypatch):
    import wowflipper.repository as module
    repo=Repository(tmp_path); now=datetime.now(timezone.utc)
    valid=Snapshot('x',None,now,None,'blizzard',(),raw_payload=b'old')
    first=repo.publish(valid)
    def fail(*a): raise OSError('fixture interruption')
    monkeypatch.setattr(module.os,'replace',fail)
    with pytest.raises(OSError): repo.publish(replace(valid,raw_payload=b'new'))
    assert repo.latest('x').id==first
    assert repo.latest('x').raw_payload==b'old'

def test_settings_roundtrip(tmp_path):
    from wowflipper.settings import Settings
    repo=Repository(tmp_path)
    settings=Settings.from_dict({'buying_realms':['agamaggan'],'selling_realms':[]})
    repo.save_settings(settings)
    assert Repository(tmp_path).load_settings()==settings
