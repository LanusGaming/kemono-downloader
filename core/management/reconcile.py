import os, logging

from ..creator import Creator
from ..file import File
from ..files import generate_hash, is_archive
from ..kemono import get_all_posts_from_creator
from ..utils import get_hash_from_url
from ..paths import DATA_DIR
from .. import db

logger = logging.getLogger("downloader")

def reconcile(creator: Creator):
    folder_path = f"{DATA_DIR}/{creator.name}_{creator.service}_{creator.id}"
    if not os.path.exists(folder_path):
        logger.info(f"No files on disk for this creator")
        return

    logger.info(f"Fetching creator posts...")
    posts = get_all_posts_from_creator(creator.service, creator.id)
    logger.info(f"Found {len(posts)} posts")

    logger.info(f"Building expected-files map...")
    expected_by_hash: dict[str, File] = {}
    for post in posts:
        post_files, _ = creator.detect_files_in_post(post)
        for file in post_files:
            expected_by_hash[get_hash_from_url(file.url)] = file

    logger.info(f"Scanning disk...")
    counts = {'matched': 0, 'archive_child': 0, 'straggler': 0, 'already_recorded': 0, 'skipped': 0}
    archive_folder_info: dict[str, dict] = {}

    for root, _, filenames in os.walk(folder_path):
        for filename in filenames:
            # sanitize_filename() strips ':' from every real downloaded name, so a literal ':'
            # can only be a filesystem artifact (e.g. Windows Zone.Identifier ADS markers left
            # behind by some file-transfer tools) - never real content.
            if ':' in filename:
                counts['skipped'] += 1
                continue

            path = os.path.join(root, filename)

            if db.path_exists_in_db(path):
                counts['already_recorded'] += 1
                continue

            file_hash = generate_hash(path)

            try:
                index_str, name = filename.split('_', 1)
                file_index = int(index_str)
            except ValueError:
                logger.warning(f"Filename doesn't match the expected convention, skipping -> {path}")
                continue

            matched = expected_by_hash.get(file_hash)
            if matched:
                db.upsert_post(creator.service, creator.id, matched.post_id, matched.post_title, matched.published)

                file = File({
                    'creator_id': creator.id,
                    'creator_service': creator.service,
                    'post_id': matched.post_id,
                    'published': matched.published,
                    'index': file_index,
                    'hash': file_hash,
                    'path': path,
                    'name': name,
                    'url': matched.url,
                    'type': matched.type
                })
                file_id = db.insert_file(file)
                counts['matched'] += 1

                if is_archive(path):
                    archive_folder_info[os.path.splitext(path)[0]] = {
                        'parent_id': file_id,
                        'post_id': matched.post_id,
                        'published': matched.published
                    }
                continue

            parent_info = archive_folder_info.get(root)
            if parent_info:
                file = File({
                    'creator_id': creator.id,
                    'creator_service': creator.service,
                    'post_id': parent_info['post_id'],
                    'published': parent_info['published'],
                    'index': file_index,
                    'hash': file_hash,
                    'path': path,
                    'name': name,
                    'type': 'archive',
                    'parent_archive_id': parent_info['parent_id']
                })
                db.insert_file(file)
                counts['archive_child'] += 1
                continue

            # Straggler: no matching post-file and no matching archive parent (e.g. a deleted
            # post, or a locally modified file). Recorded with best-effort metadata so it's
            # still protected from being re-downloaded - dedup is hash-based, not post-based.
            published = os.path.getmtime(path)
            file = File({
                'creator_id': creator.id,
                'creator_service': creator.service,
                'post_id': None,
                'published': published,
                'index': file_index,
                'hash': file_hash,
                'path': path,
                'name': name,
                'type': 'attachment'
            })
            db.insert_file(file)
            counts['straggler'] += 1

    logger.info(
        f"Reconciled {creator.name} ({creator.service}/{creator.id}): "
        f"{counts['matched']} matched, {counts['archive_child']} archive children, "
        f"{counts['straggler']} stragglers, {counts['already_recorded']} already recorded, "
        f"{counts['skipped']} skipped (non-content files)"
    )
