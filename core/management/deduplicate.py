import os, operator, logging

from ..creator import Creator
from ..files import generate_hash
from .. import db

logger = logging.getLogger("downloader")

def deduplicate(creator: Creator):

    rows = db.get_files_for_creator(creator.service, creator.id)
    creator_files = {row['path']: row['hash'] for row in rows}
    file_types = {row['path']: row['type'] for row in rows}

    folder_path = f"/{creator.name}_{creator.service}_{creator.id}"
    if not os.path.exists('/data' + folder_path):
        logger.info(f"No files for creator found")
        return

    logger.info(f"Searching files...")
    files = sort_files(recursive_scan('/data' + folder_path, creator_files, file_types))

    hashes = []
    duplicates = 0
    for path, hash, rank, time in files:
        if hash in hashes:
            logger.info(f"Found duplicate -> {path}")
            os.remove(path)
            duplicates += 1
        else:
            hashes.append(hash)

    logger.info(f"Removed {duplicates} duplicates (kept {len(hashes)})")

def recursive_scan(folder_path: str, creator_files: dict, file_types: dict) -> list[tuple[str, str, int, float]]:
    files = []

    with os.scandir(folder_path) as entries:
        for entry in entries:
            if entry.is_dir():
                files.extend(recursive_scan(entry.path, creator_files, file_types))
            else:
                rank = 0
                hash = ''

                if entry.path in creator_files:
                    if file_types[entry.path] != 'archive':
                        rank = 2
                    else:
                        rank = 1
                    hash = creator_files[entry.path]
                else:
                    hash = generate_hash(entry.path)

                time = int(os.path.getmtime(entry.path))

                files.append((entry.path, hash, rank, time))
    
    logger.info(f"Found {len(files)} files in {folder_path}")
    return files

def sort_files(files: list[tuple[str, str, int, int]]) -> list[dict]:
    logger.info(f"Sorting files...")
    return sorted(files, key=operator.itemgetter(2, 3), reverse=True)