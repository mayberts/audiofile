import { useEffect, useState } from "react";
import { api, SettingsOut } from "../api/client";

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsOut | null>(null);
  const [form, setForm] = useState<Partial<SettingsOut>>({});
  const [status, setStatus] = useState<string | null>(null);
  const [slskdConnected, setSlskdConnected] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);

  async function refresh() {
    const data = await api.getSettings();
    setSettings(data);
  }

  useEffect(() => {
    refresh();
    api
      .slskdStatus()
      .then((r) => setSlskdConnected(r.connected))
      .catch(() => setSlskdConnected(false));
  }, []);

  function field(key: keyof SettingsOut, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function onSave() {
    setSaving(true);
    setStatus(null);
    try {
      const updated = await api.updateSettings(form);
      setSettings(updated);
      setForm({});
      setStatus("Saved.");
      api
        .slskdStatus()
        .then((r) => setSlskdConnected(r.connected))
        .catch(() => setSlskdConnected(false));
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  if (!settings) return <div>Loading...</div>;

  const val = (key: keyof SettingsOut) => (form[key] !== undefined ? String(form[key]) : String(settings[key]));

  return (
    <div>
      <h1>Settings</h1>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>slskd connection</h3>
        <div className="field">
          <label>slskd URL</label>
          <input type="url" value={val("slskd_url")} onChange={(e) => field("slskd_url", e.target.value)} />
        </div>
        <div className="field">
          <label>slskd API key</label>
          <input
            type="password"
            placeholder="•••••••• (unchanged)"
            onChange={(e) => field("slskd_api_key", e.target.value)}
          />
        </div>
        {slskdConnected !== null && (
          <span className={`badge ${slskdConnected ? "done" : "failed"}`}>
            {slskdConnected ? "Connected" : "Not reachable"}
          </span>
        )}
      </div>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Plex</h3>
        <div className="field">
          <label>Plex URL</label>
          <input type="url" value={val("plex_url")} onChange={(e) => field("plex_url", e.target.value)} />
        </div>
        <div className="field">
          <label>Plex token</label>
          <input
            type="password"
            placeholder="•••••••• (unchanged)"
            onChange={(e) => field("plex_token", e.target.value)}
          />
        </div>
      </div>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Metadata & files</h3>
        <div className="field">
          <label>MusicBrainz contact (required by their API terms)</label>
          <input
            type="text"
            value={val("musicbrainz_contact")}
            onChange={(e) => field("musicbrainz_contact", e.target.value)}
          />
        </div>
        <div className="field">
          <label>Download directory (shared with slskd)</label>
          <input
            type="text"
            value={val("download_dir")}
            onChange={(e) => field("download_dir", e.target.value)}
          />
        </div>
        <div className="field">
          <label>Organized library directory</label>
          <input
            type="text"
            value={val("library_dir")}
            onChange={(e) => field("library_dir", e.target.value)}
          />
        </div>
        <div className="field">
          <label>Preferred formats (in priority order, comma separated)</label>
          <input
            type="text"
            value={val("preferred_formats")}
            onChange={(e) => field("preferred_formats", e.target.value)}
          />
        </div>
        <div className="field">
          <label>Minimum bitrate (kbps)</label>
          <input
            type="number"
            value={val("min_bitrate_kbps")}
            onChange={(e) => field("min_bitrate_kbps", e.target.value)}
          />
        </div>
        <div className="field">
          <label>Wanted list scan interval (minutes)</label>
          <input
            type="number"
            value={val("wanted_scan_interval_minutes")}
            onChange={(e) => field("wanted_scan_interval_minutes", e.target.value)}
          />
        </div>
      </div>

      <button onClick={onSave} disabled={saving}>
        {saving ? "Saving..." : "Save Settings"}
      </button>
      {status && <span style={{ marginLeft: "0.8rem" }}>{status}</span>}
    </div>
  );
}
