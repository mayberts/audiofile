import { useEffect, useMemo, useState } from "react";
import { api, MissingAlbumOut } from "../api/client";

interface DiscographyPickerProps {
  artist: string;
  onClose: () => void;
  onAdded: () => void;
}

export default function DiscographyPicker({ artist, onClose, onAdded }: DiscographyPickerProps) {
  const [albums, setAlbums] = useState<MissingAlbumOut[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api
      .getArtistDiscography(artist)
      .then(setAlbums)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [artist]);

  const allSelected = useMemo(
    () => !!albums && albums.length > 0 && selected.size === albums.length,
    [albums, selected],
  );

  function toggle(album: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(album)) next.delete(album);
      else next.add(album);
      return next;
    });
  }

  function toggleAll() {
    if (!albums) return;
    setSelected(allSelected ? new Set() : new Set(albums.map((a) => a.album)));
  }

  async function onAddSelected() {
    if (!albums || selected.size === 0) return;
    setSubmitting(true);
    setError(null);
    try {
      const toAdd = albums.filter((a) => selected.has(a.album));
      await Promise.all(
        toAdd.map((a) =>
          api.createWanted({ artist, album: a.album }),
        ),
      );
      onAdded();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function onAddWholeArtist() {
    setSubmitting(true);
    setError(null);
    try {
      await api.createWanted({ artist });
      onAdded();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-panel modal-panel-wide" onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ justifyContent: "space-between", marginBottom: "0.4rem" }}>
          <h2 style={{ margin: 0 }}>{artist}</h2>
          <button className="secondary" onClick={onClose}>
            Close
          </button>
        </div>
        <p className="muted" style={{ marginTop: 0 }}>Pick which albums to add to the wanted list.</p>

        {error && <div className="error-text" style={{ marginBottom: "0.8rem" }}>{error}</div>}

        {loading && <div className="muted">Checking MusicBrainz...</div>}

        {!loading && albums && albums.length === 0 && (
          <div className="panel empty" style={{ marginBottom: "0.8rem" }}>
            Couldn't find this artist on MusicBrainz to list albums.
          </div>
        )}

        {!loading && albums && albums.length > 0 && (
          <>
            <label className="row" style={{ marginBottom: "0.5rem", cursor: "pointer" }}>
              <input type="checkbox" checked={allSelected} onChange={toggleAll} />
              Select all ({albums.length})
            </label>
            <div className="panel" style={{ maxHeight: "60vh", overflowY: "auto", padding: "0.4rem" }}>
              {albums.map((a) => (
                <label key={a.album} className="discography-row">
                  <input type="checkbox" checked={selected.has(a.album)} onChange={() => toggle(a.album)} />
                  <AlbumCover releaseGroupMbid={a.release_group_mbid} />
                  <div className="discography-row-info">
                    <div>{a.album}</div>
                    <div className="muted">{(a.first_release_date || "").slice(0, 4) || "—"}</div>
                  </div>
                </label>
              ))}
            </div>
          </>
        )}

        <div className="row" style={{ marginTop: "1rem", justifyContent: "space-between" }}>
          {albums && albums.length > 0 ? (
            <button onClick={onAddSelected} disabled={submitting || selected.size === 0}>
              {submitting ? "Adding..." : `Add ${selected.size || ""} Selected`.trim()}
            </button>
          ) : (
            <span />
          )}
          <button className="secondary" onClick={onAddWholeArtist} disabled={submitting}>
            Add artist without picking albums
          </button>
        </div>
      </div>
    </div>
  );
}

function AlbumCover({ releaseGroupMbid }: { releaseGroupMbid: string | null }) {
  const [failed, setFailed] = useState(false);
  if (!releaseGroupMbid || failed) {
    return <div className="discography-row-cover-fallback" />;
  }
  return (
    <img
      className="discography-row-cover"
      src={api.coverArtUrl(releaseGroupMbid)}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}
