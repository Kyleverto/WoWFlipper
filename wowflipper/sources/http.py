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

class Deferred(RuntimeError): pass
class SourceError(RuntimeError): pass

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
        if parsed.scheme!='https' or parsed.hostname not in {'us.api.blizzard.com','oauth.battle.net'} or parsed.port not in {None,443} or parsed.username or parsed.password:
            raise ValueError('Upstream URL is not allowed')
        for attempt in range(3):
            try:
                response=self.transport(method,url,headers or {},body)
                if len(response.body)>self.max_bytes: raise SourceError('Response exceeds size limit')
            except (OSError,TimeoutError,http.client.HTTPException):
                if attempt==2: raise SourceError('Upstream transport failed') from None
                self.sleep(2**attempt); continue
            if response.status!=429 and not 500<=response.status<600: return response
            retry=next((v for k,v in response.headers.items() if k.lower()=='retry-after'),None)
            delay=2**attempt
            if retry:
                try: delay=max(0,float(retry))
                except ValueError:
                    try: delay=max(0,(parsedate_to_datetime(retry)-datetime.now(timezone.utc)).total_seconds())
                    except (TypeError,ValueError): raise Deferred('Invalid Retry-After') from None
            if delay>30: raise Deferred('Upstream requested delayed retry')
            if attempt==2: raise SourceError('Upstream unavailable')
            self.sleep(delay)
