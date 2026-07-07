# Kemono Downloader

Automatically download content from your favorite creators.
This project is based almost entirely on the open source project <https://github.com/VoxDroid/KemonoDownloader⁠>

## Work in progress

### Not ready for use

## Scheduling & triggering other containers

By default the container runs `main.py` once and exits (for setups where something external -
a host cron job, Unraid User Scripts, etc. - triggers it periodically). Set `CRON_EXPRESSION`
(see `.env.example`) to have the container schedule itself instead, via
[supercronic](https://github.com/aptible/supercronic) - no external scheduler needed.

To also kick off an Immich album-creator run right after a download run finishes, set
`TRIGGER_ALBUM_CREATOR=true`. This expects the sibling
[immich-album-webhook](https://git.patrick-dev.net/patrick/immich-album-webhook) project to be
running - a thin wrapper around
[immich-folder-album-creator](https://github.com/Salvoxia/immich-folder-album-creator) that idles
until triggered over plain HTTP, instead of needing its own cron schedule or a container that
gets started/stopped from outside. No Docker socket/API access needed on either side.

One-time setup to let the two containers reach each other (they're separate compose projects):

```
docker network create kemono-net
```

Then add a gitignored `docker-compose.override.yml` next to this repo's `compose.yml`:

```yaml
services:
  kemono-downloader:
    networks:
      - kemono-net

networks:
  kemono-net:
    external: true
```

...and the equivalent is already wired into immich-album-webhook's own `compose.yml`. Set
`ALBUM_CREATOR_WEBHOOK_URL` in `.env` if you rename that service away from the `album-creator`
default.

## Local development

### Testing with Docker (closest to production)

```
cp .env.example .env   # fill in SESSION_COOKIE at minimum
docker compose up --build
```

Downloaded files, config, and logs land in `./data`, `./config`, `./temp` next to the repo — no need to push to a registry or touch a server to test a change.

### Native / debugger-based testing (fastest inner loop)

`main.py` can also run directly with a normal Python interpreter, without Docker, which lets you set breakpoints and step through with a debugger. Three env vars — `DATA_DIR`, `CONFIG_DIR`, `TEMP_DIR` (see `core/paths.py`) — control where it reads/writes; they default to the container's absolute paths (`/data`, `/config`, `/temp`), so leave them unset for Docker and only override them for native runs.

One-time setup:

1. Create a venv and install dependencies: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
2. Build the Cython extension (needs a C compiler, e.g. `build-essential` on Debian/Ubuntu):
   ```
   cd ext
   python3 dezip_setup.py build_ext --inplace
   cd ..
   cp ext/dezip*.so .
   ```
   The copy step matches what the Dockerfile does (`COPY --from=builder /build/dezip.* ./`) so `core/files.py`'s `from dezip import _ZipDecrypter_C` resolves the same way in both environments. The `.so` is gitignored either way.
3. Optional: install `unrar` (only needed to extract `.rar` archives; everything else works without it).

Then run natively with the three dir vars pointed at local folders, e.g.:

```
DATA_DIR=./data CONFIG_DIR=./config TEMP_DIR=./temp SESSION_COOKIE=... python3 main.py
```

A ready-to-use VS Code debug config is at `.vscode/launch.json` (gitignored, local-only) — it loads `SESSION_COOKIE`/`CREATOR_URL_FILE`/etc. from `.env` and points `DATA_DIR`/`CONFIG_DIR`/`TEMP_DIR` at the same `./data`/`./config`/`./temp` folders Docker Compose uses, so you can switch between the two freely against the same local test data. Just hit F5 with `main.py` open.
