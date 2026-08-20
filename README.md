# audiofile

A self-hosted Soulseek music downloader — search, grab, auto-tag, and organize —
built as a replacement for tools like Dropped Needle / SoulSync.

- **Search & download** tracks and albums from Soulseek through a web UI.
- **Library**: browse your Plex music library — artists, then their albums,
  then track listings, with cover art and bios pulled straight from Plex.
  Each artist page also checks MusicBrainz live for studio albums you don't
  have yet, right alongside the ones you do — one click adds a missing one
  to the wanted list.
- **Wanted list** (Lidarr-style): add an artist/album/track, and a background
  job periodically searches Soulseek for it and grabs the best match.
- **Metadata & cover art**: every completed download is tagged (artist,
  album, title, track number, year) and embedded with cover art from
  MusicBrainz / the Cover Art Archive, then moved into an
  `Artist/Album (Year)/NN - Title.ext` library layout.

## Architecture

```
slskd (Soulseek client/daemon, REST API) — you bring your own, already running
   ↑
backend (FastAPI + SQLite)
   - clients/    slskd, MusicBrainz, Plex
   - services/   search scoring, tagging (mutagen), library organizer,
                 wanted-list processing, per-artist MusicBrainz gap check
   - scheduler   polls active downloads, post-processes completed ones,
                 periodically works the wanted list
   ↑
frontend (React + Vite, served by nginx)
   - Search, Library (+ artist/album detail), Downloads, Wanted, Settings
```

Soulseek connectivity is handled entirely by [slskd](https://github.com/slskd/slskd),
which the backend drives over its REST API — this avoids reimplementing the
Soulseek protocol. `docker-compose.yml` here does **not** run slskd itself —
it assumes you already have a slskd container/instance running (its own
compose file, a standalone container, whatever) and just connects to it.

## Images

Backend and frontend images are built and published to GHCR on every push to
`main` (and on version tags) by `.github/workflows/docker-publish.yml`:

- `ghcr.io/mayberts/audiofile-backend:latest`
- `ghcr.io/mayberts/audiofile-frontend:latest`

`docker-compose.yml` pulls these directly — no local build context needed, so
it works as-is on Unraid's Compose Manager (or anywhere else) without
checking out the repo. The first time the workflow runs, the packages are
created **private** by default; make them public from your GitHub profile's
**Packages** tab (Package settings → Change visibility), or `docker login
ghcr.io` with a PAT that has `read:packages` on whatever host is pulling
them.

## Running it

1. Copy `.env.example` to `.env` and fill in:
   - `MUSICBRAINZ_CONTACT` — a real contact (email or URL), required by
     MusicBrainz's API usage policy.
   - `HOST_DOWNLOADS_DIR` — the host folder your existing slskd container
     downloads into. This **must** be the same physical folder — audiofile
     reads finished files from it, tags them, and moves them out. Check your
     slskd container's own volume mounts (e.g. `docker inspect <slskd-container>`)
     to find the host path it uses.
   - `HOST_LIBRARY_DIR` — where the tagged, organized library should be
     written. Point this at your Plex "Music" library's root folder so new
     downloads show up there after a Plex library scan.
   - `HOST_APPDATA_DIR` — where audiofile's own database and settings live
     (e.g. `/mnt/user/appdata/audiofile` on Unraid). Back this up; it's the
     wanted list, download history, and everything set on the Settings page.

2. Start everything:

   ```sh
   docker compose up -d
   ```

   (On Unraid Compose Manager: paste `docker-compose.yml`'s contents into a
   new stack, fill in the same env vars via its `.env` editor, and hit
   Compose Up — no git clone required.)

3. Open the web UI at `http://localhost:3000` and go to **Settings** to
   connect it to your existing slskd:
   - **slskd URL** — try **Auto-detect** first (it checks `localhost:5030`,
     `host.docker.internal:5030`, and a `slskd` hostname on the same Docker
     network). Otherwise enter it manually: if your slskd container publishes
     a port, use `http://host.docker.internal:<port>`; if it doesn't, run
     `docker network connect <slskd's-network> audiofile-backend` and use its
     container name instead, e.g. `http://slskd:5030`.
   - **API key** — whatever slskd's own `SLSKD_API_KEY` (or `web.authentication.api_keys`)
     is set to.
   - Hit **Test Connection** to confirm before saving.
   - **Plex** (optional, for gap-fill) — URL + `X-Plex-Token`, also with a
     Test Connection button. ([How to find your Plex token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/).)

   The backend API is at `http://localhost:8000` (interactive docs at
   `/docs`).

All settings (slskd URL/API key, Plex URL/token, library paths, preferred
formats, minimum bitrate, scan interval) live in the **Settings** page and
take effect immediately — no container restart needed.

## How the wanted list works

Each wanted item has an `artist` and either an `album` or a `track`. A
background job (interval configurable in Settings, default 30 minutes)
searches Soulseek for each pending item, scores the results (preferring your
configured formats, minimum bitrate, and free upload slots), and enqueues the
best match. Once slskd reports the transfer complete, the backend tags the
file with MusicBrainz metadata + cover art and moves it into the library.

## How the Library's missing-albums check works

Opening an artist's page in **Library** looks that artist up on MusicBrainz
and diffs their official studio album list against what you already have
(by album title, normalized to ignore punctuation/apostrophe differences
and edition-suffix variants like "(Deluxe Edition)"). This happens live,
just for that one artist — two MusicBrainz requests, a second or so — not a
whole-library background scan. Anything missing shows up right under your
existing albums for that artist; "Add to Wanted" queues it for the
wanted-list job above. Live albums, compilations, remixes, and other
secondary release types are excluded by default to keep the list focused on
studio albums.

## Local development (without Docker)

Backend:

```sh
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Frontend:

```sh
cd frontend
npm install
npm run dev
```

The Vite dev server proxies `/api` to `http://localhost:8000` by default
(override with `VITE_API_PROXY_TARGET`). You'll still need a running slskd
instance somewhere reachable — configure its URL from the Settings page —
for search/download to work.

To build the images locally instead of pulling from GHCR (e.g. to test a
change before it's published), run `docker build -t audiofile-backend
./backend` / `docker build -t audiofile-frontend ./frontend`, then swap the
`image:` lines in `docker-compose.yml` for `build: ./backend` / `build:
./frontend` locally — don't commit that swap.

## Known limitations (MVP)

- Tagging supports MP3, FLAC, and M4A/AAC; other formats are moved into the
  library untagged.
- The missing-albums check matches by (normalized) title only, no MBID-based
  dedup on the Plex side, so an unusually reworded title can still slip
  through as a false positive — review before adding to wanted.
- No authentication on the web UI itself; put it behind a reverse proxy or
  your home network's edge if exposing it beyond localhost.
