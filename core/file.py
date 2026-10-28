import os, logging, shutil, time
from requests import HTTPError

from .files import generate_hash
from .network import SESSION, get_headers, wait_for_request_slot
from . import config
from .config import DATA_DIR, TEMP_DIR
from .summary import NotFoundError, DownloadTimeoutError, QuotaExceededError, MirrorBlockedError
from .utils import *

logger = logging.getLogger("downloader")

def _get_retry_after(response) -> int | None:
    """Parses a rate-limited response's Retry-After header (seconds form only) into an int, or
    None if absent/unparseable."""

    value = response.headers.get('Retry-After')
    if value is None:
        return None
    try:
        return max(0, int(value))
    except ValueError:
        return None

DEFAULT_FILE = {
    'id': None,
    'creator_id': '',
    'creator_service': '',
    'creator_name': '',
    'post_id': '',
    'post_title': '',
    'published': 0.0,
    'index': 0,
    'hash': '',
    'path': '',
    'name': '',
    'url': '',
    'type': '',
    'password': '',
    'parent_archive_id': None,
    'source': 'kemono',  # 'kemono', 'gdrive', or 'mega' - see File.download()
    'ref_id': '',  # Drive file ID, or the file's path within its Mega folder
    'link_url': '',  # the Drive/Mega share link this file came from
}

class File:
    def __init__(self, data: dict):
        """Fills fields from DEFAULT_FILE, overridden by `data`. Keys in `data` not present in
        DEFAULT_FILE are silently dropped."""

        default_data = DEFAULT_FILE.copy()
        default_data.update(data)

        for key in DEFAULT_FILE:
            self.__dict__[key] = default_data[key]

    def get_data(self) -> dict:
        """Returns this File's fields as a plain dict - the inverse of __init__."""

        data = DEFAULT_FILE.copy()
        for key in data:
            data[key] = self.__dict__[key]
        
        return data
    
    def get_folder_path(self) -> str:
        return f"/{self.creator_name}_{self.creator_service}_{self.creator_id}/{self.post_title}_{self.post_id}"
    
    def get_filename(self) -> str:
        return f"{self.index:03d}_{self.name}"
    
    def get_temp_filename(self) -> str:
        return f"{self.creator_id}_{self.post_id}_{self.get_filename()}"
    
    def get_temp_download_path(self) -> str:
        return f"{TEMP_DIR}/{self.get_temp_filename()}"

    def get_dest_download_path(self) -> str:
        return DATA_DIR + os.path.join(self.get_folder_path(), self.get_filename())
    
    def download(self) -> None:
        """Downloads this file to a temp path and moves it to its destination, dispatching by
        self.source ('kemono' or 'gdrive' - 'mega' files go through Creator.download_mega_link()
        instead, calling finalize() directly). Sets self.path/self.hash on success."""

        temp_path = self.get_temp_download_path()

        if self.source == 'gdrive':
            self._fetch_gdrive(temp_path)
        else:
            self._fetch_kemono(temp_path)

        self.finalize(temp_path)

    def _fetch_kemono(self, temp_path: str) -> None:
        """Downloads self.url to temp_path, retrying up to DOWNLOAD_MAX_ATTEMPTS times. Every
        attempt waits for a request slot first (see network.wait_for_request_slot()), and a retry
        resumes from what's already in temp_path with a Range request instead of starting over -
        the mirror serves some large files slowly enough that a connection drop mid-file is
        likely, and restarting from zero each time may never finish. A 404 fails immediately
        with no retry (the file isn't imported on the mirror yet); a 403 too
        (the mirror's DDoS-Guard front blocking this IP as a bot - retrying through it, even
        slowly, only escalates the block); a 502 always waits 5s regardless of
        DOWNLOAD_RETRY_DELAY; a 429 (rate limited) honors the server's Retry-After header, or
        waits 30s if none is given, since retrying a rate limit quickly just prolongs it. Raises
        NotFoundError on a 404, MirrorBlockedError on a 403, or DownloadTimeoutError once
        retries are exhausted."""

        dest_path = self.get_dest_download_path()
        max_attempts = config.DOWNLOAD_MAX_ATTEMPTS

        logger.debug(f"Download starting... -> {dest_path}")
        for attempt in range(max_attempts):
            try:
                headers = get_headers()
                resume_from = os.path.getsize(temp_path) if os.path.exists(temp_path) else 0
                if resume_from:
                    headers['Range'] = f"bytes={resume_from}-"
                    logger.debug(f"Resuming from byte {resume_from}... -> {dest_path}")

                wait_for_request_slot()
                # (connect, read) - the read timeout is per socket read, not for the whole file:
                # the mirror sometimes leaves a connection open but stops sending, and it's
                # quicker to resume on a fresh one than to wait that out
                r = SESSION.get(self.url, headers=headers, stream=True, timeout=(30, 60))
                r.raise_for_status()

                # A 206 carries only the rest of the file; a 200 means the server ignored the
                # Range and is sending all of it again, so start over
                mode = 'ab' if resume_from and r.status_code == 206 else 'wb'
                with open(temp_path, mode) as f:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            f.write(chunk)

                if not os.path.exists(temp_path):
                    raise Exception("No file created")

                return

            except Exception as e:
                if isinstance(e, HTTPError) and e.response.status_code == 404:
                    logger.warning(f"Skipping retries (404) -> {dest_path}")
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    raise NotFoundError(f"404 -> {dest_path}") from e

                if isinstance(e, HTTPError) and e.response.status_code == 403:
                    logger.warning(f"Skipping retries (403, blocked by the mirror) -> {dest_path}")
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    raise MirrorBlockedError(f"403 -> {dest_path}") from e

                if isinstance(e, HTTPError) and e.response.status_code == 416:
                    # the partial temp file doesn't fit the remote file - start over
                    os.remove(temp_path)

                wait_time = config.DOWNLOAD_RETRY_DELAY

                if isinstance(e, HTTPError) and e.response.status_code == 502:
                    wait_time = 5
                elif isinstance(e, HTTPError) and e.response.status_code == 429:
                    wait_time = _get_retry_after(e.response) or 30

                logger.warning(f"Attempt failed ({attempt+1}/{max_attempts}) -> {dest_path} ({e})")

                if attempt < max_attempts-1:
                    logger.debug(f"Starting again in {wait_time}s... -> {dest_path}")
                    time.sleep(wait_time)
                else:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)

                    raise DownloadTimeoutError(str(e)) from e

    def _fetch_gdrive(self, temp_path: str) -> None:
        """Downloads this Drive file (self.ref_id) to temp_path, retrying up to
        DOWNLOAD_MAX_ATTEMPTS times. A quota rejection fails immediately with no retry - Drive's
        per-file quota resets on its own schedule, not by waiting seconds."""

        from .external import download_gdrive_file

        dest_path = self.get_dest_download_path()
        max_attempts = config.DOWNLOAD_MAX_ATTEMPTS

        logger.debug(f"Drive download starting... -> {dest_path}")
        for attempt in range(max_attempts):
            try:
                download_gdrive_file(self.ref_id, temp_path)
                if not os.path.exists(temp_path):
                    raise Exception("No file created")
                return

            except QuotaExceededError:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise

            except Exception as e:
                logger.warning(f"Attempt failed ({attempt+1}/{max_attempts}) -> {dest_path} ({e})")

                if attempt < max_attempts-1:
                    time.sleep(config.DOWNLOAD_RETRY_DELAY)
                else:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    raise DownloadTimeoutError(str(e)) from e

    def finalize(self, temp_path: str) -> None:
        """Moves an already-downloaded temp_path to this file's destination, sets its mtimes to
        match the post's publish time, and records self.path/self.hash."""

        dest_path = self.get_dest_download_path()

        logger.info(f"Finished download -> {dest_path}")

        logger.debug(f"Moving... -> {temp_path} to {dest_path}")
        dest_folder = os.path.dirname(dest_path)
        os.makedirs(dest_folder, exist_ok=True)

        shutil.move(temp_path, dest_path)

        file_time = self.published + self.index
        os.utime(dest_path, (file_time, file_time))
        os.utime(dest_folder, (self.published, self.published))
        logger.debug(f"Finished moving -> {dest_path}")

        self.path = dest_path
        self.hash = generate_hash(dest_path)