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


class TSMClient:
    """Official public retail regional CSV; scan time comes from updatedAt."""
    URL='https://public-data.tradeskillmaster.com/retail/us/region/items.csv'
    def __init__(self,http=None):
        from .http import HTTPClient
        self.http=http or HTTPClient()
    def region(self,previous=None):
        from datetime import datetime, timezone
        from dataclasses import replace
        from ..models import Snapshot
        from .http import http_status_error
        headers={'If-Modified-Since':previous.http_last_modified} if previous and previous.http_last_modified else {}
        response=self.http.request('GET',self.URL,headers)
        now=datetime.now(timezone.utc)
        if response.status==304:
            if previous is None: raise ValueError('304 without prior TSM snapshot')
            return replace(previous,fetched_at=now)
        if response.status!=200: raise http_status_error(response.status)
        try: report=parse_region_report(response.body.decode('utf-8-sig'))
        except UnicodeError: raise ValueError('Invalid TSM text encoding') from None
        if not report.records: raise ValueError('Invalid TSM regional rows')
        times={record.updated_at for record in report.records}
        if len(times)!=1: raise ValueError('Inconsistent TSM scan timestamps')
        modified=next((v for k,v in response.headers.items() if k.lower()=='last-modified'),None)
        return Snapshot('tsm:us',next(iter(times)),now,modified,'tsm',tuple(report.records),raw_payload=response.body,source_metadata={'accepted_rows':len(report.records),'rejected_rows':report.rejected_rows})
