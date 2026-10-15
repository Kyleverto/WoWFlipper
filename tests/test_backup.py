"""Portable personal backups never include market snapshots or credentials."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
import pytest

from wowflipper.models import Auction, Valuation, Candidate, Snapshot
from wowflipper.portfolio import Portfolio
from wowflipper.repository import Repository


def populated(path):
    repo=Repository(path)
    repo.save_settings(replace(repo.load_settings(),selling_realms=('agamaggan',),max_copies=10))
    now=datetime.now(timezone.utc)
    a=Auction('us',1,1,100,3,1000001,None,now,'toy',{'simple_identity':True,'tradeability':True})
    v=Valuation(100,'Toy',2000000,Decimal('.1'),Decimal('1'),2000000,2000000,now)
    c=Candidate(('us',1,1),a,v,6000000,4999999,Decimal('4'),('agamaggan',),(),'ok')
    other=replace(c,key=('us',1,2),auction=replace(a,auction_id=2))
    p=Portfolio(repo,lambda key:c if tuple(key)==c.key else other if tuple(key)==other.key else None)
    p.select(c.key,destination='agamaggan',request_id='select-one')
    purchase=p.record({'type':'purchase','key':list(c.key)},'purchase')
    sale=p.record({'type':'sale','purchase_id':purchase['id'],'quantity':1,'net_proceeds':500000},'sale')
    p.record({'type':'correction','target_id':sale['id'],'replacement':{'net_proceeds':600000}},'correction')
    p.select(other.key,destination='agamaggan',request_id='select-two')
    repo.set_resale_override(100,1234567)
    repo.cache_put('realms',[{'slug':'agamaggan','name':'Agamaggan','house_id':1}])
    repo.cache_put('credentials',{'token':'never-export-this'})
    repo.cache_put('data_epoch','old-epoch')
    repo.publish(Snapshot('auctions:us:1',now,now,None,'live',(a,)))
    return repo,p


def test_roundtrip_recovery_ids_idempotency_and_market_exclusion(tmp_path):
    from wowflipper.backup import BackupService
    source,p=populated(tmp_path/'source')
    service=BackupService(source); document=json.loads(json.dumps(service.export()))
    assert document['version']==1 and document['mode']=='live'
    assert 'never-export-this' not in json.dumps(document) and 'old-epoch' not in json.dumps(document)
    assert 'snapshots' not in document['data']
    preview=service.preview(document)
    assert preview['counts']=={'selections':1,'inventory':1,'events':3,'requests':5,'overrides':1,'realms':1}
    target=Repository(tmp_path/'target'); target.save_settings(replace(target.load_settings(),home_realm='Before'))
    target.cache_put('credentials',{'token':'target-secret'})
    before=BackupService(target).export(); target._analysis_cache=('stale',)
    restored=BackupService(target).restore(document)
    recovery=target.path/'backups'/restored['recovery_backup']
    saved=json.loads(recovery.read_text())
    assert saved['data']==before['data'] and restored['summary']==preview
    assert target.load_settings()==source.load_settings()
    assert target.cache_get('data_epoch') not in (None,'old-epoch') and target._analysis_cache is None
    assert target.cache_get('credentials')=={'token':'target-secret'} and target.latest('auctions:us:1') is None
    restored_p=Portfolio(target,lambda key:None)
    assert restored_p.history()==p.history() and restored_p.summary()==p.summary()
    assert restored_p.shopping()[0]['key']==['us',1,2]
    assert restored_p.record({'type':'purchase','key':['us',1,1]},'purchase')==p.history()[0]
    original_snapshot=source.latest('auctions:us:1')
    BackupService(source).restore(document)
    assert source.latest('auctions:us:1')==original_snapshot


@pytest.mark.parametrize('damage',[
    lambda d:d.update(version=99),
    lambda d:d.update(mode='offline-demo'),
    lambda d:d['data']['settings'].update(capital=-1),
    lambda d:d['data']['portfolio']['events'][0].update(cost=-1),
    lambda d:d['data']['portfolio']['events'][1].update(quantity=999),
    lambda d:d['data']['portfolio']['events'][2].update(target_id=999),
    lambda d:d['data']['portfolio']['events'][0].update(candidate={}),
    lambda d:d['data']['portfolio']['selected'][0].update(candidate={'auction':None}),
    lambda d:d['data']['portfolio']['selected'][0].update(key=['us',1,False]),
    lambda d:d['data']['portfolio']['requests'][0].update(payload=[]),
    lambda d:d['data']['portfolio']['requests'][0].update(result=[]),
    lambda d:d['data']['resale_overrides']['items']['100'].update(unit_price=0),
    lambda d:d['data']['realms'][0].update(house_id=[]),
    lambda d:d['data'].update(credentials={'token':'injected'}),
    lambda d:d['data']['portfolio']['selected'][0].update(key=['us','1','2']),
    lambda d:d['data']['portfolio']['selected'][0]['candidate'].update(key=['us','1','2']),
    lambda d:d['data']['portfolio']['requests'][3]['payload'].update(key=['us','1','1']),
    lambda d:d['data']['portfolio']['selected'][0]['candidate']['auction'].update(variant_key='not-json'),
    lambda d:d['data']['portfolio']['selected'][0]['candidate']['auction'].update(variant_key='[]'),
    lambda d:d['data']['portfolio']['selected'][0]['candidate']['auction']['metadata'].update(pet=[]),
])
def test_invalid_document_rejected_without_mutation_or_recovery(tmp_path,damage):
    from wowflipper.backup import BackupService
    repo,p=populated(tmp_path); service=BackupService(repo); original=service.export()
    broken=deepcopy(original); damage(broken)
    with pytest.raises(ValueError): service.preview(broken)
    with pytest.raises(ValueError): service.restore(broken)
    assert service.export()['data']==original['data']
    assert not (repo.path/'backups').exists()


def test_restore_aborts_if_recovery_cannot_be_saved(tmp_path):
    from wowflipper.backup import BackupService
    repo,p=populated(tmp_path); service=BackupService(repo); document=service.export()
    repo.cache_put('settings',replace(repo.load_settings(),home_realm='Before').to_dict())
    before=service.export()['data']; (repo.path/'backups').write_text('not a directory')
    with pytest.raises(OSError): service.restore(document)
    assert service.export()['data']==before


def test_empty_export_and_mode_boundaries(tmp_path):
    from wowflipper.backup import BackupService
    repo=Repository(tmp_path); service=BackupService(repo)
    document=service.export(); assert document['data']['portfolio']['events']==[]
    repo.cache_put('mode','offline-demo')
    with pytest.raises(ValueError): service.restore(document)
    document=service.export(); assert document['mode']=='offline-demo'
    assert service.restore(document)['summary']['mode']=='offline-demo'


def test_non_json_and_oversized_documents_are_rejected(tmp_path,monkeypatch):
    import wowflipper.backup as backup
    repo=Repository(tmp_path); service=backup.BackupService(repo); document=service.export()
    document['data']['settings']['discount']=float('nan')
    with pytest.raises(ValueError): service.preview(document)
    document=service.export()
    monkeypatch.setattr(backup,'MAX_DOCUMENT_BYTES',len(json.dumps(document))-1)
    with pytest.raises(ValueError): service.preview(document)
    with pytest.raises(ValueError): service.export()
    assert not (repo.path/'backups').exists()


def test_windows_recovery_does_not_open_directories_for_fsync(tmp_path,monkeypatch):
    import wowflipper.backup as backup
    import os
    repo=Repository(tmp_path); service=backup.BackupService(repo); document=service.export()
    class WindowsOS:
        name='nt'
        def __getattr__(self,name): return getattr(os,name)
        def open(self,path,*args,**kwargs):
            if os.path.isdir(path): raise PermissionError('Windows cannot open directories this way')
            return os.open(path,*args,**kwargs)
    monkeypatch.setattr(backup,'os',WindowsOS())
    result=service.restore(document)
    assert (repo.path/'backups'/result['recovery_backup']).is_file()


def test_restore_aborts_when_current_recovery_data_exceeds_supported_size(tmp_path,monkeypatch):
    import wowflipper.backup as backup
    repo=Repository(tmp_path); service=backup.BackupService(repo); document=service.export()
    repo.save_settings(replace(repo.load_settings(),home_realm='X'*2000))
    before=service.export()['data']
    monkeypatch.setattr(backup,'MAX_DOCUMENT_BYTES',len(json.dumps(document))+100)
    with pytest.raises(ValueError): service.restore(document)
    assert repo.load_settings().home_realm=='X'*2000
    assert not (repo.path/'backups').exists()


def test_pet_selection_and_override_roundtrip_preserves_variant(tmp_path):
    from wowflipper.backup import BackupService
    repo=Repository(tmp_path/'source'); repo.save_settings(replace(repo.load_settings(),selling_realms=('agamaggan',),max_copies=10))
    now=datetime.now(timezone.utc)
    pet={'species_id':39,'level':25,'quality':3,'breed_id':4,'name':'Chicken'}
    variant={'pet_species_id':39,'pet_level':25,'pet_quality_id':3,'pet_breed_id':4}
    a=Auction('us',1,2,82800,1,1000001,json.dumps(variant),now,'battle-pet',
              {'simple_identity':False,'tradeability':True,'pet':pet,'is_tradable':True,'is_battlepet':True})
    v=Valuation(82800,'Chicken',4000000,Decimal('.1'),Decimal('1'),4000000,4000000,now,pet_species_id=39)
    c=Candidate(('us',1,2),a,v,3800000,2799999,Decimal('2.79'),('agamaggan',),(),'ok')
    p=Portfolio(repo,lambda key:c)
    p.select(c.key,destination='agamaggan',request_id='pet-selection')
    repo.set_resale_override('pet:39:25:3:4',4000000)
    document=BackupService(repo).export(); target=Repository(tmp_path/'target')
    BackupService(target).restore(document)
    result=Portfolio(target,lambda key:None).shopping()[0]
    assert result['candidate']['auction']['metadata']['pet']==pet
    assert target.load_resale_overrides()['pet:39:25:3:4']['pet']=={key:value for key,value in pet.items() if key!='name'}
