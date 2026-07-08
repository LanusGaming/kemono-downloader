#!/usr/bin/env python3
import argparse, logging
from runtime import initialize, process_creators
from core.paths import DATA_DIR
from core.files import get_creators_from_data_dir
from core.management.reconcile import reconcile

logger = logging.getLogger("downloader")

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='reconcile.py',
        description=(
            'Rebuild file/post records from what is already on disk, instead of downloading. '
            'Always sources creators from folders on disk under DATA_DIR (same convention as '
            'CREATORS_FROM_DATA) - CREATOR_URL_FILE/favorites are never used here. Run via '
            '`docker exec <container> reconcile ...` against a running container.'
        )
    )
    parser.add_argument('--favorite', action='store_true',
        help="Also add each reconciled creator to your account's favorites on the effective domain.")
    parser.add_argument('--domain', default=None, metavar='DOMAIN',
        help="Override DOMAIN for this run only, e.g. a different kemono-schema mirror. Combine "
             "with `docker exec -e SESSION_COOKIE=...` for that mirror's account.")
    parser.add_argument('--file-domain', default=None, metavar='FILE_DOMAIN',
        help='Override FILE_DOMAIN for this run only (only needed if the mirror serves files '
             'from a separate host - see FILE_DOMAIN in .env.example).')
    parser.add_argument('--file-path-prefix', default=None, metavar='FILE_PATH_PREFIX',
        help='Override FILE_PATH_PREFIX for this run only.')
    return parser

def main():
    args = build_arg_parser().parse_args()

    initialize(domain=args.domain, file_domain=args.file_domain, file_path_prefix=args.file_path_prefix)

    logger.info(f"Discovering creators from {DATA_DIR}...")
    creators_info = get_creators_from_data_dir()
    logger.info(f"Discovered {len(creators_info)} creators")

    process_creators(creators_info, lambda creator: reconcile(creator, add_favorites=args.favorite))

if __name__ == '__main__':
    main()
