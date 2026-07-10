import time, datetime, re, os

def sanitize_filename(name: str, max_length: int = 70) -> str:
    """Strips characters invalid in filenames, collapses whitespace/underscores, and truncates
    to max_length. Trailing dots are stripped too, for Windows compatibility."""

    if not name:
        return "unnamed"

    sanitized = re.sub(r'[<>:"/\\|?*]', '_', name)
    sanitized = sanitized.replace(' ', '_')
    sanitized = re.sub(r'_+', '_', sanitized)
    sanitized = sanitized.rstrip('.')
    sanitized = sanitized.strip('_')

    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip('.').strip('_')

    return sanitized if sanitized else "unnamed"

def get_post_time(post_datetime: str) -> float:
    """Parses an ISO-ish `YYYY-MM-DDTHH:MM:SS` string into a Unix timestamp, interpreted in the
    system's local timezone (time.mktime), not UTC."""

    parts = post_datetime.split('T')
    date_parts = parts[0].split('-')
    time_parts = parts[1].split(':')

    dt = datetime.datetime(year=int(date_parts[0]), month=int(date_parts[1]), day=int(date_parts[2]), hour=int(time_parts[0]), minute=int(time_parts[1]), second=int(time_parts[2].split('.')[0]))
    return time.mktime(dt.timetuple())

def get_hash_from_url(url: str) -> str:
    """Extracts the content hash kemono embeds as the filename in its file URLs (query string
    and extension stripped) - not a hash computed here."""

    return os.path.splitext(os.path.basename(url.split('?')[0]))[0]