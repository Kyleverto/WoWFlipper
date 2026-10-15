"""Immutable domain data and validation shared by source adapters."""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import json

MAX_SAFE_INTEGER = 2**53 - 1

def integer(value, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError('Expected integral value')
    if isinstance(value, str) and not value.strip().isdigit():
        raise ValueError('Expected integral value')
    result = int(value)
    if not minimum <= result <= MAX_SAFE_INTEGER:
        raise ValueError('Integer outside supported bounds')
    return result

def decimal(value, maximum=None):
    if isinstance(value, bool):
        raise ValueError('Expected finite decimal')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('Expected finite decimal') from None
    if not result.is_finite() or result < 0 or (maximum is not None and result > maximum):
        raise ValueError('Decimal outside supported bounds')
    return result

def timestamp(value, now=None):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Timestamp requires timezone')
    result = result.astimezone(timezone.utc)
    if result > (now or datetime.now(timezone.utc)) + timedelta(minutes=5):
        raise ValueError('Timestamp is in future')
    return result

def is_fresh(source_time, max_age_hours, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        time = timestamp(source_time, now)
        return now - time <= timedelta(hours=float(max_age_hours))
    except (ValueError, TypeError, AttributeError, OverflowError):
        return False

@dataclass(frozen=True)
class Valuation:
    item_id: int
    name: str
    avg_sale_price: int
    sale_rate: Decimal
    sold_per_day: Decimal
    market_value: int
    historical: int
    updated_at: datetime
    pet_species_id: int | None = None

@dataclass(frozen=True)
class Auction:
    region: str
    house_id: int
    auction_id: int
    item_id: int
    quantity: int
    buyout: int
    variant_key: str | None
    source_time: datetime | None = None
    category: str | None = None
    metadata: dict | None = None
    provenance: str | None = None

@dataclass(frozen=True)
class Snapshot:
    source_key: str
    source_time: datetime | None
    fetched_at: datetime
    http_last_modified: str | None
    mode: str
    records: tuple
    content_hash: str = ''
    source_id: str = ''
    id: int | None = None
    raw_payload: bytes = b''
    source_metadata: dict | None = None

@dataclass(frozen=True)
class Candidate:
    key: tuple
    auction: Auction
    valuation: Valuation
    proceeds: int
    profit: int
    roi: Decimal
    destinations: tuple
    risks: tuple
    explanation: str
    assumed_unit_resale: Decimal | None = None
    resale_override: dict | None = None

def normalize_auction(data: dict, house_id: int, region: str) -> Auction | None:
    try:
        item = data['item']
        if region != 'us':
            raise ValueError('Only retail US is supported')
        variants = {key: value for key, value in item.items() if key != 'id'}
        return Auction(region, integer(house_id, 1), integer(data['id'], 1),
                       integer(item['id'], 1), integer(data['quantity'], 1),
                       integer(data['buyout'], 1),
                       json.dumps(variants, sort_keys=True, separators=(',', ':')) if variants else None)
    except (KeyError, ValueError, TypeError, AttributeError):
        return None
