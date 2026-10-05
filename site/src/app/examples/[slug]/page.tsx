import Link from "next/link";
import { notFound } from "next/navigation";
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
    <section className="section">
      <Link href="/examples/" className="muted small">← All examples</Link>
      <h1 className="page-title">{ex.title}</h1>
      <p className="lead">{ex.description}</p>
      <div className="cta">
        <a className="btn primary" href={report}>Open the full report</a>
        <a className="btn" href={`${BASE}/examples/${ex.slug}/research-bundle.json`}>Download Research Bundle (JSON)</a>
        <a className="btn ghost" href={ex.target_url}>{ex.target_url.replace("https://", "")} ↗</a>
      </div>
      <p className="muted small">
        {ex.counts.sources} sources · {ex.counts.claims} claims · {ex.counts.events} events · {ex.counts.gaps} recorded gaps ·
        generated {ex.generated_at.slice(0, 10)}
      </p>
      <iframe className="report-frame" src={report} title={`${ex.title} growth forensic`} />
    </section>
  );
}
