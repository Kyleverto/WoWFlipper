"""Transactional normalized snapshots and atomically published raw payloads."""
import sqlite3, json, hashlib, os, tempfile, threading, time
from pathlib import Path
from dataclasses import asdict, replace
from datetime import datetime
from decimal import Decimal
from .models import Snapshot, Auction, Valuation, integer, timestamp, decimal
from .settings import Settings

def encode(value):
    if isinstance(value,(datetime,Decimal)): return str(value)
    raise TypeError('Unsupported persisted value')

class Repository:
    def __init__(self,path):
        self._publish_lock=threading.Lock()
        self._analysis_lock=threading.Lock()
        self._analysis_cache=None
        self.maintenance_warning=None
        self.path=Path(path); self.path.mkdir(parents=True,exist_ok=True)
        self.payloads=self.path/'payloads'; self.payloads.mkdir(exist_ok=True)
        with self.connect() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY, source TEXT, data TEXT, payload TEXT); CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, data TEXT); CREATE TABLE IF NOT EXISTS provenance(id INTEGER PRIMARY KEY, source TEXT, data TEXT); CREATE INDEX IF NOT EXISTS snapshots_source_id ON snapshots(source,id);')
    def connect(self): return sqlite3.connect(self.path/'market.sqlite3',timeout=10)
    def publish(self,snapshot):
        with self._publish_lock: return self._publish(snapshot)
    def _publish(self,snapshot):
        if not isinstance(snapshot,Snapshot) or not snapshot.source_key or any(not isinstance(r,(Auction,Valuation)) for r in snapshot.records): raise ValueError('Invalid snapshot')
        timestamp(snapshot.fetched_at)
        if snapshot.source_time is not None: timestamp(snapshot.source_time)
        for record in snapshot.records:
            integer(record.item_id,1)
            if isinstance(record,Auction):
                for value in (record.auction_id,record.house_id,record.quantity,record.buyout): integer(value,1)
                if record.region!='us': raise ValueError('Unsupported region')
            else:
                for value in (record.avg_sale_price,record.market_value,record.historical): integer(value)
                decimal(record.sale_rate,1); decimal(record.sold_per_day)
                if not isinstance(record.name,str) or not record.name.strip(): raise ValueError('Invalid valuation name')
                timestamp(record.updated_at)
        if snapshot.records and any(type(r)!=type(snapshot.records[0]) for r in snapshot.records): raise ValueError('Mixed records')
        data=asdict(snapshot); data.pop('raw_payload'); data.pop('id')
        data['record_type']='auction' if snapshot.records and isinstance(snapshot.records[0],Auction) else 'valuation'
        payload=snapshot.raw_payload or json.dumps(data,default=encode).encode()
        digest=hashlib.sha256(payload).hexdigest(); data['content_hash']=digest
        serialized=json.dumps(data,default=encode)
        filename=digest+'.json'; target=self.payloads/filename
        if not target.exists():
            fd,temp=tempfile.mkstemp(dir=self.payloads)
            try:
                with os.fdopen(fd,'wb') as stream: stream.write(payload); stream.flush(); os.fsync(stream.fileno())
                os.replace(temp,target)
            finally:
                if os.path.exists(temp): os.unlink(temp)
        with self.connect() as db:
            cursor=db.execute('INSERT INTO snapshots(source,data,payload) VALUES(?,?,?)',(snapshot.source_key,serialized,filename)); identity=cursor.lastrowid
            compact={k:v for k,v in data.items() if k!='records'}
            db.execute('INSERT INTO provenance VALUES(?,?,?)',(identity,snapshot.source_key,json.dumps(compact,default=encode)))
            db.execute('DELETE FROM snapshots WHERE source=? AND id NOT IN (SELECT id FROM snapshots WHERE source=? ORDER BY id DESC LIMIT 2)',(snapshot.source_key,snapshot.source_key))
        self.maintenance_warning=None
        try: self.cleanup()
        except (OSError,sqlite3.Error) as exc: self.maintenance_warning=type(exc).__name__
        return identity
    def cleanup(self):
        with self.connect() as db: referenced={r[0] for r in db.execute('SELECT payload FROM snapshots')}
        for file in self.payloads.glob('*.json'):
            if file.name not in referenced: file.unlink(missing_ok=True)
    def latest(self,source_key, *, include_payload=True):
        with self.connect() as db: row=db.execute('SELECT id,data,payload FROM snapshots WHERE source=? ORDER BY id DESC LIMIT 1',(source_key,)).fetchone()
        if row is None: return None
        identity,serialized,payload=row; data=json.loads(serialized); record_type=data.pop('record_type')
        for key in ('source_time','fetched_at'):
            if data[key]: data[key]=datetime.fromisoformat(data[key])
        records=[]
        for record in data.pop('records'):
            if record_type=='auction':
                if record['source_time']: record['source_time']=datetime.fromisoformat(record['source_time'])
                records.append(Auction(**record))
            else:
                record['updated_at']=datetime.fromisoformat(record['updated_at'])
                for key in ('sale_rate','sold_per_day'): record[key]=Decimal(record[key])
                records.append(Valuation(**record))
        return Snapshot(**data,records=tuple(records),id=identity,raw_payload=(self.payloads/payload).read_bytes() if include_payload else b'')
    def analysis_revision(self):
        """Read small invalidation tokens in one consistent SQLite transaction."""
        with self.connect() as db:
            db.execute('BEGIN')
            settings=tuple(db.execute("SELECT key,data FROM cache WHERE key IN ('settings','realms') ORDER BY key"))
            snapshots=tuple(db.execute('SELECT source,MAX(id) FROM snapshots GROUP BY source ORDER BY source'))
        return settings,snapshots

    def latest_metadata(self):
        """Return source status without decoding auction records or raw payloads."""
        with self.connect() as db:
            rows=db.execute('SELECT s.source,s.id,p.data FROM snapshots s JOIN provenance p ON p.id=s.id WHERE s.id=(SELECT MAX(x.id) FROM snapshots x WHERE x.source=s.source) ORDER BY s.source').fetchall()
        result=[]
        for source,identity,serialized in rows:
            data=json.loads(serialized)
            result.append({'source':source,'snapshot_id':identity,**{key:data[key] for key in ('source_time','fetched_at','mode')}})
        return result

    def cache_get(self,key):
        with self.connect() as db: row=db.execute('SELECT data FROM cache WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else None
    def cache_put(self,key,value):
        with self.connect() as db: db.execute('INSERT OR REPLACE INTO cache VALUES(?,?)',(key,json.dumps(value,default=encode)))
    def save_settings(self,settings): self.cache_put('settings',settings.to_dict())
    def load_settings(self): return Settings.from_dict(self.cache_get('settings') or {})

    def cache_fresh(self,key,max_age_seconds):
        entry=self.cache_get('timed:'+key)
        if not isinstance(entry,dict) or time.time()-entry.get('cached_at',0)>=max_age_seconds: return None
        return entry['value']
    def cache_timed(self,key,value):
        self.cache_put('timed:'+key,{'cached_at':time.time(),'value':value})
