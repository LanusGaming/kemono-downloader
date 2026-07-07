import os, threading, shutil, zipfile, rarfile, hashlib, pyzipper, py7zr, struct
from py7zr.exceptions import PasswordRequired
from _lzma import LZMAError

from .utils import sanitize_filename
from dezip import _ZipDecrypter_C
setattr(zipfile, '_ZipDecrypter', _ZipDecrypter_C)

def get_thread_lock():
    if not hasattr(get_thread_lock, 'lock'):
        get_thread_lock.lock = threading.Lock()
    return get_thread_lock.lock

# ARCHIVES
class UnsafeArchivePath(Exception):
    pass

def _ensure_safe_archive_paths(names: list[str], directory: str):
    abs_directory = os.path.abspath(directory)
    for name in names:
        target = os.path.abspath(os.path.join(abs_directory, name))
        if target != abs_directory and not target.startswith(abs_directory + os.sep):
            raise UnsafeArchivePath(f"Archive contains a path outside the extraction directory: {name}")

def is_archive(filepath: str) -> bool:
    return os.path.splitext(filepath)[1].lower() in ['.7z', '.zip', '.rar']

def recursive_move(source_folder: str, destination_folder: str, index: int = 0) -> tuple[list[tuple[str, str]], int]:
    files = []
    with os.scandir(source_folder) as dir:
        for entry in dir:
            if entry.is_dir():
                sub_files = recursive_move(entry.path, destination_folder, index)
                files.extend(sub_files[0])
                index += sub_files[1]
            else:
                filepath = os.path.join(destination_folder, f"{index:03d}{os.path.splitext(entry.path)[1].lower()}")
                shutil.move(entry.path, filepath)
                files.append((filepath, sanitize_filename(entry.name)))

                index += 1

    return (files, index)

def unzip(filepath: str, directory: str, password: str):
    try:
        with zipfile.ZipFile(filepath, 'r') as archive:
            if password:
                archive.setpassword(bytes(password, 'utf-8'))
            archive.testzip()
            _ensure_safe_archive_paths(archive.namelist(), directory)
            archive.extractall(directory)

    except UnsafeArchivePath:
        raise
    except RuntimeError:
        if password:
            raise RuntimeError(f"Password incorrect -> {password}")
        else:
            raise RuntimeError("Password may be required")
    except Exception:
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

def unrar(filepath: str, directory: str, password: str):
    try:
        with rarfile.RarFile(filepath) as archive:
            if password:
                archive.setpassword(bytes(password, 'utf-8'))
            archive.testrar()
            _ensure_safe_archive_paths(archive.namelist(), directory)
            archive.extractall(directory)

    except RuntimeError:
        if password:
            raise RuntimeError(f"Password incorrect -> {password}")
        else:
            raise RuntimeError("Password may be required")

def un7z(filepath: str, directory: str, password: str):
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
    dir = os.path.splitext(filepath)[0]
    temp_dir = f"/temp/{generate_hash(filepath)}_temp"

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
    with open(filepath, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def generate_hashes(folder: str, allowed_exts: list[str]) -> list[tuple[str]]:
    file_hashes = []

    with os.scandir(folder) as files:
        for file in files:
            if not file.is_file() or not os.path.splitext(file.name)[1].lower() in allowed_exts:
                continue

            file_hashes.append((file.path, generate_hash(file.path)))

    return file_hashes


def get_creators_from_file(filepath: str) -> list[tuple[str]]:
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
