# Kemono Downloader

A self-hosted downloader for [kemono.cr](https://kemono.cr/) (and compatible mirrors). Point it at
your favorite creators — or a plain list of profile URLs — and it periodically fetches new posts,
downloads attachments/thumbnails/embedded images, extracts password-protected archives, and keeps
a local database so nothing is ever downloaded twice.

Runs as a single Docker container: one-shot (triggered by an external scheduler) or with its own
built-in cron schedule.

## Table of Contents
- [Features](#features)
- [How It Works](#how-it-works)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
  - [Session Cookie (`.env`)](#session-cookie-env)
  - [Global Config (`config/config.conf`)](#global-config-configconfconf)
  - [Per-Creator Config (`config/creators/<service>_<id>.conf`)](#per-creator-config-configcreatorsservice_idconf)
- [Choosing Which Creators to Download](#choosing-which-creators-to-download)
- [Data Layout](#data-layout)
- [Filtering Posts and Files](#filtering-posts-and-files)
- [Archive Extraction & Passwords](#archive-extraction--passwords)
- [External Links (Google Drive & Mega)](#external-links-google-drive--mega)
- [Scheduling](#scheduling)
- [Domains & Mirrors](#domains--mirrors)
- [Reconciling Files Already on Disk](#reconciling-files-already-on-disk)
- [Album-Creator Integration](#album-creator-integration)
- [Logs & Failures](#logs--failures)
- [Building From Source](#building-from-source)

## Features
- Downloads attachments, post thumbnails, and (optionally) images embedded in post content.
- Optionally follows Google Drive/Mega links posted in the body text itself, for creators who
  share files that way instead of attaching them — see
  [External Links](#external-links-google-drive--mega).
- Hash-based deduplication via a local SQLite database — already-downloaded files are skipped
  without re-fetching them, even across restarts.
- Automatic extraction of `.zip` (incl. AES-encrypted), `.rar`, and `.7z` archives, with a
  per-creator password list that self-learns: once a password works, it's remembered and tried
  first on that creator's future archives.
- Per-post title filtering (regex include/exclude) and per-file filtering (extension/type).
- Three ways to choose which creators to download: your kemono favorites, a plain-text list of
  profile URLs, or auto-discovery from folders already on disk.
- Built-in cron-style scheduler (no supercronic/host cron required), or run once and exit.
- Works against kemono-schema-compatible mirrors, not just kemono.cr.
- Optional webhook trigger to a sibling album-creator service after each run.
- A `reconcile` command to rebuild the database from files already on disk, without re-downloading
  anything — useful for first-time setup against an existing library or recovering from DB loss.

## How It Works
For each creator, the script fetches every post via the kemono API, extracts the downloadable
files from each one (attachments, thumbnail, and optionally embedded `<img>` tags from the post
body), filters them, and downloads whatever's new. Every downloaded file's hash is recorded in a
local SQLite database (`config/kemono.db`) scoped per creator, so the same file is never
downloaded twice — even if it's since been removed from disk (see
[Reconciling Files Already on Disk](#reconciling-files-already-on-disk) for the recovery path).

Files land on disk under:
```
/data/<creator name>_<service>_<creator id>/<post title>_<post id>/<file index>_<file name>
```
See [Data Layout](#data-layout) for a concrete example.

## Requirements
- A kemono.cr (or mirror) account, and its session cookie value (see [below](#session-cookie-env)).
- Docker, to run the provided image/Dockerfile. (Bare Python is possible but not the supported
  path — see [Building From Source](#building-from-source).)

## Quick Start
1. Copy the environment template and fill in your session cookie:
   ```bash
   cp .env.example .env
   ```
   ```dotenv
   SESSION_COOKIE=your-cookie-value-here
   ```
2. Start the container:
   ```bash
   docker compose up --build
   ```
   With no further configuration, this performs a single run, downloading everything from your
   kemono favorites into `./data`, and exits.
3. To keep it running on a schedule instead of a single run, edit `config/config.conf` (created
   automatically after the first run — see [Global Config](#global-config-configconfconf)) and set
   `CRON_EXPRESSION`, then restart the container.

`compose.yml` mounts three folders next to the repo:

| Host path | Container path | Purpose |
|-----------|-----------------|---------|
| `./data`  | `/data`  | Downloaded files, organized per creator/post. |
| `./config`| `/config`| `config.conf`, per-creator configs, the SQLite database, logs, and failure records. |
| `./temp`  | `/temp`  | Scratch space for in-progress downloads and archive extraction. Wiped on every container start. |

> [!IMPORTANT]
> `SESSION_COOKIE` is the only setting read from the environment — everything else lives in
> `config/config.conf` and `config/creators/*.conf`, both of which are created from shipped
> defaults on first run and can be hand-edited afterwards.

## Configuration

### Session Cookie (`.env`)
`.env` (gitignored — never commit your real cookie) holds credentials:

| Variable | Required | Description |
|---|---|---|
| `SESSION_COOKIE` | yes | The value of kemono's `session` cookie, copied from your browser's dev tools after logging in. Needed to access favorites, non-public content, and post data. Only read once at startup — changing it requires a container restart. |
| `GOOGLE_API_KEY` | only for `external`+Drive | A free Google Cloud API key with the Drive API enabled. Only needed if a creator's `ALLOWED_TYPES` includes `external` and they post Google Drive links — see [External Links](#external-links-google-drive--mega). Not needed for Mega links. |

### Global Config (`config/config.conf`)
Created automatically from [`core/config.conf.default`](core/config.conf.default) the first time
the container runs. A flat `KEY=VALUE` file with comments — hand-edit it and restart to apply
changes. Any key that's missing or fails to parse falls back to its shipped default (a warning is
logged).

| Key | Default | Description |
|---|---|---|
| `DOMAIN` | `kemono.cr` | Which kemono-schema instance to use. Can be pointed at a compatible mirror. See [Domains & Mirrors](#domains--mirrors). |
| `FILE_DOMAIN` | *(empty)* | Separate host to download files from, if a mirror splits file-hosting off from its API/site domain. Empty falls back to `DOMAIN`. |
| `FILE_PATH_PREFIX` | *(empty)* | Path prefix to prepend to file URLs — a quirk of some mirrors. Only relevant alongside `FILE_DOMAIN`. |
| `CREATOR_URL_FILE` | *(empty)* | Path to a text file of creator profile URLs to import, one per line. See [Choosing Which Creators to Download](#choosing-which-creators-to-download). |
| `CREATORS_FROM_DATA` | `false` | Discover creators from existing folder names under `/data` instead of `CREATOR_URL_FILE` or favorites. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `LOG_RETENTION_DAYS` | `14` | Days to keep old files in `config/logs` and `config/failed` before pruning. `0` disables pruning. |
| `CRON_EXPRESSION` | *(empty)* | A [crontab-style expression](https://crontab.guru/) for the built-in scheduler. Empty means run once and exit. See [Scheduling](#scheduling). |
| `RUN_IMMEDIATELY` | `false` | Also run once at startup, in addition to the cron schedule. Has no effect if `CRON_EXPRESSION` is empty. |
| `TRIGGER_ALBUM_CREATOR` | `false` | POST a webhook after each run finishes. See [Album-Creator Integration](#album-creator-integration). |
| `ALBUM_CREATOR_WEBHOOK_URL` | `http://album-creator:8080/run` | Webhook URL used when `TRIGGER_ALBUM_CREATOR` is enabled. |
| `DOWNLOAD_MAX_ATTEMPTS` | `60` | Max attempts per file download before giving up. An HTTP 404 always skips straight to giving up, regardless of this value. |
| `DOWNLOAD_RETRY_DELAY` | `1` | Seconds to wait between retry attempts (a 502 always waits 5s instead). |

### Per-Creator Config (`config/creators/<service>_<id>.conf`)
Created automatically for each creator the first time it's processed, from
[`core/creator.conf.default`](core/creator.conf.default). Lets you tune filtering and archive
behavior independently per creator.

| Key | Default | Description |
|---|---|---|
| `INCLUDE_REGEX` | *(empty)* | Only download posts whose **title** fully matches this regex. Mutually exclusive with `EXCLUDE_REGEX` — if both are set, `EXCLUDE_REGEX` is ignored (a warning is logged). |
| `EXCLUDE_REGEX` | *(empty)* | Skip posts whose title fully matches this regex. Ignored if `INCLUDE_REGEX` is set. |
| `ALLOWED_EXTENSIONS` | `.jpg,.jpeg,.png,.zip,.mp4,.gif,.pdf,.7z,.mp3,.wav,.rar,.mov,.docx,.jpe,.webp` | Comma-separated list of file extensions to download. Empty allows any extension. |
| `ALLOWED_TYPES` | `attachment` | Comma-separated subset of `attachment`, `thumbnail`, `embed`, `external`. Empty allows `attachment`/`thumbnail`/`embed` (not `external` — see below). `embed` (images referenced in the post body) and `external` both cost one extra API call per post to fetch the full content, where the mirror doesn't already send it. |
| `AUTO_UNZIP` | `true` | Automatically extract downloaded `.zip`/`.rar`/`.7z` archives. |
| `KEEP_UNPACKED_ARCHIVES` | `true` | Keep the archive file on disk after successful extraction. |
| `KEEP_FAILED_ARCHIVES` | `false` | If extraction fails (no known password works, or an error occurs), keep the archive on disk as-is instead of deleting it. When `false` (default), a failed archive is deleted and **retried on every subsequent run**; when `true`, it's kept and left alone. |
| `ARCHIVE_PASSWORDS` | `""` | Comma-separated list of passwords to try, in order, before giving up. The shipped `""` entry means "also try no password". Grows automatically: whenever a new password is discovered for one of this creator's archives, it's appended here so future archives try it first. A password containing a literal comma must be quoted, e.g. `"pass,word",other`. |

> [!TIP]
> Passwords are also looked up from kemono's own per-file metadata (if the file's hash is known to
> the API) before falling back to `ARCHIVE_PASSWORDS` — so archives with a publicly known password
> often extract with no configuration at all.

## Choosing Which Creators to Download
Exactly one of the following sources is used per run, in this priority order:

1. **`CREATORS_FROM_DATA=true`** — discover creators from folder names already under `/data`,
   matching the `<name>_<service>_<id>` convention this script itself uses (see
   [Data Layout](#data-layout)). Only recognizes a fixed set of service names (`patreon`,
   `fanbox`, `fantia`, `boosty`, `gumroad`, `subscribestar`, `dlsite`) with a numeric id — folders
   that don't match are silently skipped. Useful once you already have a populated library and
   don't want to maintain a separate creator list.
2. **`CREATOR_URL_FILE`** — a plain-text file with one creator profile URL per line, e.g.:
   ```
   https://kemono.cr/patreon/user/12345678
   https://kemono.cr/fanbox/user/87654321
   ```
   If the file doesn't exist yet, an empty one is created at that path automatically. Make sure
   the path points somewhere inside `/config` so it persists across container restarts.
3. **Neither is set** — falls back to your kemono account's favorited creators, fetched live from
   the API each run.

> [!IMPORTANT]
> These sources are **not** combined — setting `CREATORS_FROM_DATA` means `CREATOR_URL_FILE` and
> favorites are both ignored for that run, regardless of whether they're also configured.

## Data Layout
Given a creator named `SomeArtist` on `patreon` with ID `12345`, downloads land at:
```
/data/SomeArtist_patreon_12345/
└── My_Post_Title_987654/
    ├── 000_thumbnail.jpg
    ├── 001_cover.png
    ├── 002_bonus_content.zip
    └── 002_bonus_content/
        ├── 002_000_page01.jpg
        ├── 002_001_page02.jpg
        └── 002_002_page03.jpg
```
- The top-level folder is `<creator name>_<service>_<creator id>` — this convention is what lets
  `CREATORS_FROM_DATA` and `reconcile` recognize a creator's folder later.
- Each post gets its own subfolder, `<post title>_<post id>`.
- Files are prefixed with a zero-padded index reflecting their order within the post (thumbnail
  first, then attachments, then embedded images).
- File and folder modification times are set to match the post's publish time, so sorting a
  creator's folder by date reflects posting order.
- If `AUTO_UNZIP` extracts an archive (`002_bonus_content.zip` above), its contents land in a
  sibling folder named after the archive with the extension stripped (`002_bonus_content/`), and
  each entry inside is prefixed with the archive's own index (`002`) plus its own index within the
  archive (`000`, `001`, ...), e.g. `002_000_page01.jpg`. The archive file itself is only kept
  alongside it if `KEEP_UNPACKED_ARCHIVES=true` (the default).

## Filtering Posts and Files
Filtering happens in two independent layers, both configured [per creator](#per-creator-config-configcreatorsservice_idconf):

- **Post-level**: `INCLUDE_REGEX` / `EXCLUDE_REGEX` match against the post **title** using
  `re.fullmatch` — the pattern must match the entire title, not just part of it (wrap it in `.*`
  on either side to match a substring).
- **File-level**: `ALLOWED_EXTENSIONS` and `ALLOWED_TYPES` are applied to every attachment,
  thumbnail, or embedded image individually, regardless of which post it came from.

## Archive Extraction & Passwords
When `AUTO_UNZIP` is enabled, every downloaded `.zip`, `.rar`, or `.7z` file is extracted right
after download:
- Passwords are tried in this order: any password kemono's API already associates with that exact
  file, then this creator's `ARCHIVE_PASSWORDS` list (in order), including the implicit "no
  password" entry.
- The first password that works is remembered — if it's not already in `ARCHIVE_PASSWORDS`, it's
  appended and the creator's `.conf` file is rewritten immediately, so later archives for the same
  creator try it first.
- `.zip` extraction falls back from the standard library to `pyzipper` automatically for
  AES-encrypted zips; `.rar` requires the `unrar` binary (bundled in the Docker image); `.7z` uses
  `py7zr`.
- Extracted paths are checked against the extraction directory before writing, rejecting archives
  that attempt to write outside it (zip-slip protection).

> [!CAUTION]
> If no password works and `KEEP_FAILED_ARCHIVES=false` (the default), the archive is deleted and
> will be **re-downloaded and retried on every future run** until it succeeds or you set
> `KEEP_FAILED_ARCHIVES=true` for that creator.

## External Links (Google Drive & Mega)
Some creators don't attach their actual files to the post at all — instead they paste a Google
Drive or Mega share link into the post body, sometimes with a password for the archive inside,
because their platform can't host the file directly (size limits, disallowed file types, etc).
Setting `ALLOWED_TYPES` to include `external` for a creator makes this script follow those links
and download what's behind them, the same as any other file type — with the same hash-based dedup,
the same `AUTO_UNZIP`/`ARCHIVE_PASSWORDS` extraction pipeline, and the same `ALLOWED_EXTENSIONS`
filtering (applied to the linked file's own name, once it's known).

- **Unlike `attachment`/`thumbnail`/`embed`, `external` is never implied by an empty
  `ALLOWED_TYPES`** — it must be added explicitly, since resolving these links costs an extra
  network call (a Drive API request, or a `megatools` subprocess) per link found, not just per
  post.
- **A password, if found, is scraped from the post's own text** near the link (e.g. `pass:
  hunter2`, in English/Japanese/Chinese/Korean) and tried before the creator's `ARCHIVE_PASSWORDS`
  list. This is best-effort — most creators don't password-protect these links at all, and if no
  password is found, extraction just proceeds with none, same as it always does otherwise.
- **Google Drive** links (both a single file and a whole folder) are resolved via the Drive API v3
  using `GOOGLE_API_KEY` — a free key, no OAuth or billing needed, since these are all
  publicly-shared ("anyone with the link") files. Folders are enumerated recursively; native
  Google Docs/Sheets/etc. entries (not actual files) are skipped.
- **Mega** links need no credentials — the decryption key lives in the URL itself — but do need
  the `megatools` binary, bundled in the Docker image. A whole shared folder is listed and
  downloaded through it directly; only files not already recorded for that creator are selected.
  Mega's own *link-level* password feature (a `mega.nz/#P!...` URL — distinct from an archive
  password, and rare) isn't supported by `megatools` and is skipped with a warning rather than
  silently failing.
- **Downloads run in their own worker pools**, separate from the main kemono download pool and
  from each other: Drive and kemono files share retry/backoff logic but are otherwise independent,
  while Mega downloads are grouped by shared link (a whole folder link downloads in one
  `megatools` invocation) and kept to a small pool, since Mega's anonymous bandwidth quota is
  shared per-IP across all concurrent downloads — more workers there just exhausts the daily cap
  faster rather than finishing faster.
- A quota rejection (Drive's per-file quota, or Mega's bandwidth cap) fails that file immediately
  without retrying within the run — both quotas reset on their own schedule, not by waiting a few
  seconds — and shows up in the run summary as `external_quota_exceeded`.

## Scheduling
Controlled entirely by `CRON_EXPRESSION` and `RUN_IMMEDIATELY` in
[the global config](#global-config-configconfconf) — no supercronic or host cron needed.

- **`CRON_EXPRESSION` empty** (default): the container performs a single run and exits. Use this
  if something external (host cron, Unraid User Scripts, an orchestrator) triggers it periodically.
- **`CRON_EXPRESSION` set**: the container stays running and triggers a run on that schedule
  internally, e.g. `0 * * * *` for every hour. Set `RUN_IMMEDIATELY=true` to also run once at
  startup rather than waiting for the first scheduled tick.

Changes to `CRON_EXPRESSION` made by hand-editing `config/config.conf` while the container is
running are picked up at the next restart.

## Domains & Mirrors
`DOMAIN` defaults to `kemono.cr` but can point at any kemono-schema-compatible mirror. If a mirror
serves downloadable files from a different host or path than its API/site, set `FILE_DOMAIN`
and/or `FILE_PATH_PREFIX` to match.

`reconcile` (see below) also accepts `--domain`, `--file-domain`, and `--file-path-prefix` flags to
override these for a single run — handy for reconciling a library against a different mirror than
the one it was originally downloaded from.

## Reconciling Files Already on Disk
`reconcile` rebuilds the `posts`/`files` database records by scanning `/data` directly, instead of
downloading anything. Use it when setting up against a library that already has files on disk (so
they aren't re-downloaded), or to recover the database after data loss.

```bash
docker exec <container name> reconcile
docker exec <container name> reconcile --favorite
docker exec <container name> reconcile --domain pawchive.pw
```

- Always discovers creators from `/data` folder names (same convention as `CREATORS_FROM_DATA`) —
  `CREATOR_URL_FILE` and favorites are never consulted.
- For each creator, it fetches their current posts, then walks their folder on disk matching each
  file's hash against the expected download for that post. Files that match are recorded normally;
  files inside an extracted-archive folder are recorded as archive children; anything left over
  (e.g. from a deleted post, or a manually added file) is recorded as a best-effort "straggler" so
  it's still protected from being redundantly re-downloaded later.
- `--favorite` additionally favorites each reconciled creator on the active domain.
- `--domain` / `--file-domain` / `--file-path-prefix` override the corresponding global config
  values for this run only.

## Album-Creator Integration
Setting `TRIGGER_ALBUM_CREATOR=true` POSTs to `ALBUM_CREATOR_WEBHOOK_URL` after every completed
run — fire-and-forget, the download run doesn't wait for it to finish. This is meant to pair with
the sibling [immich-album-creator-webhook](https://git.patrick-dev.net/patrick/immich-album-creator-webhook)
project, a thin wrapper that idles until triggered over plain HTTP and then runs
[immich-folder-album-creator](https://github.com/Salvoxia/immich-folder-album-creator) against
this container's `/data` output — no Docker socket access needed on either side.

Just make sure the two containers sit on a common network, so the webhook can be reached from here.

## Logs & Failures
Everything under `config/`, alongside the config files and database:
- `config/logs/<timestamp>.log` — a full debug-level log for each run. The end of the log includes
  a run summary: totals of files downloaded/skipped/failed (with failure reasons), and a
  per-creator breakdown for creators that had files processed or that failed entirely.
- `config/failed/<timestamp>.json` — one JSON object per line for each file that failed to
  download or extract during that run, with its full metadata (creator, post, URL, path) and a
  `reason` field, for troubleshooting or manual retry.
- `config/summary/<timestamp>.json` — the same run summary that's printed at the end of the log,
  as structured JSON.
- All three are pruned automatically per `LOG_RETENTION_DAYS`.
- `config/kemono.db` — the SQLite database of known creators, posts, and files.

## Building From Source
The Dockerfile is a two-stage build: the builder stage compiles
[`ext/dezip.pyx`](ext/dezip.pyx), a Cython-accelerated zip decrypter that patches
`zipfile._ZipDecrypter` for faster password-protected `.zip` extraction, and the runtime stage
copies in the built extension plus the `unrar` binary (from Debian's non-free repo, needed for
`.rar` support) and the `megatools` binary (needed for Mega links — see
[External Links](#external-links-google-drive--mega)).

To build and run locally instead of pulling a prebuilt image:
```bash
docker compose up --build
```

To run natively without Docker (e.g. for debugging), the app respects `DATA_DIR`, `CONFIG_DIR`,
and `TEMP_DIR` env vars, defaulting to the container's absolute paths (`/data`, `/config`,
`/temp`) — override them to point at local folders instead:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd ext && python3 dezip_setup.py build_ext --inplace && cd ..
cp ext/dezip*.so .

DATA_DIR=./data CONFIG_DIR=./config TEMP_DIR=./temp SESSION_COOKIE=... python3 app.py
```
Building the Cython extension needs a C compiler (`build-essential` on Debian/Ubuntu). `unrar` is
only required to extract `.rar` archives — everything else works without it.

## Testing
```bash
pip install -r requirements.txt -r requirements-test.txt
cd ext && python3 dezip_setup.py build_ext --inplace && cd ..
pytest --cov=core --cov=app --cov=download --cov=scheduler --cov=reconcile --cov-report=term-missing
```
`pytest.ini` blocks real network access (`--disable-socket`) so the suite can't accidentally hit
kemono/mirror servers. CI runs this on every push and pull request as a required gate before the
image is built and pushed.
