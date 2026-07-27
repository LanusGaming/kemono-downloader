import logging, os, shutil, sys, time

from . import conf

logger = logging.getLogger("downloader")
failure_logger = logging.getLogger("failed")

DATA_DIR = os.getenv('DATA_DIR', '/data')
CONFIG_DIR = os.getenv('CONFIG_DIR', '/config')
TEMP_DIR = os.getenv('TEMP_DIR', '/temp')
SESSION_COOKIE = os.getenv('SESSION_COOKIE', '')
# Google Drive API v3 key - only needed if ALLOWED_TYPES includes 'external' and a creator posts
# Drive links. Not required for Mega links (megatools needs no credentials for public links).
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY', '')
# Path to the megatools binary - only needed if ALLOWED_TYPES includes 'external' and a creator
# posts Mega links.
MEGATOOLS_BIN = os.getenv('MEGATOOLS_BIN', 'megatools')

CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.conf')
DEFAULT_CONFIG_TEMPLATE = os.path.join(os.path.dirname(__file__), 'config.conf.default')

SCHEMA = {
    'DOMAIN': str, 'FILE_DOMAIN': str, 'FILE_PATH_PREFIX': str, 'CREATOR_URL_FILE': str,
    'CREATORS_FROM_DATA': bool, 'LOG_LEVEL': str, 'LOG_RETENTION_DAYS': int,
    'CRON_EXPRESSION': str, 'RUN_IMMEDIATELY': bool, 'TRIGGER_ALBUM_CREATOR': bool,
    'ALBUM_CREATOR_WEBHOOK_URL': str, 'DOWNLOAD_MAX_ATTEMPTS': int, 'DOWNLOAD_RETRY_DELAY': int,
}

DOMAIN = 'kemono.cr'
FILE_DOMAIN = ''
FILE_PATH_PREFIX = ''
CREATOR_URL_FILE = ''
CREATORS_FROM_DATA = False
LOG_LEVEL = 'INFO'
LOG_RETENTION_DAYS = 14
CRON_EXPRESSION = ''
RUN_IMMEDIATELY = False
TRIGGER_ALBUM_CREATOR = False
ALBUM_CREATOR_WEBHOOK_URL = 'http://album-creator:8080/run'
DOWNLOAD_MAX_ATTEMPTS = 60
DOWNLOAD_RETRY_DELAY = 1

RUN_TIMESTAMP = ''  # set once in _setup_loggers() - shared by the log/failed/summary filenames
                     # for a run, so they can be correlated by name

def _prune_old_logs(retention_days: int):
    """Deletes files older than retention_days from both config/logs and config/failed."""

    if retention_days <= 0:
        return
    cutoff = time.time() - retention_days * 86400
    for dirpath in (f"{CONFIG_DIR}/logs", f"{CONFIG_DIR}/failed", f"{CONFIG_DIR}/summary"):
        for filename in os.listdir(dirpath):
            filepath = os.path.join(dirpath, filename)
            if os.path.isfile(filepath) and os.path.getmtime(filepath) < cutoff:
                os.remove(filepath)

def _setup_loggers(log_level: str, log_retention_days: int):
    """Prunes old log/failure files, then attaches stdout + file handlers to the downloader and
    failure loggers."""

    global RUN_TIMESTAMP

    log_format = "[%(asctime)s] %(levelname)s: %(message)s"
    date_format = '%y-%m-%d %H:%M:%S'
    numeric_level = getattr(logging, log_level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError('Invalid log level: %s' % log_level)

    _prune_old_logs(log_retention_days)

    RUN_TIMESTAMP = time.strftime(date_format)

    logger.setLevel(logging.DEBUG)
    stdout_h = logging.StreamHandler(sys.stdout)
    stdout_h.setLevel(numeric_level)
    stdout_h.setFormatter(logging.Formatter(log_format, date_format))
    logger.addHandler(stdout_h)

    file_h = logging.FileHandler(f"{CONFIG_DIR}/logs/{RUN_TIMESTAMP}.log", mode='w', encoding="utf-8")
    file_h.setLevel(logging.DEBUG)
    file_h.setFormatter(logging.Formatter(log_format, date_format))
    logger.addHandler(file_h)

    failure_logger.setLevel(logging.DEBUG)
    failure_h = logging.FileHandler(f"{CONFIG_DIR}/failed/{RUN_TIMESTAMP}.json", mode='w', encoding="utf-8")
    failure_h.setLevel(logging.DEBUG)
    failure_h.setFormatter(logging.Formatter("%(message)s"))
    failure_logger.addHandler(failure_h)

def load():
    """Loads config.conf into this module's globals, creating it from the default template if
    missing."""

    global DOMAIN, FILE_DOMAIN, FILE_PATH_PREFIX, CREATOR_URL_FILE, CREATORS_FROM_DATA, \
           LOG_LEVEL, LOG_RETENTION_DAYS, CRON_EXPRESSION, RUN_IMMEDIATELY, \
           TRIGGER_ALBUM_CREATOR, ALBUM_CREATOR_WEBHOOK_URL, \
           DOWNLOAD_MAX_ATTEMPTS, DOWNLOAD_RETRY_DELAY

    if not os.path.exists(CONFIG_PATH):
        logger.info(f"No config.conf found - creating {CONFIG_PATH} from defaults")
        shutil.copy(DEFAULT_CONFIG_TEMPLATE, CONFIG_PATH)

    try:
        data = conf.read(CONFIG_PATH, DEFAULT_CONFIG_TEMPLATE, SCHEMA)
    except OSError as e:
        logger.error(f"Could not read {CONFIG_PATH} ({e}) - using built-in defaults for this run")
        return

    DOMAIN = data['DOMAIN']
    FILE_DOMAIN = data['FILE_DOMAIN']
    FILE_PATH_PREFIX = data['FILE_PATH_PREFIX']
    CREATOR_URL_FILE = data['CREATOR_URL_FILE']
    CREATORS_FROM_DATA = data['CREATORS_FROM_DATA']
    LOG_LEVEL = data['LOG_LEVEL']
    LOG_RETENTION_DAYS = data['LOG_RETENTION_DAYS']
    CRON_EXPRESSION = data['CRON_EXPRESSION']
    RUN_IMMEDIATELY = data['RUN_IMMEDIATELY']
    TRIGGER_ALBUM_CREATOR = data['TRIGGER_ALBUM_CREATOR']
    ALBUM_CREATOR_WEBHOOK_URL = data['ALBUM_CREATOR_WEBHOOK_URL']
    DOWNLOAD_MAX_ATTEMPTS = data['DOWNLOAD_MAX_ATTEMPTS']
    DOWNLOAD_RETRY_DELAY = data['DOWNLOAD_RETRY_DELAY']

def init():
    """Creates the config/data/temp directories (wiping and recreating TEMP_DIR), loads
    config.conf, and sets up logging. Each entrypoint (app.py, download.py, reconcile.py) must
    call this once, early, before using anything that depends on it being ready."""

    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(f"{CONFIG_DIR}/logs", exist_ok=True)
    os.makedirs(f"{CONFIG_DIR}/failed", exist_ok=True)
    os.makedirs(f"{CONFIG_DIR}/summary", exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR, ignore_errors=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    load()
    _setup_loggers(LOG_LEVEL, LOG_RETENTION_DAYS)
