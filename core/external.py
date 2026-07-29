import os, re, logging, subprocess, time
import requests
from bs4 import BeautifulSoup

from . import config
from .summary import QuotaExceededError, UnsupportedLinkError, ExternalDownloadError, DownloadTimeoutError

logger = logging.getLogger("downloader")

class ListingError(Exception):
    """A Drive/Mega link's contents couldn't be enumerated - bad/missing API key, dead link, or
    network error."""

# --- Link + password detection ---------------------------------------------------------------

GDRIVE_FILE_RE = re.compile(
    r'https?://drive\.google\.com/(?:file/d/([\w-]{10,})|(?:open|uc)\?(?:[^\s<>]*[?&])?id=([\w-]{10,}))',
    re.I)
GDRIVE_FOLDER_RE = re.compile(
    r'https?://drive\.google\.com/(?:drive/(?:u/\d+/)?folders/([\w-]{10,})|folderview\?id=([\w-]{10,}))',
    re.I)

# `\s*` after the domain also matches a space some creators insert before '#...' to dodge
# auto-linkification. Both the current and pre-2018 URL schemes are matched.
MEGA_NEW_FILE_RE = re.compile(r'https?://mega\.(?:nz|co\.nz)/file/([\w-]+)#([\w-]+)', re.I)
MEGA_NEW_FOLDER_RE = re.compile(r'https?://mega\.(?:nz|co\.nz)/folder/([\w-]+)#([\w-]+)', re.I)
MEGA_OLD_FOLDER_RE = re.compile(r'https?://mega\.(?:nz|co\.nz)/\s*#F!([\w-]+)!([\w-]+)', re.I)
MEGA_OLD_FILE_RE = re.compile(r'https?://mega\.(?:nz|co\.nz)/\s*#!([\w-]+)!([\w-]+)', re.I)
# Mega's link-level password feature - megatools can't open these, so they're only detected to
# be reported as unsupported.
MEGA_PROTECTED_RE = re.compile(r'https?://mega\.(?:nz|co\.nz)/\s*#P!\S+', re.I)

# The value is restricted to ASCII alnum/-/_ so a CJK label can't false-positive into the kana
# that follows an unrelated word (e.g. "パスタ" / pasta). Latin "pass"/"password" requires a
# colon or whitespace before the value ("passenger" shouldn't match); CJK labels don't need a
# separator at all, since a script change is already an unambiguous boundary (e.g. "密码0000").
_LATIN_LABEL = r'pass(?:word)?'
_CJK_LABEL = r'パスワード|パス|密码|解压密码|口令|비밀번호|암호'
_VALUE = r'[A-Za-z0-9][A-Za-z0-9_\-]{1,39}'
PASSWORD_RE = re.compile(
    rf'(?:{_LATIN_LABEL})(?:\s*[:：]\s*({_VALUE})|\s+([A-Za-z0-9][A-Za-z0-9_\-]{{3,39}}))'
    rf'|(?:{_CJK_LABEL})(?:\s*[:：]\s*({_VALUE})|\s*({_VALUE}))',
    re.I)

def _find_password_near(text: str, start: int, end: int, window: int = 300) -> str | None:
    """Best-effort scrape for a password near a link's position in the post's plain text - checks
    after the link first, then before. Often returns None."""

    m = PASSWORD_RE.search(text[end:end + window])
    if not m:
        m = PASSWORD_RE.search(text[max(0, start - window):start])
    if not m:
        return None

    return next((g for g in m.groups() if g), None)

def find_post_password(html_content: str) -> str | None:
    """Best-effort scrape for a password anywhere in the post's plain text, for archives whose
    password isn't tied to a specific external link (e.g. a directly-attached protected zip)."""

    if not html_content:
        return None

    text = BeautifulSoup(html_content, 'html.parser').get_text('\n')
    m = PASSWORD_RE.search(text)
    if not m:
        return None

    return next((g for g in m.groups() if g), None)

def find_external_links(html_content: str) -> list[dict]:
    """Scans a post's HTML content for Google Drive/Mega links in plain text (not hidden behind
    an href with different link text), one dict per distinct link: {'platform': 'gdrive'|'mega',
    'kind': 'file'|'folder'|'unsupported', 'url', 'ref_id', 'password'}."""

    if not html_content:
        return []

    text = BeautifulSoup(html_content, 'html.parser').get_text('\n')

    links = []
    seen = set()

    def add(platform: str, kind: str, match: re.Match, ref_id: str | None):
        url = match.group(0).strip()
        if url in seen:
            return
        seen.add(url)
        password = _find_password_near(text, match.start(), match.end())
        links.append({'platform': platform, 'kind': kind, 'url': url, 'ref_id': ref_id, 'password': password})

    for m in GDRIVE_FILE_RE.finditer(text):
        add('gdrive', 'file', m, m.group(1) or m.group(2))
    for m in GDRIVE_FOLDER_RE.finditer(text):
        add('gdrive', 'folder', m, m.group(1) or m.group(2))
    for m in MEGA_NEW_FILE_RE.finditer(text):
        add('mega', 'file', m, m.group(1))
    for m in MEGA_NEW_FOLDER_RE.finditer(text):
        add('mega', 'folder', m, m.group(1))
    for m in MEGA_OLD_FILE_RE.finditer(text):
        add('mega', 'file', m, m.group(1))
    for m in MEGA_OLD_FOLDER_RE.finditer(text):
        add('mega', 'folder', m, m.group(1))
    for m in MEGA_PROTECTED_RE.finditer(text):
        add('mega', 'unsupported', m, None)

    return links

# --- Google Drive (Drive API v3, read-only, public links only) -------------------------------

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
DRIVE_FOLDER_MIMETYPE = 'application/vnd.google-apps.folder'

# Set once a Drive download comes back quota-blocked, so the rest of the run can skip straight
# to failing instead of paying EXTERNAL_DRIVE_DELAY first. Module-level rather than per-Creator
# so it persists across every creator in the run - download.py runs as a fresh subprocess each
# time (see scheduler.py), so this resets naturally on the next run.
_gdrive_blocked = False

def is_gdrive_blocked() -> bool:
    return _gdrive_blocked

def mark_gdrive_blocked() -> None:
    global _gdrive_blocked
    _gdrive_blocked = True

def mark_gdrive_unblocked() -> None:
    global _gdrive_blocked
    _gdrive_blocked = False

def list_gdrive(link: dict) -> list[dict]:
    """Returns one dict per downloadable file for a Drive link: {'id', 'name', 'size'}. A file
    link yields a single entry; a folder link recurses into subfolders (native Google Docs/
    Sheets/etc. entries are skipped - they have no raw bytes to download via alt=media)."""

    if not config.GOOGLE_API_KEY:
        raise ListingError("GOOGLE_API_KEY is not set - cannot resolve Google Drive links")

    if link['kind'] == 'file':
        return [_gdrive_file_meta(link['ref_id'])]
    if link['kind'] == 'folder':
        return _gdrive_list_folder(link['ref_id'])

    raise ListingError(f"Unsupported Google Drive link kind: {link['kind']}")

def _gdrive_get(path: str, params: dict, timeout: int = 20) -> dict:
    try:
        r = requests.get(f"{DRIVE_API_BASE}/{path}", params={**params, 'key': config.GOOGLE_API_KEY}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise ListingError(f"Google Drive API request failed ({path}): {e}") from e

def _gdrive_file_meta(file_id: str) -> dict:
    data = _gdrive_get(f"files/{file_id}", {'fields': 'id,name,size,mimeType', 'supportsAllDrives': 'true'})
    return {'id': data['id'], 'name': data.get('name') or data['id'], 'size': int(data.get('size') or 0)}

def _gdrive_list_folder(folder_id: str) -> list[dict]:
    entries = []
    stack = [folder_id]

    while stack:
        current = stack.pop()
        page_token = None

        while True:
            params = {
                'q': f"'{current}' in parents and trashed = false",
                'fields': 'nextPageToken, files(id,name,size,mimeType)',
                'pageSize': 1000,
                'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true',
            }
            if page_token:
                params['pageToken'] = page_token

            data = _gdrive_get('files', params)

            for item in data.get('files', []):
                if item['mimeType'] == DRIVE_FOLDER_MIMETYPE:
                    stack.append(item['id'])
                elif item['mimeType'].startswith('application/vnd.google-apps.'):
                    logger.debug(f"Skipping native Google Docs file (no raw download) -> {item.get('name')}")
                else:
                    entries.append({'id': item['id'], 'name': item.get('name') or item['id'], 'size': int(item.get('size') or 0)})

            page_token = data.get('nextPageToken')
            if not page_token:
                break

    return entries

def download_gdrive_file(file_id: str, dest_path: str) -> None:
    """Streams a Drive file to dest_path via alt=media. Raises QuotaExceededError on any 403 -
    Google's anti-abuse block sometimes returns a plain HTML page with no machine-readable
    reason, not just the documented "download quota exceeded" message."""

    time.sleep(config.EXTERNAL_DRIVE_DELAY)

    try:
        r = requests.get(
            f"{DRIVE_API_BASE}/files/{file_id}",
            params={'key': config.GOOGLE_API_KEY, 'alt': 'media', 'supportsAllDrives': 'true'},
            stream=True, timeout=3600,
        )

        if r.status_code == 403:
            # The anti-abuse block is an HTML page, not JSON - pull its text out for the log.
            if 'html' in r.headers.get('Content-Type', ''):
                reason = ' '.join(BeautifulSoup(r.text, 'html.parser').stripped_strings)[:200]
            else:
                reason = r.text.strip().replace('\n', ' ')[:200]
            raise QuotaExceededError(f"Google Drive rejected the download (403) -> {file_id}: {reason}")

        r.raise_for_status()

        with open(dest_path, 'wb') as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)

    except requests.RequestException as e:
        raise ExternalDownloadError(str(e)) from e

# --- Mega (via the megatools binary - public links need no account) --------------------------

ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')
_MEGA_LISTING_LINE_RE = re.compile(r'^(\d+)\.\s+(.*?)\s*(?:\(([\d.]+)\s*(\S*)\))?$')
_MEGA_SIZE_UNITS = {
    'B': 1, 'KIB': 1024, 'MIB': 1024**2, 'GIB': 1024**3, 'TIB': 1024**4,
    'KB': 1000, 'MB': 1000**2, 'GB': 1000**3, 'TB': 1000**4,
}

def _parse_mega_size(value: str | None, unit: str | None) -> int | None:
    if not value or unit is None or unit.upper() not in _MEGA_SIZE_UNITS:
        return None
    return int(float(value) * _MEGA_SIZE_UNITS[unit.upper()])

def _run_megatools(args: list[str], input_text: str | None = None, timeout: int = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            [config.MEGATOOLS_BIN, *args], input=input_text,
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError as e:
        raise ListingError("megatools binary not found - required for Mega links") from e
    except subprocess.TimeoutExpired as e:
        raise ListingError(f"megatools timed out running: {' '.join(args)}") from e

def _dir_bytes(path: str) -> int:
    total = 0
    for root, _, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total

def _run_megatools_watched(args: list[str], input_text: str, dest_dir: str, timeout: int,
                            stall_timeout: int) -> subprocess.CompletedProcess:
    """Like _run_megatools(), but polls dest_dir's total size and kills the process early if it
    goes stall_timeout seconds without growing - megatools can hang on Mega's bandwidth quota
    instead of reporting it as an error."""

    try:
        proc = subprocess.Popen(
            [config.MEGATOOLS_BIN, *args], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except FileNotFoundError as e:
        raise ListingError("megatools binary not found - required for Mega links") from e

    proc.stdin.write(input_text)
    proc.stdin.close()

    start = last_progress = time.time()
    last_size = _dir_bytes(dest_dir)

    while True:
        try:
            proc.wait(timeout=5)
            break
        except subprocess.TimeoutExpired:
            pass

        now = time.time()
        size = _dir_bytes(dest_dir)
        if size != last_size:
            last_size = size
            last_progress = now

        if now - last_progress > stall_timeout:
            proc.kill()
            proc.wait()
            raise QuotaExceededError(
                f"Mega download stalled with no progress for {stall_timeout}s (likely bandwidth quota) -> {dest_dir}")

        if now - start > timeout:
            proc.kill()
            proc.wait()
            raise DownloadTimeoutError(f"megatools timed out after {timeout}s -> {dest_dir}")

    return subprocess.CompletedProcess(args, proc.returncode, proc.stdout.read(), proc.stderr.read())

def list_mega_folder(url: str) -> list[dict]:
    """Enumerates a Mega folder link's contents via `megatools dl --choose-files`, aborting
    before any download by piping in a non-numeric answer. Returns one dict per file: {'number',
    'name', 'path', 'size'} - 'number' is only valid against a listing made just before it's used;
    'path' is approximate for deeply-nested folders; 'size' is bytes, or None if not shown."""

    result = _run_megatools(
        ['dl', '--choose-files', '--no-progress', '--path', config.TEMP_DIR, url],
        input_text='q\n', timeout=60,
    )

    entries = []
    stack: list[tuple[int, str]] = []  # (depth, folder name), innermost last

    for line in result.stdout.splitlines():
        clean = ANSI_RE.sub('', line)
        depth = clean.count('|--')
        stripped = clean.replace('|--', '').strip()

        m = _MEGA_LISTING_LINE_RE.match(stripped)
        if not m:
            continue

        number, name, size_value, size_unit = m.groups()
        is_folder = name.endswith('/')
        name = name.rstrip('/')

        while stack and stack[-1][0] >= depth:
            stack.pop()

        path = '/'.join([n for _, n in stack] + [name])

        if is_folder:
            stack.append((depth, name))
        else:
            entries.append({
                'number': int(number), 'name': name, 'path': path,
                'size': _parse_mega_size(size_value, size_unit),
            })

    if not entries and 'ERROR' in result.stdout + result.stderr:
        raise ListingError(f"megatools could not list {url}: {(result.stderr or result.stdout).strip()}")

    return entries

def download_mega_selection(url: str, numbers: list[int], dest_dir: str) -> None:
    """Downloads only the given item numbers (from a list_mega_folder() call against this same
    link, made shortly before) into dest_dir, preserving their folder structure."""

    os.makedirs(dest_dir, exist_ok=True)
    selection = ' '.join(str(n) for n in numbers)

    # megatools resumes partial downloads itself, and a multi-GB selection can run for hours, so
    # this is one generous overall timeout - MEGA_STALL_TIMEOUT is what actually catches a stuck
    # transfer in practice.
    result = _run_megatools_watched(
        ['dl', '--choose-files', '--no-progress', '--path', dest_dir, url],
        selection + '\n', dest_dir, timeout=14400, stall_timeout=config.MEGA_STALL_TIMEOUT,
    )
    _raise_for_megatools_failure(result, url)

def download_mega_file(url: str, dest_dir: str) -> str:
    """Downloads a single Mega file link into dest_dir. Returns the downloaded file's path."""

    os.makedirs(dest_dir, exist_ok=True)
    before = set(os.listdir(dest_dir))

    result = _run_megatools_watched(['dl', '--no-progress', '--path', dest_dir, url], '', dest_dir,
                                     timeout=14400, stall_timeout=config.MEGA_STALL_TIMEOUT)
    _raise_for_megatools_failure(result, url)

    new_files = set(os.listdir(dest_dir)) - before
    if not new_files:
        raise ExternalDownloadError(f"megatools reported success but no file appeared -> {url}")

    return os.path.join(dest_dir, next(iter(new_files)))

def _raise_for_megatools_failure(result: subprocess.CompletedProcess, url: str) -> None:
    if result.returncode == 0 and 'ERROR' not in result.stderr:
        return

    text = (result.stdout + result.stderr).lower()
    if 'quota' in text or 'bandwidth' in text or 'limit' in text:
        raise QuotaExceededError(f"Mega bandwidth quota exceeded -> {url}")
    if 'invalid mega' in text or 'unsupported' in text:
        raise UnsupportedLinkError(f"megatools rejected this Mega link -> {url}")

    raise ExternalDownloadError((result.stderr or result.stdout).strip() or f"megatools failed -> {url}")
