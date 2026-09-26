import { useCallback, useEffect, useState } from "react";
import { api, setUnauthorizedHandler } from "./api";
import { PreviewBanner } from "./components/PreviewBanner";
import { BriefEditorPage } from "./pages/BriefEditorPage";
import { BriefPage } from "./pages/BriefPage";
import { BriefsPage } from "./pages/BriefsPage";
import { CasePage } from "./pages/CasePage";
import { CasesPage } from "./pages/CasesPage";
import { EvidencePage } from "./pages/EvidencePage";
import { LoginPage } from "./pages/LoginPage";
import { ShortlistPage } from "./pages/ShortlistPage";
import { Link, matchRoute, useLocation } from "./router";

type Auth = "checking" | "in" | "out";

export function App() {
  const [auth, setAuth] = useState<Auth>("checking");
  const { path, search } = useLocation();

  useEffect(() => {
    setUnauthorizedHandler(() => setAuth("out"));
    // Any failure (401, server down) shows the login page; login reports the real error.
    api.me().then(
      () => setAuth("in"),
      () => setAuth("out"),
    );
  }, []);

  const logout = useCallback(() => {
    api.logout().finally(() => setAuth("out"));
  }, []);

  if (auth === "checking") return <p className="page muted">Loading…</p>;

  const route = matchRoute(path);
  return (
    <>
      <PreviewBanner />
      <header className="topbar">
        <Link href="/cases" className="brand">
          pigtail <span className="muted">Forensics Explorer</span>
        </Link>
        {auth === "in" && (
          <nav className="topnav">
            <Link href="/cases">Cases</Link>
            <Link href="/briefs">Briefs</Link>
            <button type="button" className="link-button" onClick={logout}>
              Log out
            </button>
          </nav>
        )}
      </header>
      <main className="page">
        {auth === "out" ? (
          <LoginPage onLogin={() => setAuth("in")} />
        ) : route.name === "cases" ? (
          <CasesPage />
        ) : route.name === "case" ? (
          <CasePage id={route.id} />
        ) : route.name === "evidence" ? (
          <EvidencePage id={route.id} />
        ) : route.name === "briefs" ? (
          <BriefsPage />
        ) : route.name === "briefNew" ? (
          <BriefEditorPage importYaml={search.get("import") === "yaml"} />
        ) : route.name === "brief" ? (
          <BriefPage id={route.id} />
        ) : route.name === "briefEdit" ? (
          <BriefEditorPage key={route.id} id={route.id} />
        ) : route.name === "briefShortlist" ? (
          <ShortlistPage key={route.id} id={route.id} />
        ) : (
          <p>
            Page not found. <Link href="/cases">Back to cases</Link>
          </p>
        )}
      </main>
    </>
  );
}
