import { NavLink, Route, Routes } from "react-router-dom";
import AlbumDetailPage from "./pages/AlbumDetailPage";
import ArtistDetailPage from "./pages/ArtistDetailPage";
import DownloadsPage from "./pages/DownloadsPage";
import LibraryPage from "./pages/LibraryPage";
import SearchPage from "./pages/SearchPage";
import SettingsPage from "./pages/SettingsPage";
import WantedPage from "./pages/WantedPage";

const links = [
  { to: "/", label: "Search", end: true },
  { to: "/library", label: "Library" },
  { to: "/downloads", label: "Downloads" },
  { to: "/wanted", label: "Wanted" },
  { to: "/settings", label: "Settings" },
];

export default function App() {
  return (
    <div className="layout">
      <header className="topbar">
        <div className="brand">audiofile</div>
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
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  );
}
