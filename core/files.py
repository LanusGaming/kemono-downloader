import os, threading, shutil, zipfile, rarfile, hashlib, pyzipper, py7zr, struct
from py7zr.exceptions import PasswordRequired
from _lzma import LZMAError

from .utils import sanitize_filename
from .config import TEMP_DIR, DATA_DIR
from dezip import _ZipDecrypter_C
setattr(zipfile, '_ZipDecrypter', _ZipDecrypter_C)

def get_thread_lock():
    """Returns a shared lock, created once and cached on this function, for guarding DB/hash-set
    access across concurrent download threads."""

    if not hasattr(get_thread_lock, 'lock'):
        get_thread_lock.lock = threading.Lock()
    return get_thread_lock.lock

# ARCHIVES
class UnsafeArchivePath(Exception):
    pass

def _ensure_safe_archive_paths(names: list[str], directory: str):
    """Raises UnsafeArchivePath if any archive member would extract outside `directory`
    (zip-slip protection)."""

    abs_directory = os.path.abspath(directory)
    for name in names:
        target = os.path.abspath(os.path.join(abs_directory, name))
        if target != abs_directory and not target.startswith(abs_directory + os.sep):
            raise UnsafeArchivePath(f"Archive contains a path outside the extraction directory: {name}")

def is_archive(filepath: str) -> bool:
    return os.path.splitext(filepath)[1].lower() in ['.7z', '.zip', '.rar']

def recursive_move(source_folder: str, destination_folder: str, index: int = 0) -> tuple[list[tuple[str, str]], int]:
    """Flattens `source_folder` (recursively) into `destination_folder`, renaming each file to
    a zero-padded index plus its original extension. Returns the (new path, original sanitized
    name) pairs and the next free index."""

    files = []
    with os.scandir(source_folder) as dir:
        for entry in dir:
            if entry.is_dir():
                sub_files = recursive_move(entry.path, destination_folder, index)
                files.extend(sub_files[0])
                index = sub_files[1]  # the recursive call's return is already the next free
                                       # index, not a count to add to this call's own index
            else:
                filepath = os.path.join(destination_folder, f"{index:03d}{os.path.splitext(entry.path)[1].lower()}")
                shutil.move(entry.path, filepath)
                files.append((filepath, sanitize_filename(entry.name)))

                index += 1

    return (files, index)

def unzip(filepath: str, directory: str, password: str):
    """Extracts a .zip to `directory`, falling back to pyzipper for AES-encrypted zips the
    standard library can't read. Raises RuntimeError on a wrong or missing password."""

    def _aes_fallback():
        if os.path.exists(directory):
            shutil.rmtree(directory, ignore_errors=True)

        try:
            with pyzipper.AESZipFile(filepath) as archive:
                if password:
                    archive.setpassword(bytes(password, 'utf-8'))
                archive.testzip()
                _ensure_safe_archive_paths(archive.namelist(), directory)
                archive.extractall(directory)

        except RuntimeError:
            if password:
                raise RuntimeError(f"Password incorrect -> {password}")
            else:
                raise RuntimeError("Password may be required")

    try:
        with zipfile.ZipFile(filepath, 'r') as archive:
            if password:
                archive.setpassword(bytes(password, 'utf-8'))
            archive.testzip()
            _ensure_safe_archive_paths(archive.namelist(), directory)
            archive.extractall(directory)

    except UnsafeArchivePath:
        raise
    except NotImplementedError:
        # A RuntimeError subclass raised by the standard library for AES-encrypted zips it
        # can't decrypt - not an actual wrong-password signal, so this needs its own clause
        # ahead of the plain RuntimeError one below, or it would be misreported as such.
        _aes_fallback()
    except RuntimeError:
        if password:
            raise RuntimeError(f"Password incorrect -> {password}")
        else:
            raise RuntimeError("Password may be required")
    except Exception:
        _aes_fallback()

def unrar(filepath: str, directory: str, password: str):
    """Extracts a .rar to `directory`. Raises RuntimeError on a wrong or missing password."""

    try:
        with rarfile.RarFile(filepath) as archive:
            if password:
                archive.setpassword(bytes(password, 'utf-8'))
            archive.testrar()
            _ensure_safe_archive_paths(archive.namelist(), directory)
            archive.extractall(directory)

    except (RuntimeError, rarfile.Error):
        # rarfile raises its own exception types (BadRarFile, PasswordRequired,
        # RarWrongPassword, ...) for a wrong/missing password, none of which are RuntimeError
        # subclasses - without this, Creator.unpack()'s "try the next password" loop would
        # abort instead of continuing past the first wrong .rar password.
        if password:
            raise RuntimeError(f"Password incorrect -> {password}")
        else:
            raise RuntimeError("Password may be required")

def un7z(filepath: str, directory: str, password: str):
    """Extracts a .7z to `directory`. Raises RuntimeError on a wrong or missing password."""

    try:
        with py7zr.SevenZipFile(filepath, mode='r', password=password) as archive:
            _ensure_safe_archive_paths(archive.getnames(), directory)
            archive.extractall(path=directory)

    except (LZMAError, PasswordRequired, EOFError, struct.error, OSError, ValueError):
        if password:
            raise RuntimeError(f"Password incorrect -> {password}")
        else:
            raise RuntimeError("Password may be required")

def extract(filepath: str, password: str) -> list[tuple[str, str]]:
    """Extracts `filepath` (by extension: zip/rar/7z) into a fresh directory named after the
    archive, flattening any nested folders. Returns the (path, name) pairs produced. Raises
    RuntimeError if the password is wrong or missing."""

    dir = os.path.splitext(filepath)[0]
    temp_dir = f"{TEMP_DIR}/{generate_hash(filepath)}_temp"

    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)

    ext = os.path.splitext(filepath)[1].lower()

    try:
        if ext == '.zip':
            unzip(filepath, temp_dir, password)

        if ext == '.rar':
            unrar(filepath, temp_dir, password)

        if ext == '.7z':
            un7z(filepath, temp_dir, password)

    except:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    if os.path.exists(dir):
        shutil.rmtree(dir, ignore_errors=True)

    os.makedirs(dir)
    files, count = recursive_move(temp_dir, dir)
    shutil.rmtree(temp_dir, ignore_errors=True)

    return files

# HASHES
def generate_hash(filepath: str) -> str:
    """Returns the SHA-256 hex digest of a file's contents."""

    with open(filepath, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def generate_hashes(folder: str, allowed_exts: list[str]) -> list[tuple[str]]:
    """Returns (path, sha256) for every file directly in `folder` (non-recursive) whose
    extension is in allowed_exts."""

    file_hashes = []

    with os.scandir(folder) as files:
        for file in files:
            if not file.is_file() or not os.path.splitext(file.name)[1].lower() in allowed_exts:
                continue

            file_hashes.append((file.path, generate_hash(file.path)))

    return file_hashes


def get_creators_from_file(filepath: str) -> list[tuple[str]]:
    """Parses one creator profile URL per line from `filepath` (expects a `.../<service>/user/
    <id>` suffix), returning (service, id) pairs. Malformed lines are skipped."""

    if not os.path.exists(filepath):
        return []

    creators = []

    with open(filepath, 'r') as creators_file:
        count = 0
        for creator_url in creators_file.readlines():
            count += 1

            parts = creator_url.strip().split('/')

            if len(parts) < 5 or parts[-2] != 'user':
                continue

            service, creator_id = parts[-3], parts[-1]

            creators.append((service, creator_id))

    return creators

# Matches the folder-naming convention from File.get_folder_path(): {name}_{service}_{id}
KNOWN_SERVICES = {'patreon', 'fanbox', 'fantia', 'boosty', 'gumroad', 'subscribestar', 'dlsite'}

def get_creators_from_data_dir() -> list[tuple[str, str]]:
    """Discovers creators from /data folder names matching `<name>_<service>_<id>`, filtering
    to KNOWN_SERVICES with a numeric id."""

    if not os.path.exists(DATA_DIR):
        return []

    creators = []

    with os.scandir(DATA_DIR) as entries:
        for entry in entries:
            if not entry.is_dir():
                continue

            parts = entry.name.split('_')
            if len(parts) < 3:
                continue

            creator_id = parts[-1]
            service = parts[-2]

            if not creator_id.isdigit() or service not in KNOWN_SERVICES:
                continue

            creators.append((service, creator_id))

    return creators
