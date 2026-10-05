export function Sparkline({ points, label }: { points: { t: string; v: number }[]; label: string }) {
  if (points.length < 2) return null;
  const W = 280, H = 56;
  const t0 = Date.parse(points[0].t), t1 = Date.parse(points[points.length - 1].t);
  const vMax = Math.max(...points.map((p) => p.v)) || 1;
  const d = points
    .map((p, i) => {
      const x = ((Date.parse(p.t) - t0) / Math.max(1, t1 - t0)) * (W - 4) + 2;
      const y = H - 3 - (p.v / vMax) * (H - 6);
      return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join("");
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
      <path d={d} fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
    </svg>
  );
}
