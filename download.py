#!/usr/bin/env python3
import logging, os
from runtime import initialize, process_creators
from core.paths import DATA_DIR
from core.kemono import get_favorite_creators
from core.files import get_creators_from_file, get_creators_from_data_dir
from core.management.album_creator import trigger_album_creator

logger = logging.getLogger("downloader")

def resolve_creators(creators_from_data: bool, creator_url_filepath: str) -> list[tuple[str, str]]:
    """Existing three-way source resolution, unchanged - moved here since only download.py uses
    it (reconcile.py always sources from disk, see reconcile.py)."""
    if creators_from_data:
        logger.info(f"Discovering creators from {DATA_DIR}...")
        creators_info = get_creators_from_data_dir()
        logger.info(f"Discovered {len(creators_info)} creators")

    elif creator_url_filepath:
        if not os.path.exists(creator_url_filepath):
            logger.warning(f"File does not exist: {creator_url_filepath}")
            logger.info("Creating...")
            creator_file = open(creator_url_filepath, 'w')
            creator_file.close()

        logger.info(f"Collecting creator data for creators in {creator_url_filepath}...")
        creators_info = [(service, id) for service, id in get_creators_from_file(creator_url_filepath)]
        logger.info(f"Retrived {len(creators_info)} creators")

    else:
        logger.info(f"Collecting creator data for favorite creators...")
        creators_info = [(creator_data['service'], creator_data['id']) for creator_data in get_favorite_creators()]
        logger.info(f"Retrived {len(creators_info)} creators")

    return creators_info

def main():
    initialize()

    creators_from_data = os.getenv('CREATORS_FROM_DATA', 'false').lower() == 'true'
    creator_url_filepath = os.getenv('CREATOR_URL_FILE', '')
    trigger_album_creator_enabled = os.getenv('TRIGGER_ALBUM_CREATOR', 'false').lower() == 'true'
    album_creator_webhook_url = os.getenv('ALBUM_CREATOR_WEBHOOK_URL', 'http://album-creator:8080/run')
    # DOWNLOAD_ALL/SORT_BY_RECENCY stay exactly as unused as they are today (already documented
    # as such in .env.example) - kept here only for parity, no behavior change.
    download_all = os.getenv('DOWNLOAD_ALL', 'false').lower() == 'true'
    sort_by_recency = os.getenv('SORT_BY_RECENCY', 'false').lower() == 'true'

    creators_info = resolve_creators(creators_from_data, creator_url_filepath)

    #if sort_by_recency:
        #creators = sorted(creators, key=lambda creator: creator.last_imported, reverse=True)

    process_creators(creators_info, lambda creator: creator.download())

    if trigger_album_creator_enabled:
        trigger_album_creator(album_creator_webhook_url)

if __name__ == '__main__':
    main()
