"""Synthetic opt-in market and explicit offline imports."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
from .repository import Repository
from .settings import Settings
from .models import Auction, Valuation, Snapshot, timestamp, integer, normalize_auction
from .sources.tsm import parse_region_report, parse_pet_report

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

def seed_coverage_demo(path):
    """Opt-in synthetic Decor and species/variant coverage; legacy demo stays stable."""
    seed_demo(path)
    repo=Repository(path)
    if repo.latest('tsm-pets:us'): return
    now=datetime.now(timezone.utc)
    item_snapshot=repo.latest('tsm:us'); auction_snapshot=repo.latest('auctions:us:1')
    decor=Valuation(245392,'Sturdy Wooden Interior Pillar',12000000,Decimal('.18'),Decimal('2.4'),12000000,12000000,now)
    pets=tuple(Valuation(82800,name,price,Decimal('.2'),Decimal('2.4'),price,price,now,pet_species_id=species)
        for species,name,price in [(39,'Chicken',10000000),(40,'Mechanical Squirrel',14000000)])
    records=list(auction_snapshot.records)
    records.append(Auction('us',1,201,245392,1,6000000,None,now,'decor',{'simple_identity':True,'tradeability':True}))
    for auction_id,species,level,buyout in [(202,39,1,4000000),(203,40,1,6000000),(204,39,25,5000000)]:
        raw={'id':auction_id,'item':{'id':82800,'pet_species_id':species,'pet_level':level,'pet_quality_id':3,'pet_breed_id':3},'quantity':1,'buyout':buyout}
        pet={'species_id':species,'level':level,'quality':3,'breed_id':3,'name':next(value.name for value in pets if value.pet_species_id==species)}
        records.append(replace(normalize_auction(raw,1,'us'),source_time=now,category='battle-pet',metadata={'simple_identity':False,'tradeability':True,'is_tradable':True,'is_battlepet':True,'pet':pet}))
    repo.save_settings(replace(repo.load_settings(),categories=('recipe','mount','toy','decor','battle-pet')))
    repo.publish(replace(item_snapshot,records=item_snapshot.records+(decor,),source_time=now,fetched_at=now,raw_payload=b'',id=None))
    repo.publish(Snapshot('tsm-pets:us',now,now,None,'offline-demo',pets))
    repo.publish(replace(auction_snapshot,records=tuple(records),source_time=now,fetched_at=now,raw_payload=b'',id=None))

def import_snapshot(repo,data):
    if set(data)-{'kind','text','source_time','house_id'}: raise ValueError('Unknown import field')
    now=datetime.now(timezone.utc); source_time=timestamp(data['source_time']) if data.get('source_time') else None
    text=data.get('text'); kind=data.get('kind')
    if not isinstance(text,str) or not text.strip(): raise ValueError('Choose a nonempty source file')
    if kind in {'tsm','tsm-pets'}:
        report=(parse_pet_report if kind=='tsm-pets' else parse_region_report)(text)
        if not report.records or report.rejected_rows: raise ValueError('CSV contains invalid or conflicting rows; inspect the required headers and values')
        # Explicit user time is required for actionable imported evidence.
        records=tuple(replace(v,updated_at=source_time) if source_time else v for v in report.records)
        key='tsm-pets:us' if kind=='tsm-pets' else 'tsm:us'
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
