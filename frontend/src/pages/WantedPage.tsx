import { FormEvent, useEffect, useState } from "react";
import { api, WantedOut } from "../api/client";
import DiscographyPicker from "../components/DiscographyPicker";

export default function WantedPage() {
  const [items, setItems] = useState<WantedOut[]>([]);
  const [artist, setArtist] = useState("");
  const [album, setAlbum] = useState("");
  const [track, setTrack] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [scanningAll, setScanningAll] = useState(false);
  const [pickingArtist, setPickingArtist] = useState<string | null>(null);
  const [scanningIds, setScanningIds] = useState<Set<number>>(new Set());

  async function refresh() {
    try {
      setItems(await api.listWanted());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, []);

  async function onAdd(e: FormEvent) {
    e.preventDefault();
    if (adding) return; // a fast double-click/double-Enter shouldn't fire this twice
    const artistName = artist.trim();
    if (!artistName) return;

    // With just an artist name, a single wanted item can only ever chase
    // down one random matching file — not the "whole discography" the
    // empty field implies. Let the user pick specific albums instead.
    if (!album.trim() && !track.trim()) {
      setPickingArtist(artistName);
      return;
    }

    setAdding(true);
    try {
      await api.createWanted({
        artist: artistName,
        album: album.trim() || undefined,
        track: track.trim() || undefined,
      });
      setArtist("");
      setAlbum("");
      setTrack("");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setAdding(false);
    }
  }

  async function onDelete(id: number) {
    await api.deleteWanted(id);
    refresh();
  }

  async function onScanNow(id: number) {
    setScanningIds((prev) => new Set(prev).add(id));
    try {
      await api.scanWantedNow(id);
      refresh();
    } finally {
      setScanningIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

  async function onScanAll() {
    setScanningAll(true);
    try {
      await api.scanAllWanted();
    } finally {
      setTimeout(() => {
        setScanningAll(false);
        refresh();
      }, 2000);
    }
  }

  return (
    <div>
      <h1>Wanted List</h1>

      <form className="panel inline" onSubmit={onAdd}>
        <input type="text" placeholder="Artist" value={artist} onChange={(e) => setArtist(e.target.value)} />
        <input
          type="text"
          placeholder="Album (optional)"
          value={album}
          onChange={(e) => setAlbum(e.target.value)}
        />
        <input
          type="text"
          placeholder="Track (optional, for single tracks)"
          value={track}
          onChange={(e) => setTrack(e.target.value)}
        />
        <button type="submit" disabled={adding}>
          {adding ? "Adding..." : "Add"}
        </button>
        <button type="button" className="secondary" onClick={onScanAll} disabled={scanningAll}>
          {scanningAll ? "Scanning..." : "Scan All Now"}
        </button>
      </form>

      {error && <div className="panel error-text">{error}</div>}

      <div className="panel">
        {items.length === 0 && <div className="empty">Nothing wanted yet — add an artist or album above.</div>}
        {items.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Artist</th>
                <th>Album / Track</th>
                <th>Source</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((w) => (
                <tr key={w.id}>
                  <td>{w.artist}</td>
                  <td>
                    {w.track || w.album || <span className="muted">whole discography</span>}
                    {w.last_error && <div className="error-text">{w.last_error}</div>}
                  </td>
                  <td className="muted">{w.source === "plex_gap" ? "Plex gap" : "Manual"}</td>
                  <td>
                    <span className={`badge ${w.status}`}>{w.status.replace("_", " ")}</span>
                  </td>
                  <td className="row">
                    <button
                      className="secondary"
                      onClick={() => onScanNow(w.id)}
                      disabled={scanningIds.has(w.id) || w.status === "searching" || w.status === "downloading"}
                    >
                      {scanningIds.has(w.id) ? "Scanning..." : "Scan"}
                    </button>
                    <button className="danger" onClick={() => onDelete(w.id)}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {pickingArtist && (
        <DiscographyPicker
          artist={pickingArtist}
          onClose={() => setPickingArtist(null)}
          onAdded={() => {
            setArtist("");
            setAlbum("");
            setTrack("");
            refresh();
          }}
        />
      )}
    </div>
  );
}
