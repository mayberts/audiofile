import { AlbumTrackGapOut, LibraryAlbumOut } from "./api/client";

// Shared across LibraryPage and ArtistDetailPage so navigating between them
// (and back) doesn't re-fetch the whole Plex library each time.
export const libraryStore: {
  albums: LibraryAlbumOut[] | null;
  // The last missing-tracks scan's results, cached the same way -- used to
  // show "N missing" indicators while browsing instead of only on the
  // separate Missing Tracks page. Same staleness caveat as that page: a
  // point-in-time snapshot, not re-verified live on every visit.
  trackGaps: AlbumTrackGapOut[] | null;
} = { albums: null, trackGaps: null };
