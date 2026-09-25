import { useCallback, useEffect, useState } from "react";
import { api, setUnauthorizedHandler } from "./api";
import { PreviewBanner } from "./components/PreviewBanner";
import { CasePage } from "./pages/CasePage";
import { CasesPage } from "./pages/CasesPage";
import { EvidencePage } from "./pages/EvidencePage";
import { LoginPage } from "./pages/LoginPage";
import { Link, matchRoute, useLocation } from "./router";

type Auth = "checking" | "in" | "out";

export function App() {
  const [auth, setAuth] = useState<Auth>("checking");
  const { path } = useLocation();

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
          <button type="button" className="link-button" onClick={logout}>
            Log out
          </button>
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
        ) : (
          <p>
            Page not found. <Link href="/cases">Back to cases</Link>
          </p>
        )}
      </main>
    </>
  );
}
