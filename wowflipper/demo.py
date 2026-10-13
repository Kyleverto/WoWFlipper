"""Synthetic opt-in market and explicit offline imports."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
from .repository import Repository
from .settings import Settings
from .models import Auction, Valuation, Snapshot, timestamp, integer, normalize_auction
from .sources.tsm import parse_region_report

def seed_demo(path):
    repo=Repository(path)
    repo.cache_put('mode','offline-demo')
    if repo.latest('tsm:us'): return
    now=datetime.now(timezone.utc)
    repo.cache_put('realms',[{'slug':'agamaggan','name':'Agamaggan','house_id':1}])
    repo.save_settings(replace(Settings(),selling_realms=('agamaggan',),max_copies=20))
    values=[]; auctions=[]
    for i,(name,category,quantity,buyout,price,rate) in enumerate([('Clockwork Picnic Basket','toy',3,3000000,2000000,'0.28'),('Pattern: Azure Travel Pack','recipe',1,8000000,12000000,'0.12'),('Copperwing Harness','mount',1,15000000,22000000,'0.06')],1):
        values.append(Valuation(900000+i,name,price,Decimal(rate),Decimal('2.4'),price,price,now))
        auctions.append(Auction('us',1,100+i,900000+i,quantity,buyout,None,now,category,{'simple_identity':True,'tradeability':'tradeable'}))
    repo.publish(Snapshot('tsm:us',now,now,None,'offline-demo',tuple(values)))
    repo.publish(Snapshot('auctions:us:1',now,now,None,'offline-demo',tuple(auctions)))

def import_snapshot(repo,data):
    if set(data)-{'kind','text','source_time','house_id'}: raise ValueError('Unknown import field')
    now=datetime.now(timezone.utc); source_time=timestamp(data['source_time']) if data.get('source_time') else None
    text=data.get('text'); kind=data.get('kind')
    if not isinstance(text,str) or not text.strip(): raise ValueError('Choose a nonempty source file')
    if kind=='tsm':
        report=parse_region_report(text)
        if not report.records or report.rejected_rows: raise ValueError('CSV contains invalid or conflicting rows; inspect the required headers and values')
        # Explicit user time is required for actionable imported evidence.
        records=tuple(replace(v,updated_at=source_time) if source_time else v for v in report.records)
        key='tsm:us'
    elif kind=='auctions':
        house=integer(data.get('house_id'),1); payload=json.loads(text)
        if not isinstance(payload,dict) or not isinstance(payload.get('auctions'),list) or not payload['auctions']: raise ValueError('Expected auction JSON with a nonempty auctions list')
        records=[]
        for raw in payload['auctions']:
            auction=normalize_auction(raw,house,'us')
            if not auction: raise ValueError('Auction contains invalid identity, quantity or total buyout')
            metadata=raw.get('metadata')
            if metadata is not None and not isinstance(metadata,dict): raise ValueError('Invalid metadata')
            records.append(replace(auction,source_time=source_time,category=raw.get('category'),metadata=metadata))
        records=tuple(records); key=f'auctions:us:{house}'
    else: raise ValueError('Choose regional CSV or auction JSON')
    identity=repo.publish(Snapshot(key,source_time,now,None,'offline-import',records,raw_payload=text.encode(),source_metadata={'offline':True,'user_supplied_source_time':bool(source_time)}))
    return {'snapshot_id':identity,'source':key,'records':len(records),'mode':'offline-import','warning':'Missing source time excludes actionable opportunities' if not source_time else 'Offline imported evidence; verify in game'}
