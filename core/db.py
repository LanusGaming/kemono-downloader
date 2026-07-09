import sqlite3, os

from .config import CONFIG_DIR

DB_PATH = os.path.join(CONFIG_DIR, 'kemono.db')

_connection = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS creators (
    service TEXT NOT NULL,
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    last_imported REAL,
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

def ensure_creator(service: str, id: str, name: str, last_imported: float):
    conn = get_connection()
    conn.execute("""
        INSERT INTO creators (service, id, name, last_imported)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (service, id) DO UPDATE SET
            name=excluded.name,
            last_imported=excluded.last_imported
    """, (service, id, name, last_imported))
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
