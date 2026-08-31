import { useEffect, useState } from "react";
import { api, SettingsOut } from "../api/client";

type TestState = { status: "idle" | "testing" | "ok" | "fail"; detail?: string | null };

const IDLE: TestState = { status: "idle" };

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsOut | null>(null);
  const [form, setForm] = useState<Partial<SettingsOut>>({});
  const [saveStatus, setSaveStatus] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [slskdTest, setSlskdTest] = useState<TestState>(IDLE);
  const [plexTest, setPlexTest] = useState<TestState>(IDLE);
  const [detecting, setDetecting] = useState(false);

  useEffect(() => {
    api.getSettings().then(setSettings);
  }, []);

  function field(key: keyof SettingsOut, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  const val = (key: keyof SettingsOut) =>
    form[key] !== undefined ? String(form[key]) : settings ? String(settings[key]) : "";

  async function onSave() {
    setSaving(true);
    setSaveStatus(null);
    try {
      const updated = await api.updateSettings(form);
      setSettings(updated);
      setForm({});
      setSaveStatus("Saved.");
    } catch (err) {
      setSaveStatus(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function onTestSlskd() {
    setSlskdTest({ status: "testing" });
    try {
      const res = await api.testSlskd(val("slskd_url"), (form.slskd_api_key as string) || "");
      setSlskdTest({ status: res.connected ? "ok" : "fail", detail: res.detail });
    } catch (err) {
      setSlskdTest({ status: "fail", detail: err instanceof Error ? err.message : String(err) });
    }
  }

  async function onDetectSlskd() {
    setDetecting(true);
    setSlskdTest(IDLE);
    try {
      const found = await api.detectSlskd();
      if (found.length > 0) {
        field("slskd_url", found[0]);
        setSlskdTest({ status: "ok" });
      } else {
        setSlskdTest({ status: "fail", detail: "No local slskd found — enter the URL manually." });
      }
    } catch (err) {
      setSlskdTest({ status: "fail", detail: err instanceof Error ? err.message : String(err) });
    } finally {
      setDetecting(false);
    }
  }

  async function onTestPlex() {
    setPlexTest({ status: "testing" });
    try {
      const res = await api.testPlex(val("plex_url"), (form.plex_token as string) || "");
      setPlexTest({ status: res.connected ? "ok" : "fail", detail: res.detail });
    } catch (err) {
      setPlexTest({ status: "fail", detail: err instanceof Error ? err.message : String(err) });
    }
  }

  if (!settings) return <div>Loading...</div>;

  return (
    <div>
      <h1>Settings</h1>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Soulseek (slskd)</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          Already running your own slskd container? Point this at wherever it's reachable — its
          published host port (e.g. <code>http://host.docker.internal:5030</code>) or its
          container name if you've joined it to the same Docker network.
        </p>
        <div className="field">
          <label>slskd URL</label>
          <div className="row">
            <input type="url" placeholder="http://localhost:5030" value={val("slskd_url")} onChange={(e) => field("slskd_url", e.target.value)} />
            <button className="secondary" onClick={onDetectSlskd} disabled={detecting}>
              {detecting ? "Detecting..." : "Auto-detect"}
            </button>
          </div>
        </div>
        <div className="field">
          <label>API key</label>
          <input
            type="password"
            placeholder="•••••••• (unchanged)"
            onChange={(e) => field("slskd_api_key", e.target.value)}
          />
        </div>
        <div className="row">
          <button className="secondary" onClick={onTestSlskd} disabled={slskdTest.status === "testing"}>
            {slskdTest.status === "testing" ? "Testing..." : "Test Connection"}
          </button>
          {slskdTest.status === "ok" && <span className="badge done">Connected</span>}
          {slskdTest.status === "fail" && <span className="badge failed">Not reachable</span>}
          {slskdTest.detail && <span className="muted">{slskdTest.detail}</span>}
        </div>
      </div>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Plex</h3>
        <div className="field">
          <label>Plex server URL</label>
          <input type="url" placeholder="http://localhost:32400" value={val("plex_url")} onChange={(e) => field("plex_url", e.target.value)} />
        </div>
        <div className="field">
          <label>X-Plex-Token</label>
          <input
            type="password"
            placeholder="•••••••• (unchanged)"
            onChange={(e) => field("plex_token", e.target.value)}
          />
        </div>
        <div className="row">
          <button className="secondary" onClick={onTestPlex} disabled={plexTest.status === "testing"}>
            {plexTest.status === "testing" ? "Testing..." : "Test Connection"}
          </button>
          {plexTest.status === "ok" && <span className="badge done">Connected</span>}
          {plexTest.status === "fail" && <span className="badge failed">Not reachable</span>}
          {plexTest.detail && <span className="muted">{plexTest.detail}</span>}
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
          <label>MusicBrainz API base URL</label>
          <input
            type="url"
            placeholder="https://musicbrainz.org/ws/2"
            value={val("musicbrainz_base_url")}
            onChange={(e) => field("musicbrainz_base_url", e.target.value)}
          />
          <span className="muted">
            Point this at your own self-hosted MusicBrainz mirror instead of the public API — e.g.{" "}
            <code>http://host.docker.internal:5000/ws/2</code>. Leave as the default to use
            musicbrainz.org.
          </span>
        </div>
        <div className="row" style={{ gap: "1rem", alignItems: "flex-start" }}>
          <div className="field" style={{ flex: 1 }}>
            <label>Rate Limit (requests/sec)</label>
            <input
              type="number"
              min={0}
              value={val("musicbrainz_rate_limit_per_sec")}
              onChange={(e) => field("musicbrainz_rate_limit_per_sec", e.target.value)}
            />
            <span className="muted">
              1 for the public API (its documented limit). Up to 500 on your own or a chosen server.
              0 = unlimited (no client-side limiter). Be polite with servers you don't own.
            </span>
          </div>
          <div className="field" style={{ flex: 1 }}>
            <label>Concurrent Requests</label>
            <input
              type="number"
              min={1}
              value={val("musicbrainz_concurrent_requests")}
              onChange={(e) => field("musicbrainz_concurrent_requests", e.target.value)}
            />
            <span className="muted">
              How many MusicBrainz lookups run at once — also sizes the missing-tracks scan's
              worker pool. MusicBrainz's own official default is 6.
            </span>
          </div>
        </div>
        <div className="field">
          <label>Download directory (path inside this container)</label>
          <input
            type="text"
            value={val("download_dir")}
            onChange={(e) => field("download_dir", e.target.value)}
          />
          <span className="muted">
            This has to be the same physical folder slskd downloads into — set{" "}
            <code>HOST_DOWNLOADS_DIR</code> in <code>.env</code> to bind-mount it here; a path
            typed here alone won't create the mount.
          </span>
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
      {saveStatus && <span style={{ marginLeft: "0.8rem" }}>{saveStatus}</span>}
    </div>
  );
}
