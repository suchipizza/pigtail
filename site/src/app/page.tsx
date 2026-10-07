import Link from "next/link";
import { preload } from "react-dom";
import { HeroIntro } from "@/components/HeroIntro";
import { Sparkline, starCurve } from "@/components/Sparkline";
import { EXAMPLES, GITHUB, MASCOT, fmt } from "@/lib";

// Copy follows the owner's model (Documents/pigtail_landing_page_clay_inspired.html); stars and
// charts come from the published examples.
const STORIES = [
  { slug: "pocketbase", cls: "story story-featured", no: "Case 01 · Open source", stroke: "#ff78ab",
    text: "A one-developer backend whose stars grew in bursts, mostly from posts by other people.",
    tags: ["growth bursts", "third-party posts"], question: "Why did it keep moving?" },
  { slug: "hatchet", cls: "story", no: "Case 02 · Open source", stroke: "#d92f72",
    text: "An open-source task queue that launched repeatedly on Hacker News — Show HN, Launch HN, then v1.",
    tags: ["Show HN", "Launch HN", "repeated launches"], question: "What changed between launches?" },
  { slug: "plausible", cls: "story", no: "Case 03 · Open source", stroke: "#d92f72",
    text: "Privacy-first analytics that grew through opinionated blog posts reaching Hacker News, alongside a bootstrapped paid cloud.",
    tags: ["Hacker News", "opinionated content", "bootstrapped"], question: "How did content compound?" },
];

const STAGES = [
  "Resolve target", "Repository history", "Discover sources", "Fetch evidence", "Extract claims",
  "Reconstruct timeline", "Analyze growth", "Validate evidence and gaps", "Write Research Bundle", "Render report",
];

const bySlug = (slug: string) => EXAMPLES.find((e) => e.slug === slug);

// Hero chart: an illustrative, made-up repository (not a real project) whose star history has three
// clear bursts, each next to the public post it lines up with.
const DEMO = {
  repo: "github.com/example-org/example-repo",
  history: [
    ["2021-01-01", 0], ["2021-06-01", 300], ["2021-12-01", 700], ["2022-04-01", 1000],
    ["2022-04-12", 1050], ["2022-04-19", 4600], ["2022-05-01", 4900], ["2022-10-01", 5400],
    ["2023-03-01", 5800], ["2023-04-20", 5900], ["2023-04-27", 7000], ["2023-05-10", 7150],
    ["2023-12-01", 7700], ["2024-05-01", 8100], ["2024-07-08", 8200], ["2024-07-15", 10600],
    ["2024-08-01", 10900], ["2025-03-01", 11600], ["2025-09-30", 12400],
  ].map(([t, v]) => ({ t: t as string, v: v as number })),
  // Each marker sits on top of its burst. Labels: right/left of the dot, above/below it (`phone`
  // overrides the placement on narrow screens, where labels show their title only).
  markers: [
    { cls: "a", at: "2022-04-19", title: "Product Hunt launch", note: "+3.5K stars in a week", h: "right", v: "below" },
    { cls: "b", at: "2023-04-27", title: "Reddit post", note: "+1.1K stars in a week", h: "right", v: "below" },
    { cls: "c", at: "2024-07-15", title: "HN post", note: "+2.4K stars in a week", h: "left", v: "above", phone: "right-below" },
  ] as const,
  years: [2022, 2023, 2024, 2025],
};

function CaseChart() {
  const pts = DEMO.history;
  const { line, area, yAt } = starCurve(pts, 700, 300, 22, 280);
  const t0 = Date.parse(pts[0].t), t1 = Date.parse(pts[pts.length - 1].t);
  const frac = (date: string) => (Date.parse(date) - t0) / (t1 - t0);
  const pctY = (f: number) => (yAt(f) / 300) * 100;
  const dot = (f: number) => ({
    left: `calc(${f * 100}% - 7.5px)`, top: `calc(${pctY(f)}% - 7.5px)`, right: "auto", bottom: "auto",
  });
  const anchor = (f: number) => ({ "--fx": `${f * 100}%`, "--fy": `${pctY(f)}%` }) as React.CSSProperties;
  return (
    <div className="chart">
      <svg viewBox="0 0 700 300" preserveAspectRatio="none" role="img"
        aria-label="Illustrative chart: GitHub stars by year for a made-up repository, with jumps after a Product Hunt launch, a Reddit post and a Hacker News post">
        <defs><linearGradient id="area" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#d92f72" stopOpacity=".22" /><stop offset="100%" stopColor="#d92f72" stopOpacity="0" /></linearGradient></defs>
        <path d={area} fill="url(#area)" />
        <path d={line} fill="none" stroke="#d92f72" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <div className="axis-title">GitHub stars vs. years</div>
      {DEMO.years.map((y) => (
        <span key={y} className="axis-year" style={{ left: `${frac(`${y}-01-01`) * 100}%` }}>{y}</span>
      ))}
      {DEMO.markers.map((m) => <span key={m.cls} className={`dot ${m.cls}`} style={dot(frac(m.at))}></span>)}
      {DEMO.markers.map((m) => (
        <div key={m.cls} className={`chart-label ${m.cls} h-${m.h} v-${m.v}${"phone" in m ? ` m-${m.phone}` : ""}`} style={anchor(frac(m.at))}>
          <b>{m.title}</b><small>{m.note}</small>
        </div>
      ))}
    </div>
  );
}

export default function Home() {
  preload(MASCOT, { as: "image", fetchPriority: "high" });
  const others = EXAMPLES.filter((e) => !STORIES.some((s) => s.slug === e.slug));
  return (
    <>
      <HeroIntro>
        <div className="hero-copy">
          <div className="pig-stage" aria-hidden="true">
            <div className="pig"><img src={MASCOT} alt="" width={1275} height={1234} decoding="async" /></div>
          </div>
          <div className="eyebrow">Open-source growth forensics</div>
          <h1>Want to grow your open-source project? <em>Study the ones that did.</em></h1>
          <p className="lead">See how projects went from zero to thousands of stars — the launches, Show HN posts, releases, communities and tactics that happened along the way. <span className="hero-punch">Pigtail reconstructs the public evidence so you can see what they tried, when they tried it, and what happened next.</span></p>
          <div className="cta"><Link className="btn pink" href="/examples/">Explore growth stories <span>→</span></Link><a className="btn" href="#try">Run Pigtail on your repo</a></div>
          <div className="proofline"><span>Open source</span><span>Evidence-linked</span><span>No Pigtail account</span></div>
        </div>
        <div className="case">
          <div className="case-top">
            <div><div className="repo">{DEMO.repo} · illustrative example</div><h3>Why did this repo keep moving?</h3></div>
            <div className="star-now"><strong>{fmt(DEMO.history[DEMO.history.length - 1].v)} ★</strong><span>current GitHub stars</span></div>
          </div>
          <CaseChart />
          <div className="chart-footer"><span><b>Growth in bursts.</b> Each jump lines up with a public post.</span><Link className="subtle-link" href="/examples/">See real reports →</Link></div>
        </div>
      </HeroIntro>

      <section className="section" id="stories">
        <div className="section-header">
          <div><div className="eyebrow">Real Pigtail output</div><h2>Example reports generated by Pigtail.</h2></div>
          <p>Each card is a report Pigtail produced from public sources. Open one to see the full output: timeline, growth episodes, tactics, gaps and sources.</p>
        </div>
        <div className="story-grid">
          {STORIES.map((s) => {
            const ex = bySlug(s.slug);
            if (!ex) return null;
            return (
              <Link key={s.slug} className={s.cls} href={`/examples/${s.slug}/`}>
                <div className="case-no">{s.no}</div>
                <h3>{ex.title}</h3>
                <p>{s.text}</p>
                {ex.stars_now != null && <div className="metric-row"><strong>{fmt(ex.stars_now)} ★</strong><span>current stars</span></div>}
                <div className="mini-chart"><Sparkline points={ex.sparkline} stroke={s.stroke} label={`${ex.title} GitHub stars over time`} /></div>
                <div className="tags">{s.tags.map((t) => <span key={t} className="tag">{t}</span>)}</div>
                <div className="story-foot"><span>{s.question}</span><b>Investigate →</b></div>
              </Link>
            );
          })}
        </div>
        <div className="stories-more">
          {others.length > 0 && <span>Also: {others.map((e) => e.title).join(", ")}</span>}
          <Link className="btn" href="/examples/">All examples <span>→</span></Link>
        </div>
      </section>

      <section className="section pain">
        <div className="pain-copy">
          <div className="eyebrow">The actual problem</div>
          <h2>You shipped it. Now how do you get anyone to care?</h2>
          <p>You can read another generic thread telling you to post on Hacker News, launch on Product Hunt and “build in public.” Or you can inspect what real projects actually did — at the stage they did it — and what happened afterward.</p>
          <div className="quote-box">Pigtail turns growth advice into growth evidence.</div>
        </div>
        <div className="questions">
          <div className="q"><span>↳</span>How did they get their first users?</div>
          <div className="q"><span>↳</span>What happened when the stars started climbing?</div>
          <div className="q"><span>↳</span>Which launches went nowhere?</div>
          <div className="q"><span>↳</span>When did they post on HN, Reddit or Product Hunt?</div>
          <div className="q"><span>↳</span>What changed between launch #1 and launch #2?</div>
          <div className="q"><span>↳</span>Did the spike fade — or keep compounding?</div>
        </div>
      </section>

      <section className="section" id="how">
        <div className="section-header">
          <div><div className="eyebrow">Three steps</div><h2>Repo in. Growth trail out.</h2></div>
          <p>Pigtail does the source hunting and reconstruction, then gives you both a human-readable forensic and structured research data.</p>
        </div>
        <div className="flow">
          <div className="flow-card"><div className="num">01 · TARGET</div><h3>Give it a repo or product.</h3><p>Start from a public GitHub repository or a product website.</p><div className="codebox">pigtail https://github.com/owner/repository</div></div>
          <div className="arrow">→</div>
          <div className="flow-card"><div className="num">02 · FOLLOW THE TRAIL</div><h3>Pigtail gathers the evidence.</h3><p>Repository history, releases, launch posts, Hacker News, Product Hunt, founder material, interviews and other public evidence.</p></div>
          <div className="arrow">→</div>
          <div className="flow-card"><div className="num">03 · FORENSIC</div><h3>Inspect what happened.</h3><p>Growth timeline, launch history, metrics, tactics, growth engines, gaps and sources — plus the Research Bundle behind it.</p></div>
        </div>
      </section>

      <section className="section" id="method">
        <div className="section-header">
          <div><div className="eyebrow">No growth mythology</div><h2>Interesting without pretending correlation is causation.</h2></div>
          <p>The point is not to manufacture a clean founder story. It is to reconstruct what public evidence can actually support.</p>
        </div>
        <div className="trust">
          <div className="trust-card"><span className="stamp">Evidence check</span><h3>Every material claim points back.</h3><p>Claims retain the source, date and supporting material so you can inspect the trail yourself.</p></div>
          <div className="trust-card"><span className="stamp">Causality check</span><h3>Timing is not cause.</h3><p>An event near a star spike is shown as an observed association unless stronger attribution evidence exists.</p></div>
          <div className="trust-card"><span className="stamp">Gap accepted</span><h3>“We don’t know” is allowed.</h3><p>If no high-confidence public event explains a growth episode, Pigtail says so instead of inventing a narrative.</p></div>
        </div>
      </section>

      <section className="try" id="try">
        <div>
          <div className="eyebrow">Your repo is the next case file</div>
          <h2>See what Pigtail finds in your growth trail.</h2>
          <p>Open source. Runs locally. Bring your own Anthropic API key. Your output is a portable HTML forensic plus a structured Research Bundle.</p>
          <div className="cta"><a className="btn pink" href={`${GITHUB}#install`} target="_blank" rel="noreferrer">Install Pigtail →</a><a className="btn" href={GITHUB} target="_blank" rel="noreferrer">View GitHub</a></div>
        </div>
        <div className="terminal">
          <div className="dots"><i></i><i></i><i></i></div>
          <pre><span className="pink">$</span> pigtail https://github.com/owner/repository{"\n\n"}{STAGES.map((s) => <span key={s}><span className="green">✓</span> {s}{"\n"}</span>)}{"\n"}<span className="pink">→</span> report.html{"\n"}<span className="pink">→</span> research-bundle.json</pre>
        </div>
      </section>

      <section className="request">
        <img className="request-pig" src={MASCOT} alt="" width={1275} height={1234} />
        <div className="eyebrow">Next investigation</div>
        <h2>Which repo should Pigtail investigate next?</h2>
        <p>Found a project with a fascinating growth curve? Request a forensic and help build a public library of how open source actually grows.</p>
        <a className="btn primary" href={`${GITHUB}/issues/new`} target="_blank" rel="noreferrer">Request a repo forensic →</a>
      </section>
    </>
  );
}
