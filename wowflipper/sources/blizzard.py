"""Retail US Game Data adapter. No credentials leave this backend."""
import os, json, base64, time
from datetime import datetime, timezone
from dataclasses import replace
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import RLock
from .http import HTTPClient, SourceError, http_status_error, source_diagnostic
from ..models import Snapshot, normalize_auction, integer

class BlizzardClient:
    def __init__(self,http=None,client_id=None,client_secret=None,clock=time.time):
        self.http=http or HTTPClient()
        self.client_id=client_id or os.getenv('BLIZZARD_CLIENT_ID')
        self.client_secret=client_secret or os.getenv('BLIZZARD_CLIENT_SECRET')
        self.clock=clock; self._token=None; self._expires=0
        self._auth_lock=RLock()

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
        with self._auth_lock:
            if not self._token or self.clock()>=self._expires: self._authenticate()
            token=self._token
        url='https://us.api.blizzard.com'+path+'?namespace='+('static-us' if static else 'dynamic-us')+'&locale=en_US'
        for attempt in range(2):
            response=self.http.request('GET',url,{'Authorization':'Bearer '+token,**(headers or {})})
            if response.status==401 and attempt==0:
                with self._auth_lock:
                    # Another request may already have replaced the rejected token.
                    if self._token==token: self._authenticate()
                    token=self._token
                continue
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

    def realms(self,progress=None):
        houses=self.connected_realm_ids()
        data=self._json(self._get('/data/wow/realm/index'))
        if not isinstance(data.get('realms'),list) or not data['realms']: raise ValueError('Invalid realms schema')
        def discover(realm):
            detail=self._json(self._get('/data/wow/realm/'+realm['slug']))
            href=detail.get('connected_realm',{}).get('href')
            house=self._connected_house(href) if href else None
            # The us namespace includes Oceanic/Brazil/Latin America. Only the
            # official realm detail category establishes United States membership.
            return {**realm,'house_id':house,
                'category':detail.get('category'), 'locale':detail.get('locale'),
                'timezone':detail.get('timezone'),
                'is_us':detail.get('category') == 'United States',
                'is_auction_realm':house in houses,
                'is_tournament':detail.get('is_tournament',False)}
        realms=data['realms']; result=[None]*len(realms); completed=0
        with ThreadPoolExecutor(max_workers=4) as pool:
            # Keep both active requests and queued work bounded.
            for start in range(0,len(realms),4):
                futures={pool.submit(discover,realm):start+offset for offset,realm in enumerate(realms[start:start+4])}
                for future in as_completed(futures):
                    result[futures[future]]=future.result(); completed+=1
                    if progress: progress({'completed':completed,'total':len(realms),'pending':len(realms)-completed,'failed':0,'unavailable':0})
        return result

    def item(self,item_id):
        data=self._json(self._get('/data/wow/item/'+str(int(item_id)),static=True))
        category='unknown'
        class_id=data.get('item_class',{}).get('id'); subclass=data.get('item_subclass',{}).get('id')
        if class_id==9: category='recipe'
        elif class_id==15 and subclass==5: category='mount'
        elif class_id==20 and subclass==0: category='decor'
        # The item endpoint does not reliably establish toy membership or tradeability.
        binding=data.get('binding')
        binding=binding.get('type') if isinstance(binding,dict) else None
        return {**data,'category':category,'tradeability':False if binding=='ON_ACQUIRE' else True if binding in {'ON_EQUIP','ON_USE'} else None}

    def auctions(self,house_id,previous=None):
        response=self._get(f'/data/wow/connected-realm/{int(house_id)}/auctions',headers={'If-Modified-Since':previous.http_last_modified} if previous and previous.http_last_modified else None)
        now=datetime.now(timezone.utc)
        if response.status==304:
            if previous is None: raise ValueError('304 without prior snapshot')
            return replace(previous,fetched_at=now)
        return self._auction_snapshot(house_id,response)

    def auction_update(self,house_id,previous_header=None):
        """Return only changed auction data; a 304 requires no previous records."""
        response=self._get(f'/data/wow/connected-realm/{int(house_id)}/auctions',
            headers={'If-Modified-Since':previous_header} if previous_header else None)
        if response.status==304:
            if not previous_header: raise ValueError('304 without prior auction header')
            return None
        return self._auction_snapshot(house_id,response)

    def _auction_snapshot(self,house_id,response):
        now=datetime.now(timezone.utc)
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

    def toy_items(self,repository,limit=50,progress=None):
        return self._collectible_items(repository,'toy','toys','item',limit,progress)

    def decor_items(self,repository,limit=50,progress=None):
        # Decor IDs are collection identifiers; only detail items establish membership.
        return self._collectible_items(repository,'decor','decor_items','items',limit,progress)

    def _collectible_items(self,repository,kind,index_field,item_field,limit,progress=None):
        key=f'{"toys" if kind == "toy" else "decor"}:us'
        legacy=repository.cache_get('timed:'+key) or {}
        state=repository.cache_get(key) or legacy.get('value') or {}
        state={**state,'items':list(state.get('items',[])),
               'remaining':list(state.get('remaining',[])),
               'resolved':dict(state.get('resolved',{})),
               'failures':dict(state.get('failures',{})),
               'unavailable':dict(state.get('unavailable',{}))}
        fresh=repository.cache_fresh(key+':index',7*86400)
        dirty=False
        if fresh is None:
            data=self._json(self._get(f'/data/wow/{kind}/index',static=True))
            entries=data.get(index_field)
            if not isinstance(entries,list): raise ValueError(f'Invalid {kind} index')
            try: ids=sorted({integer(entry['id'],1) for entry in entries})
            except (KeyError,TypeError,ValueError): raise ValueError(f'Invalid {kind} index') from None
            # Reconsider unavailable details only when the weekly index is refreshed.
            state['remaining']=[identity for identity in ids if str(identity) not in state['resolved']]
            state['unavailable']={}
            state['failures']={identity:error for identity,error in state['failures'].items() if int(identity) in ids}
            repository.cache_timed(key+':index',{'ids':ids})
            dirty=True
        fetched=attempted=errors=0
        selected=state['remaining'][:max(0,min(integer(limit),50))]
        total=len(state['resolved'])+len(state['remaining'])+len(state['unavailable'])
        def detail(identity):
            data=self._json(self._get(f'/data/wow/{kind}/{identity}',static=True))
            try: return integer(data[item_field]['id'],1)
            except (KeyError,TypeError,ValueError): raise ValueError(f'Invalid {kind} item link') from None
        def persist():
            repository.cache_put(key,state)
            repository.cache_timed(key,state)
        def notify():
            if progress:
                progress({'completed':len(state['resolved']),'total':total,
                          'pending':len(state['remaining']),'failed':len(state['failures']),
                          'unavailable':len(state['unavailable']),'attempted':attempted})
        with ThreadPoolExecutor(max_workers=4) as pool:
            for start in range(0,len(selected),4):
                futures={pool.submit(detail,identity):identity for identity in selected[start:start+4]}
                stop_error=None
                for future in as_completed(futures):
                    identity=futures[future]; identity_key=str(identity); attempted+=1
                    state['remaining'].remove(identity)
                    try:
                        item_id=future.result()
                    except Exception as error:
                        # Keep only allowlisted diagnostics, never upstream messages or payloads.
                        diagnostic=source_diagnostic(error)
                        diagnostic.pop('error',None)
                        previous=state['failures'].get(identity_key,{})
                        diagnostic['attempts']=previous.get('attempts',0)+1
                        if isinstance(error,SourceError) and error.status==404:
                            state['unavailable'][identity_key]=diagnostic
                            state['failures'].pop(identity_key,None)
                        else:
                            state['failures'][identity_key]=diagnostic
                            # Move retries behind untouched work so a bad ID cannot starve it.
                            state['remaining'].append(identity)
                        if isinstance(error,SourceError) and error.code in {'auth_missing','auth_denied','rate_limited'}:
                            stop_error=stop_error or error
                        errors+=1
                    else:
                        state['items']=sorted(set(state['items'])|{item_id})
                        state['resolved'][identity_key]=item_id
                        state['failures'].pop(identity_key,None)
                        fetched+=1
                persist(); dirty=False
                notify()
                # Authentication and rate limits affect the entire source. Finish
                # this bounded batch so healthy results survive, then defer the
                # untouched queue instead of repeating a global failure per ID.
                if stop_error is not None: raise stop_error
        if dirty: persist()
        if not selected: notify()
        result={'items':state['items'],'pending':len(state['remaining']),
                'details_fetched':fetched,'details_attempted':attempted,
                'unavailable':len(state['unavailable']),'errors':errors}
        return result
