import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { GITHUB } from "@/lib";

export const metadata: Metadata = {
  title: "Pigtail — open-source growth forensics",
  description:
    "Pigtail reconstructs how a product or GitHub repository grew from public evidence: star history, launches, posts, releases, tactics and sources in one report.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="nav">
          <Link href="/" className="brand">pigtail<span>.</span></Link>
          <nav>
            <Link href="/examples/">Examples</Link>
            <a href={`${GITHUB}/blob/main/docs/quickstart.md`}>Quick start</a>
            <a href={`${GITHUB}/tree/main/docs`}>Docs</a>
            <a className="gh" href={GITHUB}>★ Star on GitHub</a>
          </nav>
        </header>
        <main>{children}</main>
        <footer className="foot">
          <p>
            Pigtail is open source (MIT). <a href={GITHUB}>GitHub</a> ·{" "}
            <a href={`${GITHUB}/blob/main/docs/methodology.md`}>Methodology</a> ·{" "}
            <a href={`${GITHUB}/blob/main/docs/source-policies.md`}>Source policies</a> ·{" "}
            <a href={`${GITHUB}/issues`}>Issues</a>
          </p>
          <p className="muted">Reports are machine-generated from public evidence and reviewed before publication. Check linked sources before relying on a claim.</p>
        </footer>
      </body>
    </html>
  );
}
