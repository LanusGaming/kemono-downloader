import json, time, logging
from .network import call_api

logger = logging.getLogger("downloader")

def get_favorite_creators(timeout: int = 15, max_attempts: int = 7) -> list[dict]:
    response = call_api(f"account/favorites?type=artist", timeout, max_attempts)

    if not response:
        logger.warning(f"Failed retrieving favorite creators")
        return []
    
    try:
        return json.loads(response)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(f"Failed decoding JSON: {e}")
        return []

def get_creator_data(service: str, creator_id: str, timeout: int = 15, max_attempts: int = 7) -> dict:
    response = call_api(f"{service}/user/{creator_id}/profile", timeout, max_attempts)

    if not response:
        logger.warning(f"Failed retrieving creator data -> {creator_id} ({service})")
        return {}
    
    try:
        return json.loads(response)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(f"Failed decoding JSON: {e}")
        return {}

def get_all_posts_from_creator(service: str, creator_id: str, page_size: int = 50, max_pages: int = 200, timeout: int = 15, max_attempts: int = 3) -> list[dict]:
    posts = []

    base_request = f"{service}/user/{creator_id}"

    offset = 0
    for page in range(max_pages):
        url_variants = [
          f"{base_request}/posts?o={offset}", # Try with /posts suffix
          f"{base_request}?o={offset}", # Original format as fallback
          f"{base_request}?offset={offset}&limit={page_size}", # Try different parameter names
        ]

        success = False
        response = None

        for url in url_variants:
            response = call_api(url, timeout, max_attempts, {'Cache-Control': 'max-age=0'})
            if response:
                success = True
                break

        if not success:
            break
        
        try:
            data = json.loads(response)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"Failed decoding JSON: {e}")
            break

        if not isinstance(data, list):
            # Sometimes the API returns an object with a posts array
            if isinstance(data, dict):
                if 'posts' in data:
                    data = data['posts']
                elif 'data' in data:
                    data = data['data']
                else:
                    break
            else:
                break

        if not data:
            logger.warning(f"Failed retrieving posts (page {page + 1}) -> -> {creator_id} ({service})")
            break

        posts.extend(data)

        if len(data) < page_size:
            break
            
        offset += page_size
        time.sleep(0.5)

    if len(posts) == 0:
        logger.warning(f"Could not find any posts -> {creator_id} ({service})")
    
    return posts

def get_post_data(service: str, creator_id: str, post_id: str, timeout: int = 10, max_attempts: int = 7) -> dict:
    response = call_api(f"{service}/user/{creator_id}/post/{post_id}", timeout, max_attempts)

    if not response:
        logger.warning(f"Failed retrieving file data -> {post_id} from {creator_id} ({service})")
        return {}
    
    try:
        return json.loads(response)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(f"Failed decoding JSON: {e}")
        return {}

def get_post_by_file_hash(file_hash: str, timeout: int = 10, max_attempts: int = 7) -> dict:
    response = call_api(f"search_hash/{file_hash}", timeout, max_attempts)

    if not response:
        logger.warning(f"Failed retrieving file data -> {file_hash}")
        return {}
    
    try:
        return json.loads(response)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(f"Failed decoding JSON: {e}")
        return {}
    
def get_file_data(hash: str, timeout: int = 10, max_attempts: int = 7) -> dict:
    response = call_api(f"file/{hash}", timeout, max_attempts)

    if not response:
        #logger.warning(f"Failed retrieving file data -> {hash}")
        return {}
    
    try:
        return json.loads(response)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(f"Failed decoding JSON: {e}")
        return {}