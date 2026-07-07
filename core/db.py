import sqlite3, json, os

DB_PATH = '/config/kemono.db'

_connection = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS creators (
    service TEXT NOT NULL,
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    last_imported REAL,
    include_regex TEXT NOT NULL DEFAULT '',
    exclude_regex TEXT NOT NULL DEFAULT '',
    allowed_extensions TEXT NOT NULL DEFAULT '[]',
    allowed_types TEXT NOT NULL DEFAULT '[]',
    auto_unzip INTEGER NOT NULL DEFAULT 1,
    keep_unpacked_archives INTEGER NOT NULL DEFAULT 1,
    keep_failed_archives INTEGER NOT NULL DEFAULT 0,
    archive_passwords TEXT NOT NULL DEFAULT '[null]',
    PRIMARY KEY (service, id)
);

CREATE TABLE IF NOT EXISTS posts (
    creator_service TEXT NOT NULL,
    creator_id TEXT NOT NULL,
    post_id TEXT NOT NULL,
    title TEXT,
    published REAL,
    PRIMARY KEY (creator_service, creator_id, post_id),
    FOREIGN KEY (creator_service, creator_id) REFERENCES creators(service, id)
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_service TEXT NOT NULL,
    creator_id TEXT NOT NULL,
    post_id TEXT,
    file_index INTEGER NOT NULL,
    published REAL,
    hash TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    name TEXT,
    url TEXT,
    type TEXT NOT NULL,
    password TEXT,
    parent_archive_id INTEGER REFERENCES files(id),
    FOREIGN KEY (creator_service, creator_id, post_id) REFERENCES posts(creator_service, creator_id, post_id)
);

CREATE INDEX IF NOT EXISTS idx_files_creator_hash ON files(creator_service, creator_id, hash);
CREATE INDEX IF NOT EXISTS idx_files_post ON files(creator_service, creator_id, post_id);
"""

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

def get_connection() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _connection = sqlite3.connect(DB_PATH, check_same_thread=False)
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA journal_mode=WAL")
        _connection.execute("PRAGMA foreign_keys=ON")
        _connection.executescript(SCHEMA)
        _connection.commit()
    return _connection

def get_creator_config(service: str, id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM creators WHERE service=? AND id=?", (service, id)).fetchone()
    if not row:
        return None

    config = dict(row)
    config['ALLOWED_EXTENSIONS'] = json.loads(config.pop('allowed_extensions'))
    config['ALLOWED_TYPES'] = json.loads(config.pop('allowed_types'))
    config['ARCHIVE_PASSWORDS'] = json.loads(config.pop('archive_passwords'))
    config['INCLUDE_REGEX'] = config.pop('include_regex')
    config['EXCLUDE_REGEX'] = config.pop('exclude_regex')
    config['AUTO_UNZIP'] = bool(config.pop('auto_unzip'))
    config['KEEP_UNPACKED_ARCHIVES'] = bool(config.pop('keep_unpacked_archives'))
    config['KEEP_FAILED_ARCHIVES'] = bool(config.pop('keep_failed_archives'))

    return config

def save_creator_config(service: str, id: str, name: str, last_imported: float, config: dict):
    conn = get_connection()
    conn.execute("""
        INSERT INTO creators (service, id, name, last_imported, include_regex, exclude_regex,
                               allowed_extensions, allowed_types, auto_unzip,
                               keep_unpacked_archives, keep_failed_archives, archive_passwords)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (service, id) DO UPDATE SET
            name=excluded.name,
            last_imported=excluded.last_imported,
            include_regex=excluded.include_regex,
            exclude_regex=excluded.exclude_regex,
            allowed_extensions=excluded.allowed_extensions,
            allowed_types=excluded.allowed_types,
            auto_unzip=excluded.auto_unzip,
            keep_unpacked_archives=excluded.keep_unpacked_archives,
            keep_failed_archives=excluded.keep_failed_archives,
            archive_passwords=excluded.archive_passwords
    """, (
        service, id, name, last_imported,
        config['INCLUDE_REGEX'], config['EXCLUDE_REGEX'],
        json.dumps(config['ALLOWED_EXTENSIONS']), json.dumps(config['ALLOWED_TYPES']),
        int(bool(config['AUTO_UNZIP'])), int(bool(config['KEEP_UNPACKED_ARCHIVES'])),
        int(bool(config['KEEP_FAILED_ARCHIVES'])), json.dumps(config['ARCHIVE_PASSWORDS'])
    ))
    conn.commit()

def update_archive_passwords(service: str, id: str, passwords: list):
    conn = get_connection()
    conn.execute(
        "UPDATE creators SET archive_passwords=? WHERE service=? AND id=?",
        (json.dumps(passwords), service, id)
    )
    conn.commit()

def get_creator_hashes(service: str, id: str) -> set[str]:
    conn = get_connection()
    rows = conn.execute("SELECT hash FROM files WHERE creator_service=? AND creator_id=?", (service, id)).fetchall()
    return {row['hash'] for row in rows}

def upsert_post(service: str, id: str, post_id: str, title: str, published: float):
    conn = get_connection()
    conn.execute("""
        INSERT INTO posts (creator_service, creator_id, post_id, title, published)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (creator_service, creator_id, post_id) DO UPDATE SET
            title=excluded.title,
            published=excluded.published
    """, (service, id, post_id, title, published))
    conn.commit()

def insert_file(file) -> int:
    conn = get_connection()
    cur = conn.execute("""
        INSERT INTO files (creator_service, creator_id, post_id, file_index, published, hash, path,
                            name, url, type, password, parent_archive_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        file.creator_service, file.creator_id, file.post_id, file.index, file.published, file.hash, file.path,
        file.name, file.url, file.type, file.password, file.parent_archive_id
    ))
    conn.commit()
    return cur.lastrowid

def path_exists_in_db(path: str) -> bool:
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM files WHERE path=?", (path,)).fetchone()
    return row is not None

def _file_row_to_dict(row: sqlite3.Row) -> dict:
    """Maps a `files` row to a dict usable as `File(...)` constructor input (renames file_index -> index)."""
    data = dict(row)
    data['index'] = data.pop('file_index')
    return data

def get_files_for_creator(service: str, id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM files WHERE creator_service=? AND creator_id=?", (service, id)).fetchall()
    return [_file_row_to_dict(row) for row in rows]

def get_file_by_path(path: str) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM files WHERE path=?", (path,)).fetchone()
    return _file_row_to_dict(row) if row else None

def delete_file(file_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM files WHERE id=?", (file_id,))
    conn.commit()

def count_files_for_creator(service: str, id: str) -> tuple[int, int]:
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) FROM files WHERE creator_service=? AND creator_id=?", (service, id)).fetchone()[0]
    archive = conn.execute("SELECT COUNT(*) FROM files WHERE creator_service=? AND creator_id=? AND type='archive'", (service, id)).fetchone()[0]
    return total, archive
