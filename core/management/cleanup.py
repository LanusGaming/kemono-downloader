import os, logging

from ..creator import Creator
from ..file import File
from ..files import is_archive, get_thread_lock

logger = logging.getLogger("downloader")

def cleanup(creator: Creator):
    files = []

    logger.info(f"Collecting archives...")
    for file_id in creator.files:
        if not is_archive(creator.files[file_id]['path']) or not os.path.exists(creator.files[file_id]['path']) or creator.files[file_id]['type'] == 'archive':
            continue

        files.append(File(creator.files[file_id]))

    logger.info(f"Found {len(files)} archives")

    for file in files:
        unpack(creator, file)

def unpack(creator: Creator, file: File) -> bool:
    logger.debug(f"Extracting... -> {file.path}")
    if not creator.unpack(file):
        logger.error(f"Extraction failed -> {file.path}")
        return False

    logger.debug(f"Saving file... -> {file.path}")
    with get_thread_lock():
        creator.save_file(file)

    logger.info(f"Extraction successful -> {file.path}")
    
    return True