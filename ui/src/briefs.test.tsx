// D7 /briefs (M12): routes, form ↔ JSON path editing, field-named errors, diff and estimate views.
// Synthetic brief data only.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { BriefData, BriefDiff, Estimate } from "./api";
import { errorsFor, getPath, linesToList, linesToMap, mapToLines, setPath } from "./briefDraft";
import { BriefDiffView } from "./components/BriefDiffView";
import { BriefForm } from "./components/BriefForm";
import { EstimatePanel } from "./components/EstimatePanel";
import { matchRoute } from "./router";

afterEach(cleanup);

const draft: BriefData = {
  schema_version: "brief/v1",
  brief_id: "synthetic-brief",
  project: { name: "Synthetic", description: "A synthetic project for UI tests.", target_users: { primary: "devs" } },
  field: { core_field: "synthetic tools", include: ["a", "b"] },
  success: { primary: "adoption", minimums: { attention: "at_least_median" } },
  panel: { winners: 20, losers: 20 },
  own_audience: { channels: { github: "r1" } },
};

describe("D7 briefs", () => {
  it("routes /briefs, /briefs/new, /briefs/:id and /briefs/:id/edit", () => {
    expect(matchRoute("/briefs")).toEqual({ name: "briefs" });
    expect(matchRoute("/briefs/new")).toEqual({ name: "briefNew" });
    expect(matchRoute("/briefs/synthetic-brief")).toEqual({ name: "brief", id: "synthetic-brief" });
    expect(matchRoute("/briefs/synthetic-brief/edit")).toEqual({ name: "briefEdit", id: "synthetic-brief" });
    expect(matchRoute("/briefs/../etc")).toEqual({ name: "notFound" });
  });

  it("R18.2: edits the brief JSON by path without mutating it", () => {
    const next = setPath(draft, "panel.winners", 25);
    expect(getPath(next, "panel.winners")).toBe(25);
    expect(getPath(draft, "panel.winners")).toBe(20);
    expect(getPath(setPath(draft, "expansion.keywords", ["k"]), "expansion.keywords")).toEqual(["k"]);
    const removed = setPath(draft, "project.name", undefined);
    expect(getPath(removed, "project")).not.toHaveProperty("name");
    expect(linesToList(" a \n\n b")).toEqual(["a", "b"]);
    expect(linesToMap("github: r2\nbad line\nreddit: none")).toEqual({ github: "r2", reddit: "none" });
    expect(mapToLines({ github: "r1" })).toBe("github: r1");
  });

  it("D7: shows each validation error next to the field it names", () => {
    const errors = [
      { path: "panel.winners", message: "Input should be less than or equal to 25 (got 30)" },
      { path: "success.primary", message: "is required" },
    ];
    expect(errorsFor(errors, "panel").length).toBe(1);
    render(<BriefForm draft={draft} errors={errors} onChange={() => undefined} />);
    const alerts = screen.getAllByRole("alert").map((a) => a.textContent);
    expect(alerts).toContain("panel.winners: Input should be less than or equal to 25 (got 30)");
    expect(alerts).toContain("success.primary: is required");
  });

  it("R18.1: the guided form covers every schema section and edits by path", () => {
    const onChange = vi.fn();
    render(<BriefForm draft={draft} errors={[]} onChange={onChange} lockId />);
    for (const title of [
      "Project and target users",
      "Field boundaries",
      "Time window",
      "Success definition",
      "Your own audience",
      "Channels and geography",
      "Winners and losers",
      "Budget",
      "Expansion (editable before saving)",
    ]) {
      expect(screen.getByText(title)).toBeTruthy();
    }
    expect((screen.getByLabelText(/Brief id/) as HTMLInputElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Winners (15–25)"), { target: { value: "18" } });
    expect(getPath(onChange.mock.calls.at(-1)?.[0], "panel.winners")).toBe(18);
    fireEvent.change(screen.getByLabelText(/^Include/), { target: { value: "x\ny" } });
    expect(getPath(onChange.mock.calls.at(-1)?.[0], "field.include")).toEqual(["x", "y"]);
    fireEvent.change(screen.getByLabelText("Minimum on attention"), { target: { value: "" } });
    expect(getPath(onChange.mock.calls.at(-1)?.[0], "success.minimums")).toEqual({});
  });

  it("R18.4: the diff view lists field changes, or says the versions are equal", () => {
    const diff: BriefDiff = {
      brief_id: "synthetic-brief",
      from_version: 1,
      to_version: 2,
      changes: [{ path: "window.months", kind: "changed", old: 18, new: 12 }],
      unified: "-  months: 18\n+  months: 12\n",
    };
    render(<BriefDiffView diff={diff} />);
    expect(screen.getByText("window.months")).toBeTruthy();
    expect(screen.getByText("18")).toBeTruthy();
    cleanup();
    render(<BriefDiffView diff={{ ...diff, changes: [] }} />);
    expect(screen.getByText(/have the same content/)).toBeTruthy();
  });

  it("R18.5: the estimate is labelled an estimate and flags paid steps", () => {
    const e: Estimate = {
      label: "estimate",
      model: "estimate-v0",
      github: { requests: { core: 10, graphql: 2, search: 3 }, hours_at_default_caps: 0.1 },
      other_requests: { hn_algolia: 4 },
      counts: { candidates: 10, shortlisted: 5, cases: 40 },
      llm: {
        backend: "subscription",
        calls: 5,
        tokens: 1000,
        stages: [],
        subscription: {
          share_of_weekly_allowance: 0.2,
          share_cap: 0.5,
          used_last_7_days_tokens: 0,
          weeks: 1,
          allowance: { weekly_tokens: 5000, basis: "assumed_default", label: "estimate" },
        },
        api_usd: 0,
      },
      money: { usd: null, paid_steps: [{ step: "x collection", source: "x", est_usd: null, note: "" }], requires_approval: true },
      reuse: null,
    };
    render(<EstimatePanel e={e} />);
    expect(screen.getByText("estimate")).toBeTruthy();
    expect(screen.getByText("unknown")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toMatch(/explicit approval/);
  });
});

describe("D7 pages with a mocked API", () => {
  const stored = {
    brief: { ...draft, version: 2 },
    yaml: "schema_version: brief/v1\nbrief_id: synthetic-brief\n",
    version: 2,
    content_hash: "a".repeat(64),
    warnings: [],
  };
  const json = (body: unknown, status = 200) =>
    Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

  afterEach(() => vi.unstubAllGlobals());

  it("the editor saves a new version and shows server errors next to fields", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      calls.push({ url, ...(init ? { init } : {}) });
      if (url === "/api/briefs/synthetic-brief") return json({ ...stored, versions: [] });
      if (url === "/api/briefs/synthetic-brief/versions")
        return json({ detail: "invalid brief", errors: [{ path: "panel.winners", message: "too many" }] }, 422);
      return json({}, 404);
    });
    const { BriefEditorPage } = await import("./pages/BriefEditorPage");
    render(<BriefEditorPage id="synthetic-brief" />);
    const save = await screen.findByRole("button", { name: "Save as new version" });
    fireEvent.click(save);
    expect(await screen.findByText("panel.winners: too many")).toBeTruthy();
    const post = calls.find((c) => c.url.endsWith("/versions"));
    expect(post?.init?.method).toBe("POST");
    expect(JSON.parse(String(post?.init?.body))).toMatchObject({ base_version: 2, brief: { brief_id: "synthetic-brief" } });
  });

  it("the brief page lists versions, a diff and the estimate", async () => {
    vi.stubGlobal("fetch", (url: string) => {
      if (url === "/api/briefs/synthetic-brief")
        return json({
          ...stored,
          versions: [
            { version: 1, edited_at: "2026-09-26T12:00:00Z", supersedes: null, content_hash: "b".repeat(64) },
            { version: 2, edited_at: "2026-09-26T13:00:00Z", supersedes: 1, content_hash: "a".repeat(64) },
          ],
        });
      if (url.startsWith("/api/briefs/synthetic-brief/diff"))
        return json({ brief_id: "synthetic-brief", from_version: 1, to_version: 2, unified: "", changes: [{ path: "window.months", kind: "changed", old: 18, new: 12 }] });
      if (url.startsWith("/api/briefs/synthetic-brief/estimate")) return json({}, 500);
      return json({}, 404);
    });
    const { BriefPage } = await import("./pages/BriefPage");
    render(<BriefPage id="synthetic-brief" />);
    expect(await screen.findByText("window.months")).toBeTruthy();
    expect(screen.getAllByText(/^v1$/).length).toBeGreaterThan(0);
    expect(await screen.findByText(/Estimate unavailable/)).toBeTruthy();
  });
});
