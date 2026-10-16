"""Persistent first-seen opportunity cursors, separate from financial history."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import threading
from .models import integer
from .query import MarketQuery


def _key(value):
    return json.dumps(list(value),separators=(',',':'))


def keys_since(repository,cursor):
    cursor=integer(cursor)
    with repository.connect() as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='opportunity_seen'").fetchone()
        if not exists:return set()
        return {tuple(json.loads(key)) for key, in db.execute('SELECT key FROM opportunity_seen WHERE first_cursor>?',(cursor,))}


class OpportunityUpdates:
    def __init__(self,repository,refresh_running=None):
        self.repository=repository
        self.refresh_running=refresh_running or (lambda:False)
        self._lock=threading.Lock()
        with repository.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS opportunity_seen (
                    key TEXT PRIMARY KEY, first_cursor INTEGER NOT NULL, last_seen TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS opportunity_seen_cursor ON opportunity_seen(first_cursor);
                CREATE TABLE IF NOT EXISTS opportunity_feed (
                    id INTEGER PRIMARY KEY CHECK(id=1), cursor INTEGER NOT NULL,
                    epoch TEXT NOT NULL, source_revision TEXT NOT NULL,
                    config_revision TEXT NOT NULL, baseline_cursor INTEGER NOT NULL);
            ''')

    @staticmethod
    def _hash(value):
        return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

    def _state(self,db):
        row=db.execute('SELECT cursor,epoch,source_revision,config_revision,baseline_cursor FROM opportunity_feed WHERE id=1').fetchone()
        return dict(zip(('cursor','epoch','source_revision','config_revision','baseline_cursor'),row)) if row else None

    def _epoch(self,db):
        row=db.execute("SELECT data FROM cache WHERE key='data_epoch'").fetchone()
        return json.loads(row[0]) if row else ''

    def _observe(self):
        """Only analyze changed market revisions; commit first-seen IDs atomically."""
        for _ in range(4):
            if self.refresh_running():return None,None
            revision=self.repository.analysis_revision()
            config=self._hash(revision[0]); sources=self._hash(revision[1])
            with self.repository.connect() as db:
                previous=self._state(db); epoch=self._epoch(db)
            if previous and previous['epoch']==epoch and previous['source_revision']==sources and previous['config_revision']==config:
                return previous,None
            query=MarketQuery(self.repository)
            with self.repository.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if self.refresh_running():return None,None
                # No settings or snapshot writer can change the database between
                # this comparison and the cursor commit. A racing refresh is
                # retried instead of permanently consuming its partial results.
                if self.repository.analysis_revision()!=query.revision:
                    continue
                revision=query.revision
                config=self._hash(revision[0]); sources=self._hash(revision[1])
                previous=self._state(db); epoch=self._epoch(db)
                reset=not previous or previous['epoch']!=epoch
                quiet=reset or previous['config_revision']!=config
                cursor=(previous['cursor'] if previous else 0)+1
                if reset:db.execute('DELETE FROM opportunity_seen')
                now=datetime.now(timezone.utc)
                db.executemany('''INSERT INTO opportunity_seen VALUES(?,?,?)
                    ON CONFLICT(key) DO UPDATE SET last_seen=excluded.last_seen''',
                    [(_key(key),0 if quiet else cursor,now.isoformat()) for key in query.candidates])
                # Active listings are touched above; expired historical IDs have
                # no reason to live forever in this non-financial runtime index.
                db.execute('DELETE FROM opportunity_seen WHERE last_seen<?',((now-timedelta(days=7)).isoformat(),))
                baseline=cursor if quiet else previous['baseline_cursor']
                db.execute('INSERT OR REPLACE INTO opportunity_feed VALUES(1,?,?,?,?,?)',
                    (cursor,epoch,sources,config,baseline))
                return {'cursor':cursor,'epoch':epoch,'source_revision':sources,'config_revision':config,'baseline_cursor':baseline},query
        raise ValueError('Market data changed during the update check. Retry shortly.')

    def summary(self,args,running=False):
        after=integer(args['after']) if args.get('after') is not None else None
        notify=integer(args['notify_after']) if args.get('notify_after') is not None else after
        with self._lock:
            if running or self.refresh_running():
                with self.repository.connect() as db:meta=self._state(db);epoch=self._epoch(db)
                return {'cursor':meta['cursor'] if meta else 0,'data_epoch':epoch,'deferred':True,
                        'baseline':False,'updated':False,'new_count':0,'notify_count':0,'preview':[]}
            meta,query=self._observe()
            if meta is None:
                with self.repository.connect() as db:previous=self._state(db);epoch=self._epoch(db)
                return {'cursor':previous['cursor'] if previous else 0,'data_epoch':epoch,'deferred':True,
                        'baseline':False,'updated':False,'new_count':0,'notify_count':0,'preview':[]}
            result={'cursor':meta['cursor'],'data_epoch':meta['epoch'],'baseline':False,
                    'updated':False,'new_count':0,'notify_count':0,'preview':[]}
            if after is None or after<meta['baseline_cursor'] or after>meta['cursor']:
                return {**result,'baseline':True}
            # A separate notification cursor cannot resurrect pre-baseline notices.
            notify=max(meta['baseline_cursor'],min(after if notify is None else notify,meta['cursor']))
            if after==meta['cursor'] and notify>=meta['cursor']:return result
            query=query or MarketQuery(self.repository)
            # Alerts follow the accepted view's search/categories, not page or
            # sort changes and not a previously displayed new-only cursor.
            filters={key:args[key] for key in ('q','categories') if key in args}
            matching=query._ordered(filters)
            with self.repository.connect() as db:
                cursors={key:cursor for key,cursor in db.execute('SELECT key,first_cursor FROM opportunity_seen WHERE first_cursor>?',(min(after,notify),))}
            new=[candidate for candidate in matching if cursors.get(_key(candidate.key),0)>after]
            notices=[candidate for candidate in matching if cursors.get(_key(candidate.key),0)>notify]
            # Pending-view matching may rebuild analysis after the observe fast
            # path. A refresh can start during that work, so check again before
            # exposing any counts or previews to notification consumers.
            if self.refresh_running():return {**result,'deferred':True}
            result.update(updated=meta['cursor']>after,new_count=len(new),notify_count=len(notices),
                preview=[{'key':list(candidate.key),'name':candidate.valuation.name,'category':candidate.auction.category,
                          'profit':candidate.profit} for candidate in (notices or new)[:3]])
            return result
