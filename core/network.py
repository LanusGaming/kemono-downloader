import requests, gzip, logging, time, os
from fake_useragent import UserAgent

logger = logging.getLogger("downloader")

ua = UserAgent()
user_agent = ua.chrome

# Default headers (override referer per request)
HEADERS = {
    'User-Agent': user_agent,
    'Accept': 'text/css',
    'Accept-Language': "en-US,en;q=0.9",
    'Connection': 'keep-alive'
}

# Kemono-schema-compatible mirrors (e.g. pawchive.pw) can be used by setting DOMAIN - the
# API path structure is shared, so the config only needs the hostname to build every URL.
# Some mirrors (e.g. pawchive) serve actual file downloads from a separate subdomain, and/or
# need a path prefix inserted before the hash-bucket path returned by the API (kemono.cr's API
# returns bare "/xx/yy/hash.ext" paths that work directly on its own domain; pawchive needs
# "file.pawchive.pw" + "/data" prepended instead). FILE_DOMAIN/FILE_PATH_PREFIX cover that,
# defaulting to DOMAIN/'' so single-host mirrors like kemono.cr need no extra config.
DOMAIN = os.getenv('DOMAIN') or 'kemono.cr'
FILE_DOMAIN = os.getenv('FILE_DOMAIN') or DOMAIN
FILE_PATH_PREFIX = os.getenv('FILE_PATH_PREFIX') or ''
DOMAIN_CONFIG = {
    'domain': DOMAIN,
    'base_url': f"https://{DOMAIN}",
    'api_base': f"https://{DOMAIN}/api/v1",
    'referer': f"https://{DOMAIN}/",
    'file_base_url': f"https://{FILE_DOMAIN}{FILE_PATH_PREFIX}"
}

# Build a single session with pooling
SESSION = requests.Session()

def init_network(cookie: str):
    HEADERS['Referer'] = DOMAIN_CONFIG['referer']
    HEADERS['Cookie'] = f"session={cookie}"

    # Configure connection pool
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=20,
        pool_maxsize=20,
        max_retries=3,
        pool_block=False
    )

    SESSION.mount('http://', adapter)
    SESSION.mount('https://', adapter)

def call_api(api_call: str, timeout: int = 15, max_attempts: int = 7, additional_headers: dict = {}) -> str:
    url = f"{DOMAIN_CONFIG['api_base']}/{api_call}"
    headers = HEADERS.copy()
    headers.update(additional_headers)

    safe_headers = {k: ('***redacted***' if k.lower() == 'cookie' else v) for k, v in headers.items()}

    for attempt in range(max_attempts):
        try:
            logger.debug(f"# API #\nCalling kemono API at {url} with headers {safe_headers} and timeout {timeout}s")
            response = SESSION.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()

            response_text = None

            is_gzipped = response.content[:2] == b'\x1f\x8b'

            if is_gzipped:
                try:
                    decompressed = gzip.decompress(response.content)
                    response_text = decompressed.decode('utf-8')
                except (gzip.BadGzipFile, UnicodeDecodeError):
                    response_text = response.text
            else:
                response_text = response.text

            if not response_text.strip():
                logger.debug(f"# API #\nRetrieved data was empty for {url}")
                return None

            logger.debug(f"# API #\nSuccessfully called API at {url}:\n{response_text}")
            return response_text

        except Exception as e:
            if isinstance(e, requests.HTTPError) and e.response.status_code == 404:
                logger.debug(f"# API #\nCould not find {url}")
                return None

            logger.debug(f"# API #\nAttempt failed ({attempt+1}/{max_attempts}) at {url}\n[Exception]\n{e}")

            if attempt < max_attempts-1:
                wait_time = 2**attempt
                logger.debug(f"# API #\nTrying again in {wait_time}s at {url}")
                time.sleep(wait_time)

    logger.debug(f"# API #\nFailed API call {url}")
    return None

def call_api_action(api_call: str, method: str = 'POST', timeout: int = 15, max_attempts: int = 3) -> bool:
    """For write-style endpoints (favorite/unfavorite, etc.) that return no useful body -
    success is judged by status code alone, unlike call_api()'s text-returning read path."""
    url = f"{DOMAIN_CONFIG['api_base']}/{api_call}"
    headers = HEADERS.copy()
    safe_headers = {k: ('***redacted***' if k.lower() == 'cookie' else v) for k, v in headers.items()}

    for attempt in range(max_attempts):
        try:
            logger.debug(f"# API #\n{method} {url} with headers {safe_headers} and timeout {timeout}s")
            response = SESSION.request(method, url, headers=headers, timeout=timeout)
            response.raise_for_status()
            logger.debug(f"# API #\n{method} succeeded at {url} ({response.status_code})")
            return True

        except Exception as e:
            if isinstance(e, requests.HTTPError) and e.response.status_code == 404:
                logger.debug(f"# API #\nCould not find {url}")
                return False

            logger.debug(f"# API #\nAttempt failed ({attempt+1}/{max_attempts}) at {url}\n[Exception]\n{e}")

            if attempt < max_attempts-1:
                wait_time = 2**attempt
                logger.debug(f"# API #\nTrying again in {wait_time}s at {url}")
                time.sleep(wait_time)

    logger.debug(f"# API #\nFailed API call {url}")
    return False
