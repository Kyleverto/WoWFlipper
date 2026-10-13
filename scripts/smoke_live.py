"""Opt-in network checks. Run after installing wowflipper; never prints secrets."""
import argparse
import json
from pathlib import Path
import tempfile
import sqlite3

from wowflipper.sources.tsm import TSMClient
from wowflipper.sources.blizzard import BlizzardClient
from wowflipper.sources.http import SourceError
from wowflipper.settings import Settings
from wowflipper.repository import Repository
from wowflipper.refresh import RefreshService
from wowflipper.query import MarketQuery


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tsm-only', action='store_true', help='Download and parse public TSM CSV only')
    parser.add_argument('--data-dir', type=Path, default=Path.home()/'.local/share/wowflipper', help='Read configured settings; smoke snapshots use temporary isolated storage')
    args = parser.parse_args(argv)
    try:
        client = BlizzardClient()
        if not args.tsm_only and (not client.client_id or not client.client_secret):
            print('Missing BLIZZARD_CLIENT_ID or BLIZZARD_CLIENT_SECRET. Export both in this shell; .env is not loaded automatically.')
            return 2
        snapshot = TSMClient().region()
        print(json.dumps({'source': snapshot.source_key, 'source_time': str(snapshot.source_time), **snapshot.source_metadata}, sort_keys=True))
        if args.tsm_only:
            return 0
        settings = Settings()
        database = args.data_dir/'market.sqlite3'
        if database.exists():
            with sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True) as connection:
                rows = dict(connection.execute("SELECT key, data FROM cache WHERE key IN ('settings', 'mode')"))
            if json.loads(rows.get('mode', 'null')) == 'offline-demo':
                print('Choose live settings storage; demo storage is synthetic.')
                return 2
            settings = Settings.from_dict(json.loads(rows.get('settings', '{}')))
        class DownloadedTSM:
            def region(self, previous=None):
                return snapshot
        with tempfile.TemporaryDirectory(prefix='wowflipper-smoke-') as directory:
            repo = Repository(Path(directory))
            service = RefreshService(repo, client, DownloadedTSM())
            try:
                status = service.run(settings)
                query = MarketQuery(repo)
                print(json.dumps(status, sort_keys=True))
                for house, auction_snapshot in sorted(query.snapshots.items()):
                    if auction_snapshot:
                        print(json.dumps({'source': auction_snapshot.source_key, 'source_time': str(auction_snapshot.source_time), 'rows': len(auction_snapshot.records)}, sort_keys=True))
                print(json.dumps({'configured_buying_realms': list(settings.buying_realms), 'configured_selling_realms': list(settings.selling_realms), 'auction_rows': sum(len(s.records) for s in query.snapshots.values() if s), 'qualifying_rows': len(query.rows({}))}, sort_keys=True))
                return 1 if any(s['state']=='failed' for s in status['sources'].values()) else 0
            finally:
                service._worker.shutdown(wait=True)
    except (SourceError, ValueError, OSError, TypeError, KeyError, sqlite3.Error):
        print('Smoke failed: source download, schema, or settings could not be validated. No upstream bodies or credentials are printed.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
