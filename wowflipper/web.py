"""Loopback-only browser adapter. Never exposes upstream exception bodies."""
from .realms import selectable_us_realm
import csv, io, os, secrets, json
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from flask import Flask, request, session, jsonify, render_template, Response, g
from werkzeug.exceptions import HTTPException
from .repository import Repository
from .portfolio import Portfolio
from .query import MarketQuery, safe
from .settings import Settings
from .refresh import RefreshService
from .sources.blizzard import BlizzardClient
from .demo import import_snapshot

def create_app(data_dir: Path, testing: bool=False) -> Flask:
    app=Flask(__name__)
    app.config.update(TESTING=testing,SECRET_KEY=secrets.token_hex(32),MAX_CONTENT_LENGTH=16*1024*1024,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Strict')
    repo=Repository(data_dir); refresh=RefreshService(repo,BlizzardClient())
    app.extensions['repository']=repo; app.extensions['refresh']=refresh
    def market():
        if 'market' not in g: g.market=MarketQuery(repo)
        return g.market
    def portfolio():
        if 'portfolio' not in g: g.portfolio=Portfolio(repo,lambda key:market().candidate(key))
        return g.portfolio
    @app.before_request
    def guard():
        try:
            parsed=urlsplit('http://'+request.host)
            if parsed.hostname not in ('127.0.0.1','localhost') or parsed.username or parsed.password or parsed.path: return jsonify(error='This service accepts loopback hosts only'),403
            # Reject malformed ports and alternative host spellings.
            port=parsed.port
            canonical=parsed.hostname+(f':{port}' if port is not None else '')
            if canonical!=request.host: return jsonify(error='Invalid local host'),403
        except ValueError: return jsonify(error='Invalid local host'),403
        if request.method not in ('GET','HEAD','OPTIONS'):
            origin=request.headers.get('Origin')
            if origin and origin!='http://'+request.host: return jsonify(error='External origins cannot change local data'),403
            if request.headers.get('Sec-Fetch-Site') in ('cross-site','same-site'): return jsonify(error='External sites cannot change local data'),403
            expected=session.get('csrf')
            if not expected or not secrets.compare_digest(request.headers.get('X-CSRF-Token',''),expected): return jsonify(error='Reload this page to renew its local security token'),403
    @app.after_request
    def headers(response):
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers['X-Content-Type-Options']='nosniff'; response.headers['Cache-Control']='no-store'
        return response
    @app.errorhandler(Exception)
    def errors(exc):
        if isinstance(exc,HTTPException): return jsonify(error='Request is too large' if exc.code==413 else 'Invalid request'),exc.code
        if isinstance(exc,(ValueError,TypeError,KeyError)): return jsonify(error=str(exc) if isinstance(exc,ValueError) else 'Invalid request fields'),400
        if isinstance(exc,RuntimeError): return jsonify(error='Refresh is already running; wait for completion'),409
        return jsonify(error='Local operation failed. Retry or inspect your data directory permissions.'),500
    def body(allowed):
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or set(data)-set(allowed): raise ValueError('Unknown or invalid request fields')
        return data
    @app.get('/')
    def index(): return render_template('index.html')
    @app.get('/api/state')
    def state():
        session.setdefault('csrf',secrets.token_urlsafe(32))
        sources=repo.latest_metadata()
        return jsonify(safe({'csrf':session['csrf'],'settings':repo.load_settings().to_dict(),'realms':repo.cache_get('realms') or [],'mode':repo.cache_get('mode') or 'live','sources':sources,'refresh':refresh.status,'credentials_configured':bool(os.environ.get('BLIZZARD_CLIENT_ID') and os.environ.get('BLIZZARD_CLIENT_SECRET')),'summary':portfolio().summary(),'history':portfolio().history()}))
    @app.get('/api/realms')
    def realms(): return jsonify(repo.cache_get('realms') or [])
    @app.post('/api/settings')
    def settings():
        data=body(repo.load_settings().to_dict())
        old=repo.load_settings(); updated=Settings.from_dict({**old.to_dict(),**data})
        if portfolio().history() and updated.capital!=old.capital: raise ValueError('Initial capital is frozen after ledger activity. Record a balance adjustment instead.')
        selectable={r['slug'] for r in repo.cache_get('realms') or [] if selectable_us_realm(r)}
        for field in ('buying_realms','selling_realms'):
            added=set(getattr(updated,field))-set(getattr(old,field))
            if added-selectable: raise ValueError('Select realms from the discovered US list')
        repo.save_settings(updated); return jsonify(updated.to_dict())
    @app.post('/api/refresh')
    def start_refresh():
        body([])
        if repo.cache_get('mode')=='offline-demo': raise ValueError('Offline demo cannot refresh live sources. Start without --demo to use live data.')
        refresh.start(repo.load_settings()); return jsonify(started=True),202
    @app.get('/api/refresh/status')
    def refresh_status(): return jsonify(refresh.status)
    @app.get('/api/results')
    def results(): return jsonify(market().page(request.args))
    @app.get('/api/exclusions')
    def exclusions(): return jsonify(market().exclusions_page(request.args))
    @app.get('/api/shopping')
    def shopping(): return jsonify(safe(portfolio().shopping()))
    @app.post('/api/shopping')
    def select():
        data=body(['key','strategy','destination','request_id'])
        return jsonify(safe(portfolio().select(tuple(data['key']),data.get('strategy','general'),data['destination'],data['request_id'])))
    @app.post('/api/shopping/remove')
    def remove():
        data=body(['key','request_id']); return jsonify(portfolio().remove(tuple(data['key']),data['request_id']))
    @app.get('/api/ledger')
    def ledger(): return jsonify(safe(portfolio().history()))
    @app.post('/api/ledger')
    def record():
        data=body(['event','request_id']); return jsonify(safe(portfolio().record(data['event'],data['request_id'])))
    @app.post('/api/import')
    def import_data(): return jsonify(import_snapshot(repo,body(['kind','text','source_time','house_id'])))
    @app.get('/api/export')
    def export():
        rows=market().rows(request.args); output=io.StringIO(); writer=csv.writer(output)
        writer.writerow(['Item','Item ID','Auction ID','Source realms','Quantity','Total buyout copper','Unit buyout copper','Assumed unit resale copper','Estimated net proceeds copper','Estimated profit copper','ROI','Regional sale rate','Regional sold per day','Market value copper','Historical copper','Auction source time','Valuation source time','Snapshot provenance','Mode','Auction cut','Extra costs copper','Resale factor','Minimum discount','Minimum profit copper','Minimum ROI','Minimum sale rate','Minimum sold per day','Maximum copies','Maximum auction age hours','Maximum valuation age hours','Destination evidence','Assumptions and risks'])
        def text(value):
            value=str(value)
            return "'"+value if value.lstrip().startswith(('=','+','-','@','\t','\r')) else value
        for row in rows:
            a=row['auction']; v=row['valuation']
            writer.writerow([text(v['name']),a['item_id'],a['auction_id'],text(', '.join(row['source_realms'])),a['quantity'],a['buyout'],Decimal(a['buyout'])/a['quantity'],str(int(v['avg_sale_price'])*market().settings.resale_factor),row['proceeds'],row['profit'],row['roi'],v['sale_rate'],v['sold_per_day'],v['market_value'],v['historical'],text(a['source_time']),text(v['updated_at']),text(a['provenance']),text('Offline '+row['mode'] if row['mode'].startswith('offline') else row['mode']),market().settings.auction_cut,market().settings.extra_costs,market().settings.resale_factor,market().settings.discount,market().settings.min_profit,market().settings.min_roi,market().settings.min_sale_rate,market().settings.min_sold_per_day,market().settings.max_copies,market().settings.max_auction_age_hours,market().settings.max_tsm_age_hours,text(json.dumps(row['comparisons'],separators=(',',':'))),text('Regional demand only; manual verification required; '+row['explanation']+'; '+', '.join(row['risks']))])
        return Response(output.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=wowflipper-visible.csv'})
    return app
