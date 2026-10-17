"""Synthetic memory profile; never opens the user's market database or network.

Run from a checkout with PYTHONPATH=. python scripts/profile_memory.py --houses 24 --rows 2000.
Only 1% of generated listings are eligible. Values describe this fixture, not a
promised memory ceiling for live auction payloads or large eligible inventories.
"""
import argparse
import sys,tracemalloc,json
try:
 import resource
except ImportError:
 resource=None
from tempfile import TemporaryDirectory
from datetime import datetime,timezone
from dataclasses import replace
from decimal import Decimal
from wowflipper.repository import Repository
from wowflipper.models import Auction,Snapshot,Valuation
from wowflipper.refresh import RefreshService
from wowflipper.settings import Settings
from wowflipper.query import MarketQuery
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--houses',type=int,default=24)
parser.add_argument('--rows',type=int,default=2000)
args=parser.parse_args()
if args.houses<1 or args.rows<1:parser.error('houses and rows must be positive')
houses=args.houses;rows=args.rows;now=datetime.now(timezone.utc)
with TemporaryDirectory() as path:
 r=Repository(path);realms=[{'slug':f'h{i}','house_id':i,'category':'United States','is_auction_realm':True} for i in range(1,houses+1)]
 r.cache_put('realms',realms);r.cache_timed('realms',realms)
 settings=replace(Settings(),buying_realms=tuple(x['slug'] for x in realms),selling_realms=('h1',),categories=('recipe',))
 r.save_settings(settings);r.cache_timed('item:us:2',{'category':'recipe','tradeability':True})
 r.publish(Snapshot('tsm:us',now,now,'tsm','fixture',(Valuation(2,'Recipe',10000000,Decimal('.5'),Decimal('5'),10000000,10000000,now),)))
 def snapshot(house):
  records=tuple(Auction('us',house,house*1000000+j,2 if j%100==0 else 3,1,1000,None,now,'recipe' if j%100==0 else 'unknown',{'simple_identity':True,'tradeability':True}) for j in range(rows))
  return Snapshot(f'auctions:us:{house}',now,now,'header','fixture',records,raw_payload=b'raw'+str(house).encode())
 for house in range(1,houses+1):r.publish(snapshot(house))
 class Client:
  def auctions(self,house,previous):return snapshot(house)
  def item(self,item):return {'category':'recipe','tradeability':True}
 class TSM:
  def region(self,previous):return previous
 tracemalloc.start();q=MarketQuery(r);query_memory=tracemalloc.get_traced_memory();tracemalloc.reset_peak()
 service=RefreshService(r,Client(),TSM());service.run(settings);current,peak=tracemalloc.get_traced_memory()
 print(json.dumps({'houses':houses,'rows_per_house':rows,'query_current_mib':round(query_memory[0]/2**20,2),'query_peak_mib':round(query_memory[1]/2**20,2),'refresh_peak_mib':round(peak/2**20,2),'rss_peak_mib':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(2**20 if sys.platform=='darwin' else 1024),2) if resource else None}))
