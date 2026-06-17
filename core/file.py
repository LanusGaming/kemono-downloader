import os, logging, shutil, time
from requests import HTTPError

from .files import generate_hash
from .network import SESSION, HEADERS
from .utils import *

logger = logging.getLogger("downloader")

DEFAULT_FILE = {
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
    'archive_files': []
}

class File:
    def __init__(self, data: dict):
        default_data = DEFAULT_FILE.copy()
        default_data.update(data)

        for key in DEFAULT_FILE:
            self.__dict__[key] = default_data[key]
    
    def get_id(self) -> str:
        if self.type == 'archive':
            return f"{self.archive_files[0]}_{self.index}"
        else:
            return f"{self.post_id}_{self.index}"
        
    def get_data(self) -> dict:
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
        return f"/temp/{self.get_temp_filename()}"
    
    def get_dest_download_path(self) -> str:
        return '/data' + os.path.join(self.get_folder_path(), self.get_filename())
    
    def download(self, max_attempts: int = 60) -> bool:
        temp_path = self.get_temp_download_path()
        dest_path = self.get_dest_download_path()

        logger.debug(f"Download starting... -> {dest_path}")
        for attempt in range(max_attempts):
            try:
                r = SESSION.get(self.url, headers=HEADERS, stream=True, timeout=3600)
                r.raise_for_status()

                with open(temp_path, 'wb') as f:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            f.write(chunk)
                
                if not os.path.exists(temp_path):
                    raise Exception("No file created")
                
                break
            
            except Exception as e:
                wait_time = 1

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