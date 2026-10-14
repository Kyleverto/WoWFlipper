"""Local shopping commitments and an append-only, transactionally replayed ledger.

candidate_provider(key) returns a fresh Candidate or None, without network writes.
All money is copper. Event IDs identify purchased lots, including after corrections.
"""
import json
from dataclasses import asdict
from datetime import datetime, timezone
from .repository import encode
from .models import integer
from .engine import evaluate

# Historical labels remain accepted for immutable event replay and corrections.
STRATEGIES = {'general', 'stronger-demand', 'longer-term'}
EVENT_FIELDS = {
    'purchase': {'type', 'key', 'cost', 'note'},
    'sale': {'type', 'purchase_id', 'quantity', 'net_proceeds', 'note'},
    'fee': {'type', 'purchase_id', 'amount', 'note'},
    'balance-adjustment': {'type', 'amount', 'note'},
    'rejection': {'type', 'purchase_id', 'note'},
    'expiration': {'type', 'purchase_id', 'note'},
    'cancellation': {'type', 'purchase_id', 'note'},
    'correction': {'type', 'target_id', 'replacement', 'note'},
}


def serialize(value):
    return json.dumps(value, default=encode, sort_keys=True, separators=(',', ':'))


def auction_key(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3 or value[0] != 'us':
        raise ValueError('Expected US auction identity')
    return ['us', integer(value[1], 1), integer(value[2], 1)]


class Portfolio:
    def __init__(self, repository, candidate_provider):
        self.repository = repository
        self.candidate_provider = candidate_provider
        with repository.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS portfolio_requests (
                    request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS portfolio_selected (
                    key TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS portfolio_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS portfolio_meta (
                    key TEXT PRIMARY KEY, data TEXT NOT NULL);
            ''')

    def _settings(self, db):
        from .settings import Settings
        row = db.execute("SELECT data FROM cache WHERE key='settings'").fetchone()
        return Settings.from_dict(json.loads(row[0]) if row else {})

    def _request(self, db, request_id, payload):
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError('A request ID is required')
        row = db.execute('SELECT payload,result FROM portfolio_requests WHERE request_id=?', (request_id,)).fetchone()
        if row:
            if row[0] != serialize(payload):
                raise ValueError('Request ID reused for a different action')
            return json.loads(row[1])
        return None

    def _save_request(self, db, request_id, payload, result):
        db.execute('INSERT INTO portfolio_requests VALUES(?,?,?)', (request_id, serialize(payload), serialize(result)))
        return result

    def _events(self, db):
        return [dict(json.loads(data), id=identity) for identity, data in db.execute('SELECT id,data FROM portfolio_events ORDER BY id')]

    def _base(self, db, settings):
        row = db.execute("SELECT data FROM portfolio_meta WHERE key='initial_capital'").fetchone()
        return int(row[0]) if row else settings.capital

    def _replay(self, events, capital):
        effective = {}
        for event in events:
            if event['type'] == 'correction':
                target = event['target_id']
                if target not in effective:
                    raise ValueError('Correction target must be an earlier outcome event')
                replacement = event['replacement']
                if replacement is not None:
                    replacement = dict(replacement, id=target)
                effective[target] = replacement
            else:
                effective[event['id']] = event
        cash, realized, fees = capital, 0, 0
        lots, purchased = {}, set()
        for identity, event in effective.items():
            if event is None:
                continue
            kind = event['type']
            if kind == 'purchase':
                key = serialize(event['key'])
                if key in purchased:
                    raise ValueError('Auction already purchased')
                purchased.add(key)
                cash -= event['cost']
                lots[identity] = dict(event, remaining_quantity=event['quantity'], remaining_cost=event['cost'])
            elif kind == 'sale':
                lot = lots.get(event['purchase_id'])
                if not lot or event['quantity'] > lot['remaining_quantity']:
                    raise ValueError('Sale exceeds inventory or has no purchase')
                quantity = event['quantity']
                released = (lot['remaining_cost'] if quantity == lot['remaining_quantity'] else
                            lot['cost'] * quantity // lot['quantity'])
                lot['remaining_quantity'] -= quantity
                lot['remaining_cost'] -= released
                cash += event['net_proceeds']
                realized += event['net_proceeds'] - released
            elif kind == 'fee':
                if 'purchase_id' in event and event['purchase_id'] not in lots:
                    raise ValueError('Fee has no purchase')
                cash -= event['amount']
                fees += event['amount']
                realized -= event['amount']
            elif kind == 'balance-adjustment':
                cash += event['amount']
            elif kind in {'rejection', 'expiration', 'cancellation'}:
                if 'purchase_id' in event and event['purchase_id'] not in lots:
                    raise ValueError('Outcome has no purchase')
            else:
                raise ValueError('Unknown outcome type')
            if cash < 0:
                raise ValueError('Event chain would create negative cash')
        return {'initial_capital': capital, 'cash': cash, 'realized_profit': realized,
                'fees': fees, 'inventory': [lot for lot in lots.values() if lot['remaining_quantity']],
                'purchased_keys': purchased}

    def _selected(self, db):
        return [json.loads(row[0]) for row in db.execute('SELECT data FROM portfolio_selected ORDER BY rowid')]

    def _candidate(self, selection, settings):
        candidate = self.candidate_provider(tuple(selection['key']))
        if candidate is None or list(candidate.key) != selection['key']:
            raise ValueError('Selection is stale: auction no longer actionable')
        if evaluate(candidate.auction, candidate.valuation, settings, datetime.now(timezone.utc)) is None:
            raise ValueError('Selection is stale or excluded by current settings')
        if selection['destination'] not in settings.selling_realms:
            raise ValueError('Selling realm is no longer configured')
        if candidate.auction.buyout != selection['cost'] or candidate.auction.quantity != selection['quantity'] or candidate.auction.item_id != selection['item_id']:
            raise ValueError('Listing changed; remove and select again')
        return candidate

    def _limits(self, state, selections, settings):
        costs, copies = {}, {}
        for lot in state['inventory']:
            item = lot['item_id']
            costs[item] = costs.get(item, 0) + lot['remaining_cost']
            copies[item] = copies.get(item, 0) + lot['remaining_quantity']
        committed = 0
        for selection in selections:
            if serialize(selection['key']) in state['purchased_keys']:
                raise ValueError('Auction already purchased')
            item = selection['item_id']
            costs[item] = costs.get(item, 0) + selection['cost']
            copies[item] = copies.get(item, 0) + selection['quantity']
            committed += selection['cost']
        if any(value > settings.item_exposure for value in costs.values()):
            raise ValueError('Item exposure limit exhausted')
        if any(value > settings.max_copies for value in copies.values()):
            raise ValueError('Item quantity limit exhausted')
        if committed > state['cash'] - settings.reserve:
            raise ValueError('Cash reserve prevents commitment')
        return committed

    def select(self, candidate_key, strategy='general', destination=None, request_id=None):
        key = auction_key(candidate_key)
        payload = {'action': 'select', 'key': key, 'strategy': strategy, 'destination': destination}
        with self.repository.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = self._request(db, request_id, payload)
            if prior is not None:
                return prior
            if strategy not in STRATEGIES:
                raise ValueError('Unknown strategy')
            settings = self._settings(db)
            if db.execute('SELECT 1 FROM portfolio_selected WHERE key=?', (serialize(key),)).fetchone():
                raise ValueError('Auction already selected')
            candidate = self.candidate_provider(tuple(key))
            if candidate is None:
                raise ValueError('Auction is no longer actionable')
            selection = {'key': key, 'strategy': strategy, 'destination': destination,
                         'item_id': candidate.auction.item_id, 'quantity': candidate.auction.quantity,
                         'cost': candidate.auction.buyout, 'candidate': asdict(candidate)}
            selection = json.loads(serialize(selection))
            self._candidate(selection, settings)
            state = self._replay(self._events(db), self._base(db, settings))
            self._limits(state, self._selected(db) + [selection], settings)
            db.execute('INSERT INTO portfolio_selected VALUES(?,?)', (serialize(key), serialize(selection)))
            return self._save_request(db, request_id, payload, selection)

    def change_destination(self, candidate_key, destination, request_id):
        """Edit a selected plan without changing its quote, provenance, or ledger."""
        key = auction_key(candidate_key)
        payload = {'action': 'change_destination', 'key': key, 'destination': destination}
        with self.repository.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = self._request(db, request_id, payload)
            if prior is not None:
                return prior
            row = db.execute('SELECT data FROM portfolio_selected WHERE key=?', (serialize(key),)).fetchone()
            if row is None:
                raise ValueError('Listing must be selected before changing destination')
            settings = self._settings(db)
            if not isinstance(destination, str) or destination not in settings.selling_realms:
                raise ValueError('Destination must be a configured selling realm')
            selection = json.loads(row[0])
            selection['destination'] = destination
            # Stale plans remain editable; record() revalidates every purchase.
            db.execute('UPDATE portfolio_selected SET data=? WHERE key=?', (serialize(selection), serialize(key)))
            return self._save_request(db, request_id, payload, selection)

    def remove(self, candidate_key, request_id):
        key = auction_key(candidate_key)
        payload = {'action': 'remove', 'key': key}
        with self.repository.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = self._request(db, request_id, payload)
            if prior is not None:
                return prior
            db.execute('DELETE FROM portfolio_selected WHERE key=?', (serialize(key),))
            return self._save_request(db, request_id, payload, {'key': key, 'removed': True})

    def _normalize(self, event, effective=None):
        if not isinstance(event, dict):
            raise ValueError('Expected outcome object')
        kind = event.get('type')
        result = {'type': kind}
        if 'note' in event:
            if not isinstance(event['note'], str):
                raise ValueError('Expected note text')
            result['note'] = event['note']
        if kind == 'sale':
            result.update(purchase_id=integer(event.get('purchase_id'), 1),
                          quantity=integer(event.get('quantity'), 1), net_proceeds=integer(event.get('net_proceeds')))
        elif kind == 'fee':
            result['amount'] = integer(event.get('amount'), 1)
        elif kind == 'balance-adjustment':
            amount = event.get('amount')
            if isinstance(amount, bool) or not isinstance(amount, int) or abs(amount) > 2**53-1 or amount == 0:
                raise ValueError('Expected nonzero signed copper adjustment')
            if not result.get('note', '').strip():
                raise ValueError('Balance adjustment requires a note')
            result['amount'] = amount
        elif kind in {'rejection', 'expiration', 'cancellation'}:
            pass
        elif kind == 'purchase':
            # Only correction replay accepts a fully materialized purchase.
            if effective is None:
                raise ValueError('Purchase requires a selected listing')
            result = dict(effective)
            result.update({key: event[key] for key in ('cost', 'quantity', 'strategy', 'destination', 'note') if key in event})
            result.pop('id', None)
            result['cost'] = integer(result['cost'], 1)
            result['quantity'] = integer(result['quantity'], 1)
            if result['strategy'] not in STRATEGIES:
                raise ValueError('Unknown strategy')
            if not isinstance(result['destination'], str) or not result['destination']:
                raise ValueError('Expected destination realm')
        else:
            raise ValueError('Unknown outcome type')
        if 'purchase_id' in event and kind in {'fee', 'rejection', 'expiration', 'cancellation'}:
            result['purchase_id'] = integer(event['purchase_id'], 1)
        return result

    def record(self, event, request_id):
        payload = {'action': 'record', 'event': event}
        with self.repository.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = self._request(db, request_id, payload)
            if prior is not None:
                return prior
            settings = self._settings(db)
            events = self._events(db)
            capital = self._base(db, settings)
            state = self._replay(events, capital)
            if not isinstance(event, dict):
                raise ValueError('Expected outcome object')
            kind = event.get('type')
            if kind not in EVENT_FIELDS or set(event) - EVENT_FIELDS[kind]:
                raise ValueError('Unknown outcome fields or type')
            if kind == 'purchase':
                key = auction_key(event.get('key'))
                selections = self._selected(db)
                selected = next((s for s in selections if s['key'] == key), None)
                if selected is None:
                    raise ValueError('Select this listing before recording purchase')
                fresh = self._candidate(selected, settings)
                for selection in selections:
                    self._candidate(selection, settings)
                self._limits(state, selections, settings)
                actual_cost = integer(event.get('cost', selected['cost']), 1)
                spending = [dict(s, cost=actual_cost) if s['key'] == key else s for s in selections]
                self._limits(state, spending, settings)
                normalized = dict(selected, type='purchase', cost=actual_cost, quoted_cost=selected['cost'],
                                  candidate=json.loads(serialize(asdict(fresh))))
                if 'note' in event:
                    if not isinstance(event['note'], str):
                        raise ValueError('Expected note text')
                    normalized['note'] = event['note']
            elif kind == 'correction':
                if 'replacement' not in event:
                    raise ValueError('Correction requires explicit replacement or null')
                if 'note' in event and not isinstance(event['note'], str):
                    raise ValueError('Expected note text')
                target_id = integer(event.get('target_id'), 1)
                effective = {e['id']: e for e in events if e['type'] != 'correction'}
                for correction in events:
                    if correction['type'] == 'correction':
                        effective[correction['target_id']] = correction['replacement']
                if target_id not in effective:
                    raise ValueError('Unknown correction target')
                original = effective[target_id]
                replacement = event.get('replacement')
                if replacement is not None:
                    if original is None:
                        original = next(e for e in events if e['id'] == target_id)
                    if not isinstance(replacement, dict):
                        raise ValueError('Expected replacement object or null')
                    allowed = EVENT_FIELDS[original['type']]
                    if original['type'] == 'purchase':
                        allowed = {'type', 'cost', 'quantity', 'strategy', 'destination', 'note'}
                    if set(replacement) - allowed:
                        raise ValueError('Unknown correction replacement fields')
                    replacement = dict(original, **replacement)
                    if replacement['type'] != original['type']:
                        raise ValueError('Correction must preserve event type')
                    replacement = self._normalize(replacement, original)
                    if replacement['type'] == 'purchase' and replacement['destination'] != original['destination'] and replacement['destination'] not in settings.selling_realms:
                        raise ValueError('Replacement destination must be configured')
                normalized = {'type': 'correction', 'target_id': target_id, 'replacement': replacement}
                if isinstance(event.get('note'), str):
                    normalized['note'] = event['note']
            else:
                normalized = self._normalize(event)
            normalized['recorded_at'] = datetime.now(timezone.utc).isoformat()
            cursor = db.execute('INSERT INTO portfolio_events(data) VALUES(?)', (serialize(normalized),))
            result = dict(normalized, id=cursor.lastrowid)
            self._replay(events + [result], capital)  # Any invalid dependency rolls back the whole transaction.
            db.execute("INSERT OR IGNORE INTO portfolio_meta VALUES('initial_capital',?)", (str(capital),))
            if kind == 'purchase':
                db.execute('DELETE FROM portfolio_selected WHERE key=?', (serialize(key),))
            return self._save_request(db, request_id, payload, result)

    def shopping(self):
        with self.repository.connect() as db:
            db.execute('BEGIN')
            settings = self._settings(db)
            selections = self._selected(db)
            state = self._replay(self._events(db), self._base(db, settings))
            global_reason = None
            try:
                self._limits(state, selections, settings)
            except ValueError as exc:
                global_reason = str(exc)
            result = []
            for selection in selections:
                reason = global_reason
                try:
                    self._candidate(selection, settings)
                except ValueError as exc:
                    reason = str(exc)
                result.append(dict(selection, valid=reason is None, validation_error=reason))
            return result

    def summary(self):
        with self.repository.connect() as db:
            db.execute('BEGIN')
            settings = self._settings(db)
            state = self._replay(self._events(db), self._base(db, settings))
            selected = self._selected(db)
            purchased_keys = state.pop('purchased_keys')
            state['committed'] = sum(s['cost'] for s in selected)
            state['reserve'] = settings.reserve
            state['available_cash'] = max(0, state['cash'] - settings.reserve - state['committed'])
            state['inventory_cost'] = sum(lot['remaining_cost'] for lot in state['inventory'])
            try:
                check = dict(state, purchased_keys=purchased_keys)
                self._limits(check, selected, settings)
                state['limits_error'] = None
            except ValueError as exc:
                state['limits_error'] = str(exc)
            return state

    def history(self):
        with self.repository.connect() as db:
            return self._events(db)
