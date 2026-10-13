"""Strict regional CSV parsing; prices are already integer copper."""
import csv
import io
from dataclasses import dataclass
from ..models import Valuation, integer, decimal, timestamp

REQUIRED = ('itemId','name','marketValue','historical','avgSalePrice','saleRate','soldPerDay','updatedAt')

@dataclass(frozen=True)
class ParseReport:
    records: list[Valuation]
    rejected_rows: int

def parse_region_report(text: str) -> ParseReport:
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    headers = reader.fieldnames or []
    if not set(REQUIRED).issubset(headers) or len(headers) != len(set(headers)):
        raise ValueError('Missing or duplicate regional CSV headers')
    records = {}
    counts = {}
    conflicts = set()
    rejected = 0
    for row in reader:
        try:
            if None in row or any(row.get(key) is None for key in REQUIRED):
                raise ValueError('Malformed row')
            record = Valuation(integer(row['itemId'],1), row['name'],
                               integer(row['avgSalePrice']), decimal(row['saleRate'],1),
                               decimal(row['soldPerDay']), integer(row['marketValue']),
                               integer(row['historical']), timestamp(row['updatedAt']))
            if not record.name.strip():
                raise ValueError('Missing name')
            item = record.item_id
            counts[item] = counts.get(item,0) + 1
            if item in conflicts:
                rejected += 1
            elif item in records and records[item] != record:
                conflicts.add(item)
                rejected += counts[item]
                del records[item]
            else:
                records[item] = record
        except (ValueError, TypeError, AttributeError, OverflowError):
            rejected += 1
    return ParseReport(list(records.values()), rejected)

def parse_region(text: str) -> list[Valuation]:
    return parse_region_report(text).records
