import logging, os, shutil, sys, time
from core.paths import DATA_DIR, CONFIG_DIR, TEMP_DIR
from core.network import init_network, set_domain
from core.creator import Creator

logger = logging.getLogger("downloader")
failure_logger = logging.getLogger("failed")

def prune_old_logs(retention_days: int):
    if retention_days <= 0:
        return

    cutoff = time.time() - retention_days * 86400
    for dirpath in (f"{CONFIG_DIR}/logs", f"{CONFIG_DIR}/failed"):
        for filename in os.listdir(dirpath):
            filepath = os.path.join(dirpath, filename)
            if os.path.isfile(filepath) and os.path.getmtime(filepath) < cutoff:
                os.remove(filepath)

def setup_loggers(log_level: str, log_retention_days: int):
    log_format = "[%(asctime)s] %(levelname)s: %(message)s"
    date_format = '%y-%m-%d %H:%M:%S'

    numeric_level = getattr(logging, log_level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError('Invalid log level: %s' % log_level)

    prune_old_logs(log_retention_days)

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

def initialize(domain: str | None = None, file_domain: str | None = None, file_path_prefix: str | None = None):
    """Shared prerequisites for every entry point: dirs, logging, optional domain override,
    cookie check, network init. domain/file_domain/file_path_prefix let a single invocation
    (e.g. `reconcile.py --domain=...`) override the configured mirror without touching anything
    persisted - must be applied here, after logging is set up (so the override is actually logged)
    but before init_network() (which copies DOMAIN_CONFIG['referer'] into HEADERS once)."""
    cookie = os.getenv('SESSION_COOKIE', '')
    log_level = os.getenv('LOG_LEVEL', 'INFO')
    log_retention_days = int(os.getenv('LOG_RETENTION_DAYS', '14'))

    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(f"{CONFIG_DIR}/logs", exist_ok=True)
    os.makedirs(f"{CONFIG_DIR}/failed", exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR, ignore_errors=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    setup_loggers(log_level, log_retention_days)

    if domain:
        logger.info(f"Overriding domain for this run: {domain}")
        set_domain(domain, file_domain, file_path_prefix)

    if cookie == '':
        logger.critical("No session cookie has been provided")
        exit(1)

    init_network(cookie)

def process_creators(creators_info: list[tuple[str, str]], action):
    """Shared per-creator loop: constructs the Creator, then calls action(creator). One bad
    creator shouldn't abort the whole batch (the earlier crash-isolation fix) - centralizing this
    means every entry point gets that resilience for free."""
    for service, id in creators_info:
        try:
            creator = Creator(service, id)
            action(creator)
        except Exception as e:
            logger.error(f"Failed processing creator {service}/{id}: {e}")
            continue
