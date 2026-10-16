"""Opt-in process-local market scheduling; source timestamps remain authoritative."""
from dataclasses import replace
from datetime import datetime, timezone
import threading
import time
from .sources.http import source_diagnostic

INTERVAL_SECONDS=120
MAX_BACKOFF_SECONDS=900
_UNSET=object()
PREFERENCES={'enabled':'auto_refresh','sound':'sound_alerts','desktop':'desktop_alerts'}


class MarketMonitor:
    def __init__(self,repository,refresh,mutation_lock,*,clock=time.time,monotonic=time.monotonic,poll_interval=1.0):
        self.repository=repository;self.refresh=refresh;self.mutation_lock=mutation_lock
        self.clock=clock;self.monotonic=monotonic;self.poll_interval=poll_interval
        self._preferences=None;self._epoch=_UNSET;self._offline=False
        self._due=None;self._last_check_at=None;self._failures=0;self._error_code=None
        self._future=None;self._was_running=False;self._completion_token=self._token(refresh.status_snapshot())
        self._wake=threading.Event();self._lifecycle=threading.Lock();self._thread=None;self._stopped=False

    def _iso(self,epoch):
        return datetime.fromtimestamp(epoch,timezone.utc).isoformat()

    def _sync(self):
        settings=self.repository.load_settings()
        preferences={alias:getattr(settings,field) for alias,field in PREFERENCES.items()}
        enabled=preferences['enabled'];previous=self._preferences
        epoch=self.repository.cache_get('data_epoch')
        epoch_changed=self._epoch is not _UNSET and epoch!=self._epoch
        mode=self.repository.cache_get('mode') or 'live';offline=mode=='offline-demo'
        now=self.monotonic()
        if previous is None or previous['enabled']!=enabled:
            self._due=now if enabled else None
        if self._offline and not offline and enabled: self._due=now
        if epoch_changed:
            # Restore creates a quiet scheduling baseline and allows time to reload.
            self._due=now+INTERVAL_SECONDS if enabled else None
            self._last_check_at=None;self._failures=0;self._error_code=None
            # Root rejects restore while refresh is running. Forget an old completed
            # future as well, so its errors/timestamps cannot cross dataset epochs.
            self._future=None
            self._completion_token=_UNSET;self._was_running=False
        if offline or not enabled: self._due=None
        self._preferences=preferences;self._epoch=epoch;self._offline=offline
        return settings

    def _snapshot(self):
        due=self._due if not self._stopped and not self._offline else None
        result={**self._preferences,'interval_seconds':INTERVAL_SECONDS,
                'next_check_at':self._iso(self.clock()+max(0,due-self.monotonic())) if due is not None else None,
                'last_check_at':self._last_check_at}
        if self._error_code: result['error_code']=self._error_code
        return result

    def status_snapshot(self):
        with self.mutation_lock:
            self._sync()
            return self._snapshot()

    def configure(self,**options):
        if set(options)-PREFERENCES.keys() or any(type(value) is not bool for value in options.values()):
            raise ValueError('Expected enabled, sound and desktop boolean preferences')
        with self.mutation_lock:
            settings=self.repository.load_settings()
            if options.get('enabled') is True and self.repository.cache_get('mode')=='offline-demo':
                raise ValueError('Offline demo cannot enable automatic live source checks')
            if options:
                self.repository.save_settings(replace(settings,**{PREFERENCES[key]:value for key,value in options.items()}))
            self._sync()
            result=self._snapshot()
        self._wake.set()
        return result

    @staticmethod
    def _token(status):
        return status.get('run_id'),status.get('completed_at')

    def _completed(self,status,error=None):
        codes={source.get(field) for source in status.get('sources',{}).values() if isinstance(source,dict)
            for field in ('error_code','metadata_error_code')}
        code=source_diagnostic(error)['error_code'] if error is not None else next(
            (candidate for candidate in ('auth_missing','auth_denied','rate_limited','network_error','upstream_error') if candidate in codes),None)
        self._error_code=code
        if code: self._failures=min(self._failures+1,5)
        else: self._failures=0
        delay=min(INTERVAL_SECONDS*2**max(0,self._failures-1),MAX_BACKOFF_SECONDS)
        self._last_check_at=self._iso(self.clock())
        self._due=self.monotonic()+delay if self._preferences['enabled'] and not self._offline else None
        self._completion_token=self._token(status);self._was_running=False

    def _observe(self,status):
        if status.get('running'):
            self._was_running=True;self._due=None
            return
        if self._future is not None:
            if not self._future.done(): return
            future=self._future;self._future=None
            try:
                result=future.result()
            except Exception as error: self._completed(status,error)
            else: self._completed(result if isinstance(result,dict) else status)
            return
        token=self._token(status)
        changed=self._completion_token is not _UNSET and token!=self._completion_token and token!=(None,None)
        if self._was_running or changed: self._completed(status)
        else: self._completion_token=token

    def tick(self):
        """One deterministic scheduler step; it never waits for source requests."""
        with self.mutation_lock:
            settings=self._sync()
            if self._stopped: return self._snapshot()
            status=self.refresh.status_snapshot()
            self._observe(status)
            if not self._preferences['enabled'] or self._offline: return self._snapshot()
            if status.get('running') or self._future is not None or self._due is None or self.monotonic()<self._due:
                return self._snapshot()
            try:
                self._future=self.refresh.start(settings,changed_only=True)
            except RuntimeError as error:
                # A manual refresh may win the refresh service's own singleton lock.
                current=self.refresh.status_snapshot()
                if current.get('running'): self._was_running=True;self._due=None
                else: self._completed(current,error)
            except Exception as error: self._completed(status,error)
            else:
                self._due=None;self._was_running=True
                self._future.add_done_callback(lambda future:self._wake.set())
            return self._snapshot()

    def _loop(self):
        while not self._stopped:
            self._wake.clear()
            try: self.tick()
            except Exception as error:
                # Keep the daemon alive without logging upstream text or credentials.
                with self.mutation_lock:
                    self._error_code=source_diagnostic(error)['error_code']
                    self._due=self.monotonic()+INTERVAL_SECONDS
            self._wake.wait(self.poll_interval)

    def start(self):
        """CLI lifecycle hook; constructing a monitor never starts a thread."""
        with self._lifecycle:
            if self._thread is not None and self._thread.is_alive(): return
            self._stopped=False
            self._thread=threading.Thread(target=self._loop,name='wowflipper-market-monitor',daemon=True)
            self._thread.start()

    def stop(self):
        """Stop scheduling without cancelling an active refresh or changing preferences."""
        with self._lifecycle:
            with self.mutation_lock:
                self._stopped=True;self._wake.set();thread=self._thread
        # Never join while holding the shared mutation lock: the daemon may need it.
        if thread is not None and thread is not threading.current_thread(): thread.join(timeout=2)
