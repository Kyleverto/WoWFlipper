"""Deterministic automatic scheduling with synthetic stores and refresh futures."""
from concurrent.futures import Future
from dataclasses import replace
from threading import Event,RLock
import pytest
from wowflipper.repository import Repository
from wowflipper.settings import Settings


class Clock:
    def __init__(self): self.wall=1700000000.; self.elapsed=100.
    def time(self): return self.wall
    def monotonic(self): return self.elapsed
    def advance(self,seconds): self.wall+=seconds;self.elapsed+=seconds


class Refresh:
    def __init__(self): self.status={'running':False,'sources':{}}; self.calls=[];self.future=None;self.started=Event()
    def status_snapshot(self): return dict(self.status)
    def start(self,settings,changed_only=False):
        assert not self.status['running']
        self.calls.append((settings,changed_only));self.status={'running':True,'run_id':len(self.calls),'sources':{}}
        self.future=Future();self.started.set();return self.future
    def complete(self,error_code=None):
        self.status={**self.status,'running':False,'completed_at':str(len(self.calls)),
                     'sources':{'realm':{'state':'failed','error_code':error_code}} if error_code else {}}
        self.future.set_result(self.status)


@pytest.fixture
def setup(tmp_path):
    from wowflipper.monitor import MarketMonitor
    repo=Repository(tmp_path); repo.cache_put('data_epoch','first');clock=Clock();refresh=Refresh()
    monitor=MarketMonitor(repo,refresh,RLock(),clock=clock.time,monotonic=clock.monotonic)
    return repo,clock,refresh,monitor


def test_preferences_default_off_and_strict_bool_validation():
    settings=Settings.from_dict({})
    assert settings.auto_refresh is settings.sound_alerts is settings.desktop_alerts is False
    assert Settings.from_dict({'auto_refresh':True,'sound_alerts':True,'desktop_alerts':True}).auto_refresh is True
    for field in ('auto_refresh','sound_alerts','desktop_alerts'):
        for value in (0,1,'true',None,[],{}):
            with pytest.raises(ValueError): Settings.from_dict({field:value})


def test_first_enable_immediate_then_waits_120_seconds_after_completion(setup):
    repo,clock,refresh,monitor=setup
    monitor.tick();assert refresh.calls==[]
    state=monitor.configure(enabled=True,sound=True,desktop=False)
    assert state['enabled'] is True and state['sound'] is True and state['interval_seconds']==120
    monitor.tick();assert len(refresh.calls)==1 and refresh.calls[0][1] is True
    clock.advance(300);monitor.tick();assert len(refresh.calls)==1
    refresh.complete();monitor.tick()
    assert monitor.status_snapshot()['last_check_at'] is not None
    clock.advance(119);monitor.tick();assert len(refresh.calls)==1
    clock.advance(1);monitor.tick();assert len(refresh.calls)==2


def test_manual_completion_moves_due_and_does_not_overlap(setup):
    repo,clock,refresh,monitor=setup
    monitor.configure(enabled=True)
    refresh.start(repo.load_settings());monitor.tick();assert len(refresh.calls)==1
    clock.advance(200);refresh.complete();monitor.tick()
    clock.advance(119);monitor.tick();assert len(refresh.calls)==1
    clock.advance(1);monitor.tick();assert len(refresh.calls)==2


def test_disable_preserves_running_future_and_saved_preferences(setup):
    repo,clock,refresh,monitor=setup
    monitor.configure(enabled=True,sound=True,desktop=True);monitor.tick();future=refresh.future
    state=monitor.configure(enabled=False)
    assert not future.cancelled() and state['next_check_at'] is None
    refresh.complete();clock.advance(1000);monitor.tick();assert len(refresh.calls)==1
    from wowflipper.monitor import MarketMonitor
    reopened=MarketMonitor(repo,refresh,RLock(),clock=clock.time,monotonic=clock.monotonic)
    state=reopened.status_snapshot()
    assert (state['enabled'],state['sound'],state['desktop'])==(False,True,True)


@pytest.mark.parametrize('options',[{'enabled':1},{'sound':'false'},{'desktop':None},{'unknown':True}])
def test_invalid_configuration_does_not_mutate_preferences(setup,options):
    repo,clock,refresh,monitor=setup;before=repo.load_settings()
    with pytest.raises(ValueError): monitor.configure(**options)
    assert repo.load_settings()==before


def test_error_backoff_is_safe_and_resets_after_success(setup):
    repo,clock,refresh,monitor=setup;monitor.configure(enabled=True)
    for delay in (120,240):
        monitor.tick();refresh.complete('rate_limited');monitor.tick()
        assert monitor.status_snapshot()['error_code']=='rate_limited'
        count=len(refresh.calls);clock.advance(delay-1);monitor.tick();assert len(refresh.calls)==count
        clock.advance(1)
    monitor.tick();refresh.complete();monitor.tick()
    assert monitor.status_snapshot().get('error_code') is None
    clock.advance(120);monitor.tick();assert len(refresh.calls)==4


def test_future_failure_never_exposes_messages_and_loop_keeps_scheduling(setup):
    repo,clock,refresh,monitor=setup;monitor.configure(enabled=True);monitor.tick()
    refresh.status['running']=False;refresh.future.set_exception(RuntimeError('secret OAuth upstream payload'))
    monitor.tick();assert 'secret' not in str(monitor.status_snapshot())
    clock.advance(120);monitor.tick();assert len(refresh.calls)==2


def test_offline_demo_does_not_start_and_restore_epoch_defers_check(setup):
    repo,clock,refresh,monitor=setup;repo.cache_put('mode','offline-demo')
    with pytest.raises(ValueError): monitor.configure(enabled=True)
    assert repo.load_settings().auto_refresh is False
    repo.save_settings(replace(repo.load_settings(),auto_refresh=True))
    monitor.tick();assert refresh.calls==[]
    assert monitor.status_snapshot()['next_check_at'] is None
    repo.cache_put('mode','live');repo.cache_put('data_epoch','restored');monitor.tick()
    assert monitor.status_snapshot()['last_check_at'] is None
    clock.advance(119);monitor.tick();assert refresh.calls==[]
    clock.advance(1);monitor.tick();assert len(refresh.calls)==1


def test_external_settings_and_monotonic_deadline_are_observed(setup):
    repo,clock,refresh,monitor=setup
    repo.save_settings(replace(repo.load_settings(),auto_refresh=True));monitor.tick();refresh.complete();monitor.tick()
    clock.wall+=50000;monitor.tick();assert len(refresh.calls)==1
    clock.advance(120);monitor.tick();assert len(refresh.calls)==2


def test_explicit_start_is_singleton_and_stop_does_not_cancel_refresh(setup):
    repo,clock,refresh,monitor=setup;monitor.configure(enabled=True)
    try:
        monitor.start();monitor.start();assert refresh.started.wait(2)
        assert len(refresh.calls)==1
    finally: monitor.stop()
    assert not refresh.future.cancelled()
    clock.advance(1000);monitor.tick();assert len(refresh.calls)==1


def test_manual_finishes_between_scheduler_ticks_and_shared_mutation_lock_blocks_auto(setup):
    from concurrent.futures import ThreadPoolExecutor
    repo,clock,refresh,monitor=setup
    monitor.configure(enabled=True)
    entered=Event()
    def tick(): entered.set();return monitor.tick()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with monitor.mutation_lock:
            pending=pool.submit(tick);assert entered.wait(2)
            refresh.start(repo.load_settings());refresh.complete()
            assert len(refresh.calls)==1
        pending.result(timeout=2)
    assert len(refresh.calls)==1
    clock.advance(120);monitor.tick();assert len(refresh.calls)==2


def test_restore_clears_previous_check_and_waits_full_interval(setup):
    repo,clock,refresh,monitor=setup
    monitor.configure(enabled=True);monitor.tick();refresh.complete();monitor.tick()
    assert monitor.status_snapshot()['last_check_at'] is not None
    clock.advance(60);repo.cache_put('data_epoch','new-dataset');monitor.tick()
    assert monitor.status_snapshot()['last_check_at'] is None
    clock.advance(119);monitor.tick();assert len(refresh.calls)==1
    clock.advance(1);monitor.tick();assert len(refresh.calls)==2


def test_restore_discards_unobserved_old_completion_and_error(setup):
    repo,clock,refresh,monitor=setup
    monitor.configure(enabled=True);monitor.tick();refresh.complete('rate_limited')
    repo.cache_put('data_epoch','restored-before-next-tick')
    monitor.tick();state=monitor.status_snapshot()
    assert state['last_check_at'] is None and state.get('error_code') is None
    clock.advance(119);monitor.tick();assert len(refresh.calls)==1
    clock.advance(1);monitor.tick();assert len(refresh.calls)==2


def test_metadata_only_auth_and_rate_failures_back_off_even_when_house_publication_succeeds(setup):
    repo,clock,refresh,monitor=setup;monitor.configure(enabled=True)
    for delay in (120,240):
        monitor.tick()
        refresh.status={**refresh.status,'running':False,'completed_at':str(len(refresh.calls)),
            'sources':{'auctions:us:1':{'state':'success','metadata_error_code':'rate_limited','metadata_deferred':100}}}
        refresh.future.set_result(refresh.status)
        monitor.tick()
        assert monitor.status_snapshot()['error_code']=='rate_limited'
        count=len(refresh.calls);clock.advance(delay-1);monitor.tick();assert len(refresh.calls)==count
        clock.advance(1)
