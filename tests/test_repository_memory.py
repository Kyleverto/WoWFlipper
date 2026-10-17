"""Publication preserves archive bytes without duplicating the metadata graph."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import tracemalloc

import pytest

from wowflipper.models import Auction, Snapshot, Valuation
from wowflipper.repository import Repository, encode


def snapshot():
    now=datetime.now(timezone.utc)
    metadata={'simple_identity':True,'tradeability':True,'nested':{'values':[1,2,3]},'price':Decimal('12.30')}
    auction=Auction('us',1,1,2,1,100,'{"context":0}',now,'recipe',metadata,'fixture-row')
    return Snapshot('auctions:us:1',now,now,'source-header','fixture',(auction,),source_id='fixture-source',
        source_metadata={'accepted_rows':1,'detail':{'nested':[1,2]}})


def legacy_data(value):
    # This is the previously published format, including insertion order. The
    # compatibility fixture is deliberately small; production must not deep-copy.
    data=asdict(value); data.pop('raw_payload'); data.pop('id')
    data['record_type']='auction' if value.records and isinstance(value.records[0],Auction) else 'valuation'
    return data


def test_publish_peak_does_not_duplicate_shared_item_metadata(tmp_path):
    repo=Repository(tmp_path); base=snapshot()
    shared={'simple_identity':True,'tradeability':True,'name':'Fixture item',
        'details':[{'id':number,'label':'Repeated metadata descriptor'} for number in range(32)]}
    rows=tuple(replace(base.records[0],auction_id=number+1,metadata=shared) for number in range(3000))
    value=replace(base,records=rows,raw_payload=b'{"original":true}')
    tracemalloc.start()
    try:
        repo.publish(value)
        _,peak=tracemalloc.get_traced_memory()
    finally: tracemalloc.stop()
    with repo.connect() as db: normalized_bytes=db.execute('SELECT length(data) FROM snapshots').fetchone()[0]
    # Old publication peaks at ~4.7 times its 6 MB JSON output because it copies
    # all 3,000 nested metadata graphs first. Allow ample encoder overhead while
    # catching that extra full graph; the source objects predate tracing.
    assert peak<normalized_bytes*3.5
    assert rows[0].metadata is rows[-1].metadata is shared


@pytest.mark.parametrize('raw',[b'',b'{"original_raw":true}'])
def test_shallow_publication_preserves_normalized_json_provenance_and_archive_format(tmp_path,raw):
    repo=Repository(tmp_path); value=replace(snapshot(),raw_payload=raw)
    expected=legacy_data(value)
    expected_payload=raw or json.dumps(expected,default=encode).encode()
    digest=hashlib.sha256(expected_payload).hexdigest()
    expected['content_hash']=digest
    identity=repo.publish(value)
    with repo.connect() as db:
        data,payload=db.execute('SELECT data,payload FROM snapshots WHERE id=?',(identity,)).fetchone()
        compact=db.execute('SELECT data FROM provenance WHERE id=?',(identity,)).fetchone()[0]
    assert data==json.dumps(expected,default=encode)
    assert compact==json.dumps({key:item for key,item in expected.items() if key!='records'},default=encode)
    assert payload==digest+'.json'
    latest=repo.latest(value.source_key)
    assert latest.raw_payload==expected_payload and latest.content_hash==digest
    assert latest.records[0].metadata['price']=='12.30'
    assert latest.records[0].variant_key==value.records[0].variant_key
    assert latest.source_metadata==value.source_metadata


def test_shallow_valuation_serialization_and_archive_reuse_roundtrip(tmp_path):
    repo=Repository(tmp_path); now=datetime.now(timezone.utc)
    valuation=Valuation(2,'Recipe',100,Decimal('.2'),Decimal('4.5'),90,80,now)
    value=Snapshot('tsm:us',now,now,'header','fixture',(valuation,),raw_payload=b'original CSV bytes')
    repo.publish(value)
    previous=repo.latest('tsm:us',include_payload=False)
    digest=previous.content_hash
    repo.publish(replace(previous,fetched_at=datetime.now(timezone.utc)))
    latest=repo.latest('tsm:us')
    assert latest.records==(valuation,)
    assert latest.raw_payload==b'original CSV bytes' and latest.content_hash==digest
    assert len(list(repo.payloads.glob('*.json')))==1


def test_fallback_serialization_failure_removes_temporary_archive_and_preserves_previous(tmp_path):
    repo=Repository(tmp_path); value=snapshot()
    identity=repo.publish(replace(value,raw_payload=b'old raw'))
    invalid=replace(value,records=(replace(value.records[0],metadata={'not_json':object()}),))
    with pytest.raises(TypeError): repo.publish(invalid)
    assert repo.latest(value.source_key).id==identity
    assert repo.latest(value.source_key).raw_payload==b'old raw'
    assert len(list(repo.payloads.iterdir()))==1


def test_streamed_fallback_rename_failure_preserves_previous_and_cleans_temp(tmp_path,monkeypatch):
    import wowflipper.repository as module
    repo=Repository(tmp_path); value=snapshot()
    identity=repo.publish(replace(value,raw_payload=b'old raw'))
    def fail(*args): raise OSError('simulated interrupted archive replacement')
    monkeypatch.setattr(module.os,'replace',fail)
    with pytest.raises(OSError): repo.publish(value)
    assert repo.latest(value.source_key).id==identity
    assert repo.latest(value.source_key).raw_payload==b'old raw'
    assert len(list(repo.payloads.iterdir()))==1


def test_archive_removed_during_reuse_never_writes_empty_bytes_under_previous_hash(tmp_path,monkeypatch):
    import wowflipper.repository as module
    repo=Repository(tmp_path); value=replace(snapshot(),raw_payload=b'original raw bytes')
    identity=repo.publish(value)
    previous=repo.latest(value.source_key,include_payload=False)
    archive=repo.payloads/(previous.content_hash+'.json')
    original_dumps=module.json.dumps
    def dumps(data,*args,**kwargs):
        if isinstance(data,dict) and data.get('source_key')==value.source_key and 'records' in data:
            archive.unlink(missing_ok=True)
        return original_dumps(data,*args,**kwargs)
    monkeypatch.setattr(module.json,'dumps',dumps)
    with pytest.raises(FileNotFoundError): repo.publish(previous)
    with repo.connect() as db:
        assert db.execute('SELECT MAX(id) FROM snapshots WHERE source=?',(value.source_key,)).fetchone()[0]==identity
    assert not archive.exists()
