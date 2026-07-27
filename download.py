#!/usr/bin/env python3
import json, logging, os
from core import config
from core.creator import Creator
from core.kemono import get_favorite_creators
from core.files import get_creators_from_file, get_creators_from_data_dir
from core.management.album_creator import trigger_album_creator
from core.summary import CreatorSummary, RunSummary

logger = logging.getLogger("downloader")

def resolve_creators(creators_from_data: bool, creator_url_filepath: str) -> list[tuple[str, str]]:
    """Resolves which creators to process: CREATORS_FROM_DATA, then CREATOR_URL_FILE, then
    favorites - only one source is used per run."""

    if creators_from_data:
        logger.info(f"Discovering creators from {config.DATA_DIR}...")
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
    """Downloads every resolved creator in turn. A single creator's failure is logged and
    skipped rather than aborting the rest of the run."""

    config.init()

    if not config.SESSION_COOKIE:
        logger.critical("No session cookie has been provided")
        exit(1)

    creators_info = resolve_creators(config.CREATORS_FROM_DATA, config.CREATOR_URL_FILE)

    run_summary = RunSummary()

    for service, id in creators_info:
        try:
            creator = Creator(service, id)
            run_summary.creators.append(creator.download())
        except Exception as e:
            logger.error(f"Failed processing creator {service}/{id}: {e}")
            run_summary.creators.append(CreatorSummary(
                service=service, id=id, status='failed', error=str(e)))
            continue

    logger.info(run_summary.render_text())

    summary_path = f"{config.CONFIG_DIR}/summary/{config.RUN_TIMESTAMP}.json"
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(run_summary.to_dict(), f, indent=2)

    if config.TRIGGER_ALBUM_CREATOR:
        trigger_album_creator(config.ALBUM_CREATOR_WEBHOOK_URL)

if __name__ == '__main__':
    main()
