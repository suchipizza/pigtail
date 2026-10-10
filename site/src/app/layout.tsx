import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { GITHUB, MASCOT } from "@/lib";

export const metadata: Metadata = {
  title: "Pigtail — Open-source growth forensics",
  description:
    "Study how open-source projects actually grew. Pigtail reconstructs launches, posts, releases, growth episodes and evidence on one timeline.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="wrap">
          <header className="nav">
            <Link className="brand" href="/"><img className="brand-pig" src={MASCOT} alt="" width={1275} height={1234} />pigtail<span style={{ color: "var(--pink)" }}>.</span></Link>
            <nav className="nav-links">
              <Link href="/examples/">Growth stories</Link>
              <Link href="/#how">How it works</Link>
              <Link href="/#method">Method</Link>
              <a className="gh-icon" href={GITHUB} target="_blank" rel="noreferrer" aria-label="Pigtail on GitHub" title="Pigtail on GitHub">
                <svg viewBox="0 0 16 16" width="26" height="26" aria-hidden="true"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>
              </a>
            </nav>
          </header>
          <main>{children}</main>
          <footer>
            <div className="foot">
              <span><b style={{ color: "var(--ink)" }}>pigtail<span style={{ color: "var(--pink)" }}>.</span></b> &nbsp; Open-source growth forensics.</span>
              <span><a href={GITHUB} target="_blank" rel="noreferrer">GitHub</a> · MIT · <Link href="/legal/">Legal &amp; privacy</Link></span>
              <span>Follow the trail <span className="squiggle">〰〰</span></span>
            </div>
          </footer>
        </div>
      </body>
    </html>
  );
}
