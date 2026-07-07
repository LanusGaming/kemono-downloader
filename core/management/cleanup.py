import os, logging

from ..creator import Creator
from ..file import File
from ..files import is_archive, get_thread_lock
from .. import db

logger = logging.getLogger("downloader")

def cleanup(creator: Creator):
    files = []

    logger.info(f"Collecting archives...")
    for file_row in db.get_files_for_creator(creator.service, creator.id):
        if not is_archive(file_row['path']) or not os.path.exists(file_row['path']) or file_row['type'] == 'archive':
            continue

        files.append(File(file_row))

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