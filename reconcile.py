#!/usr/bin/env python3
import argparse, logging
from core import config
from core.creator import Creator
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
             'from a separate host).')
    parser.add_argument('--file-path-prefix', default=None, metavar='FILE_PATH_PREFIX',
        help='Override FILE_PATH_PREFIX for this run only.')
    return parser

def main():
    """Applies any --domain/--file-domain/--file-path-prefix overrides, then reconciles every
    creator found under DATA_DIR. A single creator's failure is logged and skipped."""

    args = build_arg_parser().parse_args()

    if args.domain:
        logger.info(f"Overriding domain for this run: {args.domain}")
        config.DOMAIN = args.domain
    if args.file_domain:
        config.FILE_DOMAIN = args.file_domain
    if args.file_path_prefix:
        config.FILE_PATH_PREFIX = args.file_path_prefix

    if not config.SESSION_COOKIE:
        logger.critical("No session cookie has been provided")
        exit(1)

    logger.info(f"Discovering creators from {config.DATA_DIR}...")
    creators_info = get_creators_from_data_dir()
    logger.info(f"Discovered {len(creators_info)} creators")

    for service, id in creators_info:
        try:
            creator = Creator(service, id)
            reconcile(creator, add_favorites=args.favorite)
        except Exception as e:
            logger.error(f"Failed processing creator {service}/{id}: {e}")
            continue

if __name__ == '__main__':
    main()
