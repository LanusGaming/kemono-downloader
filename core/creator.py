import os, logging, re
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

from .files import extract, is_archive, get_thread_lock, generate_hash
from .kemono import get_creator_data, get_all_posts_from_creator, get_post_data, get_file_data
from .network import DOMAIN_CONFIG
from .file import File
from .utils import *
from . import db

logger = logging.getLogger("downloader")
failure_logger = logging.getLogger("failed")

DEFAULT_CREATOR_CONFIG = {
    'INCLUDE_REGEX': '',
    'EXCLUDE_REGEX': '',
    'ALLOWED_EXTENSIONS': ['.jpg', '.jpeg', '.png', '.zip', '.mp4', '.gif', '.pdf', '.7z', '.mp3', '.wav', '.rar', '.mov', '.docx', '.jpe', '.webp'],
    'ALLOWED_TYPES': ['attachment'],
    'AUTO_UNZIP': True,
    'KEEP_UNPACKED_ARCHIVES': True,
    'KEEP_FAILED_ARCHIVES': False,
    'ARCHIVE_PASSWORDS': [None]
}

class Creator:
    def __init__(self, service: str, id: str):
        data = get_creator_data(service, id)
        if not data:
            raise RuntimeError(f"Could not find creator: {service} - {id}")

        self.name = sanitize_filename(data['name'])
        self.service = data['service']
        self.id = data['id']
        self.last_imported = data['last_imported'] if 'last_imported' in data else data['updated']

        logger.info(f"\n\n----------|| {self.name} - {self.service} - {self.id} ||----------\n")

        logger.info(f"Loading config...")
        self.load_config()
        logger.info(f"Loading hashes...")
        self.load_files()
    
    def load_config(self):
        config = db.get_creator_config(self.service, self.id)
        creator_config = DEFAULT_CREATOR_CONFIG.copy()

        for entry in creator_config:
            if config and entry in config and config[entry]:
                creator_config[entry] = config[entry]

            self.__dict__[entry] = creator_config[entry]

        db.save_creator_config(self.service, self.id, self.name, self.last_imported, creator_config)

        if self.INCLUDE_REGEX and self.EXCLUDE_REGEX:
            logger.info("Both INCLUDE_REGEX and EXCLUDE_REGEX are set - EXCLUDE_REGEX will be ignored")

    def load_files(self):
        total, archive_count = db.count_files_for_creator(self.service, self.id)
        if total == 0:
            logger.debug(f"Couldnt load any existing files")

        logger.info(f"Found {total} existing files ({archive_count} from archives)")

        self.hashes = db.get_creator_hashes(self.service, self.id)

    def save_file(self, file: File):
        file.id = db.insert_file(file)
    
    def detect_files_in_post(self, post: dict) -> tuple[list[File], int]:
        file_datas = []
        index = 0

        # Thumbnail detection
        if 'file' in post and post['file'] and 'path' in post['file']:
            file_data = post['file']
            file_datas.append((file_data['path'], file_data.get('name', ''), index, 'thumbnail'))
            index += 1

        # Attachments detection
        if 'attachments' in post:
            for attachment in post['attachments']:
                if isinstance(attachment, dict) and 'path' in attachment:
                    file_datas.append((attachment['path'], attachment.get('name', ''), index, 'attachment'))
                    index += 1

        # Content images detection
        if (not self.ALLOWED_TYPES or 'embed' in self.ALLOWED_TYPES) and 'substring' in post and post['substring']:
            post_data = get_post_data(post['service'], post['user'], post['id'])

            soup = BeautifulSoup(post_data['post']['content'], 'html.parser')

            for img in soup.select('img[src]'):
                img_url = img['src']
                img_name = os.path.basename(img_url)
                file_datas.append((img_url, img_name, index, 'embed'))
                
                index += 1
        
        files = []
        skipped = 0

        for file_data in file_datas:
            file_path = file_data[0]
            file_name = file_data[1]
            file_index = file_data[2]
            file_type = file_data[3]

            if self.ALLOWED_TYPES and not file_type in self.ALLOWED_TYPES:
                skipped += 1
                continue

            name_ext = os.path.splitext(file_name)[1].lower()
            path_ext = os.path.splitext(file_path)[1].lower()
            file_ext = name_ext if name_ext else path_ext

            if self.ALLOWED_EXTENSIONS and not file_ext in self.ALLOWED_EXTENSIONS:
                skipped += 1
                continue

            file_url = (DOMAIN_CONFIG['file_base_url'] + file_path).split('f=')[0]
            hash = get_hash_from_url(file_url)
            
            if not file_name:
                if 'f=' in file_path:
                    file_name = file_path.split("f=")[1]
                else:
                    file_name = hash + file_ext
            
            file = File({
                'creator_id': self.id,
                'creator_service': self.service,
                'creator_name': self.name,
                'post_id': post['id'],
                'post_title': sanitize_filename(post['title']),
                'published': get_post_time(post['published']) if post['published'] else None,
                'index': file_index,
                'name': sanitize_filename(file_name),
                'url': file_url,
                'type': file_type
            })

            files.append(file)

        return (files, skipped)

    def download(self):
        logger.info(f"Fetching creator posts...")
        posts = get_all_posts_from_creator(self.service, self.id)

        logger.info(f"Found {len(posts)} posts")
        logger.info("Detecting files in posts...")

        files = []
        skipped = {"attachments": 0, "regex": 0, "existing": 0}
        for i, post in enumerate(posts):
            post_files, post_skipped = self.detect_files_in_post(post)
            skipped["attachments"] += post_skipped

            if self.INCLUDE_REGEX:
                if not re.fullmatch(self.INCLUDE_REGEX, post['title']):
                    skipped["regex"] += len(post_files)
                    continue
            elif self.EXCLUDE_REGEX:
                if re.fullmatch(self.EXCLUDE_REGEX, post['title']):
                    skipped["regex"] += len(post_files)
                    continue

            post_has_new_files = False
            for file in post_files:
                hash = get_hash_from_url(file.url)

                if hash in self.hashes:
                    skipped["existing"] += 1
                    continue

                if not file.published:
                    if i > 0:
                        fallback_published = posts[i - 1]['published']
                    elif i + 1 < len(posts):
                        fallback_published = posts[i + 1]['published']
                    else:
                        fallback_published = None

                    if fallback_published:
                        post['published'] = fallback_published
                        file.published = get_post_time(fallback_published) - 1000
                    else:
                        file.published = time.time()

                files.append(file)
                self.hashes.add(hash)
                post_has_new_files = True

            if post_has_new_files:
                db.upsert_post(self.service, self.id, post['id'], sanitize_filename(post['title']),
                                get_post_time(post['published']) if post['published'] else None)

        logger.info(f"Found {len(files) + sum(skipped.values())} files ({sum(skipped.values())} skipped - {skipped['attachments']} ATTACH - {skipped['regex']} REGEX - {skipped['existing']} EXIST)")
        if len(files) == 0:
            logger.info("Skipping...")
            time.sleep(3)
            return

        logger.info(f"Starting download for {len(files)} files...")
        res = self.download_all_files(files)

        success = sum(v for v in res.values())
        logger.info(f"Downloaded {success}/{len(files)} files")
        time.sleep(5)
    
    def download_all_files(self, files: list[File], max_workers: int = 5) -> dict[File, bool]:
        results = {}
        with ThreadPoolExecutor(max_workers=max_workers) as exe:
            futures = {exe.submit(self.download_file, file): file for file in files}

            for fut in as_completed(futures):
                file = futures[fut]
                results[file] = fut.result()

        return results

    def download_file(self, file: File) -> bool:
        if not file.download():
            logger.error(f"Download failed -> {file.get_dest_download_path()}")
            failure_logger.error(file.get_data())
            return False

        logger.debug(f"Saving file data... -> {file.path}")
        with get_thread_lock():
            self.save_file(file)

        if self.AUTO_UNZIP and is_archive(file.path):
            logger.info(f"Extracting... -> {file.path}")

            if not self.unpack(file):
                logger.error(f"Extraction failed -> {file.path}")
                failure_logger.error(file.get_data())
                with get_thread_lock():
                    db.delete_file(file.id)
                os.remove(file.path)
                return False

            if not self.KEEP_UNPACKED_ARCHIVES:
                os.remove(file.path)

        return True

    def unpack(self, file: File) -> bool:
        files = []
        passwords = []

        file_data = get_file_data(file.hash)
        if file_data and 'password' in file_data:
            logger.debug(f"Found password {file_data['password']} -> {file.path}")
            passwords.append(file_data['password'])

        for pwd in self.ARCHIVE_PASSWORDS:
            if not pwd in passwords:
                passwords.append(pwd)

        logger.debug(f"Passwords: {passwords}")

        logger.debug(f"Trying passwords... -> {file.path}")
        success = False
        for password in passwords:
            try:
                logger.debug(f"Trying password {password} -> {file.path}")
                files = extract(file.path, password)

                logger.debug(f"Correct password is {password} -> {file.path}")
                file.password = password

                if not password in self.ARCHIVE_PASSWORDS:
                    self.ARCHIVE_PASSWORDS.append(password)

                    with get_thread_lock():
                        db.update_archive_passwords(self.service, self.id, self.ARCHIVE_PASSWORDS)

                success = True
                break
            
            except RuntimeError as e:
                logger.debug(f"[Exception] {e}")
                continue

            except Exception as e:
                logger.warning(f"An error occured during extraction -> {file.path}")
                logger.warning(f"[Exception] {e}")
                return False

        if not success:
            logger.warning(f"Could not find matching password -> {file.path}")
            return False

        logger.debug(f"Renaming archive files... -> {file.path}")
        archive_folder = os.path.splitext(file.path)[0]
        for archive_file_path, archive_file_name in files:
            archive_file_hash = generate_hash(archive_file_path)
            wrong_ext = os.path.splitext(archive_file_path)[1] not in self.ALLOWED_EXTENSIONS

            with get_thread_lock():
                duplicate = archive_file_hash in self.hashes
                if not duplicate and not wrong_ext:
                    self.hashes.add(archive_file_hash)

            if duplicate or wrong_ext:
                if os.path.exists(archive_file_path):
                    os.remove(archive_file_path)
                continue

            archive_file_index = os.path.splitext(os.path.basename(archive_file_path))[0]
            new_archive_file_name = f"{archive_file_index}_{archive_file_name}"
            new_archive_file_path = f"{archive_folder}/{file.index:03d}_{new_archive_file_name}"
            os.rename(archive_file_path, new_archive_file_path)

            time = file.published + int(archive_file_index)/1000.0
            os.utime(new_archive_file_path, (time, time))

            archive_file = File(file.get_data())
            archive_file.index = int(archive_file_index)
            archive_file.path = new_archive_file_path
            archive_file.name = archive_file_name
            archive_file.hash = archive_file_hash
            archive_file.type = 'archive'
            archive_file.parent_archive_id = file.id

            with get_thread_lock():
                self.save_file(archive_file)

        os.utime(archive_folder, (file.published, file.published))

        return True