// Build input step: copy publication-gated examples into the static site (spec §28, publication gate §14).
// Reads ../examples/reviewed/<slug>/{metadata.json,public-report-bundle.json,report.html,publication-manifest.json}.
// The build fails if any example is not a current PASS from the publication gate.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const SUPPORTED_POLICY_VERSIONS = ["0.1.0"];
const REQUIRED = ["metadata.json", "report.html", "public-report-bundle.json", "publication-manifest.json"];
const FORBIDDEN = [
  "research-bundle.json",
  "research-bundle.invalid.json",
  "source-index.json",
  "run.json",
  "publication-audit.json",
  "publication-review.yaml",
];
const NOTICE_TEXT =
  "Pigtail's software license applies to Pigtail code. Third-party names, source material, excerpts and " +
  "linked data remain subject to their respective rights and are not relicensed by Pigtail.";

const root = path.resolve(import.meta.dirname, "..", "..");
const src = path.join(root, "examples", "reviewed");
const pub = path.join(import.meta.dirname, "..", "public", "examples");
const dataOut = path.join(import.meta.dirname, "..", "src", "data", "examples.json");

const sha = (p) => "sha256:" + crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex");
const squash = (s) => s.split(/\s+/).join(" ");

function checkExample(slug, dir) {
  const problems = [];
  for (const f of FORBIDDEN) if (fs.existsSync(path.join(dir, f))) problems.push(`${f} must not be published`);
  for (const f of REQUIRED) if (!fs.existsSync(path.join(dir, f))) problems.push(`${f} is missing`);
  if (problems.length) return problems;
  const m = JSON.parse(fs.readFileSync(path.join(dir, "publication-manifest.json"), "utf8"));
  if (m.status !== "PASS") problems.push(`publication status is ${m.status}, not PASS`);
  if (m.unresolved_findings !== 0) problems.push(`${m.unresolved_findings} unresolved finding(s)`);
  if (!SUPPORTED_POLICY_VERSIONS.includes(m.publication_policy_version))
    problems.push(`publication policy ${m.publication_policy_version} is not supported`);
  if (sha(path.join(dir, "report.html")) !== m.report_hash) problems.push("report.html changed after the gate ran");
  if (sha(path.join(dir, "public-report-bundle.json")) !== m.public_bundle_hash)
    problems.push("public-report-bundle.json changed after the gate ran");
  const bundle = JSON.parse(fs.readFileSync(path.join(dir, "public-report-bundle.json"), "utf8"));
  if (bundle.projection !== "pigtail-public-report") problems.push("public-report-bundle.json is not a public projection");
  for (const k of ["bundle_id", "run", "people"]) if (k in bundle) problems.push(`internal field '${k}' is present`);
  return problems;
}

fs.rmSync(pub, { recursive: true, force: true });
fs.mkdirSync(pub, { recursive: true });
fs.mkdirSync(path.dirname(dataOut), { recursive: true });

const slugs = fs.existsSync(src)
  ? fs.readdirSync(src).filter((s) => fs.statSync(path.join(src, s)).isDirectory()).sort()
  : [];
const failures = [];
if (slugs.length) {
  const notice = path.join(src, "NOTICE.md");
  if (!fs.existsSync(notice) || !squash(fs.readFileSync(notice, "utf8")).includes(NOTICE_TEXT))
    failures.push("examples/reviewed/NOTICE.md is missing the third-party content notice");
}
const examples = [];
for (const slug of slugs) {
  const dir = path.join(src, slug);
  const problems = checkExample(slug, dir);
  if (problems.length) {
    failures.push(...problems.map((p) => `${slug}: ${p}`));
    continue;
  }
  const meta = JSON.parse(fs.readFileSync(path.join(dir, "metadata.json"), "utf8"));
  const bundle = JSON.parse(fs.readFileSync(path.join(dir, "public-report-bundle.json"), "utf8"));
  if (bundle.schema_version !== meta.bundle_schema_version) {
    failures.push(`${slug}: metadata says ${meta.bundle_schema_version}, bundle is ${bundle.schema_version}`);
    continue;
  }
  const out = path.join(pub, slug);
  fs.mkdirSync(out, { recursive: true });
  for (const f of ["report.html", "public-report-bundle.json"]) fs.copyFileSync(path.join(dir, f), path.join(out, f));
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
    generated_at: bundle.report.source_cutoff_at,
  });
}
if (failures.length) {
  console.error("Refusing to build: these examples have not passed the publication gate:");
  for (const f of failures) console.error(`  - ${f}`);
  process.exit(1);
}
examples.sort((a, b) => Number(b.featured) - Number(a.featured) || a.title.localeCompare(b.title));
fs.writeFileSync(dataOut, JSON.stringify(examples, null, 2));
console.log(`synced ${examples.length} publication-gated example(s)`);
