import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type RefObject } from "react";
import { api, type Bucket, type GithubPoint, type Timeline } from "../api";
import { fmtDay, fmtNum, fmtTime, ROLE_LABEL, STATE_LABEL } from "../format";
import { Link, navigate } from "../router";
import { useAsync } from "../useAsync";

// Braided timeline, D1 preview: time-aligned lanes on one shared x axis (one y axis per lane,
// never two per lane). Every mark opens the evidence it was computed from (R13.2).

const MARGIN = { left: 64, right: 16 };
const LANES = { stars: 170, hn: 130, events: 46 } as const;
const GAP = 26;
const HOUR = 3_600_000;
const DAY = 24 * HOUR;

type Range = { from: string; to: string } | null;

interface Hover {
  x: number;
  y: number;
  lines: string[];
}

interface Selection {
  title: string;
  evidence: string[];
}

export function linePath(
  pts: { t: number; v: number | null }[],
  x: (t: number) => number,
  y: (v: number) => number,
  step: number,
): string {
  // Break the line at unknown values and at buckets that were never scanned (gaps).
  let d = "";
  let prev: number | null = null;
  for (const p of pts) {
    if (p.v === null) {
      prev = null;
      continue;
    }
    const cmd = prev !== null && p.t - prev <= step ? "L" : "M";
    d += `${cmd}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`;
    prev = p.t;
  }
  return d;
}

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

function timeTicks(from: number, to: number, count: number): number[] {
  const span = to - from;
  const steps = [HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR, DAY, 2 * DAY, 7 * DAY, 14 * DAY, 30 * DAY, 90 * DAY];
  const step = steps.find((s) => span / s <= count) ?? 180 * DAY;
  const out: number[] = [];
  for (let t = Math.ceil(from / step) * step; t <= to; t += step) out.push(t);
  return out;
}

function tickLabel(t: number, span: number): string {
  const iso = new Date(t).toISOString();
  return span <= 3 * DAY ? `${iso.slice(5, 10)} ${iso.slice(11, 13)}h` : iso.slice(5, 10);
}

function useWidth(ref: RefObject<HTMLDivElement | null>): number {
  const [w, setW] = useState(900);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const cw = entries[0]?.contentRect.width;
      if (cw) setW(Math.max(320, Math.floor(cw)));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

function openEvidence(ids: string[], title: string, select: (s: Selection) => void): void {
  if (ids.length === 1 && ids[0]) navigate(`/evidence/${ids[0]}`);
  else if (ids.length > 1) select({ title, evidence: ids });
}

function Chart({ data, onZoom }: { data: Timeline; onZoom: (r: Range) => void }) {
  const wrap = useRef<HTMLDivElement>(null);
  const width = useWidth(wrap);
  const [hover, setHover] = useState<Hover | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [drag, setDrag] = useState<{ a: number; b: number } | null>(null);

  const from = Date.parse(data.range.from);
  const to = Date.parse(data.range.to);
  const bw = data.range.bucket === "hour" ? HOUR : DAY;
  const plotW = width - MARGIN.left - MARGIN.right;
  const x = (t: number) => MARGIN.left + ((t - from) / (to - from)) * plotW;
  const tOf = (px: number) => from + ((px - MARGIN.left) / plotW) * (to - from);

  const top = { stars: 18, hn: 0, events: 0 };
  top.hn = top.stars + LANES.stars + GAP;
  top.events = top.hn + LANES.hn + GAP;
  const height = top.events + LANES.events + 28;

  // Star history is daily whatever the bucket (endpoint day labels, not UTC-aligned).
  const gh = data.github.map((p) => ({ ...p, t: Date.parse(p.t) + DAY / 2 }));
  const starMax = niceMax(Math.max(0, ...gh.map((p) => p.stars_net)));
  const starMin = Math.min(0, ...gh.map((p) => p.stars_net));
  const yStars = (v: number) => top.stars + LANES.stars - ((v - starMin) / (starMax - starMin)) * LANES.stars;
  const dayW = Math.max(2, (DAY / (to - from)) * plotW);

  const ranks = data.hn.ranks.map((r) => ({ ...r, tt: Date.parse(r.t) + bw / 2 }));
  const rankMax = Math.max(30, ...ranks.map((r) => r.best_rank));
  const yRank = (r: number) => top.hn + 10 + ((r - 1) / (rankMax - 1)) * (LANES.hn - 20);
  const stories = useMemo(() => [...new Set(data.hn.ranks.map((r) => r.item_id))], [data]);

  const ticks = timeTicks(from, to, Math.max(3, Math.floor(plotW / 110)));
  const inRange = (t: number) => t >= from && t <= to;

  const tipFor = (p: Omit<GithubPoint, "t"> & { t: number }): string[] => [
    `day ${fmtDay(new Date(p.t - DAY / 2).toISOString())} (endpoint label)`,
    `stars ${fmtNum(p.stars_net)} (net of un-stars)`,
    p.is_partial ? "day still filling when fetched (partial)" : "",
    p.evidence_ids.length === 1
      ? "click: open evidence"
      : p.evidence_ids.length > 1
        ? `click: list ${p.evidence_ids.length} evidence records`
        : "no evidence link",
  ].filter(Boolean);

  // Drag anywhere on the chart to zoom; a drag never counts as a click on a mark.
  const svgRef = useRef<SVGSVGElement>(null);
  const dragged = useRef(false);
  const localX = (e: ReactPointerEvent<SVGSVGElement>) =>
    e.clientX - (svgRef.current?.getBoundingClientRect().left ?? 0);
  const onPointerDown = (e: ReactPointerEvent<SVGSVGElement>) => {
    dragged.current = false;
    const px = localX(e);
    setDrag({ a: px, b: px });
  };
  const onPointerMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (!drag) return;
    const b = localX(e);
    if (Math.abs(b - drag.a) > 8) dragged.current = true;
    setDrag({ ...drag, b });
  };
  const onPointerUp = () => {
    if (drag && dragged.current) {
      const a = tOf(Math.max(MARGIN.left, Math.min(drag.a, drag.b)));
      const b = tOf(Math.min(MARGIN.left + plotW, Math.max(drag.a, drag.b)));
      if (b - a >= HOUR) onZoom({ from: new Date(a).toISOString(), to: new Date(b).toISOString() });
    }
    setDrag(null);
  };
  const go = (fn: () => void) => () => {
    if (!dragged.current) fn();
  };

  const laneLabel = (y: number, text: string) => (
    <text x={MARGIN.left} y={y - 6} className="lane-label">
      {text}
    </text>
  );

  return (
    <div className="chart-wrap" ref={wrap}>
      <svg
        ref={svgRef}
        width={width}
        height={height}
        role="img"
        aria-label="Case timeline"
        className="chart"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => setDrag(null)}
      >
        <rect x={MARGIN.left} y={0} width={plotW} height={height - 24} className="brush-area" />
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={top.stars} y2={height - 24} className="grid" />
            <text x={x(t)} y={height - 8} className="axis" textAnchor="middle">
              {tickLabel(t, to - from)}
            </text>
          </g>
        ))}

        {/* GitHub stars per day (star-history endpoint) */}
        {laneLabel(top.stars, "GitHub stars per day (star history, net of un-stars)")}
        <text x={MARGIN.left - 6} y={top.stars + 4} className="axis" textAnchor="end">
          {fmtNum(starMax)}
        </text>
        <text x={MARGIN.left - 6} y={top.stars + LANES.stars} className="axis" textAnchor="end">
          {fmtNum(starMin)}
        </text>
        <line x1={MARGIN.left} x2={width - MARGIN.right} y1={yStars(0)} y2={yStars(0)} className="baseline" />
        <path d={linePath(gh.map((p) => ({ t: p.t, v: p.stars_net })), x, yStars, DAY)} className="line filtered" />

        {/* hover/click columns over the stars lane */}
        {gh.map((p) => (
          <rect
            key={`h${p.t}`}
            x={x(p.t) - dayW / 2}
            y={top.stars}
            width={dayW}
            height={LANES.stars}
            className="hit"
            onMouseEnter={() => setHover({ x: x(p.t), y: top.stars, lines: tipFor(p) })}
            onMouseLeave={() => setHover(null)}
            onClick={go(() => openEvidence(p.evidence_ids, `GitHub ${tipFor(p)[0]}`, setSelection))}
          >
            <title>{tipFor(p).join("\n")}</title>
          </rect>
        ))}
        {gh.map((p) =>
          dayW >= 4 ? (
            <circle
              key={`d${p.t}`}
              cx={x(p.t)}
              cy={yStars(p.stars_net)}
              r={3}
              className={`dot filtered${p.is_partial ? " partial" : ""}`}
              pointerEvents="none"
            />
          ) : null,
        )}

        {/* Hacker News: front-page rank (1 = top) and mentions */}
        {laneLabel(top.hn, "Hacker News rank (best per " + data.range.bucket + "; shaded = front page 1–30)")}
        <rect x={MARGIN.left} y={yRank(1) - 4} width={plotW} height={yRank(30) - yRank(1) + 8} className="band" />
        <text x={MARGIN.left - 6} y={yRank(1) + 4} className="axis" textAnchor="end">
          #1
        </text>
        <text x={MARGIN.left - 6} y={yRank(rankMax) + 4} className="axis" textAnchor="end">
          #{rankMax}
        </text>
        {stories.map((id) => (
          <path
            key={`s${id}`}
            d={linePath(
              ranks.filter((r) => r.item_id === id).map((r) => ({ t: r.tt, v: r.best_rank })),
              x,
              yRank,
              bw,
            )}
            className="line hn"
          />
        ))}
        {ranks.map((r) => (
          <circle
            key={`r${r.item_id}-${r.t}`}
            cx={x(r.tt)}
            cy={yRank(r.best_rank)}
            r={4}
            className="dot hn clickable"
            onMouseEnter={() =>
              setHover({
                x: x(r.tt),
                y: yRank(r.best_rank),
                lines: [
                  `HN story ${r.item_id}`,
                  `best rank #${r.best_rank} at ${fmtTime(r.observed_at)}`,
                  `score ${fmtNum(r.score)} · ${r.observations} polls`,
                  "click: open the rank poll snapshot record",
                ],
              })
            }
            onMouseLeave={() => setHover(null)}
            onClick={go(() => r.evidence_id && navigate(`/evidence/${r.evidence_id}`))}
          >
            <title>{`HN ${r.item_id} rank #${r.best_rank}`}</title>
          </circle>
        ))}
        {data.hn.mentions.map((m) => {
          const t = Date.parse(m.created_at ?? "");
          if (!inRange(t)) return null;
          const ev = m.evidence_id ?? m.item_evidence_id;
          const px = x(t);
          const py = top.hn + LANES.hn - 2;
          return (
            <path
              key={`m${m.item_id}`}
              d={`M${px},${py - 10}L${px + 6},${py}L${px - 6},${py}Z`}
              className="mention clickable"
              onMouseEnter={() =>
                setHover({
                  x: px,
                  y: py - 10,
                  lines: [
                    `HN ${m.item_type} ${m.item_id}${m.show_hn ? " · Show HN" : ""}${m.ask_hn ? " · Ask HN" : ""}`,
                    m.title ?? "(no title)",
                    `${fmtTime(m.created_at)} · match ${m.match_kind}`,
                    ev ? "click: open evidence" : "no evidence link",
                  ],
                })
              }
              onMouseLeave={() => setHover(null)}
              onClick={go(() => ev && navigate(`/evidence/${ev}`))}
            >
              <title>{`HN mention ${m.item_id}`}</title>
            </path>
          );
        })}

        {/* evidence events */}
        {laneLabel(top.events, `Captured evidence (${data.events.length})`)}
        {data.events.map((e) => {
          const t = Date.parse(e.t);
          const gone = e.evidence.deletion_state !== "present";
          return (
            <line
              key={e.evidence.id}
              x1={x(t)}
              x2={x(t)}
              y1={top.events + 4}
              y2={top.events + LANES.events - 4}
              className={`event clickable${gone ? " gone" : ""}`}
              onMouseEnter={() =>
                setHover({
                  x: x(t),
                  y: top.events,
                  lines: [
                    `${e.evidence.source} · ${e.roles.map((r) => ROLE_LABEL[r] ?? r).join(", ")}`,
                    fmtTime(e.t),
                    STATE_LABEL[e.evidence.deletion_state] ?? e.evidence.deletion_state,
                    "click: open evidence",
                  ],
                })
              }
              onMouseLeave={() => setHover(null)}
              onClick={go(() => navigate(`/evidence/${e.evidence.id}`))}
            >
              <title>{e.evidence.id}</title>
            </line>
          );
        })}

        {drag && (
          <rect
            x={Math.min(drag.a, drag.b)}
            y={0}
            width={Math.abs(drag.b - drag.a)}
            height={height - 24}
            className="brush"
            pointerEvents="none"
          />
        )}
        {hover && <line x1={hover.x} x2={hover.x} y1={top.stars} y2={height - 24} className="crosshair" pointerEvents="none" />}
      </svg>
      {hover && (
        <div
          className="tooltip"
          role="status"
          style={{ left: Math.min(hover.x + 12, width - 260), top: Math.max(0, hover.y) }}
        >
          {hover.lines.map((l, i) => (
            <div key={i} className={i === 0 ? "tip-title" : undefined}>
              {l}
            </div>
          ))}
        </div>
      )}
      <ul className="legend" aria-label="Legend">
        <li>
          <span className="swatch filtered" /> stars per day (net)
        </li>
        <li>
          <span className="swatch hn" /> HN rank
        </li>
        <li>
          <span className="swatch mention" /> HN mention
        </li>
        <li className="muted">Days never fetched have no point (unknown, not zero).</li>
        <li className="muted">Drag across the chart to zoom.</li>
      </ul>
      {selection && (
        <div className="card selection">
          <strong>{selection.title}</strong> — {selection.evidence.length} evidence records{" "}
          <button type="button" className="link-button" onClick={() => setSelection(null)}>
            close
          </button>
          <ul>
            {selection.evidence.map((id) => (
              <li key={id}>
                <Link href={`/evidence/${id}`}>{id}</Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function DataTable({ data }: { data: Timeline }) {
  return (
    <table className="data compact">
      <caption>GitHub stars per day as a table (star-history endpoint day labels)</caption>
      <thead>
        <tr>
          <th>Day</th>
          <th className="num">Stars (net)</th>
          <th>Partial</th>
          <th>Evidence</th>
        </tr>
      </thead>
      <tbody>
        {data.github.map((p) => (
          <tr key={p.t}>
            <td>{fmtDay(p.t)}</td>
            <td className="num">{fmtNum(p.stars_net)}</td>
            <td>{p.is_partial ? "yes" : "no"}</td>
            <td>
              {p.evidence_ids.slice(0, 3).map((id) => (
                <Link key={id} href={`/evidence/${id}`} className="ev-chip">
                  {id.slice(0, 9)}…
                </Link>
              ))}
              {p.evidence_ids.length > 3 ? ` +${p.evidence_ids.length - 3}` : ""}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function TimelineView({ caseId, openedAt }: { caseId: string; openedAt: string }) {
  const [range, setRange] = useState<Range>(null);
  const [bucket, setBucket] = useState<Bucket | "auto">("auto");
  const [table, setTable] = useState(false);
  const q = { from: range?.from, to: range?.to, bucket: bucket === "auto" ? undefined : bucket };
  const res = useAsync(() => api.timeline(caseId, q), caseId + JSON.stringify(q));
  const opened = Date.parse(openedAt);
  const around = (h: number): Range => ({
    from: new Date(opened - h * HOUR).toISOString(),
    to: new Date(opened + h * HOUR).toISOString(),
  });
  const zoomOut = () => {
    if (!res.data) return;
    const a = Date.parse(res.data.range.from);
    const b = Date.parse(res.data.range.to);
    const half = b - a;
    setRange({ from: new Date(a - half / 2).toISOString(), to: new Date(b + half / 2).toISOString() });
  };

  return (
    <section aria-label="Timeline">
      <div className="filters" role="group" aria-label="Range">
        <span>Range:</span>
        <button type="button" onClick={() => setRange(null)} aria-pressed={range === null}>
          All captured
        </button>
        <button type="button" onClick={() => setRange(around(7 * 24))}>
          ±7 days
        </button>
        <button type="button" onClick={() => setRange(around(48))}>
          ±48 h
        </button>
        <button type="button" onClick={() => setRange(around(12))}>
          ±12 h
        </button>
        <button type="button" onClick={zoomOut} disabled={!res.data}>
          Zoom out
        </button>
        <label>
          Buckets
          <select value={bucket} onChange={(e) => setBucket(e.target.value as Bucket | "auto")}>
            <option value="auto">auto</option>
            <option value="hour">hour</option>
            <option value="day">day</option>
          </select>
        </label>
        <label className="inline">
          <input type="checkbox" checked={table} onChange={(e) => setTable(e.target.checked)} /> table view
        </label>
      </div>
      {res.status === "error" && <p className="error">{res.error.message}</p>}
      {res.data && (
        <>
          <p className="muted small">
            {fmtTime(res.data.range.from)} – {fmtTime(res.data.range.to)} · {res.data.range.bucket} buckets ·{" "}
            {res.data.hn.stories.length} HN stories, {res.data.hn.mentions.length} mentions ·{" "}
            <span className="tab-note">uncoded preview</span>
            {res.status === "loading" ? " · updating…" : ""}
          </p>
          <Chart data={res.data} onZoom={setRange} />
          {res.data.hn.stories.length > 0 && (
            <ul className="stories">
              {res.data.hn.stories.map((s) => (
                <li key={s.item_id}>
                  HN {s.item_id}: {s.title ?? "(no title)"} · best rank {s.best_rank ? `#${s.best_rank}` : "unknown"}
                  {s.evidence_id && (
                    <>
                      {" "}
                      · <Link href={`/evidence/${s.evidence_id}`}>evidence</Link>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
          {table && <DataTable data={res.data} />}
        </>
      )}
    </section>
  );
}
