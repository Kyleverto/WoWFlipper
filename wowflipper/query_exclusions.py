"""A revision-owned, disk-paged exclusion sequence with bounded writer memory."""
from collections.abc import Sequence
from contextlib import closing
import json
import operator
from pathlib import Path
import sqlite3
import tempfile


class ExclusionStore(Sequence):
    BATCH_SIZE=256

    def __init__(self):
        self._directory=tempfile.TemporaryDirectory(prefix='wowflipper-exclusions-')
        self.path=Path(self._directory.name)/'rows.sqlite3'
        self._writer=sqlite3.connect(self.path)
        self._writer.execute('PRAGMA cache_size=-512')
        self._writer.execute('CREATE TABLE exclusions(position INTEGER PRIMARY KEY,data TEXT NOT NULL)')
        self._pending=[];self._length=0

    def append(self,row):
        if self._writer is None: raise RuntimeError('Exclusion store is sealed')
        self._pending.append((self._length,json.dumps(row,separators=(',',':'))))
        self._length+=1
        if len(self._pending)>=self.BATCH_SIZE:self._flush()

    def _flush(self):
        if self._pending:
            self._writer.executemany('INSERT INTO exclusions VALUES(?,?)',self._pending)
            self._pending.clear()

    def seal(self):
        if self._writer is not None:
            try:
                self._flush();self._writer.commit()
            finally:
                self._writer.close();self._writer=None

    def __len__(self): return self._length

    @staticmethod
    def _decode(serialized):
        row=json.loads(serialized)
        row['key']=tuple(row['key'])
        return row

    def __getitem__(self,index):
        if self._writer is not None: raise RuntimeError('Exclusion store is not sealed')
        if isinstance(index,slice):
            start,stop,step=index.indices(self._length)
            if not range(start,stop,step): return []
            if step>0:
                query='SELECT data FROM exclusions WHERE position>=? AND position<? AND (position-?) % ?=0 ORDER BY position'
                params=(start,stop,start,step)
            else:
                query='SELECT data FROM exclusions WHERE position<=? AND position>? AND (?-position) % ?=0 ORDER BY position DESC'
                params=(start,stop,start,-step)
            # Each reader owns its connection. Old request objects keep this
            # immutable store alive even after the repository cache is replaced.
            with closing(sqlite3.connect(self.path)) as db:
                return [self._decode(row[0]) for row in db.execute(query,params)]
        position=operator.index(index)
        if position<0: position+=self._length
        if not 0<=position<self._length: raise IndexError('Exclusion index outside range')
        with closing(sqlite3.connect(self.path)) as db:
            return self._decode(db.execute('SELECT data FROM exclusions WHERE position=?',(position,)).fetchone()[0])
