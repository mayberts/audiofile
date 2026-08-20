import { useEffect, useState } from "react";
import { api, PosterOut } from "../api/client";

// Candidates Plex's metadata agent found but hasn't downloaded/cached yet
// (i.e. anything other than the currently-selected poster) come back with
// thumb already pointing straight at the external source — our own-host
// proxy only knows how to fetch Plex-relative paths, so those go through
// the browser directly instead of being routed through it.
function posterImageSrc(thumb: string): string {
  return /^https?:\/\//i.test(thumb) ? thumb : api.plexImageUrl(thumb);
}

interface ArtworkPickerProps {
  ratingKey: string;
  onClose: () => void;
  onChanged: (thumb: string | null) => void;
}

export default function ArtworkPicker({ ratingKey, onClose, onChanged }: ArtworkPickerProps) {
  const [posters, setPosters] = useState<PosterOut[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [urlInput, setUrlInput] = useState("");

  useEffect(() => {
    setLoading(true);
    api
      .getItemPosters(ratingKey)
      .then(setPosters)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [ratingKey]);

  async function onSelect(poster: PosterOut) {
    setBusyKey(poster.key);
    setError(null);
    try {
      const result = await api.selectItemPoster(ratingKey, poster.key);
      onChanged(result.thumb);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyKey(null);
    }
  }

  async function onUploadUrl() {
    if (!urlInput.trim()) return;
    setBusyKey("__url__");
    setError(null);
    try {
      const result = await api.uploadItemPosterUrl(ratingKey, urlInput.trim());
      onChanged(result.thumb);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyKey(null);
    }
  }

  async function onUploadFile(file: File) {
    setBusyKey("__file__");
    setError(null);
    try {
      const result = await api.uploadItemPosterFile(ratingKey, file);
      onChanged(result.thumb);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyKey(null);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ justifyContent: "space-between", marginBottom: "0.8rem" }}>
          <h2 style={{ margin: 0 }}>Change Artwork</h2>
          <button className="secondary" onClick={onClose}>
            Close
          </button>
        </div>

        {error && <div className="error-text" style={{ marginBottom: "0.8rem" }}>{error}</div>}

        {loading && <div className="muted">Loading options from Plex...</div>}

        {!loading && posters && posters.length === 0 && (
          <div className="muted" style={{ marginBottom: "0.8rem" }}>
            Plex doesn't have any alternate artwork for this yet — upload one below.
          </div>
        )}

        {!loading && posters && posters.length > 0 && (
          <div className="poster-grid">
            {posters.map((p) => (
              <button
                key={p.key}
                className={`poster-option ${p.selected ? "poster-option-selected" : ""}`}
                onClick={() => onSelect(p)}
                disabled={busyKey !== null}
                title={p.provider || undefined}
              >
                {p.thumb ? (
                  <img src={posterImageSrc(p.thumb)} alt="" loading="lazy" />
                ) : (
                  <div className="artist-card-fallback">?</div>
                )}
                {busyKey === p.key && <div className="poster-option-busy">Setting...</div>}
              </button>
            ))}
          </div>
        )}

        <h2 style={{ marginTop: "1.2rem" }}>Upload Your Own</h2>
        <div className="field">
          <label>From an image URL</label>
          <div className="row">
            <input
              type="text"
              placeholder="https://..."
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
            />
            <button onClick={onUploadUrl} disabled={busyKey !== null || !urlInput.trim()}>
              {busyKey === "__url__" ? "Uploading..." : "Use URL"}
            </button>
          </div>
        </div>
        <div className="field">
          <label>From a file</label>
          <input
            type="file"
            accept="image/*"
            disabled={busyKey !== null}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) onUploadFile(file);
            }}
          />
          {busyKey === "__file__" && <span className="muted">Uploading...</span>}
        </div>
      </div>
    </div>
  );
}
