import Link from "next/link";
import { ExampleCard } from "@/components/ExampleCard";
import { BASE, EXAMPLES, GITHUB } from "@/lib";

export default function Home() {
  const featured = EXAMPLES.filter((e) => e.featured).slice(0, 6);
  return (
    <>
      <section className="hero">
        <div className="hero-text">
          <div className="kicker">Open-source growth forensics</div>
          <h1>See how a product actually grew.</h1>
          <p className="lead">
            Give Pigtail a GitHub repository or a product website. It reads public evidence — the
            repository&apos;s history, launch posts, Hacker News threads, founder blogs, interviews — and
            writes a report of how the product grew, with every fact linked to its source.
          </p>
          <div className="cta">
            <a className="btn primary" href={`${GITHUB}#quick-start`}>Install and run it</a>
            <Link className="btn" href="/examples/">Browse example reports</Link>
            <a className="btn ghost" href={GITHUB}>★ Star on GitHub</a>
          </div>
          <pre className="cmd"><code>pigtail https://github.com/owner/repository{"\n"}pigtail product.com</code></pre>
        </div>
        <figure className="hero-shot">
          <img src={`${BASE}/demo-chart.png`} alt="A Pigtail report: GitHub stars over time with launches, Hacker News posts and releases marked on the same timeline" />
          <figcaption>GitHub stars and public events on one timeline. Markers show timing, not cause.</figcaption>
        </figure>
      </section>

      <section className="section">
        <h2>Who it is for</h2>
        <div className="grid3">
          <div className="card"><h3>Open-source maintainers</h3><p>See what happened around every jump in your stars — or someone else&apos;s — and compare launches side by side before you plan your own.</p></div>
          <div className="card"><h3>Founders and growth people</h3><p>Get the first users, launches, metrics, tactics and strategy changes of a product, reconstructed from public sources instead of hours of searching.</p></div>
          <div className="card"><h3>Researchers and writers</h3><p>Every statement is a structured claim with a source, quote and date, saved as JSON you can check, diff and reuse.</p></div>
        </div>
      </section>

      <section className="section">
        <h2>What a report contains</h2>
        <div className="grid2">
          <ul className="checks">
            <li>A short summary of the growth story, marked as fact or interpretation</li>
            <li>Origin and how the first users were found</li>
            <li>A dated timeline of launches, posts, releases, pricing and strategy changes</li>
            <li>Historical metrics (users, revenue, stars) as reported, with conflicts shown</li>
            <li>Growth engines and reusable tactics, each tied to evidence</li>
            <li>Research gaps: what could not be established</li>
          </ul>
          <ul className="checks">
            <li><b>For GitHub projects:</b> exact daily star history</li>
            <li>Show HN, Hacker News, Product Hunt, Reddit and release markers on the same time axis</li>
            <li>Growth episodes, with &ldquo;No high-confidence public event found&rdquo; when nothing explains a spike</li>
            <li>Launch episodes with stars before, +24h, +7 days, +30 days, +90 days</li>
            <li>A Research Bundle (JSON) and a single HTML file that works offline</li>
          </ul>
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <h2>Example reports</h2>
          <Link href="/examples/">All examples →</Link>
        </div>
        <div className="grid3">{featured.map((ex) => <ExampleCard key={ex.slug} ex={ex} />)}</div>
      </section>

      <section className="section" id="how">
        <h2>Try it in five minutes</h2>
        <ol className="steps">
          <li><b>Install</b> (Python 3.12+): <code>uv tool install git+{GITHUB}</code></li>
          <li><b>Add a key:</b> <code>export ANTHROPIC_API_KEY=…</code> (optional: <code>GITHUB_TOKEN</code>)</li>
          <li><b>Check setup:</b> <code>pigtail doctor</code></li>
          <li><b>Run:</b> <code>pigtail https://github.com/owner/repository</code> — about 3–5 minutes and $1–3 of API usage</li>
        </ol>
        <p className="muted">No Pigtail account, database or server. The report is a single HTML file on your computer.</p>
      </section>

      <section className="section">
        <h2>How Pigtail stays honest</h2>
        <div className="grid3">
          <div className="card"><h3>Quotes are checked</h3><p>Each extracted claim must quote its source word for word. Claims whose quote is not in the page are dropped.</p></div>
          <div className="card"><h3>Timing is not cause</h3><p>An event near a star spike is labelled &ldquo;observed near this period&rdquo;. Causes are shown only when the company states them.</p></div>
          <div className="card"><h3>Unknown is an answer</h3><p>Missing evidence becomes a visible gap, not a guess. Thin evidence gives a short report, not a padded one.</p></div>
        </div>
      </section>
    </>
  );
}
