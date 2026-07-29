import hashlib, json, os, logging, re, shutil
from bs4 import BeautifulSoup
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from .files import extract, is_archive, get_thread_lock, generate_hash
from .kemono import get_creator_data, get_all_posts_from_creator, get_post_data, get_file_data
from .network import get_domain_config
from .file import File
from .utils import *
from . import config as app_config
from . import conf
from . import db
from . import external
from . import summary
from .summary import (
    DownloadError, ArchivePasswordMissingError, ArchivePasswordIncorrectError, ExtractionError,
    CreatorSummary, FileOutcome,
)

logger = logging.getLogger("downloader")
failure_logger = logging.getLogger("failed")

CREATOR_CONFIG_SCHEMA = {
    'INCLUDE_REGEX': str, 'EXCLUDE_REGEX': str, 'ALLOWED_EXTENSIONS': list, 'ALLOWED_TYPES': list,
    'AUTO_UNZIP': bool, 'KEEP_UNPACKED_ARCHIVES': bool, 'KEEP_FAILED_ARCHIVES': bool,
    'ARCHIVE_PASSWORDS': list,
}
DEFAULT_CREATOR_CONFIG_TEMPLATE = os.path.join(os.path.dirname(__file__), 'creator.conf.default')

class Creator:
    def __init__(self, service: str, id: str):
        """Fetches the creator's profile, then loads its per-creator config and known file
        hashes. Raises RuntimeError if the creator can't be found, or if the profile has an
        `ever_imported` field (some mirrors only) that's explicitly False."""

        data = get_creator_data(service, id)
        if not data:
            raise RuntimeError(f"Could not find creator: {service} - {id}")

        if 'ever_imported' in data and not data['ever_imported']:
            raise RuntimeError(f"Creator not yet imported: {service} - {id}")

        self.name = sanitize_filename(data['name'])
        self.service = data['service']
        self.id = data['id']
        self.last_imported = data['last_imported'] if 'last_imported' in data else data['updated']

        logger.info(f"\n\n----------|| {self.name} - {self.service} - {self.id} ||----------\n")

        logger.info(f"Loading config...")
        self.load_config()
        logger.info(f"Loading hashes...")
        self.load_files()
    
    def _config_path(self) -> str:
        return os.path.join(app_config.CONFIG_DIR, 'creators', f'{self.service}_{self.id}.conf')

    def load_config(self):
        """Reads this creator's .conf (creating it from the template if missing) and applies
        each setting as an instance attribute."""

        path = self._config_path()

        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.copy(DEFAULT_CREATOR_CONFIG_TEMPLATE, path)

        creator_config = conf.read(path, DEFAULT_CREATOR_CONFIG_TEMPLATE, CREATOR_CONFIG_SCHEMA)
        for entry, value in creator_config.items():
            self.__dict__[entry] = value

        db.ensure_creator(self.service, self.id, self.name, self.last_imported)

        if self.INCLUDE_REGEX and self.EXCLUDE_REGEX:
            logger.info("Both INCLUDE_REGEX and EXCLUDE_REGEX are set - EXCLUDE_REGEX will be ignored")

    def save_config(self):
        """Writes this creator's settings to its .conf file. Also called from unpack() when a
        new archive password is learned."""

        path = self._config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        values = {entry: self.__dict__[entry] for entry in CREATOR_CONFIG_SCHEMA}
        conf.write(path, DEFAULT_CREATOR_CONFIG_TEMPLATE, values)

    def load_files(self):
        """Loads this creator's known file hashes into self.hashes, and known external-file
        identities into self.external_urls, for dedup."""

        total, archive_count = db.count_files_for_creator(self.service, self.id)
        if total == 0:
            logger.debug(f"Couldnt load any existing files")

        logger.info(f"Found {total} existing files ({archive_count} from archives)")

        self.hashes = db.get_creator_hashes(self.service, self.id)
        self.external_urls = db.get_creator_external_urls(self.service, self.id)

    def save_file(self, file: File):
        """Inserts `file` into the DB and sets its `.id` in place."""

        file.id = db.insert_file(file)
    
    def detect_files_in_post(self, post: dict, include_kemono_files: bool = True) -> tuple[list[File], int]:
        """Collects the post's thumbnail, attachments, (if allowed) embedded images, and (if
        allowed) linked-out Google Drive/Mega files into File objects, applying
        ALLOWED_TYPES/ALLOWED_EXTENSIONS. Returns (files, skipped count) - does not check against
        already-downloaded hashes. include_kemono_files=False skips the thumbnail/attachment/embed
        detection - for a post not yet imported by the mirror, whose external links (unlike its
        own kemono-hosted files) are still resolvable."""

        file_datas = []
        index = 0

        if include_kemono_files:
            if 'file' in post and post['file'] and 'path' in post['file']:
                file_data = post['file']
                file_datas.append((file_data['path'], file_data.get('name', ''), index, 'thumbnail'))
                index += 1

            if 'attachments' in post:
                for attachment in post['attachments']:
                    if isinstance(attachment, dict) and 'path' in attachment:
                        file_datas.append((attachment['path'], attachment.get('name', ''), index, 'attachment'))
                        index += 1

        # Embeds and external links both need the post's full HTML content. kemono.cr's
        # post-list response only has a truncated `substring` and needs an extra API call for
        # the rest; some mirrors (pawchive.pw) already send full `content` up front.
        needs_content = (not self.ALLOWED_TYPES or 'embed' in self.ALLOWED_TYPES) or 'external' in self.ALLOWED_TYPES
        content = post.get('content') or None

        if needs_content and not content and post.get('substring'):
            post_data = get_post_data(post['service'], post['user'], post['id'])
            if post_data:
                # kemono.cr wraps the post as {"post": {...}}; pawchive.pw returns it flat.
                content = post_data.get('post', post_data).get('content')

        if include_kemono_files and content and (not self.ALLOWED_TYPES or 'embed' in self.ALLOWED_TYPES):
            soup = BeautifulSoup(content, 'html.parser')

            for img in soup.select('img[src]'):
                img_url = img['src']
                img_name = os.path.basename(img_url)
                file_datas.append((img_url, img_name, index, 'embed'))

                index += 1

        # Best-effort, not link-specific - lets a directly-attached protected archive pick up a
        # password the creator wrote in the post body instead of only known/configured ones.
        post_password = external.find_post_password(content) if content else None

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

            file_url = (get_domain_config()['file_base_url'] + file_path).split('f=')[0]
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
                'type': file_type,
                'password': post_password or '',
            })

            files.append(file)

        # External links are opt-in only, never implied by an empty ALLOWED_TYPES - resolving
        # them costs a Drive API call or a megatools subprocess per link.
        if content and 'external' in self.ALLOWED_TYPES:
            external_files, external_skipped = self._detect_external_files(content, post, index)
            files.extend(external_files)
            skipped += external_skipped

        return (files, skipped)

    def _detect_external_files(self, content: str, post: dict, start_index: int) -> tuple[list[File], int]:
        """Finds Google Drive/Mega links in a post's HTML content and expands each into one File
        per underlying file, enumerating folder contents via the Drive API / a megatools listing
        call. A link whose contents can't be listed, or that duplicates an already-recorded
        external file (self.external_urls), is skipped rather than failing the whole post."""

        files = []
        skipped = 0
        index = start_index

        for link in external.find_external_links(content):
            if link['kind'] == 'unsupported':
                logger.warning(f"Skipping unsupported {link['platform']} link -> {link['url']}")
                skipped += 1
                continue

            try:
                if link['platform'] == 'gdrive':
                    entries = external.list_gdrive(link)
                else:
                    entries = ([{'name': None, 'path': ''}] if link['kind'] == 'file'
                               else external.list_mega_folder(link['url']))
            except external.ListingError as e:
                logger.warning(f"Skipping external link ({e}) -> {link['url']}")
                skipped += 1
                continue

            for entry in entries:
                if link['platform'] == 'gdrive':
                    ref_id = entry['id']
                    name = entry['name']
                    identity = f"gdrive:{ref_id}"
                else:
                    # ref_id '' (a lone file link, nothing to enumerate) tells
                    # download_mega_link() to fetch directly instead of selecting from a folder.
                    ref_id = entry['path']
                    name = entry['name'] or os.path.basename(link['url'])
                    identity = f"mega:{link['ref_id']}:{ref_id}" if ref_id else f"mega:{link['ref_id']}"

                if identity in self.external_urls:
                    continue

                file_ext = os.path.splitext(name or '')[1].lower()
                if self.ALLOWED_EXTENSIONS and file_ext not in self.ALLOWED_EXTENSIONS:
                    skipped += 1
                    continue

                files.append(File({
                    'creator_id': self.id,
                    'creator_service': self.service,
                    'creator_name': self.name,
                    'post_id': post['id'],
                    'post_title': sanitize_filename(post['title']),
                    'published': get_post_time(post['published']) if post['published'] else None,
                    'index': index,
                    'name': sanitize_filename(name) if name else identity,
                    'url': identity,
                    'type': 'external',
                    'source': link['platform'],
                    'ref_id': ref_id,
                    'link_url': link['url'],
                    'password': link['password'] or '',
                }))
                self.external_urls.add(identity)
                index += 1

        return (files, skipped)

    def download(self) -> CreatorSummary:
        """Fetches all posts, detects and filters their files (has_full=False skip, then
        INCLUDE/EXCLUDE_REGEX on the post title, then hash dedup against self.hashes), then
        downloads what's new. A file with no publish date borrows a neighboring post's date as a
        fallback. Returns a CreatorSummary of what happened."""

        logger.info(f"Fetching creator posts...")
        posts = get_all_posts_from_creator(self.service, self.id)

        logger.info(f"Found {len(posts)} posts")
        logger.info("Detecting files in posts...")

        files = []
        skipped = {"attachments": 0, "regex": 0, "existing": 0}
        not_imported = 0
        for i, post in enumerate(posts):
            # Some mirrors mark posts whose files haven't been imported yet with has_full=False -
            # kemono.cr itself has no such field, so only act on it when present. External links
            # don't depend on kemono's own import, so they're still checked if enabled.
            not_yet_imported = post.get('has_full') is False
            if not_yet_imported:
                not_imported += 1
                if 'external' not in self.ALLOWED_TYPES:
                    continue

            post_files, post_skipped = self.detect_files_in_post(post, include_kemono_files=not not_yet_imported)
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
                # 'external' files are already deduped via self.external_urls - file.url there
                # is a 'gdrive:'/'mega:' identity, not a kemono URL.
                if file.type != 'external':
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
                if file.type != 'external':
                    self.hashes.add(hash)
                post_has_new_files = True

            if post_has_new_files:
                db.upsert_post(self.service, self.id, post['id'], sanitize_filename(post['title']),
                                get_post_time(post['published']) if post['published'] else None)

        not_imported_note = f" - {not_imported} posts not yet imported" if not_imported else ""
        logger.info(f"Found {len(files) + sum(skipped.values())} files ({sum(skipped.values())} skipped - {skipped['attachments']} ATTACH - {skipped['regex']} REGEX - {skipped['existing']} EXIST){not_imported_note}")

        creator_summary = CreatorSummary(
            service=self.service, id=self.id, name=self.name,
            files_skipped={
                reason: count for reason, count in {
                    summary.SKIP_FILTERED: skipped['attachments'],
                    summary.SKIP_REGEX: skipped['regex'],
                    summary.SKIP_EXISTING: skipped['existing'],
                }.items() if count > 0
            },
            posts_not_imported=not_imported,
        )

        if len(files) == 0:
            logger.info("Skipping...")
            time.sleep(3)
            creator_summary.status = 'no_new_files'
            return creator_summary

        logger.info(f"Starting download for {len(files)} files...")
        res = self.download_all_files(files)

        success = sum(1 for outcome in res.values() if outcome.status == 'success')
        logger.info(f"Downloaded {success}/{len(files)} files")
        time.sleep(5)

        failed_counts = Counter()
        for outcome in res.values():
            if outcome.status == 'failed':
                failed_counts[outcome.reason] += 1

        creator_summary.files_downloaded = success
        creator_summary.files_failed = dict(failed_counts)
        creator_summary.status = 'completed'
        return creator_summary

    def download_all_files(self, files: list[File], max_workers: int = 5,
                            mega_max_workers: int = 2) -> dict[File, FileOutcome]:
        """Downloads kemono files through the main pool, Drive files one at a time (concurrency
        tripped Google's anti-automation block in testing), and Mega files - grouped by shared
        link - through a small pool. Once Drive comes back quota-blocked, the rest of this
        creator's Drive files fail immediately without trying - except the first file of every
        creator, which always attempts a download to probe whether the block has lifted (see
        external.is_gdrive_blocked())."""

        kemono_files = [f for f in files if f.source == 'kemono']
        gdrive_files = [f for f in files if f.source == 'gdrive']
        mega_files = [f for f in files if f.source == 'mega']

        results = {}
        with ThreadPoolExecutor(max_workers=max_workers) as exe:
            futures = {exe.submit(self.download_file, file): file for file in kemono_files}

            for fut in as_completed(futures):
                file = futures[fut]
                results[file] = fut.result()

        for i, file in enumerate(gdrive_files):
            if i > 0 and external.is_gdrive_blocked():
                results[file] = FileOutcome('failed', summary.FAIL_EXTERNAL_QUOTA)
                continue

            results[file] = self.download_file(file)
            if results[file].reason == summary.FAIL_EXTERNAL_QUOTA:
                logger.warning("Google Drive rate limit hit - skipping remaining Drive downloads for this run")
                external.mark_gdrive_blocked()
            elif external.is_gdrive_blocked():
                logger.info("Google Drive download succeeded - block has lifted, resuming normal downloads")
                external.mark_gdrive_unblocked()

        if mega_files:
            by_link = {}
            for file in mega_files:
                by_link.setdefault(file.link_url, []).append(file)

            with ThreadPoolExecutor(max_workers=mega_max_workers) as exe:
                futures = {exe.submit(self.download_mega_link, link, group): group for link, group in by_link.items()}

                for fut in as_completed(futures):
                    results.update(fut.result())

        return results

    def download_file(self, file: File) -> FileOutcome:
        """Downloads and records one file, auto-extracting it if it's an archive. On extraction
        failure, deletes the archive (to retry next run) unless KEEP_FAILED_ARCHIVES is set."""

        try:
            file.download()
        except DownloadError as e:
            logger.error(f"Download failed -> {file.get_dest_download_path()}")
            failure_logger.error(json.dumps({**file.get_data(), 'reason': e.reason}))
            return FileOutcome('failed', e.reason)

        return self._finish_download(file)

    def download_mega_link(self, link_url: str, files: list[File]) -> dict[File, FileOutcome]:
        """Downloads every `files` entry from a single shared Mega link in one megatools
        invocation, then finalizes and records each individually. A lone file link is fetched
        directly; a folder's selection numbers are re-resolved from a fresh listing (contents can
        change between detection and download). On a partial failure, only files not fully on
        disk (size-checked against the listing) are failed - the rest are still recorded."""

        results = {}
        temp_dir = f"{app_config.TEMP_DIR}/mega_{hashlib.sha1(link_url.encode()).hexdigest()}"
        path_map = {}
        path_to_entry = {}
        download_error = None

        try:
            if len(files) == 1 and not files[0].ref_id:
                downloaded_path = external.download_mega_file(link_url, temp_dir)
                path_map = {files[0]: downloaded_path}
            else:
                fresh_listing = external.list_mega_folder(link_url)
                path_to_entry = {entry['path']: entry for entry in fresh_listing}

                numbers = []
                for file in files:
                    entry = path_to_entry.get(file.ref_id)
                    if entry is None:
                        logger.warning(f"Mega file no longer found in folder listing -> {file.link_url}/{file.ref_id}")
                        results[file] = FileOutcome('failed', summary.FAIL_NOT_FOUND)
                        continue
                    numbers.append(entry['number'])
                    path_map[file] = os.path.join(temp_dir, file.ref_id)

                if numbers:
                    external.download_mega_selection(link_url, numbers, temp_dir)

        except DownloadError as e:
            logger.error(f"Mega download failed -> {link_url}")
            download_error = e

        for file, src_path in path_map.items():
            expected_size = path_to_entry.get(file.ref_id, {}).get('size')
            complete = os.path.exists(src_path) and (expected_size is None or os.path.getsize(src_path) == expected_size)

            if not complete:
                reason = download_error.reason if download_error else summary.FAIL_NOT_FOUND
                logger.error(f"Mega download did not produce the expected file -> {src_path}")
                failure_logger.error(json.dumps({**file.get_data(), 'reason': reason}))
                results[file] = FileOutcome('failed', reason)
                continue

            file.finalize(src_path)
            results[file] = self._finish_download(file)

        for file in files:
            if file not in results:
                reason = download_error.reason if download_error else summary.FAIL_NOT_FOUND
                failure_logger.error(json.dumps({**file.get_data(), 'reason': reason}))
                results[file] = FileOutcome('failed', reason)

        shutil.rmtree(temp_dir, ignore_errors=True)
        return results

    def _finish_download(self, file: File) -> FileOutcome:
        """Shared tail for a file whose bytes already exist at file.path (set by File.download()
        or File.finalize()): records it in the DB, then auto-extracts it if it's an archive."""

        logger.debug(f"Saving file data... -> {file.path}")
        with get_thread_lock():
            self.save_file(file)

        if self.AUTO_UNZIP and is_archive(file.path):
            logger.info(f"Extracting... -> {file.path}")

            try:
                self.unpack(file)
            except DownloadError as e:
                logger.error(f"Extraction failed -> {file.path}")
                failure_logger.error(json.dumps({**file.get_data(), 'reason': e.reason}))
                if self.KEEP_FAILED_ARCHIVES:
                    logger.info(f"Keeping failed archive (KEEP_FAILED_ARCHIVES=true) -> {file.path}")
                else:
                    with get_thread_lock():
                        db.delete_file(file.id)
                    os.remove(file.path)
                return FileOutcome('failed', e.reason)

            if not self.KEEP_UNPACKED_ARCHIVES:
                os.remove(file.path)

        return FileOutcome('success')

    def unpack(self, file: File) -> None:
        """Extracts `file`'s archive, trying kemono's known password for its hash before
        ARCHIVE_PASSWORDS. A newly discovered password is persisted via save_config(). Each
        extracted entry is recorded as its own File, skipping ones that duplicate an existing
        hash or have a disallowed extension. Raises ArchivePasswordMissingError /
        ArchivePasswordIncorrectError / ExtractionError on failure."""

        files = []
        passwords = []

        file_data = get_file_data(file.hash)
        if file_data and 'password' in file_data:
            logger.debug(f"Found password {file_data['password']} -> {file.path}")
            passwords.append(file_data['password'])

        # file.password may be pre-filled from the post text - tried before ARCHIVE_PASSWORDS.
        if file.password and file.password not in passwords:
            logger.debug(f"Trying password scraped from post text -> {file.path}")
            passwords.append(file.password)

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
                        self.save_config()

                success = True
                break
            
            except RuntimeError as e:
                logger.debug(f"[Exception] {e}")
                continue

            except Exception as e:
                logger.warning(f"An error occured during extraction -> {file.path}")
                logger.warning(f"[Exception] {e}")
                raise ExtractionError(str(e)) from e

        if not success:
            logger.warning(f"Could not find matching password -> {file.path}")
            # Only "no real password was ever tried" counts as missing - the default config
            # always includes a blank/None entry to test "no password", so an empty `passwords`
            # list isn't the right signal (see core/conf.py's list parser).
            if not any(passwords):
                raise ArchivePasswordMissingError(f"No password configured -> {file.path}")
            else:
                raise ArchivePasswordIncorrectError(f"No matching password -> {file.path}")

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

            time = file.published + file.index + int(archive_file_index)/1000.0
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