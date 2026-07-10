import os, logging, shutil, time
from requests import HTTPError

from .files import generate_hash
from .network import SESSION, get_headers
from . import config
from .config import DATA_DIR, TEMP_DIR
from .utils import *

logger = logging.getLogger("downloader")

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
    'parent_archive_id': None
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
    
    def download(self) -> bool:
        """Downloads self.url to a temp path and moves it to its destination on success,
        retrying up to DOWNLOAD_MAX_ATTEMPTS times. A 503 fails immediately with no retry; a
        502 always waits 5s regardless of DOWNLOAD_RETRY_DELAY. Sets self.path/self.hash on
        success."""

        temp_path = self.get_temp_download_path()
        dest_path = self.get_dest_download_path()
        max_attempts = config.DOWNLOAD_MAX_ATTEMPTS

        logger.debug(f"Download starting... -> {dest_path}")
        for attempt in range(max_attempts):
            try:
                r = SESSION.get(self.url, headers=get_headers(), stream=True, timeout=3600)
                r.raise_for_status()

                with open(temp_path, 'wb') as f:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            f.write(chunk)
                
                if not os.path.exists(temp_path):
                    raise Exception("No file created")
                
                break
            
            except Exception as e:
                if isinstance(e, HTTPError) and e.response.status_code == 503:
                    logger.warning(f"Skipping retries (503) -> {dest_path}")
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    return False

                wait_time = config.DOWNLOAD_RETRY_DELAY

                if isinstance(e, HTTPError) and e.response.status_code == 502:
                    wait_time = 5

                logger.warning(f"Attempt failed ({attempt+1}/{max_attempts}) -> {dest_path}")
                logger.debug(f"[Exception] {e}")

                if attempt < max_attempts-1:
                    logger.debug(f"Starting again in {wait_time}s... -> {dest_path}")
                    time.sleep(wait_time)
                else:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    
                    return False

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

        return True