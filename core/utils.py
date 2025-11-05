import time, datetime, re, os

# Sanitize a filename by removing invalid characters, trailing dots, and limiting length.
def sanitize_filename(name: str, max_length: int = 70) -> str:
    if not name:
        return "unnamed"
    
    # Remove invalid characters
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', name)
    # Replace spaces with underscores
    sanitized = sanitized.replace(' ', '_')
    # Remove multiple consecutive underscores
    sanitized = re.sub(r'_+', '_', sanitized)
    # Remove trailing dots (Windows compatibility)
    sanitized = sanitized.rstrip('.')
    # Trim leading/trailing underscores
    sanitized = sanitized.strip('_')

    # Limit length
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip('.').strip('_')

    # Ensure non-empty
    return sanitized if sanitized else "unnamed"

def get_post_time(post_datetime: str) -> float:
    parts = post_datetime.split('T')
    date_parts = parts[0].split('-')
    time_parts = parts[1].split(':')

    dt = datetime.datetime(year=int(date_parts[0]), month=int(date_parts[1]), day=int(date_parts[2]), hour=int(time_parts[0]), minute=int(time_parts[1]), second=int(time_parts[2].split('.')[0]))
    return time.mktime(dt.timetuple())

def get_hash_from_url(url: str) -> str:
    return os.path.splitext(os.path.basename(url.split('?')[0]))[0]

def sort_creators_by_recency(creators: list[dict], newest_first: bool = True) -> list[dict]:
    return sorted(creators, key=lambda creator: creator['last_imported'], reverse=newest_first)