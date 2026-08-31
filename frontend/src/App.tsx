import { NavLink, Route, Routes } from "react-router-dom";
import AlbumDetailPage from "./pages/AlbumDetailPage";
import ArtistDetailPage from "./pages/ArtistDetailPage";
import DownloadsPage from "./pages/DownloadsPage";
import LibraryPage from "./pages/LibraryPage";
import MissingTracksPage from "./pages/MissingTracksPage";
import SearchPage from "./pages/SearchPage";
import SettingsPage from "./pages/SettingsPage";
import WantedPage from "./pages/WantedPage";

function BrandMark() {
  return (
    <svg className="brand-mark" viewBox="0 0 100 100" fill="none" aria-hidden="true">
      <rect x="8" y="16" width="84" height="68" rx="8" stroke="currentColor" strokeWidth="5" />
      <line x1="14" y1="38" x2="86" y2="38" stroke="currentColor" strokeWidth="2" opacity="0.45" />
      <rect x="16" y="44" width="68" height="30" rx="4" fill="#d97f3f" opacity="0.55" />
      <circle cx="32" cy="59" r="9" stroke="currentColor" strokeWidth="4" />
      <rect x="28" y="57.8" width="8" height="2.4" rx="1" fill="currentColor" />
      <rect x="30.8" y="55" width="2.4" height="8" rx="1" fill="currentColor" />
      <circle cx="68" cy="59" r="9" stroke="currentColor" strokeWidth="4" />
      <rect x="64" y="57.8" width="8" height="2.4" rx="1" fill="currentColor" />
      <rect x="66.8" y="55" width="2.4" height="8" rx="1" fill="currentColor" />
      <rect x="34" y="76" width="7" height="5" rx="1.5" fill="currentColor" />
      <rect x="59" y="76" width="7" height="5" rx="1.5" fill="currentColor" />
    </svg>
  );
}

const links = [
  { to: "/", label: "Search", end: true },
  { to: "/library", label: "Library" },
  { to: "/downloads", label: "Downloads" },
  { to: "/wanted", label: "Wanted" },
  { to: "/missing-tracks", label: "Missing Tracks" },
  { to: "/settings", label: "Settings" },
];

export default function App() {
  return (
    <div className="layout">
      <header className="topbar">
        <div className="brand">
          <BrandMark />
          audiofile
        </div>
        <nav>
          {links.map((l) => (
            <NavLink key={l.to} to={l.to} end={l.end} className={({ isActive }) => (isActive ? "active" : "")}>
              {l.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<SearchPage />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/library/:artist" element={<ArtistDetailPage />} />
          <Route path="/library/:artist/:album" element={<AlbumDetailPage />} />
          <Route path="/downloads" element={<DownloadsPage />} />
          <Route path="/wanted" element={<WantedPage />} />
          <Route path="/missing-tracks" element={<MissingTracksPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  );
}
