import logging
import requests

logger = logging.getLogger("downloader")

def trigger_album_creator(webhook_url: str, timeout: int = 10) -> bool:
    """POSTs to webhook_url to kick off an album-creator run. Treats a 409 response
    (already running) as success."""

    try:
        logger.info(f"Triggering album creator at {webhook_url}...")
        response = requests.post(webhook_url, timeout=timeout)

        if response.status_code == 409:
            logger.info("Album creator run already in progress, skipped")
            return True

        response.raise_for_status()
        logger.info("Triggered album creator successfully")
        return True

    except Exception as e:
        logger.error(f"Could not trigger album creator: {e}")
        return False
