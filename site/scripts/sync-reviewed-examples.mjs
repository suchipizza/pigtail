// Build input step: copy reviewed example artifacts into the static site (spec §28).
// Reads ../examples/reviewed/<slug>/{metadata.json,research-bundle.json,report.html}.
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..", "..");
const src = path.join(root, "examples", "reviewed");
const pub = path.join(import.meta.dirname, "..", "public", "examples");
const dataOut = path.join(import.meta.dirname, "..", "src", "data", "examples.json");

fs.rmSync(pub, { recursive: true, force: true });
fs.mkdirSync(pub, { recursive: true });
fs.mkdirSync(path.dirname(dataOut), { recursive: true });

const examples = [];
for (const slug of fs.existsSync(src) ? fs.readdirSync(src).sort() : []) {
  const dir = path.join(src, slug);
  const metaPath = path.join(dir, "metadata.json");
  if (!fs.existsSync(metaPath)) continue;
  const meta = JSON.parse(fs.readFileSync(metaPath, "utf8"));
  const bundle = JSON.parse(fs.readFileSync(path.join(dir, "research-bundle.json"), "utf8"));
  if (bundle.schema_version !== meta.bundle_schema_version) {
    throw new Error(`${slug}: metadata says ${meta.bundle_schema_version}, bundle is ${bundle.schema_version}`);
  }
  const out = path.join(pub, slug);
  fs.mkdirSync(out, { recursive: true });
  for (const f of ["report.html", "research-bundle.json", "source-index.json"]) {
    if (fs.existsSync(path.join(dir, f))) fs.copyFileSync(path.join(dir, f), path.join(out, f));
  }
  const repo = bundle.repositories.find((r) => r.id === bundle.target.primary_repository_id);
  const stars = bundle.metric_snapshots
    .filter((m) => m.metric_key === "github_stars" && repo && m.repository_id === repo.id)
    .map((m) => ({ t: (m.time.end || m.time.start).slice(0, 10), v: m.value_numeric }))
    .sort((a, b) => a.t.localeCompare(b.t));
  const step = Math.max(1, Math.ceil(stars.length / 80));
  examples.push({
    ...meta,
    target_url: bundle.target.canonical_url,
    summary: bundle.narrative.thirty_second?.text ?? null,
    counts: {
      sources: bundle.sources.length,
      claims: bundle.claims.length,
      events: bundle.events.length,
      tactics: bundle.tactics.length,
      gaps: bundle.gaps.length,
    },
    stars_now: repo?.current?.stars ?? null,
    sparkline: stars.filter((_, i) => i % step === 0 || i === stars.length - 1),
    generated_at: bundle.generated_at,
  });
}
examples.sort((a, b) => Number(b.featured) - Number(a.featured) || a.title.localeCompare(b.title));
fs.writeFileSync(dataOut, JSON.stringify(examples, null, 2));
console.log(`synced ${examples.length} reviewed example(s)`);
