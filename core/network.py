import requests, gzip, logging, time
from fake_useragent import UserAgent
from . import config

logger = logging.getLogger("downloader")

ua = UserAgent()
user_agent = ua.chrome

def get_domain_config() -> dict:
    """Built fresh from core.config's current values every call, so a change made through the
    (future) API is picked up on the very next call - no manual resyncing needed."""
    domain = config.DOMAIN
    file_domain = config.FILE_DOMAIN or domain
    return {
        'domain': domain,
        'base_url': f"https://{domain}",
        'api_base': f"https://{domain}/api/v1",
        'referer': f"https://{domain}/",
        'file_base_url': f"https://{file_domain}{config.FILE_PATH_PREFIX}"
    }

def get_headers() -> dict:
    return {
        'User-Agent': user_agent,
        'Accept': 'text/css',
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'keep-alive',
        'Referer': get_domain_config()['referer'],
        'Cookie': f"session={config.SESSION_COOKIE}",
    }

SESSION = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=3, pool_block=False)
SESSION.mount('http://', _adapter)
SESSION.mount('https://', _adapter)

def call_api(api_call: str, timeout: int = 15, max_attempts: int = 7, additional_headers: dict = {}) -> str:
    domain_config = get_domain_config()
    url = f"{domain_config['api_base']}/{api_call}"
    headers = get_headers()
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
    domain_config = get_domain_config()
    url = f"{domain_config['api_base']}/{api_call}"
    headers = get_headers()
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
