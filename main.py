import logging, os, shutil, time, sys
from core.paths import DATA_DIR, CONFIG_DIR, TEMP_DIR
from core.network import init_network
from core.kemono import get_favorite_creators
from core.creator import Creator
from core.files import get_creators_from_file, get_creators_from_data_dir
from core.management.cleanup import cleanup
from core.management.deduplicate import deduplicate
from core.management.reconcile import reconcile
from core.management.album_creator import trigger_album_creator

logger = logging.getLogger("downloader")
failure_logger = logging.getLogger("failed")

def setup_loggers(log_level: str):
    log_format = "[%(asctime)s] %(levelname)s: %(message)s"
    date_format = '%y-%m-%d %H:%M:%S'

    numeric_level = getattr(logging, log_level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError('Invalid log level: %s' % log_level)

    #logging.basicConfig(level=numeric_level, format=log_format, datefmt=date_format)

    logger.setLevel(logging.DEBUG)

    # Handler for stdout
    stdout_h = logging.StreamHandler(sys.stdout)
    stdout_h.setLevel(numeric_level)
    stdout_h.setFormatter(logging.Formatter(log_format, date_format))
    logger.addHandler(stdout_h)

    # Handler for the log file
    file_h = logging.FileHandler(f"{CONFIG_DIR}/logs/{time.strftime(date_format)}.log", mode='w', encoding="utf-8")
    file_h.setLevel(logging.DEBUG)
    file_h.setFormatter(logging.Formatter(log_format, date_format))
    logger.addHandler(file_h)

    failure_logger.setLevel(logging.DEBUG)

    # Handler for the failed downloads
    failure_h = logging.FileHandler(f"{CONFIG_DIR}/failed/{time.strftime(date_format)}.json", mode='w', encoding="utf-8")
    failure_h.setLevel(logging.DEBUG)
    failure_h.setFormatter(logging.Formatter("%(message)s"))
    failure_logger.addHandler(failure_h)

def main():
    cookie = os.getenv('SESSION_COOKIE', '')
    creator_url_filepath = os.getenv('CREATOR_URL_FILE', '')
    log_level = os.getenv('LOG_LEVEL', 'INFO')
    download_all = os.getenv('DOWNLOAD_ALL', 'false').lower() == 'true'
    sort_by_recency = os.getenv('SORT_BY_RECENCY', 'false').lower() == 'true'
    reconcile_mode = os.getenv('RECONCILE', 'false').lower() == 'true'
    creators_from_data = os.getenv('CREATORS_FROM_DATA', 'false').lower() == 'true'
    trigger_album_creator_enabled = os.getenv('TRIGGER_ALBUM_CREATOR', 'false').lower() == 'true'
    album_creator_webhook_url = os.getenv('ALBUM_CREATOR_WEBHOOK_URL', 'http://album-creator:8080/run')

    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(f"{CONFIG_DIR}/logs", exist_ok=True)
    os.makedirs(f"{CONFIG_DIR}/failed", exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR, ignore_errors=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    setup_loggers(log_level)

    if cookie == '':
        logger.critical("No session cookie has been provided")
        exit(1)

    init_network(cookie)

    creators_info = []

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

    #if sort_by_recency:
        #creators = sorted(creators, key=lambda creator: creator.last_imported, reverse=True)

    for service, id in creators_info:
        try:
            creator = Creator(service, id)
            if reconcile_mode:
                reconcile(creator)
            else:
                creator.download()
        except Exception as e:
            logger.error(f"Failed processing creator {service}/{id}: {e}")
            continue

    if trigger_album_creator_enabled:
        trigger_album_creator(album_creator_webhook_url)

if __name__ == '__main__':
    main()
