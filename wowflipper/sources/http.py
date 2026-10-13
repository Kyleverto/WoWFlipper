"""Bounded HTTPS transport with injectable fixtures and conservative retries."""
from dataclasses import dataclass
from urllib.parse import urlsplit
import http.client
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

@dataclass(frozen=True)
class Response:
    status: int
    headers: dict
    body: bytes

ERROR_CODES=frozenset({'source_error','auth_missing','auth_denied','unavailable','rate_limited','upstream_error','network_error','invalid_schema'})

class SourceError(RuntimeError):
    def __init__(self, message='Upstream source failed', *, code='source_error', status=None):
        super().__init__(message)
        self.code=code if code in ERROR_CODES else 'source_error'
        self.status=status if type(status) is int and 100<=status<=599 else None

class Deferred(SourceError): pass

def http_status_error(status):
    code=('auth_denied' if status in {401,403} else 'unavailable' if status==404 else
          'rate_limited' if status==429 else 'upstream_error' if 500<=status<600 else 'source_error')
    return SourceError('Upstream request failed',code=code,status=status)

def source_diagnostic(error):
    """Public failure fields; never serialize exception messages or upstream data."""
    diagnostic={'error':type(error).__name__, 'error_code':'invalid_schema' if isinstance(error,ValueError) else 'source_error'}
    if isinstance(error,SourceError):
        diagnostic['error_code']=error.code if error.code in ERROR_CODES else 'source_error'
        if type(error.status) is int and 100<=error.status<=599: diagnostic['http_status']=error.status
    return diagnostic

class HTTPClient:
    def __init__(self, transport=None, sleep=time.sleep, max_bytes=128*1024*1024):
        self.transport=transport or self._transport
        self.sleep=sleep
        self.max_bytes=max_bytes

    def _transport(self, method, url, headers, body):
        parsed=urlsplit(url)
        connection=http.client.HTTPSConnection(parsed.hostname,timeout=10)
        try:
            connection.connect()
            connection.sock.settimeout(45)
            connection.request(method,parsed.path+('?' + parsed.query if parsed.query else ''),body,headers)
            response=connection.getresponse()
            data=response.read(self.max_bytes+1)
            if len(data)>self.max_bytes: raise SourceError('Response exceeds size limit')
            return Response(response.status,dict(response.getheaders()),data)
        finally: connection.close()

    def request(self,method,url,headers=None,body=None):
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.hostname not in {'us.api.blizzard.com','oauth.battle.net','public-data.tradeskillmaster.com'} or parsed.port not in {None,443} or parsed.username or parsed.password:
            raise ValueError('Upstream URL is not allowed')
        for attempt in range(3):
            try:
                response=self.transport(method,url,headers or {},body)
                if len(response.body)>self.max_bytes: raise SourceError('Response exceeds size limit')
            except (OSError,TimeoutError,http.client.HTTPException):
                if attempt==2: raise SourceError('Upstream transport failed',code='network_error') from None
                self.sleep(2**attempt); continue
            if response.status!=429 and not 500<=response.status<600: return response
            retry=next((v for k,v in response.headers.items() if k.lower()=='retry-after'),None)
            delay=2**attempt
            if retry:
                try: delay=max(0,float(retry))
                except ValueError:
                    try: delay=max(0,(parsedate_to_datetime(retry)-datetime.now(timezone.utc)).total_seconds())
                    except (TypeError,ValueError): raise Deferred('Invalid Retry-After',code=http_status_error(response.status).code,status=response.status) from None
            if delay>30: raise Deferred('Upstream requested delayed retry',code=http_status_error(response.status).code,status=response.status)
            if attempt==2: raise http_status_error(response.status)
            self.sleep(delay)
