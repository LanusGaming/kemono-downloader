import requests, gzip, logging, time
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

DOMAIN_CONFIG = {
    'domain': 'kemono.cr',
    'base_url': 'https://kemono.cr',
    'api_base': 'https://kemono.cr/api/v1',
    'referer': 'https://kemono.cr/'
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


# def get_session():
#     if not hasattr(get_session, 'session'):
#         get_session.session = requests.Session()
#         # Configure connection pool
#         adapter = requests.adapters.HTTPAdapter(
#             pool_connections=20,
#             pool_maxsize=20,
#             max_retries=3,
#             pool_block=False
#         )
#         get_session.session.mount('http://', adapter)
#         get_session.session.mount('https://', adapter)
#     return get_session.session

#def get_domain_config(url):
#    if 'coomer.st' in url:
#        return {
#            'domain': 'coomer.st',
#            'base_url': 'https://coomer.st',
#            'api_base': 'https://coomer.st/api/v1',
#            'referer': 'https://coomer.st/'
#        }
#    else:  # Default to kemono.cr
#        return {
#            'domain': 'kemono.cr',
#            'base_url': 'https://kemono.cr',
#            'api_base': 'https://kemono.cr/api/v1',
#            'referer': 'https://kemono.cr/'
#        }

def call_api(api_call: str, timeout: int = 15, max_attempts: int = 7, additional_headers: dict = {}) -> str:
    url = f"{DOMAIN_CONFIG['api_base']}/{api_call}"
    headers = HEADERS.copy()
    headers.update(additional_headers)

    for attempt in range(max_attempts):
        try:
            logger.debug(f"# API #\nCalling kemono API at {url} with headers {headers} and timeout {timeout}s")
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

#logging.basicConfig(level=logging.DEBUG)
#call_api('fanbox/user/22601389/post/10778167')
#DOMAIN_CONFIG['api_base'] = 'https://kemono.cr/api/v2'
#call_api('file/7df314a1853013aaaed7c1c4c1cf153bbd8150a017c06f86fe6104391a619f9d')