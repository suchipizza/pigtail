import Link from "next/link";
import { Example, fmt } from "@/lib";
import { Sparkline } from "./Sparkline";

export function ExampleCard({ ex }: { ex: Example }) {
  return (
    <Link className="card example" href={`/examples/${ex.slug}/`}>
      <div className="kicker">{ex.kind === "repository" ? "Open-source project" : "Product"}</div>
      <h3>{ex.title}</h3>
      <p>{ex.description}</p>
      {ex.sparkline.length > 1 && (
        <div className="spark-wrap">
          <Sparkline points={ex.sparkline} label={`${ex.title} GitHub stars over time`} />
          {ex.stars_now != null && <span className="muted small">{fmt(ex.stars_now)} stars</span>}
        </div>
      )}
      <div className="meta-row">
        <span>{ex.counts.sources} sources</span>
        <span>{ex.counts.claims} claims</span>
        <span>{ex.counts.events} events</span>
      </div>
    </Link>
  );
}
