import Link from "next/link";
import { notFound } from "next/navigation";
import { KindBadge } from "@/components/ExampleCard";
import { BASE, EXAMPLES } from "@/lib";

export function generateStaticParams() {
  return EXAMPLES.map((e) => ({ slug: e.slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const ex = EXAMPLES.find((e) => e.slug === slug);
  return { title: ex ? `${ex.title} growth forensic — Pigtail` : "Pigtail", description: ex?.description };
}

export default async function ExamplePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const ex = EXAMPLES.find((e) => e.slug === slug);
  if (!ex) notFound();
  const report = `${BASE}/examples/${ex.slug}/report.html`;
  return (
    <section className="page">
      <Link href="/examples/" className="crumb">← All examples</Link>
      <div className="ex-head">
        <KindBadge ex={ex} />
        <h1>{ex.title}</h1>
        <p className="lead">{ex.description}</p>
        <div className="cta">
          <a className="btn primary" href={report}>Open the full report</a>
          <a className="btn" href={`${BASE}/examples/${ex.slug}/public-report-bundle.json`}>Download report data (JSON)</a>
          <a className="btn" href={ex.target_url} target="_blank" rel="noreferrer">{ex.target_url.replace("https://", "")} ↗</a>
        </div>
        <p className="ex-meta">
          {ex.counts.sources} sources · {ex.counts.claims} claims · {ex.counts.events} events · {ex.counts.gaps} recorded gaps ·
          sources checked up to {ex.generated_at.slice(0, 10)}
        </p>
        <p className="ex-meta">Independent analysis based on public sources. Pigtail is not affiliated with or endorsed by {ex.title}.</p>
      </div>
      <iframe className="report-frame" src={report} title={`${ex.title} growth forensic`} />
    </section>
  );
}
