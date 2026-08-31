import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, LibraryAlbumOut, MissingAlbumOut } from "../api/client";
import ArtworkPicker from "../components/ArtworkPicker";
import { libraryStore } from "../libraryStore";

export default function ArtistDetailPage() {
  const { artist: artistName = "" } = useParams<{ artist: string }>();
  const [albums, setAlbums] = useState<LibraryAlbumOut[] | null>(libraryStore.albums);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (libraryStore.albums === null) {
      setLoading(true);
      setError(null);
      api
        .getLibrary()
        .then((data) => {
          libraryStore.albums = data;
          setAlbums(data);
        })
        .catch((err) => setError(err instanceof Error ? err.message : String(err)))
        .finally(() => setLoading(false));
    }
  }, []);

  const artistAlbums = useMemo(() => {
    if (!albums) return [];
    return albums
      .filter((a) => a.artist === artistName)
      .sort((a, b) => (a.year ?? 0) - (b.year ?? 0) || a.album.localeCompare(b.album));
  }, [albums, artistName]);

  const [artistThumbOverride, setArtistThumbOverride] = useState<string | null>(null);
  const artistThumb = artistThumbOverride ?? artistAlbums.find((a) => a.artist_thumb)?.artist_thumb ?? null;
  const artistRatingKey = artistAlbums.find((a) => a.artist_rating_key)?.artist_rating_key ?? null;
  const totalTracks = artistAlbums.reduce((sum, a) => sum + (a.track_count ?? 0), 0);
  const [showArtworkPicker, setShowArtworkPicker] = useState(false);

  function onArtworkChanged(thumb: string | null) {
    setArtistThumbOverride(thumb);
    if (libraryStore.albums) {
      libraryStore.albums = libraryStore.albums.map((a) =>
        a.artist === artistName ? { ...a, artist_thumb: thumb } : a,
      );
    }
  }

  const [bio, setBio] = useState<string | null>(null);
  useEffect(() => {
    setBio(null);
    if (!artistRatingKey) return;
    api
      .getArtistBio(artistRatingKey)
      .then((res) => setBio(res.summary))
      .catch(() => setBio(null));
  }, [artistRatingKey]);

  const [missingAlbums, setMissingAlbums] = useState<MissingAlbumOut[] | null>(null);
  const [missingLoading, setMissingLoading] = useState(false);
  const [missingError, setMissingError] = useState<string | null>(null);
  const [missingAttempt, setMissingAttempt] = useState(0);
  useEffect(() => {
    setMissingAlbums(null);
    setMissingError(null);
    if (!artistName) return;
    setMissingLoading(true);
    // Deliberately the same name-based, Plex-independent lookup
    // DiscographyPicker uses (checked against the local library snapshot,
    // not a live Plex call) rather than the rating-key-based
    // /missing-albums endpoint -- that one requires this artist to already
    // have something owned in Plex, which an artist added purely to
    // browse (see LibraryPage's "Add Artist") never will.
    api
      .getArtistDiscography(artistName)
      .then((discography) => setMissingAlbums(discography.filter((a) => !a.in_library)))
      .catch((err) => setMissingError(err instanceof Error ? err.message : String(err)))
      .finally(() => setMissingLoading(false));
  }, [artistName, missingAttempt]);

  return (
    <div>
      <p style={{ marginBottom: "0.8rem" }}>
        <Link to="/library" className="muted">
          &larr; Back to Library
        </Link>
      </p>

      <div className="row" style={{ alignItems: "center", marginBottom: "1rem", gap: "1rem" }}>
        {artistRatingKey && (
          <button
            className="image-edit-trigger"
            onClick={() => setShowArtworkPicker(true)}
            title="Change artwork"
          >
            {artistThumb ? (
              <img
                src={api.plexImageUrl(artistThumb)}
                alt={artistName}
                style={{ width: 72, height: 72, borderRadius: 8, objectFit: "cover", flexShrink: 0 }}
              />
            ) : (
              <div
                className="artist-card-fallback"
                style={{ width: 72, height: 72, borderRadius: 8, fontSize: "1.4rem" }}
              >
                {artistName.charAt(0).toUpperCase()}
              </div>
            )}
            <div className="image-edit-overlay">Edit</div>
          </button>
        )}
        <div>
          <h1 style={{ margin: 0 }}>{artistName}</h1>
          {artistAlbums.length > 0 && (
            <p className="muted" style={{ margin: "0.2rem 0 0" }}>
              {artistAlbums.length} album{artistAlbums.length === 1 ? "" : "s"} · {totalTracks} track
              {totalTracks === 1 ? "" : "s"}
            </p>
          )}
        </div>
      </div>

      {bio && <ArtistBio key={artistRatingKey} text={bio} />}

      {loading && <div className="panel">Loading...</div>}
      {error && <div className="panel error-text">{error}</div>}

      {!loading && !error && (
        <>
          <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
            <h2 style={{ marginBottom: "0.3rem" }}>Albums</h2>
            {missingLoading && <span className="muted">Checking MusicBrainz for anything missing...</span>}
          </div>
          {missingError && (
            <div className="panel">
              <p className="error-text" style={{ margin: 0 }}>
                {missingError}
              </p>
              <button
                className="secondary"
                style={{ marginTop: "0.7rem" }}
                onClick={() => setMissingAttempt((n) => n + 1)}
              >
                Try again
              </button>
            </div>
          )}
          {artistAlbums.length === 0 && (missingAlbums?.length ?? 0) === 0 && !missingLoading ? (
            <div className="panel empty">No albums found for this artist.</div>
          ) : (
            <ArtistAlbumList artist={artistName} ownedAlbums={artistAlbums} missingAlbums={missingAlbums ?? []} />
          )}
        </>
      )}

      {showArtworkPicker && artistRatingKey && (
        <ArtworkPicker
          ratingKey={artistRatingKey}
          onClose={() => setShowArtworkPicker(false)}
          onChanged={onArtworkChanged}
        />
      )}
    </div>
  );
}

// One combined, year-sorted list of everything MusicBrainz knows this
// artist released -- owned albums (from the Plex-backed library snapshot)
// and missing ones (checked live against MusicBrainz) interleaved in a
// single view, each row showing a cover thumbnail and a right-side status
// (a checkmark for owned, a download button for missing) instead of two
// visually disconnected sections.
type ArtistAlbumRowData =
  | { kind: "owned"; key: string; title: string; year: number | null; owned: LibraryAlbumOut }
  | { kind: "missing"; key: string; title: string; year: number | null; missing: MissingAlbumOut };

function ArtistAlbumList({
  artist,
  ownedAlbums,
  missingAlbums,
}: {
  artist: string;
  ownedAlbums: LibraryAlbumOut[];
  missingAlbums: MissingAlbumOut[];
}) {
  const [added, setAdded] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [bulkAdding, setBulkAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const stillMissing = missingAlbums.filter((a) => !added.has(a.album));

  async function addOne(album: MissingAlbumOut) {
    setPending((prev) => new Set(prev).add(album.album));
    setError(null);
    try {
      await api.addMissingAlbumToWanted({
        artist,
        album: album.album,
        release_group_mbid: album.release_group_mbid,
      });
      setAdded((prev) => new Set(prev).add(album.album));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending((prev) => {
        const next = new Set(prev);
        next.delete(album.album);
        return next;
      });
    }
  }

  async function addAll() {
    setBulkAdding(true);
    setError(null);
    try {
      await Promise.all(stillMissing.map((a) => addOne(a)));
    } finally {
      setBulkAdding(false);
    }
  }

  const rows: ArtistAlbumRowData[] = [
    ...ownedAlbums.map(
      (a): ArtistAlbumRowData => ({ kind: "owned", key: `owned:${a.album}`, title: a.album, year: a.year, owned: a }),
    ),
    ...stillMissing.map((a): ArtistAlbumRowData => {
      const year = a.first_release_date ? parseInt(a.first_release_date.slice(0, 4), 10) : NaN;
      return {
        kind: "missing",
        key: `missing:${a.album}`,
        title: a.album,
        year: Number.isNaN(year) ? null : year,
        missing: a,
      };
    }),
  ].sort((a, b) => (b.year ?? 0) - (a.year ?? 0) || a.title.localeCompare(b.title));

  return (
    <div className="panel" style={{ padding: 0 }}>
      <div className="row" style={{ justifyContent: "space-between", padding: "0.7rem 1rem" }}>
        <span className="muted">
          {rows.length} album{rows.length === 1 ? "" : "s"}
        </span>
        {stillMissing.length > 0 && (
          <button className="secondary" onClick={addAll} disabled={bulkAdding}>
            {bulkAdding ? "Adding..." : `Download All (${stillMissing.length})`}
          </button>
        )}
      </div>
      {error && (
        <div className="error-text" style={{ padding: "0 1rem 0.6rem" }}>
          {error}
        </div>
      )}
      {rows.map((row) =>
        row.kind === "owned" ? (
          <ArtistAlbumRow key={row.key} row={row} />
        ) : (
          <ArtistAlbumRow
            key={row.key}
            row={row}
            pending={pending.has(row.missing.album)}
            onDownload={() => addOne(row.missing)}
          />
        ),
      )}
    </div>
  );
}

function ArtistAlbumRow({
  row,
  pending,
  onDownload,
}: {
  row: ArtistAlbumRowData;
  pending?: boolean;
  onDownload?: () => void;
}) {
  const [imgFailed, setImgFailed] = useState(false);
  const coverUrl =
    row.kind === "owned"
      ? row.owned.thumb
        ? api.plexImageUrl(row.owned.thumb)
        : null
      : row.missing.release_group_mbid
        ? api.coverArtUrl(row.missing.release_group_mbid)
        : null;

  const inner = (
    <div className="row" style={{ padding: "0.5rem 1rem", gap: "0.8rem", borderTop: "1px solid var(--border)" }}>
      {coverUrl && !imgFailed ? (
        <img
          src={coverUrl}
          alt=""
          loading="lazy"
          onError={() => setImgFailed(true)}
          style={{ width: 40, height: 40, borderRadius: 6, objectFit: "cover", flexShrink: 0 }}
        />
      ) : (
        <div className="artist-card-fallback" style={{ width: 40, height: 40, borderRadius: 6, fontSize: "1rem", flexShrink: 0 }}>
          {row.title.charAt(0).toUpperCase()}
        </div>
      )}
      <div style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {row.title}
        <div className="muted">{row.year ?? "—"}</div>
      </div>
      {row.kind === "owned" ? (
        <span className="badge done" title="In your library" style={{ flexShrink: 0 }}>
          &#10003;
        </span>
      ) : (
        <button
          className="secondary"
          onClick={(e) => {
            e.preventDefault();
            onDownload?.();
          }}
          disabled={pending}
          title="Add to wanted list"
          style={{ flexShrink: 0 }}
        >
          {pending ? "..." : "⤓ Get"}
        </button>
      )}
    </div>
  );

  if (row.kind === "owned") {
    return (
      <Link
        to={`/library/${encodeURIComponent(row.owned.artist)}/${encodeURIComponent(row.owned.album)}`}
        style={{ color: "inherit", textDecoration: "none", display: "block" }}
      >
        {inner}
      </Link>
    );
  }
  return inner;
}

const BIO_PREVIEW_LENGTH = 400;

function ArtistBio({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const needsTruncation = text.length > BIO_PREVIEW_LENGTH;
  const shown = expanded || !needsTruncation ? text : `${text.slice(0, BIO_PREVIEW_LENGTH).trimEnd()}…`;

  return (
    <div className="panel">
      <p style={{ margin: 0, lineHeight: 1.5 }}>{shown}</p>
      {needsTruncation && (
        <button className="secondary" style={{ marginTop: "0.7rem" }} onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
    </div>
  );
}

