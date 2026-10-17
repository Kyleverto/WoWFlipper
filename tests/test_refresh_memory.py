"""Regression: refresh must not retain every downloaded house in memory."""
from dataclasses import replace
from datetime import datetime,timezone
from decimal import Decimal
import threading
import weakref
from wowflipper.models import Auction,Snapshot,Valuation
from wowflipper.repository import Repository
from wowflipper.refresh import RefreshService
from wowflipper.settings import Settings


def test_downloaded_houses_are_released_before_publication(tmp_path,monkeypatch):
    repo=Repository(tmp_path);now=datetime.now(timezone.utc)
    realms=[{'slug':f'h{i}','house_id':i,'category':'United States','is_auction_realm':True} for i in range(1,13)]
    repo.cache_put('realms',realms);repo.cache_timed('realms',realms)
    settings=replace(Settings(),buying_realms=tuple(r['slug'] for r in realms),selling_realms=('h1',),categories=('recipe',))
    repo.publish(Snapshot('tsm:us',now,now,None,'fixture',(Valuation(2,'Recipe',10000000,Decimal('.5'),Decimal('5'),10000000,10000000,now),)))
    refs=[];lock=threading.Lock();publication_counts=[]
    from wowflipper.refresh import _SnapshotSpool
    loaded=[];loaded_peak=[];original_load=_SnapshotSpool.__getitem__
    def load(store,house):
        snapshot=original_load(store,house)
        loaded.append(weakref.ref(snapshot))
        loaded_peak.append(sum(ref() is not None for ref in loaded))
        return snapshot
    monkeypatch.setattr(_SnapshotSpool,'__getitem__',load)
    class Client:
        def auctions(self,house,previous):
            snapshot=Snapshot(f'auctions:us:{house}',now,now,'original-header','fixture',
                (Auction('us',house,house,2,1,1000,None,now),),raw_payload=f'raw-house-{house}'.encode())
            with lock:refs.append(weakref.ref(snapshot))
            return snapshot
        def item(self,item):return {'category':'recipe','tradeability':True}
    class TSM:
        def region(self,previous):return previous
    original=repo.publish
    def publish(snapshot):
        if snapshot.source_key.startswith('auctions:'):
            publication_counts.append(sum(ref() is not None for ref in refs))
        return original(snapshot)
    monkeypatch.setattr(repo,'publish',publish)
    result=RefreshService(repo,Client(),TSM()).run(settings)
    assert len(refs)==12 and len(publication_counts)==12
    assert max(publication_counts)<=3, 'Download snapshots accumulate across all selected houses'
    assert max(loaded_peak)<=3, 'Staged snapshots accumulate during metadata or publication'
    assert not list(tmp_path.glob('refresh-*')), 'Completed refresh left scratch files behind'
    for house in range(1,13):
        snapshot=repo.latest(f'auctions:us:{house}')
        assert snapshot.raw_payload==f'raw-house-{house}'.encode()
        assert snapshot.http_last_modified=='original-header'
        assert snapshot.records[0].category=='recipe'
        assert result['sources'][snapshot.source_key]['state']=='success'
