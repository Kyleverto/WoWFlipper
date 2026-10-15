"""Versioned, validated personal-data backups; market evidence stays local."""
from dataclasses import fields
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
import re
import tempfile
from uuid import uuid4

from .identity import parse_pet_identity, pet_details
from .models import Auction, Candidate, Valuation, integer, timestamp, decimal
from .portfolio import Portfolio, STRATEGIES, EVENT_FIELDS, auction_key, serialize
from .settings import Settings

FORMAT = 'wowflipper-personal-backup'
VERSION = 1
# Leave room for the API restore envelope under its 16 MiB request limit.
MAX_DOCUMENT_BYTES = 15 * 1024 * 1024
CACHE_KEYS = ('settings', 'resale_overrides', 'realms')
TABLES = ('portfolio_selected', 'portfolio_events', 'portfolio_meta', 'portfolio_requests')
MODES = {'live', 'offline-demo', 'offline-import'}
SLUG = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')


def obj(value, required=(), optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError('Invalid backup object fields')
    return value


def text(value, nonempty=True):
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise ValueError('Invalid backup text')
    return value


def sequence(value):
    if not isinstance(value, list): raise ValueError('Invalid backup list')
    return value


def number(value, minimum=0):
    if type(value) is not int: raise ValueError('Invalid backup integer')
    return integer(value, minimum)


def canonical_key(value):
    key=auction_key(value)
    if not isinstance(value,list): raise ValueError('Invalid auction key')
    number(value[1],1); number(value[2],1)
    if value!=key: raise ValueError('Expected canonical auction key')
    return key


def date(value):
    text(value)
    try: timestamp(value)
    except (AttributeError, TypeError, OverflowError): raise ValueError('Invalid backup date') from None


def signed(value):
    if type(value) is not int or abs(value)>2**53-1: raise ValueError('Invalid backup amount')


class BackupService:
    def __init__(self, repository):
        self.repository = repository
        # Reuse the portfolio's idempotent schema initialization, without market access.
        self.portfolio = Portfolio(repository, lambda key: None)

    def _mode(self, db):
        row=db.execute("SELECT data FROM cache WHERE key='mode'").fetchone()
        mode=json.loads(row[0]) if row else 'live'
        if mode not in MODES: raise ValueError('Unsupported application mode')
        return mode

    def _document(self, db):
        cache={key:json.loads(data) for key,data in db.execute(
            "SELECT key,data FROM cache WHERE key IN ('settings','resale_overrides','realms')")}
        portfolio={
            'selected':[json.loads(data) for data, in db.execute('SELECT data FROM portfolio_selected ORDER BY rowid')],
            'events':[dict(json.loads(data),id=identity) for identity,data in db.execute('SELECT id,data FROM portfolio_events ORDER BY id')],
            'meta':{key:json.loads(data) for key,data in db.execute('SELECT key,data FROM portfolio_meta ORDER BY key')},
            'requests':[{'request_id':identity,'payload':json.loads(payload),'result':json.loads(result)}
                        for identity,payload,result in db.execute('SELECT request_id,payload,result FROM portfolio_requests ORDER BY request_id')]}
        return {'format':FORMAT,'version':VERSION,'created_at':datetime.now(timezone.utc).isoformat(),
                'mode':self._mode(db),'data':{'settings':cache.get('settings',Settings().to_dict()),
                'resale_overrides':cache.get('resale_overrides',{'revision':0,'items':{}}),
                'realms':cache.get('realms',[]),'portfolio':portfolio}}

    def export(self):
        with self.repository.connect() as db:
            db.execute('BEGIN')
            document=self._document(db)
        return self._validate(document)[0]

    def _candidate(self, candidate):
        required={field.name for field in fields(Candidate) if field.name not in {'assumed_unit_resale','resale_override'}}
        obj(candidate,required,{'assumed_unit_resale','resale_override'})
        key=canonical_key(candidate['key'])
        auction=obj(candidate['auction'],{field.name for field in fields(Auction) if field.name not in {'source_time','category','metadata','provenance'}},
                    {'source_time','category','metadata','provenance'})
        for name in ('house_id','auction_id','item_id','quantity','buyout'): number(auction[name],1)
        if auction['region']!='us' or key!=['us',auction['house_id'],auction['auction_id']]: raise ValueError('Invalid candidate identity')
        for name in ('variant_key','category','provenance'):
            if auction.get(name) is not None: text(auction[name],False)
        if auction.get('variant_key') is not None:
            if not isinstance(json.loads(auction['variant_key']),dict): raise ValueError('Invalid auction variant')
        pet=pet_details(Auction(**auction))
        if auction['item_id']==82800 and pet is None: raise ValueError('Invalid pet variant')
        if auction.get('source_time') is not None: date(auction['source_time'])
        if auction.get('metadata') is not None and not isinstance(auction['metadata'],dict): raise ValueError('Invalid auction metadata')
        metadata=auction.get('metadata') or {}
        if 'pet' in metadata:
            recorded_pet=obj(metadata['pet'],{'species_id','level','quality','breed_id'},{'name'})
            if pet is None or any(type(recorded_pet[name]) is not int or recorded_pet[name]!=value for name,value in pet.items()): raise ValueError('Mismatched pet metadata')
            if 'name' in recorded_pet: text(recorded_pet['name'])
        valuation=obj(candidate['valuation'],{field.name for field in fields(Valuation) if field.name!='pet_species_id'},{'pet_species_id'})
        number(valuation['item_id'],1); text(valuation['name']); date(valuation['updated_at'])
        if valuation['item_id']!=auction['item_id']: raise ValueError('Mismatched valuation item')
        for name in ('avg_sale_price','market_value','historical'): number(valuation[name])
        decimal(valuation['sale_rate'],1); decimal(valuation['sold_per_day'])
        if valuation.get('pet_species_id') is not None:
            number(valuation['pet_species_id'],1)
            if valuation['item_id']!=82800: raise ValueError('Invalid pet valuation')
        if (pet['species_id'] if pet else None)!=valuation.get('pet_species_id'): raise ValueError('Mismatched pet valuation')
        number(candidate['proceeds']); signed(candidate['profit'])
        try:
            if not Decimal(str(candidate['roi'])).is_finite(): raise ValueError('Invalid ROI')
        except InvalidOperation: raise ValueError('Invalid ROI') from None
        for name in ('destinations','risks'):
            for value in sequence(candidate[name]): text(value)
        text(candidate['explanation'],False)
        if candidate.get('assumed_unit_resale') is not None: decimal(candidate['assumed_unit_resale'])
        if candidate.get('resale_override') is not None: self._override(candidate['resale_override'])
        return key

    def _selection(self, value, purchase=False):
        required={'key','strategy','destination','item_id','quantity','cost','candidate'}
        obj(value,required,{'type','quoted_cost','note','recorded_at','id'} if purchase else ())
        key=canonical_key(value['key'])
        if key!=self._candidate(value['candidate']): raise ValueError('Mismatched selection identity')
        if value['strategy'] not in STRATEGIES: raise ValueError('Invalid strategy')
        text(value['destination'])
        if not SLUG.fullmatch(value['destination']): raise ValueError('Invalid destination realm')
        for name in ('item_id','quantity','cost'): number(value[name],1)
        if value['item_id']!=value['candidate']['auction']['item_id']: raise ValueError('Mismatched selection item')
        if 'quoted_cost' in value: number(value['quoted_cost'],1)
        if 'note' in value: text(value['note'],False)
        if 'recorded_at' in value: date(value['recorded_at'])

    def _override(self, value):
        obj(value,{'unit_price','updated_at'},{'item_id','identity','pet'})
        number(value['unit_price'],1); date(value['updated_at'])
        if 'identity' in value:
            pet=parse_pet_identity(value['identity'])
            if value.get('pet')!=pet or 'item_id' in value: raise ValueError('Invalid pet override')
        else: number(value.get('item_id'),1)

    def _event(self, event, originals, effective, replacement=False):
        kind=event.get('type') if isinstance(event,dict) else None
        if kind not in EVENT_FIELDS: raise ValueError('Invalid event type')
        if 'note' in event: text(event['note'],False)
        if kind=='purchase':
            self._selection(event,purchase=True)
            normalized=self.portfolio._normalize(event,event)
        elif kind=='correction':
            if replacement: raise ValueError('Nested correction')
            obj(event,{'type','target_id','replacement'},{'id','recorded_at','note'})
            target=number(event['target_id'],1)
            if target not in originals: raise ValueError('Invalid correction target')
            original=effective.get(target) or originals[target]
            changed=event['replacement']
            if changed is not None:
                if not isinstance(changed,dict) or changed.get('type')!=original['type']: raise ValueError('Correction changed event type')
                self._event(changed,originals,effective,True)
                if changed['type']=='purchase':
                    immutable=set(original)-{'id','cost','quantity','strategy','destination','note'}
                    if any(changed.get(name)!=original[name] for name in immutable): raise ValueError('Correction changed purchase identity')
            normalized={'type':kind,'target_id':target,'replacement':changed}
            effective[target]=changed
        else:
            obj(event,{'type'},(EVENT_FIELDS[kind]-{'type'})|{'id','recorded_at'})
            normalized=self.portfolio._normalize(event)
        if kind!='correction':
            # Reject unnormalized values while preserving all original event fields.
            if any(event.get(key)!=value for key,value in normalized.items()): raise ValueError('Invalid normalized event')
        if 'recorded_at' in event: date(event['recorded_at'])
        elif not replacement: raise ValueError('Event date is required')

    def _request(self, request, event_by_id):
        obj(request,{'request_id','payload','result'}); text(request['request_id'])
        payload=request['payload']; result=request['result']
        if not isinstance(payload,dict) or not isinstance(result,dict): raise ValueError('Invalid request data')
        action=payload.get('action')
        if action=='record':
            obj(payload,{'action','event'})
            event=payload['event']; kind=event.get('type') if isinstance(event,dict) else None
            if kind not in EVENT_FIELDS or set(event)-EVENT_FIELDS[kind]: raise ValueError('Invalid request event')
            number(result.get('id'),1)
            if 'key' in event: canonical_key(event['key'])
            if result.get('id') not in event_by_id or result!=event_by_id[result['id']]: raise ValueError('Invalid recorded request result')
        elif action in {'select','change_destination','remove'}:
            required={'action','key'}|({'strategy','destination'} if action=='select' else {'destination'} if action=='change_destination' else set())
            obj(payload,required); key=canonical_key(payload['key'])
            if action=='remove':
                obj(result,{'key','removed'})
                if result['removed'] is not True or canonical_key(result['key'])!=key: raise ValueError('Invalid removal result')
            else:
                text(payload['destination']); self._selection(result)
                if result['key']!=key or (action=='select' and payload['strategy'] not in STRATEGIES): raise ValueError('Invalid selection result')
        else: raise ValueError('Unknown request action')

    def _validate(self, document):
        # Own the parsed copy and reject NaN, custom objects and oversized payloads.
        try:
            serialized=json.dumps(document,allow_nan=False)
            if len(serialized.encode('utf-8'))>MAX_DOCUMENT_BYTES: raise ValueError('Backup exceeds supported size')
            document=json.loads(serialized)
            obj(document,{'format','version','created_at','mode','data'})
            if document['format']!=FORMAT or type(document['version']) is not int or document['version']!=VERSION: raise ValueError('Unsupported backup format or version')
            date(document['created_at'])
            with self.repository.connect() as db: current_mode=self._mode(db)
            if document['mode'] not in MODES or document['mode']!=current_mode: raise ValueError('Backup mode differs from this application')
            data=obj(document['data'],{'settings','resale_overrides','realms','portfolio'})
            settings=Settings.from_dict(data['settings'])
            overrides=obj(data['resale_overrides'],{'revision','items'})
            number(overrides['revision'])
            if not isinstance(overrides['items'],dict): raise ValueError('Invalid overrides')
            for key,value in overrides['items'].items():
                self._override(value)
                if key.startswith('pet:'):
                    if key!=value.get('identity'): raise ValueError('Invalid override identity')
                elif str(number(value.get('item_id'),1))!=key: raise ValueError('Invalid item override identity')
            slugs=set()
            for realm in sequence(data['realms']):
                obj(realm,{'slug','house_id'},{'name','id','category','locale','timezone','is_us','is_auction_realm','is_tournament','key'})
                text(realm['slug'])
                if not SLUG.fullmatch(realm['slug']) or realm['slug'] in slugs: raise ValueError('Invalid realm label')
                slugs.add(realm['slug'])
                if realm['house_id'] is not None: number(realm['house_id'],1)
                for name in ('name','category','locale','timezone'):
                    if realm.get(name) is not None: text(realm[name])
                for name in ('is_us','is_auction_realm','is_tournament'):
                    if name in realm and type(realm[name]) is not bool: raise ValueError('Invalid realm flag')
                if 'id' in realm: number(realm['id'],1)
                if 'key' in realm:
                    obj(realm['key'],{'href'}); text(realm['key']['href'])
            portfolio=obj(data['portfolio'],{'selected','events','meta','requests'})
            meta=obj(portfolio['meta'],(),{'initial_capital'})
            capital=number(meta['initial_capital'],1) if 'initial_capital' in meta else settings.capital
            originals={}; effective={}; previous_id=0
            for event in sequence(portfolio['events']):
                if not isinstance(event,dict): raise ValueError('Invalid event')
                identity=number(event.get('id'),1)
                if identity<=previous_id: raise ValueError('Event IDs must increase')
                previous_id=identity
                self._event(event,originals,effective)
                if event['type']!='correction': originals[identity]=event; effective[identity]=event
            state=self.portfolio._replay(portfolio['events'],capital)
            keys=set()
            for selection in sequence(portfolio['selected']):
                self._selection(selection); key=serialize(selection['key'])
                if key in keys or key in state['purchased_keys']: raise ValueError('Duplicate selected listing')
                keys.add(key)
            request_ids=set(); event_by_id={event['id']:event for event in portfolio['events']}
            for request in sequence(portfolio['requests']):
                self._request(request,event_by_id)
                if request['request_id'] in request_ids: raise ValueError('Duplicate request ID')
                request_ids.add(request['request_id'])
            summary={'version':VERSION,'created_at':document['created_at'],'mode':document['mode'],
                     'counts':{'selections':len(keys),'inventory':len(state['inventory']),'events':len(portfolio['events']),
                               'requests':len(request_ids),'overrides':len(overrides['items']),'realms':len(slugs)},
                     'cash':state['cash'],'initial_capital':state['initial_capital']}
            return document,summary
        except (TypeError,KeyError,AttributeError,OverflowError,RecursionError) as error:
            raise ValueError('Invalid backup data') from None

    def preview(self, document):
        return self._validate(document)[1]

    def _save_recovery(self, document):
        directory=self.repository.path/'backups'; directory.mkdir(exist_ok=True)
        filename='personal-recovery-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex+'.json'
        descriptor,temporary=tempfile.mkstemp(prefix='.recovery-',dir=directory)
        try:
            with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
                json.dump(document,stream,allow_nan=False,sort_keys=True,separators=(',',':'))
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary,directory/filename)
            # Windows supports file fsync and replace, but cannot open a directory this way.
            if os.name!='nt':
                directory_fd=os.open(directory,os.O_RDONLY)
                try: os.fsync(directory_fd)
                finally: os.close(directory_fd)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
        return filename

    def restore(self, document):
        document,summary=self._validate(document)
        with self.repository.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self._mode(db)!=document['mode']: raise ValueError('Backup mode differs from this application')
            # The automatic recovery must itself pass the same portable import contract.
            recovery_document,_=self._validate(self._document(db))
            recovery=self._save_recovery(recovery_document)
            data=document['data']; portfolio=data['portfolio']
            for key in CACHE_KEYS:
                db.execute('INSERT OR REPLACE INTO cache VALUES(?,?)',(key,serialize(data[key])))
            for table in TABLES: db.execute('DELETE FROM '+table)
            db.executemany('INSERT INTO portfolio_selected VALUES(?,?)',[(serialize(value['key']),serialize(value)) for value in portfolio['selected']])
            db.executemany('INSERT INTO portfolio_events(id,data) VALUES(?,?)',[(value['id'],serialize({key:item for key,item in value.items() if key!='id'})) for value in portfolio['events']])
            db.executemany('INSERT INTO portfolio_meta VALUES(?,?)',[(key,serialize(value)) for key,value in portfolio['meta'].items()])
            db.executemany('INSERT INTO portfolio_requests VALUES(?,?,?)',[(value['request_id'],serialize(value['payload']),serialize(value['result'])) for value in portfolio['requests']])
            db.execute('INSERT OR REPLACE INTO cache VALUES(?,?)',('data_epoch',serialize(uuid4().hex)))
        with self.repository._analysis_lock: self.repository._analysis_cache=None
        return {'recovery_backup':recovery,'summary':summary}
