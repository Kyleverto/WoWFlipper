"""Local research desk entry point."""
import argparse
from pathlib import Path
from .web import create_app
from .demo import seed_demo

def main():
    parser=argparse.ArgumentParser(description='Local retail US auction research; never controls the game')
    parser.add_argument('--data-dir',type=Path,default=Path.home()/'.local/share/wowflipper')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--demo',action='store_true',help='Use isolated synthetic offline storage')
    args=parser.parse_args()
    if not 1<=args.port<=65535: parser.error('port must be between 1 and 65535')
    path=args.data_dir/'offline-demo' if args.demo else args.data_dir
    if args.demo: seed_demo(path)
    elif (path/'market.sqlite3').exists():
        from .repository import Repository
        if Repository(path).cache_get('mode')=='offline-demo': parser.error('Demo storage cannot be used as live storage; choose another data directory')
    create_app(path).run(host='127.0.0.1',port=args.port,debug=False,use_reloader=False)

if __name__=='__main__': main()
