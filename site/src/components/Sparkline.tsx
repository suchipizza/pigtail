type Point = { t: string; v: number };

/** Map star history to SVG coordinates: x by date, y from `bottom` (0 stars) up to `top` (max stars). */
export function starCurve(points: Point[], W: number, H: number, top: number, bottom: number) {
  const t0 = Date.parse(points[0].t), t1 = Date.parse(points[points.length - 1].t);
  const vMax = Math.max(...points.map((p) => p.v)) || 1;
  const xy = points.map((p) => [
    ((Date.parse(p.t) - t0) / Math.max(1, t1 - t0)) * W,
    bottom - (p.v / vMax) * (bottom - top),
  ]);
  const line = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  /** y of the curve at a horizontal fraction (0..1), interpolated between data points. */
  const yAt = (frac: number) => {
    const x = frac * W;
    const i = xy.findIndex(([px]) => px >= x);
    if (i <= 0) return xy[Math.max(0, i)][1];
    const [x0, y0] = xy[i - 1], [x1, y1] = xy[i];
    return y0 + ((y1 - y0) * (x - x0)) / Math.max(1e-9, x1 - x0);
  };
  return { line, area: `${line} L${W},${H} L0,${H} Z`, yAt };
}

export function Sparkline({ points, label, stroke }: { points: Point[]; label: string; stroke: string }) {
  if (points.length < 2) return null;
  const { line } = starCurve(points, 300, 70, 5, 65);
  return (
    <svg viewBox="0 0 300 70" preserveAspectRatio="none" role="img" aria-label={label}>
      <path d={line} fill="none" stroke={stroke} strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
