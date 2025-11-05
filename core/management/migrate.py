import os, shutil, logging
from ..kemono import get_all_posts_from_creator, detect_files_in_post
from ..utils import *
from ..file.config import load_creator_config
from ..file.hashes import generate_hashes, get_hashes_filepath, load_hashes, save_hashes
from ..file.archive import unpack, is_archive

logger = logging.getLogger("downloader")

def migrate_creator(target_folder: str, source_creator_folder: str, creator_id: str, creator_name: str, delete_skipped: bool = True, delete_remaining: bool = False):
    all_posts = {}
    creator_service = None

    services = [
        'patreon',
        'fanbox',
        'fantia',
        'boosty',
        'gumroad',
        'subscribestar',
        'dlsite'
    ]

    logger.info(f"\n\n----------|| {creator_name} - {creator_id} ||----------\n")

    logger.info(f"Detecting creator service...")
    for service in services:
        all_posts = {post['id']: post for post in get_all_posts_from_creator(service, creator_id)}

        if len(all_posts) > 0:
            logger.info(f"Identified creator service -> {service}")
            creator_service = service
            break

    if not creator_service:
        logger.error(f"Could not find creator -> {creator_name} ({creator_id})")
        return
    
    logger.info(f"Loading config...")
    creator_config = load_creator_config(creator_id)

    file_count = 0
    skipped = 0
    failed = 0

    logger.info(f"Scanning posts for migratable files...")
    with os.scandir(source_creator_folder) as source_post_folders:
        for source_post_folder in source_post_folders:
            if not source_post_folder.is_dir():
                continue

            source_post_folder_parts = source_post_folder.name.split('_')

            if len(source_post_folder_parts) < 2 or not str.isdigit(source_post_folder_parts[0]):
                continue

            source_post_id = source_post_folder_parts[0]
            source_post_title = '_'.join(source_post_folder_parts[1:len(source_post_folder_parts)])

            if not source_post_id in all_posts:
                logger.warning(f"Could not find post -> {source_post_title} ({source_post_id})")
                continue

            post_data = all_posts[source_post_id]
            post_id = post_data['id']
            post_title = post_data['title']
            post_time = get_post_time(post_data['published'])

            logger.info(f"Found post -> {post_title} ({post_id})")

            post_folder = f"{creator_name}_{creator_service}_{creator_id}/{post_title}_{post_id}"
            dest_folder = os.path.join(target_folder, post_folder)

            logger.info(f"Detecting thumbnails and attachments...")
            post_files = detect_files_in_post(post_data, creator_config['ALLOWED_EXTENSIONS'], True, True, False)
            logger.info(f"Found {len(post_files)} files")

            logger.info(f"Generating hashes for local files...")
            source_hashes = generate_hashes(source_post_folder.path, creator_config['ALLOWED_EXTENSIONS'])
            logger.info(f"Generated {len(source_hashes)} hashes")

            target_hashes_path = get_hashes_filepath(post_folder)
            target_hashes = load_hashes(target_hashes_path)

            missing_hashes = []
            
            logger.info(f"Migrating files...")
            for hash in source_hashes:
                if hash[1] in target_hashes:
                    logger.info(f"File already exists -> {hash[0]}")
                    if delete_skipped:
                        logger.info(f"Deleting...")
                        os.remove(hash[0])
                    skipped += 1
                    continue

                file_count += 1
                
                found = False
                for post_file in post_files:
                    if not hash[1] in post_file[0]:
                        continue

                    found = True
                    
                    source_path = hash[0]
                    dest_path = os.path.join(dest_folder, post_file[1])

                    os.makedirs(dest_folder, exist_ok=True)

                    if creator_config['AUTO_UNZIP'] and is_archive(source_path):
                        unpacked_files = unpack(source_path, creator_config['ALLOWED_EXTENSIONS'], creator_config['ZIP_PASSWORDS'])
                            
                        if len(unpacked_files) > 0:
                            zipfile_folder = os.path.splitext(dest_path)[0]
                            os.makedirs(zipfile_folder, exist_ok=True)
                            
                            index = 0
                            for file_path in unpacked_files:
                                file_dest_path = f"{zipfile_folder}/{os.path.basename(zipfile_folder).split('_')[0]}_{os.path.basename(file_path)}"
                                shutil.move(file_path, file_dest_path)
                                os.utime(file_dest_path, (post_time, post_time))

                                unpacked_files[index] = file_dest_path
                                index += 1
                            
                            os.utime(zipfile_folder, (post_time, post_time))
                    
                    shutil.move(source_path, dest_path)
                    os.utime(dest_path, (post_time, post_time))
                    os.utime(dest_folder, (post_time, post_time))

                    target_hashes[hash[1]] = {
                        'url': post_file[0],
                        'filepath': dest_path
                    }
                    save_hashes(target_hashes, target_hashes_path)

                    logger.info(f"Migrated file -> {source_path}")
                    break
                
                if not found:
                    missing_hashes.append(hash)
            
            if len(missing_hashes) > 0:
                logger.info(f"{len(missing_hashes)} files could not be found")
                logger.info(f"Additionally detecting embedded files...")
                embedded_files = detect_files_in_post(post_data, creator_config['ALLOWED_EXTENSIONS'], False, False, True)
                logger.info(f"Found {len(embedded_files)} files")

                logger.info(f"Migrating leftover files...")
                for hash in missing_hashes:
                    found = False
                    for embedded_file in embedded_files:
                        embedded_filename = embedded_file[1]
                        clean_embedded_filename = '_'.join(embedded_filename.split('_')[1:len(embedded_filename.split('_'))])

                        source_filename = os.path.basename(hash[0])
                        clean_source_filename = '_'.join(source_filename.split('_')[1:len(source_filename.split('_'))])

                        if clean_embedded_filename != clean_source_filename:
                            continue

                        found = True

                        source_path = hash[0]
                        dest_path = os.path.join(dest_folder, embedded_file[1])

                        os.makedirs(dest_folder, exist_ok=True)

                        if creator_config['AUTO_UNZIP'] and is_archive(source_path):
                            unpacked_files = unpack(source_path, creator_config['ALLOWED_EXTENSIONS'], creator_config['ZIP_PASSWORDS'])
                            
                            if len(unpacked_files) > 0:
                                zipfile_folder = os.path.splitext(dest_path)[0]
                                os.makedirs(zipfile_folder, exist_ok=True)
                                
                                index = 0
                                for file_path in unpacked_files:
                                    file_dest_path = f"{zipfile_folder}/{os.path.basename(zipfile_folder).split('_')[0]}_{os.path.basename(file_path)}"
                                    shutil.move(file_path, file_dest_path)
                                    os.utime(file_dest_path, (post_time, post_time))

                                    unpacked_files[index] = file_dest_path
                                    index += 1
                                
                                os.utime(zipfile_folder, (post_time, post_time))
                        
                        shutil.move(source_path, dest_path)
                        os.utime(dest_path, (post_time, post_time))
                        os.utime(dest_folder, (post_time, post_time))

                        target_hashes[hash[1]] = {
                            'url': embedded_file[0],
                            'filepath': dest_path
                        }
                        save_hashes(target_hashes, target_hashes_path)

                        logger.info(f"Migrated file -> {source_path}")
                        break
                    
                    if not found:
                        logger.warning(f"Could not find file in post -> {hash[0]} not in {post_title} ({post_id})")
                        failed += 1
                
            try:
                os.removedirs(source_post_folder.path)
            except:
                pass
            
        if delete_remaining:
            logger.info(f"Deleting {skipped + failed} remaining files...")
            shutil.rmtree(source_creator_folder, ignore_errors=True)

        logger.info(f"Successfully migrated {file_count - failed}/{file_count} ({skipped} skipped)")

def migrate_original_downloads(target_folder: str, source_folder: str, delete_skipped: bool = True, delete_remaining: bool = False):
    logger.info("Scanning for migratable creators...")

    count = 0
    with os.scandir(source_folder) as creator_folders:
        for creator_folder in creator_folders:
            if not creator_folder.is_dir():
                continue

            creator_folder_parts = creator_folder.name.split('_')

            if len(creator_folder_parts) < 2 or not str.isdigit(creator_folder_parts[0]):
                continue

            creator_id = creator_folder_parts[0]
            creator_name = '_'.join(creator_folder_parts[1:len(creator_folder_parts)])

            migrate_creator(target_folder, creator_folder.path, creator_id, creator_name, delete_skipped, delete_remaining)

            try:
                os.removedirs(creator_folder.path)
            except:
                pass

            count += 1
    
    logger.info(f"Migrated {count} creators\n\n")

# Hash migration from old md5-url hashes to file-hashes
# if not isinstance(hashes, dict):
#     logger.info(f"Migrating hashes for {post_title}")
#     new_hashes = {}

#     file_hashes = {}

#     with os.scandir('/data' + target_folder) as existing_post_files:
#         for file in existing_post_files:
#             if not file.is_file() or '.zip_' in file.name:
#                 continue
#             file_hashes[get_file_hash(file.path)] = file.path

#     for url in post_files:
#         file_hash = os.path.splitext(os.path.basename(url.split('?')[0]))[0]
#         if file_hash in file_hashes:
#             new_hashes[file_hash] = {
#                 'url': url,
#                 'filepath': file_hashes.get(file_hash)
#             }
    
#     hashes = new_hashes
#     save_hashes(hashes, hashes_path)


# Old inefficient per file api call
# def migrate_creator_check_individual_hash(target_folder: str, creator_folder: str, creator_id: str, creator_name: str, creator_config: dict, delete_remaining: bool = False):
#     file_hashes = get_original_file_hashes(creator_folder, creator_config['ALLOWED_EXTENSIONS'])

#     migrated = 0
#     skipped = 0
#     for file_hash in file_hashes.keys():
#         file_hash_data = file_hashes[file_hash]
#         filepath = file_hash_data[0]

#         file_data = get_file_data_by_hash(file_hash)

#         if not file_data or not 'hash' in file_data:
#             logger.warning(f"File hash did not retrive any data -> {file_hash}")
#             continue

#         if file_data['hash'] != file_hash:
#             logger.warning(f"File hash does not align: {file_hash} != {file_data['hash']} -> {filepath}")
#             continue

#         posts_data = file_data['posts']

#         post_id = file_hash_data[1]
#         post_title = file_hash_data[2]

#         filename = None
#         file_url = None
#         creator_service = None
#         post_time = None
        
#         found = False
#         for post_data in posts_data:
#             if post_data['user'] != creator_id or post_data['id'] != post_id:
#                 continue

#             files = detect_files_in_post(post_data, creator_config['ALLOWED_EXTENSIONS'], True, True, False)

#             for file in files:
#                 if file_hash in file[0]:
#                     file_url = file[0]
#                     filename = file[1]
#                     creator_service = post_data['service']
#                     post_time = get_post_time(post_data['published'])

#                     found = True
#                     break
                
#                 index += 1
        
#         if not found:
#             logger.warning(f"Could not find file in posts -> {filepath}")
#             continue

#         logger.info(f"File data successfully retrieved -> {filepath}")

#         post_folder = f"{creator_name}_{creator_service}_{creator_id}/{post_title}_{post_id}"

#         hashes_path = get_hashes_filepath(post_folder)
#         hashes = load_hashes(hashes_path)

#         if file_hash in hashes:
#             logger.info(f"File already exists -> {filepath} == {hashes[file_hash]['filepath']}")
#             skipped += 1
#             continue

#         dest_folder = os.path.join(target_folder, post_folder)
#         dest_path = os.path.join(dest_folder, filename)

#         os.makedirs(dest_folder, exist_ok=True)

#         if creator_config['AUTO_UNZIP'] and os.path.splitext(filepath)[1] == '.zip':
#             for unzipped_filepath in unzip(filepath, creator_config['ALLOWED_EXTENSIONS'], creator_config['ZIP_PASSWORDS']):
#                 unzipped_file_dest_path = f"{os.path.splitext(dest_path)[0]}_{os.path.basename(unzipped_filepath)}"
#                 shutil.move(unzipped_filepath, unzipped_file_dest_path)
#                 os.utime(unzipped_file_dest_path, (post_time, post_time))
        
#         shutil.move(filepath, dest_path)
#         os.utime(dest_path, (post_time, post_time))
#         os.utime(dest_folder, (post_time, post_time))
        
#         hashes[file_hash] = {
#             'url': file_url,
#             'filepath': dest_path
#         }
#         save_hashes(hashes, hashes_path)

#         migrated += 1
#         logger.info(f"Successfully migrated file {filepath} -> {dest_path}")

#         try:
#             os.removedirs(os.path.dirname(filepath))
#         except OSError:
#             pass
    
#     if delete_remaining:
#         shutil.rmtree(creator_folder, ignore_errors=True)
    
#     logger.info(f"Migrated {migrated} files ({skipped} skipped)")