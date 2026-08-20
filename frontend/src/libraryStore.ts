import { LibraryAlbumOut } from "./api/client";

// Shared across LibraryPage and ArtistDetailPage so navigating between them
// (and back) doesn't re-fetch the whole Plex library each time.
export const libraryStore: { albums: LibraryAlbumOut[] | null } = { albums: null };
