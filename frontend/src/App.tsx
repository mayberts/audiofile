import { NavLink, Route, Routes } from "react-router-dom";
import AlbumDetailPage from "./pages/AlbumDetailPage";
import ArtistDetailPage from "./pages/ArtistDetailPage";
import DashboardPage from "./pages/DashboardPage";
import DownloadsPage from "./pages/DownloadsPage";
import LibraryPage from "./pages/LibraryPage";
import MissingTracksPage from "./pages/MissingTracksPage";
import SearchPage from "./pages/SearchPage";
import SettingsPage from "./pages/SettingsPage";
import WantedPage from "./pages/WantedPage";

function BrandMark() {
  // A turntable viewed from directly above: the platter (ring + spindle),
  // and a tonearm pivoting from its post down to a headshell resting near
  // the record's edge. Kept to a handful of bold shapes (no groove detail,
  // no thin lines) so it still reads clearly at ~26px in the nav.
  return (
    <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="9" fill="var(--accent)" />
      <circle cx="14" cy="18" r="8.5" fill="none" stroke="white" strokeWidth="2" />
      <circle cx="14" cy="18" r="1.7" fill="white" />
      <circle cx="23" cy="9" r="1.9" fill="white" />
      <path d="M23 9 18.2 12.3" stroke="white" strokeWidth="2" strokeLinecap="round" />
      <circle cx="18.2" cy="12.3" r="1.4" fill="white" />
    </svg>
  );
}

function iconProps() {
  return {
    width: 16,
    height: 16,
    viewBox: "0 0 24 24",
    fill: "none" as const,
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
}

function IconHome() {
  return (
    <svg {...iconProps()}>
      <path d="M3 11.5 12 4l9 7.5" />
      <path d="M5.5 10v9a1 1 0 0 0 1 1H9v-6h6v6h2.5a1 1 0 0 0 1-1v-9" />
    </svg>
  );
}

function IconSearch() {
  return (
    <svg {...iconProps()}>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <line x1="20" y1="20" x2="15.3" y2="15.3" />
    </svg>
  );
}

function IconLibrary() {
  return (
    <svg {...iconProps()}>
      <rect x="3" y="3" width="8" height="8" rx="1.5" />
      <rect x="13" y="3" width="8" height="8" rx="1.5" />
      <rect x="3" y="13" width="8" height="8" rx="1.5" />
      <rect x="13" y="13" width="8" height="8" rx="1.5" />
    </svg>
  );
}

function IconDownload() {
  return (
    <svg {...iconProps()}>
      <path d="M12 3v12" />
      <path d="M7 10l5 5 5-5" />
      <path d="M4 19h16" />
    </svg>
  );
}

function IconHeart() {
  return (
    <svg {...iconProps()}>
      <path d="M12 20.5s-7.5-4.6-9.9-9.2C.6 8 2 4.5 5.6 4c2.1-.3 3.9.9 4.9 2.6C11.5 4.9 13.3 3.7 15.4 4c3.6.5 5 4 3.5 7.3-2.4 4.6-9.9 9.2-9.9 9.2z" />
    </svg>
  );
}

function IconAlertList() {
  return (
    <svg {...iconProps()}>
      <path d="M12 3.5 21 19H3z" />
      <line x1="12" y1="9.5" x2="12" y2="13.5" />
      <circle cx="12" cy="16.3" r="0.6" fill="currentColor" stroke="none" />
    </svg>
  );
}

function IconSettings() {
  return (
    <svg {...iconProps()}>
      <circle cx="12" cy="12" r="3.2" />
      <path d="M19.4 13.5a7.6 7.6 0 0 0 0-3l2-1.4-2-3.4-2.3.8a7.6 7.6 0 0 0-2.6-1.5L14 2.5h-4l-.5 2.5a7.6 7.6 0 0 0-2.6 1.5l-2.3-.8-2 3.4 2 1.4a7.6 7.6 0 0 0 0 3l-2 1.4 2 3.4 2.3-.8a7.6 7.6 0 0 0 2.6 1.5l.5 2.5h4l.5-2.5a7.6 7.6 0 0 0 2.6-1.5l2.3.8 2-3.4-2-1.4Z" />
    </svg>
  );
}

const links = [
  { to: "/", label: "Home", end: true, Icon: IconHome },
  { to: "/search", label: "Search", Icon: IconSearch },
  { to: "/library", label: "Library", Icon: IconLibrary },
  { to: "/downloads", label: "Downloads", Icon: IconDownload },
  { to: "/wanted", label: "Wanted", Icon: IconHeart },
  { to: "/missing-tracks", label: "Missing Tracks", Icon: IconAlertList },
  { to: "/settings", label: "Settings", Icon: IconSettings },
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
              <l.Icon />
              {l.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/search" element={<SearchPage />} />
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
