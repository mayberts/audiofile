# audiofile

A self-hosted Soulseek music downloader — search, grab, auto-tag, and organize —
built as a replacement for tools like Dropped Needle / SoulSync.

- **Search & download** tracks and albums from Soulseek through a web UI.
- **Wanted list** (Lidarr-style): add an artist/album/track, and a background
  job periodically searches Soulseek for it and grabs the best match.
- **Plex gap-fill**: scans your existing Plex music library, compares each
  artist's discography against MusicBrainz, and lists studio albums you don't
  have yet — one click adds them to the wanted list.
- **Metadata & cover art**: every completed download is tagged (artist,
  album, title, track number, year) and embedded with cover art from
  MusicBrainz / the Cover Art Archive, then moved into an
  `Artist/Album (Year)/NN - Title.ext` library layout.

## Architecture

```
slskd (Soulseek client/daemon, REST API)
   ↑
backend (FastAPI + SQLite)
   - clients/    slskd, MusicBrainz, Plex
   - services/   search scoring, tagging (mutagen), library organizer,
                 wanted-list processing, Plex gap scanning
   - scheduler   polls active downloads, post-processes completed ones,
                 periodically works the wanted list
   ↑
frontend (React + Vite, served by nginx)
   - Search, Downloads, Wanted, Plex Gaps, Settings pages
```

Soulseek connectivity is handled entirely by [slskd](https://github.com/slskd/slskd),
which the backend drives over its REST API — this avoids reimplementing the
Soulseek protocol.

## Running it

1. Copy `.env.example` to `.env` and fill in:
   - `SOULSEEK_USERNAME` / `SOULSEEK_PASSWORD` — your Soulseek account.
   - `SLSKD_API_KEY` — any random string, used to lock down the API.
   - `PLEX_URL` / `PLEX_TOKEN` — optional, only needed for the Plex gap-fill
     feature. Leave blank to skip it.
   - `MUSICBRAINZ_CONTACT` — a real contact (email or URL), required by
     MusicBrainz's API usage policy.
   - `HOST_LIBRARY_DIR` — where the tagged, organized library should be
     written. Point this at your Plex "Music" library's root folder so new
     downloads show up there after a Plex library scan.

2. Start everything:

   ```sh
   docker compose up -d --build
   ```

3. Open the web UI at `http://localhost:3000`.
   - The slskd web UI is also available directly at `http://localhost:5030`
     if you want to debug connectivity or watch raw transfers.
   - The backend API is at `http://localhost:8000` (interactive docs at
     `/docs`).

All settings (slskd URL/API key, Plex URL/token, library paths, preferred
formats, minimum bitrate, scan interval) can also be changed later from the
**Settings** page without restarting containers.

## How the wanted list works

Each wanted item has an `artist` and either an `album` or a `track`. A
background job (interval configurable in Settings, default 30 minutes)
searches Soulseek for each pending item, scores the results (preferring your
configured formats, minimum bitrate, and free upload slots), and enqueues the
best match. Once slskd reports the transfer complete, the backend tags the
file with MusicBrainz metadata + cover art and moves it into the library.

## How Plex gap-fill works

The scan walks every artist in your Plex music library, looks them up on
MusicBrainz, and diffs their official studio album list against what you
already have (by album title). Anything missing is listed on the **Plex
Gaps** page; "Add to Wanted" queues it for the wanted-list job above. Live
compilations, remixes, and other secondary release types are excluded by
default to keep the list focused on studio albums.

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
instance — either via `docker compose up slskd` or installed separately —
for search/download to work.

## Known limitations (MVP)

- Tagging supports MP3, FLAC, and M4A/AAC; other formats are moved into the
  library untagged.
- Plex gap scanning matches albums by title only (no MBID-based dedup on the
  Plex side), so slightly different album title formatting can produce false
  positives — review the list before bulk-adding to wanted.
- No authentication on the web UI itself; put it behind a reverse proxy or
  your home network's edge if exposing it beyond localhost.
