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
  | "failed"
  | "awaiting_review";

export interface WantedOut {
  id: number;
  artist: string;
  album: string | null;
  track: string | null;
  release_mbid: string | null;
  status: WantedStatus;
  source: "manual" | "plex_gap";
  last_error: string | null;
}

// One scored candidate folder pooled for a human to pick from when nothing
// scored confidently enough for the backend to auto-pick (WantedStatus
// "awaiting_review") -- see score_album_candidates in the backend.
export interface WantedReviewCandidateOut {
  id: number;
  username: string;
  directory: string;
  file_count: number;
  total_size_bytes: number;
  score: number;
  tier: "auto" | "manual" | "rejected";
}

export interface ReleaseEditionOut {
  release_mbid: string;
  title: string;
  disambiguation: string | null;
  date: string | null;
  country: string | null;
  track_count: number;
  format: string | null;
  label: string | null;
  catalog_number: string | null;
  barcode: string | null;
  status: string | null;
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
  pinned_release_mbid: string | null;
  pinned_release_title: string | null;
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
  in_library: boolean;
}

// An artist added purely to browse -- see the Library page's "Add Artist".
// Tracking one never touches the wanted list or triggers any download.
export interface TrackedArtistOut {
  artist: string;
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
  release_mbid: string | null;
  release_title: string | null;
}

export type TrackGapScanState = "running" | "completed" | "cancelled" | "failed";

export interface TrackGapScanOut {
  id: number;
  status: TrackGapScanState;
  total_albums: number;
  checked_albums: number;
  started_at: string;
  finished_at: string | null;
  last_error: string | null;
}

export interface AlbumTrackGapOut {
  rating_key: string;
  artist: string;
  album: string;
  thumb: string | null;
  expected_total: number | null;
  owned_total: number;
  missing_count: number;
  missing_tracks: string[];
  release_title: string | null;
  checked_at: string;
}

export interface PosterOut {
  key: string;
  thumb: string | null;
  provider: string | null;
  selected: boolean;
}

export interface PosterResultOut {
  thumb: string | null;
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
  musicbrainz_base_url: string;
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

// Separate from request(): a multipart body needs the browser to set its
// own Content-Type (with the boundary), so it can't go through the
// JSON-only helper above.
async function requestForm<T>(path: string, formData: FormData): Promise<T> {
  const res = await fetch(path, { method: "POST", body: formData });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`POST ${path} failed (${res.status}): ${body}`);
  }
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
  retryDownload: (id: number) =>
    request<DownloadOut>(`/api/downloads/${id}/retry`, { method: "POST" }),
  clearCompletedDownloads: () =>
    request<{ cleared: number }>("/api/downloads/completed", { method: "DELETE" }),

  listWanted: () => request<WantedOut[]>("/api/wanted"),
  getArtistDiscography: (artist: string) =>
    request<MissingAlbumOut[]>(`/api/wanted/discography?artist=${encodeURIComponent(artist)}`),
  getReleaseEditions: (releaseGroupMbid: string) =>
    request<ReleaseEditionOut[]>(`/api/wanted/release-editions?release_group_mbid=${encodeURIComponent(releaseGroupMbid)}`),
  coverArtUrl: (releaseGroupMbid: string) => `/api/wanted/cover-art/${encodeURIComponent(releaseGroupMbid)}`,
  createWanted: (params: { artist: string; album?: string; track?: string; release_mbid?: string | null }) =>
    request<WantedOut>("/api/wanted", { method: "POST", body: JSON.stringify(params) }),
  deleteWanted: (id: number) => request<void>(`/api/wanted/${id}`, { method: "DELETE" }),
  scanWantedNow: (id: number) =>
    request<WantedOut>(`/api/wanted/${id}/scan-now`, { method: "POST" }),
  scanAllWanted: () => request<{ status: string }>("/api/wanted/scan-all", { method: "POST" }),
  listWantedCandidates: (id: number) =>
    request<WantedReviewCandidateOut[]>(`/api/wanted/${id}/candidates`),
  pickWantedCandidate: (id: number, candidateId: number) =>
    request<WantedOut>(`/api/wanted/${id}/candidates/${candidateId}/pick`, { method: "POST" }),
  rejectWantedCandidates: (id: number) =>
    request<WantedOut>(`/api/wanted/${id}/candidates/reject`, { method: "POST" }),

  getLibrary: () => request<LibraryAlbumOut[]>("/api/plex/library"),
  scanLibrary: () => request<LibraryAlbumOut[]>("/api/plex/library/scan", { method: "POST" }),
  listTrackedArtists: () => request<TrackedArtistOut[]>("/api/artists"),
  trackArtist: (artist: string) =>
    request<TrackedArtistOut>("/api/artists", { method: "POST", body: JSON.stringify({ artist }) }),
  untrackArtist: (artist: string) =>
    request<void>(`/api/artists/${encodeURIComponent(artist)}`, { method: "DELETE" }),
  getAlbumTracks: (ratingKey: string) => request<TrackOut[]>(`/api/plex/album/${ratingKey}/tracks`),
  getArtistBio: (ratingKey: string) =>
    request<{ summary: string }>(`/api/plex/artist/${ratingKey}/bio`),
  plexImageUrl: (path: string) => `/api/plex/image?path=${encodeURIComponent(path)}`,

  getMissingAlbums: (artistRatingKey: string) =>
    request<MissingAlbumOut[]>(`/api/plex/artist/${artistRatingKey}/missing-albums`),
  getTrackCheck: (albumRatingKey: string, releaseMbid?: string | null) =>
    request<TrackCheckOut>(
      `/api/plex/album/${albumRatingKey}/track-check${releaseMbid ? `?release_mbid=${encodeURIComponent(releaseMbid)}` : ""}`,
    ),
  pinAlbumRelease: (albumRatingKey: string, releaseMbid: string, releaseTitle: string) =>
    request<LibraryAlbumOut>(`/api/plex/album/${albumRatingKey}/release/pin`, {
      method: "POST",
      body: JSON.stringify({ release_mbid: releaseMbid, release_title: releaseTitle }),
    }),
  unpinAlbumRelease: (albumRatingKey: string) =>
    request<LibraryAlbumOut>(`/api/plex/album/${albumRatingKey}/release/unpin`, { method: "POST" }),
  getTrackGapScan: () => request<TrackGapScanOut | null>("/api/track-gaps/scan"),
  startTrackGapScan: () => request<TrackGapScanOut>("/api/track-gaps/scan", { method: "POST" }),
  cancelTrackGapScan: () => request<TrackGapScanOut>("/api/track-gaps/scan/cancel", { method: "POST" }),
  listTrackGaps: () => request<AlbumTrackGapOut[]>("/api/track-gaps"),
  searchReleases: (artist: string, query: string) =>
    request<ReleaseEditionOut[]>(
      `/api/wanted/release-search?artist=${encodeURIComponent(artist)}&query=${encodeURIComponent(query)}`,
    ),
  addMissingAlbumToWanted: (params: {
    artist: string;
    album: string;
    release_group_mbid?: string | null;
    release_mbid?: string | null;
  }) =>
    request<WantedOut>("/api/plex/missing-album/add-to-wanted", {
      method: "POST",
      body: JSON.stringify(params),
    }),

  getItemPosters: (ratingKey: string) => request<PosterOut[]>(`/api/plex/item/${ratingKey}/posters`),
  selectItemPoster: (ratingKey: string, posterKey: string) =>
    request<PosterResultOut>(`/api/plex/item/${ratingKey}/poster/select`, {
      method: "POST",
      body: JSON.stringify({ poster_key: posterKey }),
    }),
  uploadItemPosterUrl: (ratingKey: string, url: string) => {
    const form = new FormData();
    form.append("url", url);
    return requestForm<PosterResultOut>(`/api/plex/item/${ratingKey}/poster/upload`, form);
  },
  uploadItemPosterFile: (ratingKey: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<PosterResultOut>(`/api/plex/item/${ratingKey}/poster/upload`, form);
  },

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
