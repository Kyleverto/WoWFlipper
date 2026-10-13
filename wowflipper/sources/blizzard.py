"""Retail US Game Data adapter. No credentials leave this backend."""
import os, json, base64, time
from datetime import datetime, timezone
from dataclasses import replace
from email.utils import parsedate_to_datetime
from .http import HTTPClient, SourceError
from ..models import Snapshot, normalize_auction, integer

class BlizzardClient:
    def __init__(self,http=None,client_id=None,client_secret=None,clock=time.time):
        self.http=http or HTTPClient()
        self.client_id=client_id or os.getenv('BLIZZARD_CLIENT_ID')
        self.client_secret=client_secret or os.getenv('BLIZZARD_CLIENT_SECRET')
        self.clock=clock; self._token=None; self._expires=0

    def _authenticate(self):
        if not self.client_id or not self.client_secret: raise SourceError('Blizzard credentials are not configured')
        basic=base64.b64encode(f'{self.client_id}:{self.client_secret}'.encode()).decode()
        response=self.http.request('POST','https://oauth.battle.net/token',{'Authorization':'Basic '+basic,'Content-Type':'application/x-www-form-urlencoded'},b'grant_type=client_credentials')
        if response.status!=200: raise SourceError('Blizzard authentication failed')
        data=self._json(response)
        if not isinstance(data.get('access_token'),str) or not isinstance(data.get('expires_in'),int) or data['expires_in']<=0: raise ValueError('Invalid token schema')
        self._token=data['access_token']; self._expires=self.clock()+data['expires_in']-min(30,data['expires_in']/10)

    @staticmethod
    def _json(response):
        try: data=json.loads(response.body)
        except (ValueError,UnicodeError): raise ValueError('Invalid upstream JSON') from None
        if not isinstance(data,dict): raise ValueError('Invalid upstream schema')
        return data

    def _get(self,path,static=False,headers=None):
        if not self._token or self.clock()>=self._expires: self._authenticate()
        url='https://us.api.blizzard.com'+path+'?namespace='+('static-us' if static else 'dynamic-us')+'&locale=en_US'
        for attempt in range(2):
            response=self.http.request('GET',url,{'Authorization':'Bearer '+self._token,**(headers or {})})
            if response.status==401 and attempt==0: self._authenticate(); continue
            if response.status not in {200,304}: raise SourceError(f'Blizzard request failed ({response.status})')
            return response

    def realms(self):
        data=self._json(self._get('/data/wow/realm/index'))
        if not isinstance(data.get('realms'),list): raise ValueError('Invalid realms schema')
        result=[]
        for realm in data['realms']:
            detail=self._json(self._get('/data/wow/realm/'+realm['slug']))
            href=detail.get('connected_realm',{}).get('href','')
            try: house=int(href.split('?')[0].rstrip('/').split('/')[-1])
            except ValueError: raise ValueError('Invalid connected realm') from None
            result.append({**realm,'house_id':house})
        return result

    def item(self,item_id):
        data=self._json(self._get('/data/wow/item/'+str(int(item_id)),static=True))
        category='unknown'
        class_id=data.get('item_class',{}).get('id'); subclass=data.get('item_subclass',{}).get('id')
        if class_id==9: category='recipe'
        elif class_id==15 and subclass==5: category='mount'
        # The item endpoint does not reliably establish toy membership or tradeability.
        binding=data.get('binding',{}).get('type')
        return {**data,'category':category,'tradeability':False if binding=='ON_ACQUIRE' else True if binding in {'ON_EQUIP','ON_USE'} else None}

    def auctions(self,house_id,previous=None):
        response=self._get(f'/data/wow/connected-realm/{int(house_id)}/auctions',headers={'If-Modified-Since':previous.http_last_modified} if previous and previous.http_last_modified else None)
        now=datetime.now(timezone.utc)
        if response.status==304:
            if previous is None: raise ValueError('304 without prior snapshot')
            return replace(previous,fetched_at=now)
        data=self._json(response)
        if not isinstance(data.get('auctions'),list): raise ValueError('Invalid auctions schema')
        modified=next((v for k,v in response.headers.items() if k.lower()=='last-modified'),None)
        source_time=None
        if modified:
            try:
                source_time=parsedate_to_datetime(modified)
                if source_time.tzinfo is None or source_time>now: source_time=None
            except (TypeError,ValueError): pass
        records=[]
        for entry in data['auctions']:
            if not isinstance(entry,dict) or not isinstance(entry.get('item'),dict): raise ValueError('Invalid auction record')
            try:
                integer(entry['id'],1); integer(entry['item']['id'],1); integer(entry['quantity'],1)
                buyout=integer(entry.get('buyout',0))
                if buyout==0:
                    integer(entry['bid'],1)
                    continue
            except (KeyError,ValueError,TypeError): raise ValueError('Invalid auction record') from None
            record=normalize_auction(entry,house_id,'us')
            if record is None: raise ValueError('Invalid auction record')
            if record is not None: records.append(replace(record,source_time=source_time))
        return Snapshot(f'auctions:us:{house_id}',source_time,now,modified,'blizzard',tuple(records),raw_payload=response.body)

    def toy_items(self,repository,limit=50):
        """Incrementally resolve toy IDs through their item links, never equate IDs."""
        state=repository.cache_fresh('toys:us',7*86400)
        if state is None:
            data=self._json(self._get('/data/wow/toy/index',static=True))
            if not isinstance(data.get('toys'),list): raise ValueError('Invalid toy index')
            state={'items':[],'remaining':[integer(toy['id'],1) for toy in data['toys']]}
            repository.cache_timed('toys:us',state)
        for toy_id in state['remaining'][:limit]:
            data=self._json(self._get(f'/data/wow/toy/{toy_id}',static=True))
            try: item_id=integer(data['item']['id'],1)
            except (KeyError,TypeError,ValueError): raise ValueError('Invalid toy item link') from None
            state['items']=sorted(set(state['items'])|{item_id})
            state['remaining'].remove(toy_id)
            repository.cache_timed('toys:us',state)
        return {'items':state['items'],'pending':len(state['remaining'])}
