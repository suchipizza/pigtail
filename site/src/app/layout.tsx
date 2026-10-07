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
              <a className="gh-pill" href={GITHUB} target="_blank" rel="noreferrer">★ Star on GitHub</a>
            </nav>
          </header>
          <main>{children}</main>
          <footer>
            <div className="foot">
              <span><b style={{ color: "var(--ink)" }}>pigtail<span style={{ color: "var(--pink)" }}>.</span></b> &nbsp; Open-source growth forensics.</span>
              <span><a href={GITHUB} target="_blank" rel="noreferrer">GitHub</a> · MIT</span>
              <span>Follow the trail <span className="squiggle">〰〰</span></span>
            </div>
          </footer>
        </div>
      </body>
    </html>
  );
}
