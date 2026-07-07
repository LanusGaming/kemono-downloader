import logging
import requests

logger = logging.getLogger("downloader")

def trigger_album_creator(webhook_url: str, timeout: int = 10) -> bool:
    """POSTs to an immich-album-webhook instance (see the sibling immich-album-webhook
    project) to kick off one album-creator pass - fire-and-forget, doesn't wait for it
    to finish. No Docker access needed on either side, just plain HTTP."""
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
