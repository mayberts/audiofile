export interface SearchFile {
  username: string;
  filename: string;
  size: number;
  bitrate: number | null;
  length_seconds: number | null;
  extension: string;
  slots_free: boolean;
  upload_speed: number | null;
  queue_length: number | null;
  score: number;
}

export interface SearchResponse {
  query: string;
  results: SearchFile[];
}

export type DownloadStatus =
  | "queued"
  | "in_progress"
  | "completed"
  | "tagging"
  | "done"
  | "failed"
  | "cancelled";

export interface DownloadOut {
  id: number;
  slskd_username: string;
  slskd_filename: string;
  status: DownloadStatus;
  progress_percent: number;
  error: string | null;
  final_path: string | null;
  hint_artist: string | null;
  hint_album: string | null;
  hint_track: string | null;
}

export type WantedStatus =
  | "wanted"
  | "searching"
  | "downloading"
  | "downloaded"
  | "not_found"
  | "failed";

export interface WantedOut {
  id: number;
  artist: string;
  album: string | null;
  track: string | null;
  status: WantedStatus;
  source: "manual" | "plex_gap";
  last_error: string | null;
}

export interface LibraryAlbumOut {
  artist: string;
  artist_thumb: string | null;
  artist_rating_key: string | null;
  album: string;
  thumb: string | null;
  year: number | null;
  track_count: number | null;
  rating_key: string | null;
}

export interface TrackOut {
  title: string;
  track_number: number | null;
  duration_ms: number | null;
}

export interface MissingAlbumOut {
  album: string;
  release_group_mbid: string | null;
  first_release_date: string | null;
}

export interface MissingTrackOut {
  title: string;
  track_number: number | null;
  disc: number | null;
}

export interface TrackCheckOut {
  checked: boolean;
  expected_total: number | null;
  owned_total: number;
  missing_tracks: MissingTrackOut[];
}

export interface ConnectionTestResult {
  connected: boolean;
  detail: string | null;
}

export interface SettingsOut {
  slskd_url: string;
  slskd_api_key: string;
  plex_url: string;
  plex_token: string;
  musicbrainz_contact: string;
  download_dir: string;
  library_dir: string;
  wanted_scan_interval_minutes: number;
  preferred_formats: string;
  min_bitrate_kbps: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${init?.method || "GET"} ${path} failed (${res.status}): ${body}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  search: (query: string) =>
    request<SearchResponse>("/api/search", {
      method: "POST",
      body: JSON.stringify({ query }),
    }),

  download: (params: {
    username: string;
    filename: string;
    size: number;
    hint_artist?: string;
    hint_album?: string;
    hint_track?: string;
  }) =>
    request<DownloadOut>("/api/search/download", {
      method: "POST",
      body: JSON.stringify(params),
    }),

  listDownloads: () => request<DownloadOut[]>("/api/downloads"),
  cancelDownload: (id: number) =>
    request<DownloadOut>(`/api/downloads/${id}/cancel`, { method: "POST" }),

  listWanted: () => request<WantedOut[]>("/api/wanted"),
  createWanted: (params: { artist: string; album?: string; track?: string }) =>
    request<WantedOut>("/api/wanted", { method: "POST", body: JSON.stringify(params) }),
  deleteWanted: (id: number) => request<void>(`/api/wanted/${id}`, { method: "DELETE" }),
  scanWantedNow: (id: number) =>
    request<WantedOut>(`/api/wanted/${id}/scan-now`, { method: "POST" }),
  scanAllWanted: () => request<{ status: string }>("/api/wanted/scan-all", { method: "POST" }),

  getLibrary: () => request<LibraryAlbumOut[]>("/api/plex/library"),
  scanLibrary: () => request<LibraryAlbumOut[]>("/api/plex/library/scan", { method: "POST" }),
  getAlbumTracks: (ratingKey: string) => request<TrackOut[]>(`/api/plex/album/${ratingKey}/tracks`),
  getArtistBio: (ratingKey: string) =>
    request<{ summary: string }>(`/api/plex/artist/${ratingKey}/bio`),
  plexImageUrl: (path: string) => `/api/plex/image?path=${encodeURIComponent(path)}`,

  getMissingAlbums: (artistRatingKey: string) =>
    request<MissingAlbumOut[]>(`/api/plex/artist/${artistRatingKey}/missing-albums`),
  getTrackCheck: (albumRatingKey: string) =>
    request<TrackCheckOut>(`/api/plex/album/${albumRatingKey}/track-check`),
  addMissingAlbumToWanted: (params: { artist: string; album: string; release_group_mbid?: string | null }) =>
    request<WantedOut>("/api/plex/missing-album/add-to-wanted", {
      method: "POST",
      body: JSON.stringify(params),
    }),

  getSettings: () => request<SettingsOut>("/api/settings"),
  updateSettings: (patch: Partial<SettingsOut>) =>
    request<SettingsOut>("/api/settings", { method: "PUT", body: JSON.stringify(patch) }),

  testSlskd: (url: string, apiKey: string) =>
    request<ConnectionTestResult>("/api/settings/test-slskd", {
      method: "POST",
      body: JSON.stringify({ url, api_key: apiKey }),
    }),
  testPlex: (url: string, token: string) =>
    request<ConnectionTestResult>("/api/settings/test-plex", {
      method: "POST",
      body: JSON.stringify({ url, token }),
    }),
  detectSlskd: () => request<string[]>("/api/settings/detect-slskd"),
};
