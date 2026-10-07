import Link from "next/link";
import { Example, fmt, isOss } from "@/lib";
import { Sparkline } from "./Sparkline";

export function KindBadge({ ex }: { ex: Example }) {
  return <span className={`kind-badge ${isOss(ex) ? "oss" : "product"}`}>{isOss(ex) ? "Open source" : "Product"}</span>;
}

export function ExampleCard({ ex }: { ex: Example }) {
  return (
    <Link className={`ex-card ${isOss(ex) ? "oss" : "product"}`} href={`/examples/${ex.slug}/`}>
      <div><KindBadge ex={ex} /></div>
      <h3>{ex.title}</h3>
      <p>{ex.description}</p>
      {ex.stars_now != null && <div className="metric-row"><strong>{fmt(ex.stars_now)} ★</strong><span>current stars</span></div>}
      {ex.sparkline.length > 1 && (
        <div className="mini-chart"><Sparkline points={ex.sparkline} stroke="#d92f72" label={`${ex.title} GitHub stars over time`} /></div>
      )}
      <div className="ex-counts">
        <span className="tag">{ex.counts.sources} sources</span>
        <span className="tag">{ex.counts.claims} claims</span>
        <span className="tag">{ex.counts.events} events</span>
      </div>
      <div className="ex-foot"><span>{ex.target_url.replace("https://", "")}</span><b>Investigate →</b></div>
    </Link>
  );
}
