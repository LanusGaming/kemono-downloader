import hashlib
import os
import subprocess
import zipfile

import py7zr
import pyzipper
import pytest

import core.files
from core.files import (
    UnsafeArchivePath,
    _ensure_safe_archive_paths,
    extract,
    generate_hash,
    generate_hashes,
    get_creators_from_data_dir,
    get_creators_from_file,
    is_archive,
    recursive_move,
    un7z,
    unrar,
    unzip,
)

RAR_AVAILABLE = subprocess.run(['which', 'rar'], capture_output=True).returncode == 0


def make_plain_zip(path, files):
    with zipfile.ZipFile(path, 'w') as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def make_aes_zip(path, files, password):
    with pyzipper.AESZipFile(path, 'w', encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(password.encode())
        for name, content in files.items():
            zf.writestr(name, content)


def make_7z(path, files, password=None):
    with py7zr.SevenZipFile(path, 'w', password=password) as zf:
        for name, content in files.items():
            zf.writestr(content, name)


def make_rar(path, files, tmp_path, password=None):
    src = tmp_path / f"rar_src_{os.path.basename(path)}"
    src.mkdir()
    for name, content in files.items():
        (src / name).write_bytes(content)
    cmd = ['rar', 'a', '-inul']
    if password:
        cmd.append(f'-p{password}')
    cmd.append(str(path))
    cmd.extend(files.keys())
    subprocess.run(cmd, cwd=src, check=True)


# --- _ensure_safe_archive_paths (zip-slip guard) ---

def test_ensure_safe_archive_paths_rejects_parent_traversal(tmp_path):
    with pytest.raises(UnsafeArchivePath):
        _ensure_safe_archive_paths(['../evil.txt'], str(tmp_path))


def test_ensure_safe_archive_paths_rejects_absolute_escape(tmp_path):
    with pytest.raises(UnsafeArchivePath):
        _ensure_safe_archive_paths(['/etc/passwd'], str(tmp_path))


def test_ensure_safe_archive_paths_rejects_sibling_prefix_bypass(tmp_path):
    # "tmp_path_evil" starts with the string "tmp_path" but is not inside it - a naive
    # str.startswith(directory) check (without the os.sep suffix) would wrongly allow this.
    sibling = str(tmp_path) + "_evil/x.txt"
    with pytest.raises(UnsafeArchivePath):
        _ensure_safe_archive_paths([os.path.relpath(sibling, str(tmp_path))], str(tmp_path))


def test_ensure_safe_archive_paths_allows_legitimate_nested_path(tmp_path):
    _ensure_safe_archive_paths(['sub/dir/file.txt'], str(tmp_path))  # must not raise


def test_unzip_real_zip_slip_attempt_is_blocked(tmp_path):
    evil_zip = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil_zip, 'w') as zf:
        zf.writestr('../../evil.txt', b'pwned')

    with pytest.raises(UnsafeArchivePath):
        unzip(str(evil_zip), str(tmp_path / "out"), '')


# --- is_archive ---

def test_is_archive():
    assert is_archive('file.zip') is True
    assert is_archive('file.RAR') is True
    assert is_archive('file.7z') is True
    assert is_archive('file.jpg') is False


# --- unzip: plain, AES, and error paths ---

def test_unzip_plain_archive(tmp_path):
    path = tmp_path / "plain.zip"
    make_plain_zip(path, {'a.txt': b'plain content'})
    out = tmp_path / "out"

    unzip(str(path), str(out), '')

    assert (out / 'a.txt').read_bytes() == b'plain content'


def test_unzip_aes_archive_with_correct_password(tmp_path):
    path = tmp_path / "aes.zip"
    make_aes_zip(path, {'a.txt': b'secret content'}, 'correct-password')
    out = tmp_path / "out"

    unzip(str(path), str(out), 'correct-password')

    assert (out / 'a.txt').read_bytes() == b'secret content'


def test_unzip_aes_archive_with_wrong_password_raises(tmp_path):
    path = tmp_path / "aes.zip"
    make_aes_zip(path, {'a.txt': b'secret content'}, 'correct-password')
    out = tmp_path / "out"

    with pytest.raises(RuntimeError, match="Password incorrect"):
        unzip(str(path), str(out), 'wrong-password')


def test_unzip_aes_archive_with_no_password_raises(tmp_path):
    path = tmp_path / "aes.zip"
    make_aes_zip(path, {'a.txt': b'secret content'}, 'correct-password')
    out = tmp_path / "out"

    with pytest.raises(RuntimeError, match="Password may be required"):
        unzip(str(path), str(out), '')


def test_unzip_plain_zip_wrong_password_raises_via_stdlib_runtimeerror(tmp_path, monkeypatch):
    # pyzipper can't write a classic (non-AES) encrypted zip, so a real fixture for this exact
    # path isn't available - simulate what the standard library raises for a genuine wrong
    # ZipCrypto password (a plain RuntimeError, NOT the NotImplementedError subclass AES zips
    # raise), to confirm it's still routed to the "wrong password" branch, not the AES fallback.
    path = tmp_path / "plain.zip"
    make_plain_zip(path, {'a.txt': b'content'})
    out = tmp_path / "out"

    def fake_testzip(self):
        raise RuntimeError("Bad password for file 'a.txt'")

    monkeypatch.setattr(zipfile.ZipFile, "testzip", fake_testzip)

    with pytest.raises(RuntimeError, match="Password incorrect"):
        unzip(str(path), str(out), 'some-password')


# --- un7z ---

def test_un7z_correct_password(tmp_path):
    path = tmp_path / "test.7z"
    make_7z(path, {'a.txt': b'7z content'}, password='secret')
    out = tmp_path / "out"

    un7z(str(path), str(out), 'secret')

    assert (out / 'a.txt').read_bytes() == b'7z content'


def test_un7z_wrong_password_raises(tmp_path):
    path = tmp_path / "test.7z"
    make_7z(path, {'a.txt': b'7z content'}, password='secret')
    out = tmp_path / "out"

    with pytest.raises(RuntimeError, match="Password incorrect"):
        un7z(str(path), str(out), 'wrong')


def test_un7z_no_password_on_encrypted_archive_raises(tmp_path):
    path = tmp_path / "test.7z"
    make_7z(path, {'a.txt': b'7z content'}, password='secret')
    out = tmp_path / "out"

    with pytest.raises(RuntimeError, match="Password may be required"):
        un7z(str(path), str(out), '')


# --- unrar ---

@pytest.mark.skipif(not RAR_AVAILABLE, reason="rar archiver not installed")
def test_unrar_correct_password(tmp_path):
    path = tmp_path / "test.rar"
    make_rar(path, {'a.txt': b'rar content'}, tmp_path, password='secret')
    out = tmp_path / "out"

    unrar(str(path), str(out), 'secret')

    assert (out / 'a.txt').read_bytes() == b'rar content'


@pytest.mark.skipif(not RAR_AVAILABLE, reason="rar archiver not installed")
def test_unrar_wrong_password_raises(tmp_path):
    path = tmp_path / "test.rar"
    make_rar(path, {'a.txt': b'rar content'}, tmp_path, password='secret')
    out = tmp_path / "out"

    with pytest.raises(RuntimeError, match="Password incorrect"):
        unrar(str(path), str(out), 'wrong')


# --- extract() ---

def test_extract_dispatches_by_extension_and_flattens(tmp_dirs, tmp_path):
    path = tmp_path / "archive.zip"
    make_plain_zip(path, {'a.txt': b'one'})

    result = extract(str(path), '')

    assert len(result) == 1
    result_path, result_name = result[0]
    assert result_name == 'a.txt'
    assert os.path.basename(result_path) == '000.txt'
    assert os.path.dirname(result_path) == str(path.with_suffix(''))


def test_extract_cleans_up_temp_dir_on_success(tmp_dirs, tmp_path):
    path = tmp_path / "archive.zip"
    make_plain_zip(path, {'a.txt': b'one'})

    extract(str(path), '')

    leftover_temp_dirs = [f for f in os.listdir(tmp_dirs["temp"]) if f.endswith('_temp')]
    assert leftover_temp_dirs == []


def test_extract_cleans_up_temp_dir_on_failure_and_reraises(tmp_dirs, tmp_path):
    path = tmp_path / "aes.zip"
    make_aes_zip(path, {'a.txt': b'secret'}, 'correct')

    with pytest.raises(RuntimeError):
        extract(str(path), 'wrong-password')

    leftover_temp_dirs = [f for f in os.listdir(tmp_dirs["temp"]) if f.endswith('_temp')]
    assert leftover_temp_dirs == []


def test_extract_removes_stale_destination_directory_first(tmp_dirs, tmp_path):
    path = tmp_path / "archive.zip"
    make_plain_zip(path, {'a.txt': b'fresh'})
    dest_dir = path.with_suffix('')
    dest_dir.mkdir()
    (dest_dir / "stale_leftover.txt").write_text("from a previous run")

    extract(str(path), '')

    assert not (dest_dir / "stale_leftover.txt").exists()
    assert (dest_dir / "000.txt").read_bytes() == b'fresh'


# --- recursive_move ---

def test_recursive_move_flattens_nested_folders_with_zero_padded_names(tmp_path):
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    (source / "sub").mkdir(parents=True)
    (source / "top.jpg").write_bytes(b'top')
    (source / "sub" / "nested.png").write_bytes(b'nested')
    dest.mkdir()

    files, next_index = recursive_move(str(source), str(dest))

    names = {name for _, name in files}
    assert names == {'top.jpg', 'nested.png'}
    assert next_index == 2
    result_basenames = sorted(os.path.basename(p) for p, _ in files)
    assert result_basenames == ['000.jpg', '001.png']


# --- generate_hash / generate_hashes ---

def test_generate_hash_matches_known_sha256(tmp_path):
    path = tmp_path / "file.txt"
    path.write_bytes(b'hello world')

    expected = hashlib.sha256(b'hello world').hexdigest()
    assert generate_hash(str(path)) == expected


def test_generate_hashes_filters_by_extension_and_is_not_recursive(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b'a')
    (tmp_path / "b.txt").write_bytes(b'b')
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.jpg").write_bytes(b'c')

    result = generate_hashes(str(tmp_path), ['.jpg'])

    paths = {p for p, _ in result}
    assert paths == {str(tmp_path / "a.jpg")}


# --- get_creators_from_file ---

def test_get_creators_from_file_parses_valid_lines(tmp_path):
    path = tmp_path / "creators.txt"
    path.write_text(
        "https://kemono.cr/patreon/user/12345678\n"
        "https://kemono.cr/fanbox/user/87654321\n"
    )

    result = get_creators_from_file(str(path))

    assert result == [('patreon', '12345678'), ('fanbox', '87654321')]


def test_get_creators_from_file_skips_malformed_lines(tmp_path):
    path = tmp_path / "creators.txt"
    path.write_text(
        "not-a-url\n"
        "https://kemono.cr/patreon/post/12345678\n"  # missing /user/
        "https://kemono.cr/patreon/user/12345678\n"  # valid
    )

    result = get_creators_from_file(str(path))

    assert result == [('patreon', '12345678')]


def test_get_creators_from_file_missing_file_returns_empty(tmp_path):
    assert get_creators_from_file(str(tmp_path / "missing.txt")) == []


# --- get_creators_from_data_dir ---

def test_get_creators_from_data_dir_recognizes_known_service_with_numeric_id(tmp_dirs):
    (tmp_dirs["data"] / "SomeArtist_patreon_12345").mkdir()

    result = get_creators_from_data_dir()

    assert result == [('patreon', '12345')]


def test_get_creators_from_data_dir_handles_underscores_in_display_name(tmp_dirs):
    # Parsing anchors from the end of the string (parts[-1]/parts[-2]), so a display name
    # containing underscores must not break service/id extraction.
    (tmp_dirs["data"] / "My_Cool_Artist_Name_patreon_12345").mkdir()

    result = get_creators_from_data_dir()

    assert result == [('patreon', '12345')]


def test_get_creators_from_data_dir_skips_unknown_service(tmp_dirs):
    (tmp_dirs["data"] / "SomeArtist_onlyfans_12345").mkdir()

    assert get_creators_from_data_dir() == []


def test_get_creators_from_data_dir_skips_non_numeric_id(tmp_dirs):
    (tmp_dirs["data"] / "SomeArtist_patreon_notanumber").mkdir()

    assert get_creators_from_data_dir() == []


def test_get_creators_from_data_dir_skips_too_few_parts(tmp_dirs):
    (tmp_dirs["data"] / "justonename").mkdir()

    assert get_creators_from_data_dir() == []


def test_get_creators_from_data_dir_missing_dir_returns_empty(tmp_dirs):
    import shutil
    shutil.rmtree(tmp_dirs["data"])

    assert get_creators_from_data_dir() == []
