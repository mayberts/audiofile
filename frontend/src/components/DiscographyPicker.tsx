import { useEffect, useMemo, useState } from "react";
import { api, MissingAlbumOut } from "../api/client";
import ReleasePicker from "./ReleasePicker";

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
  // album title -> chosen release mbid ("auto" = explicitly picked "let
  // audiofile decide"; absent = never opened the picker for this album).
  const [editions, setEditions] = useState<Map<string, string | "auto">>(new Map());
  const [pickingEditionFor, setPickingEditionFor] = useState<MissingAlbumOut | null>(null);

  function loadDiscography() {
    setLoading(true);
    setError(null);
    api
      .getArtistDiscography(artist)
      .then(setAlbums)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }

  useEffect(loadDiscography, [artist]);

  const missingAlbums = useMemo(() => (albums || []).filter((a) => !a.in_library), [albums]);

  const allSelected = useMemo(
    () => missingAlbums.length > 0 && selected.size === missingAlbums.length,
    [missingAlbums, selected],
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
    // Already-owned albums are excluded from "select all" — someone who
    // wants to re-grab one anyway can still tick it by hand.
    setSelected(allSelected ? new Set() : new Set(missingAlbums.map((a) => a.album)));
  }

  async function onAddSelected() {
    if (!albums || selected.size === 0) return;
    setSubmitting(true);
    setError(null);
    try {
      const toAdd = albums.filter((a) => selected.has(a.album));
      await Promise.all(
        toAdd.map((a) => {
          const chosen = editions.get(a.album);
          return api.createWanted({
            artist,
            album: a.album,
            release_mbid: chosen && chosen !== "auto" ? chosen : undefined,
          });
        }),
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

        {error && (
          <div className="row" style={{ justifyContent: "space-between", marginBottom: "0.8rem" }}>
            <div className="error-text">{error}</div>
            {albums === null && (
              <button className="secondary" onClick={loadDiscography} disabled={loading}>
                {loading ? "Retrying..." : "Retry"}
              </button>
            )}
          </div>
        )}

        {loading && <div className="muted">Checking MusicBrainz...</div>}

        {!loading && albums && albums.length === 0 && (
          <div className="panel empty" style={{ marginBottom: "0.8rem" }}>
            Couldn't find this artist on MusicBrainz to list albums.
          </div>
        )}

        {!loading && albums && albums.length > 0 && (
          <>
            <label className="row" style={{ marginBottom: "0.5rem", cursor: missingAlbums.length ? "pointer" : "default" }}>
              <input type="checkbox" checked={allSelected} onChange={toggleAll} disabled={missingAlbums.length === 0} />
              Select all missing ({missingAlbums.length})
            </label>
            <div className="panel" style={{ maxHeight: "60vh", overflowY: "auto", padding: "0.4rem" }}>
              {albums.map((a) => {
                const chosen = editions.get(a.album);
                return (
                  <div key={a.album} className={`discography-row${a.in_library ? " discography-row-owned" : ""}`}>
                    <label className="row" style={{ flex: 1, cursor: "pointer" }}>
                      <input type="checkbox" checked={selected.has(a.album)} onChange={() => toggle(a.album)} />
                      <AlbumCover releaseGroupMbid={a.release_group_mbid} />
                      <div className="discography-row-info">
                        <div>{a.album}</div>
                        <div className="muted">
                          {(a.first_release_date || "").slice(0, 4) || "—"}
                          {chosen && chosen !== "auto" && " · specific edition picked"}
                        </div>
                      </div>
                    </label>
                    {a.in_library && <span className="badge in-library">In Library</span>}
                    {a.release_group_mbid && (
                      <button
                        type="button"
                        className="secondary"
                        onClick={() => setPickingEditionFor(a)}
                      >
                        {chosen && chosen !== "auto" ? "Change edition" : "Choose edition"}
                      </button>
                    )}
                  </div>
                );
              })}
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

      {pickingEditionFor && pickingEditionFor.release_group_mbid && (
        <ReleasePicker
          albumTitle={pickingEditionFor.album}
          releaseGroupMbid={pickingEditionFor.release_group_mbid}
          onClose={() => setPickingEditionFor(null)}
          onPick={(releaseMbid) => {
            const album = pickingEditionFor.album;
            setEditions((prev) => {
              const next = new Map(prev);
              next.set(album, releaseMbid ?? "auto");
              return next;
            });
            // Picking an edition for an album implies wanting it — no
            // reason to make someone re-tick the checkbox after already
            // choosing a specific pressing.
            setSelected((prev) => new Set(prev).add(album));
            setPickingEditionFor(null);
          }}
        />
      )}
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
