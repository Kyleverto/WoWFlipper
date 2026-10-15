"""Retail US Game Data adapter. No credentials leave this backend."""
import os, json, base64, time
from datetime import datetime, timezone
from dataclasses import replace
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from .http import HTTPClient, SourceError, http_status_error
from ..models import Snapshot, normalize_auction, integer

class BlizzardClient:
    def __init__(self,http=None,client_id=None,client_secret=None,clock=time.time):
        self.http=http or HTTPClient()
        self.client_id=client_id or os.getenv('BLIZZARD_CLIENT_ID')
        self.client_secret=client_secret or os.getenv('BLIZZARD_CLIENT_SECRET')
        self.clock=clock; self._token=None; self._expires=0

    def _authenticate(self):
        if not self.client_id or not self.client_secret: raise SourceError('Blizzard credentials are not configured', code='auth_missing')
        basic=base64.b64encode(f'{self.client_id}:{self.client_secret}'.encode()).decode()
        response=self.http.request('POST','https://oauth.battle.net/token',{'Authorization':'Basic '+basic,'Content-Type':'application/x-www-form-urlencoded'},b'grant_type=client_credentials')
        if response.status!=200: raise http_status_error(response.status)
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
            if response.status not in {200,304}: raise http_status_error(response.status)
            return response

    @staticmethod
    def _connected_house(href):
        if not isinstance(href,str): raise ValueError('Invalid connected realm link')
        url=urlsplit(href)
        prefix='/data/wow/connected-realm/'
        value=url.path.removeprefix(prefix)
        if url.scheme!='https' or url.hostname!='us.api.blizzard.com' or not url.path.startswith(prefix) or not value.isdigit():
            raise ValueError('Invalid connected realm link')
        return integer(int(value),1)

    def connected_realm_ids(self):
        # The broader realm index contains internal account/instance realms.
        # Membership in the canonical connected-realm index establishes support
        # for connected auction houses independently of realm names or status.
        index=self._json(self._get('/data/wow/connected-realm/index'))
        links=index.get('connected_realms')
        if not isinstance(links,list) or not links: raise ValueError('Invalid connected realm index')
        try: houses={self._connected_house(link['href']) for link in links}
        except (KeyError,TypeError): raise ValueError('Invalid connected realm index') from None
        return houses

    def realms(self):
        houses=self.connected_realm_ids()
        data=self._json(self._get('/data/wow/realm/index'))
        if not isinstance(data.get('realms'),list) or not data['realms']: raise ValueError('Invalid realms schema')
        result=[]
        for realm in data['realms']:
            detail=self._json(self._get('/data/wow/realm/'+realm['slug']))
            href=detail.get('connected_realm',{}).get('href')
            house=self._connected_house(href) if href else None
            # The us namespace includes Oceanic/Brazil/Latin America. Only the
            # official realm detail category establishes United States membership.
            result.append({**realm,'house_id':house,
                'category':detail.get('category'), 'locale':detail.get('locale'),
                'timezone':detail.get('timezone'),
                'is_us':detail.get('category') == 'United States',
                'is_auction_realm':house in houses,
                'is_tournament':detail.get('is_tournament',False)})
        return result

    def item(self,item_id):
        data=self._json(self._get('/data/wow/item/'+str(int(item_id)),static=True))
        category='unknown'
        class_id=data.get('item_class',{}).get('id'); subclass=data.get('item_subclass',{}).get('id')
        if class_id==9: category='recipe'
        elif class_id==15 and subclass==5: category='mount'
        elif class_id==20 and subclass==0: category='decor'
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

    def pet(self,species_id):
        species_id=integer(species_id,1)
        data=self._json(self._get(f'/data/wow/pet/{species_id}',static=True))
        if data.get('id') != species_id or not isinstance(data.get('name'),str) or not data['name'].strip():
            raise ValueError('Invalid pet species metadata')
        if not isinstance(data.get('is_tradable'),bool) or not isinstance(data.get('is_battlepet'),bool):
            raise ValueError('Invalid pet species tradeability')
        return {**data,'category':'battle-pet','tradeability':data['is_tradable'] and data['is_battlepet']}

    def toy_items(self,repository,limit=50):
        return self._collectible_items(repository,'toy','toys','item',limit)

    def decor_items(self,repository,limit=50):
        # Decor IDs are collection identifiers. Only the plural items object in
        # a decor detail establishes which auction item belongs to the index.
        return self._collectible_items(repository,'decor','decor_items','items',limit)

    def _collectible_items(self,repository,kind,index_field,item_field,limit):
        key=f'{"toys" if kind == "toy" else "decor"}:us'
        legacy=repository.cache_get('timed:'+key) or {}
        state=repository.cache_get(key) or legacy.get('value')
        fresh=repository.cache_fresh(key+':index',7*86400)
        if fresh is None:
            data=self._json(self._get(f'/data/wow/{kind}/index',static=True))
            entries=data.get(index_field)
            if not isinstance(entries,list): raise ValueError(f'Invalid {kind} index')
            try: ids=sorted({integer(entry['id'],1) for entry in entries})
            except (KeyError,TypeError,ValueError): raise ValueError(f'Invalid {kind} index') from None
            state=state or {'items':[],'remaining':[],'resolved':{}}
            resolved=state.get('resolved',{})
            state={**state,'remaining':[identity for identity in ids if str(identity) not in resolved]}
            repository.cache_put(key,state)
            repository.cache_timed(key+':index',{'ids':ids})
        state=state or {'items':[],'remaining':[],'resolved':{}}
        fetched=0
        for identity in state['remaining'][:max(0,min(integer(limit),50))]:
            data=self._json(self._get(f'/data/wow/{kind}/{identity}',static=True))
            try: item_id=integer(data[item_field]['id'],1)
            except (KeyError,TypeError,ValueError): raise ValueError(f'Invalid {kind} item link') from None
            state['items']=sorted(set(state['items'])|{item_id})
            state.setdefault('resolved',{})[str(identity)]=item_id
            state['remaining'].remove(identity)
            repository.cache_put(key,state)
            repository.cache_timed(key,state)
            fetched+=1
        # Preserve the legacy toy return shape for existing adapter callers.
        result={'items':state['items'],'pending':len(state['remaining'])}
        if kind == 'decor': result['details_fetched']=fetched
        return result
