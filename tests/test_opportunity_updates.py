from dataclasses import replace
from wowflipper.demo import seed_demo
from wowflipper.repository import Repository
from wowflipper.models import integer
from wowflipper.query import MarketQuery


def prepared(path):
    from wowflipper.opportunity_updates import OpportunityUpdates
    seed_demo(path); repo=Repository(path); repo.cache_put('data_epoch','initial')
    return repo,OpportunityUpdates(repo)


def add_listing(repo,identity,category='toy'):
    snapshot=repo.latest('auctions:us:1')
    source=next(a for a in snapshot.records if a.category==category)
    repo.publish(replace(snapshot,records=snapshot.records+(replace(source,auction_id=identity),)))


def test_initial_baseline_new_once_persistent_and_current_filtering(tmp_path):
    from wowflipper.opportunity_updates import OpportunityUpdates
    repo,feed=prepared(tmp_path)
    baseline=feed.summary({})
    assert baseline['baseline'] and baseline['new_count']==baseline['notify_count']==0
    cursor=baseline['cursor']; add_listing(repo,900)
    response=feed.summary({'after':str(cursor),'notify_after':str(cursor),'categories':'toy'})
    assert response['new_count']==response['notify_count']==1 and response['updated']
    assert response['preview'][0]['key']==['us',1,900]
    assert feed.summary({'after':str(cursor),'notify_after':str(cursor),'categories':'mount'})['new_count']==0
    assert feed.summary({'after':str(cursor),'notify_after':str(response['cursor'])})['new_count']==1
    assert feed.summary({'after':str(cursor),'notify_after':str(response['cursor'])})['notify_count']==0
    restored_feed=OpportunityUpdates(repo)
    assert restored_feed.summary({'after':str(response['cursor'])})['new_count']==0
    query=MarketQuery(repo)
    assert [row['key'] for row in query.rows({'new_after':str(cursor)})]==[['us',1,900]]


def test_disappearing_and_returning_listing_is_not_new_twice(tmp_path):
    repo,feed=prepared(tmp_path); cursor=feed.summary({})['cursor']
    add_listing(repo,900); current=feed.summary({'after':str(cursor)})['cursor']
    snapshot=repo.latest('auctions:us:1'); item=next(a for a in snapshot.records if a.auction_id==900)
    repo.publish(replace(snapshot,records=tuple(a for a in snapshot.records if a.auction_id!=900)))
    after_remove=feed.summary({'after':str(current)})['cursor']
    repo.publish(replace(snapshot,records=snapshot.records))
    assert feed.summary({'after':str(after_remove)})['new_count']==0


def test_config_and_restore_epoch_establish_quiet_baselines(tmp_path):
    repo,feed=prepared(tmp_path); cursor=feed.summary({})['cursor']
    add_listing(repo,900); repo.save_settings(replace(repo.load_settings(),min_profit=1))
    updated=feed.summary({'after':str(cursor)})
    assert updated['baseline'] and updated['new_count']==0
    add_listing(repo,901); repo.cache_put('data_epoch','restored')
    reset=feed.summary({'after':str(updated['cursor'])})
    assert reset['baseline'] and reset['data_epoch']=='restored' and reset['new_count']==0


def test_unchanged_poll_skips_market_analysis_and_active_refresh_defers(tmp_path,monkeypatch):
    import wowflipper.opportunity_updates as module
    repo,feed=prepared(tmp_path); cursor=feed.summary({})['cursor']
    def forbidden(*args,**kwargs):raise AssertionError('Unchanged monitoring must not rebuild or load market analysis')
    monkeypatch.setattr(module,'MarketQuery',forbidden)
    same=feed.summary({'after':str(cursor)})
    assert same['new_count']==0 and not same['updated']
    add_listing(repo,900)
    pending=feed.summary({'after':str(cursor)},running=True)
    assert pending['deferred'] and pending['cursor']==cursor


def test_only_current_matching_listings_are_reported(tmp_path):
    repo,feed=prepared(tmp_path); cursor=feed.summary({})['cursor']
    add_listing(repo,900); newer=feed.summary({'after':str(cursor)})['cursor']
    assert feed.summary({'after':str(cursor),'q':'no such item'})['new_count']==0
    snapshot=repo.latest('auctions:us:1')
    repo.publish(replace(snapshot,records=tuple(a for a in snapshot.records if a.auction_id!=900)))
    assert feed.summary({'after':str(cursor)})['new_count']==0


def test_restore_clears_runtime_feed_without_exporting_it(tmp_path):
    from wowflipper.backup import BackupService
    from wowflipper.opportunity_updates import keys_since
    repo,feed=prepared(tmp_path); cursor=feed.summary({})['cursor']
    add_listing(repo,900); current=feed.summary({'after':str(cursor)})['cursor']
    assert keys_since(repo,0)
    backup=BackupService(repo).export()
    assert 'opportunity_seen' not in str(backup) and 'opportunity_feed' not in str(backup)
    BackupService(repo).restore(backup)
    assert keys_since(repo,0)==set()
    assert feed.summary({'after':str(current)})['baseline']


def test_racing_refresh_defers_observation_until_complete(tmp_path,monkeypatch):
    import wowflipper.opportunity_updates as module
    repo,feed=prepared(tmp_path); cursor=feed.summary({})['cursor']; add_listing(repo,900)
    active=[False]; feed.refresh_running=lambda:active[0]
    original=module.MarketQuery
    def racing(*args):
        query=original(*args); active[0]=True; return query
    monkeypatch.setattr(module,'MarketQuery',racing)
    assert feed.summary({'after':str(cursor)})['deferred']
    monkeypatch.setattr(module,'MarketQuery',original); active[0]=False
    assert feed.summary({'after':str(cursor)})['new_count']==1


def test_refresh_starting_during_pending_matching_defers_notifications(tmp_path,monkeypatch):
    import wowflipper.opportunity_updates as module
    repo,feed=prepared(tmp_path); baseline=feed.summary({})['cursor']
    add_listing(repo,900); current=feed.summary({'after':baseline})['cursor']
    active=[False]; feed.refresh_running=lambda:active[0]
    original=module.MarketQuery
    def racing(*args):
        query=original(*args);active[0]=True;return query
    monkeypatch.setattr(module,'MarketQuery',racing)
    result=feed.summary({'after':baseline,'notify_after':baseline})
    assert result['deferred'] and result['new_count']==result['notify_count']==0
    assert result['preview']==[] and result['cursor']==current
    monkeypatch.setattr(module,'MarketQuery',original);active[0]=False
    result=feed.summary({'after':baseline,'notify_after':baseline})
    assert result['new_count']==result['notify_count']==1
    assert result['preview'][0]['key']==['us',1,900]


def test_old_notification_cursor_cannot_cross_quiet_settings_baseline(tmp_path):
    repo,feed=prepared(tmp_path); baseline=feed.summary({})['cursor']
    add_listing(repo,900); current=feed.summary({'after':baseline})['cursor']
    repo.save_settings(replace(repo.load_settings(),min_profit=1))
    quiet=feed.summary({'after':current});assert quiet['baseline']
    result=feed.summary({'after':quiet['cursor'],'notify_after':baseline})
    assert result['new_count']==result['notify_count']==0 and result['preview']==[]
    add_listing(repo,901)
    result=feed.summary({'after':quiet['cursor'],'notify_after':baseline})
    assert result['new_count']==result['notify_count']==1
    assert result['preview'][0]['key']==['us',1,901]
