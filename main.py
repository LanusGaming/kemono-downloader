import logging, os, shutil, time
from core.network import init_network
from core.kemono import get_favorite_creators
from core.creator import Creator
from core.utils import sort_creators_by_recency
from core.files import get_creators_from_file
from core.management.cleanup import cleanup
from core.management.deduplicate import deduplicate
#from core.mega import test_mega

logger = logging.getLogger("downloader")
failure_logger = logging.getLogger("failed")

def setup_loggers(log_level: str):
    log_format = "[%(asctime)s] %(levelname)s: %(message)s"
    date_format = '%y-%m-%d %H:%M:%S'

    numeric_level = getattr(logging, log_level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError('Invalid log level: %s' % log_level)

    logging.basicConfig(level=numeric_level, format=log_format, datefmt=date_format)
    
    # Handler for stdout
    #stdout_h = logging.StreamHandler(sys.stdout)
    #stdout_h.setLevel(numeric_level)
    #stdout_h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", date_format))
    #logger(stdout_h)

    # Handler for the log file
    file_h = logging.FileHandler(f"/config/logs/{time.strftime(date_format)}.log", mode='w', encoding="utf-8")
    file_h.setLevel(logging.DEBUG)
    file_h.setFormatter(logging.Formatter(log_format, date_format))
    logger.addHandler(file_h)

    failure_logger.setLevel(logging.DEBUG)

    # Handler for the failed downloads
    failure_h = logging.FileHandler(f"/config/failed/{time.strftime(date_format)}.json", mode='w', encoding="utf-8")
    failure_h.setLevel(logging.DEBUG)
    failure_h.setFormatter(logging.Formatter("%(message)s"))
    failure_logger.addHandler(failure_h)

def main():
    #domain = os.getenv('DEFAULT_DOMAIN', 'kemono.cr')
    cookie = os.getenv('SESSION_COOKIE', '')
    creator_url_filepath = os.getenv('CREATOR_URL_FILE', '')
    log_level = os.getenv('LOG_LEVEL', 'INFO')
    migrate_original = os.getenv('MIGRATE', 'false')
    delete_skipped = os.getenv('DELETE_SKIPPED', 'true')
    delete_remaining = os.getenv('DELETE_REMAINING', 'false')

    os.makedirs('/config', exist_ok=True)
    os.makedirs('/config/logs', exist_ok=True)
    os.makedirs('/config/failed', exist_ok=True)
    os.makedirs('/data', exist_ok=True)
    if os.path.exists('/temp'):
        shutil.rmtree('/temp', ignore_errors=True)
    os.makedirs('/temp', exist_ok=True)

    setup_loggers(log_level)

    if cookie == '':
        logger.critical("No session cookie has been provided")
        exit(1)

    init_network(cookie)

    if migrate_original.lower() == 'true':
        #migrate_original_downloads('/data', '/data', delete_skipped.lower() == 'true', delete_remaining.lower() == 'true')
        time.sleep(5)

    #test_mega()

    if creator_url_filepath:
        if not os.path.exists(creator_url_filepath):
            logger.critical(f"File does not exist: {creator_url_filepath}")
            exit(1)

        logger.info(f"Collecting creator data for creators in {creator_url_filepath}...")
        creators = get_creators_from_file(creator_url_filepath)
        logger.info(f"Retrived {len(creators)} creators")

        for creator_data in creators:
            creator = Creator(creator_data[0], creator_data[1])
            #cleanup(creator)
            #deduplicate(creator)
            creator.download()
    else:
        creators = sort_creators_by_recency(get_favorite_creators())
        for creator_data in creators:
            creator = Creator(creator_data['service'], creator_data['id'])
            #cleanup(creator)
            #deduplicate(creator)
            creator.download()

if __name__ == '__main__':
    main()