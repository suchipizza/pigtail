// D7 shortlist review (M22; PRD R4.7, R4.11): route, table with verdict and reason, filters,
// decisions always with a reason (single and bulk), add by URL, reference cases to confirm,
// precision shown against the target (also below it), finalize gated on undecided candidates.
// Synthetic candidates only (fake orgs).
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ShortlistCandidate, ShortlistView } from "./api";
import { ShortlistReview } from "./components/ShortlistReview";
import { matchRoute } from "./router";

afterEach(cleanup);

function cand(name: string, over: Partial<ShortlistCandidate> = {}): ShortlistCandidate {
  return {
    candidate_ref: `gh:${name}`,
    repo_full_name: name,
    url: `https://github.com/${name}`,
    panel: "field",
    named_index: null,
    resolution: "resolved",
    resolution_rule: null,
    matches: [],
    sources: [{ source: "github_keyword", term: "config linter" }],
    description: `synthetic ${name}`,
    stars: 120,
    created_at: null,
    first_seen_at: null,
    verdict: "relevant",
    reason: "A command-line config validator.",
    distance: 0,
    model_panel: "field",
    rubric_version: "rubric-v1-abc",
    decision: null,
    on_shortlist: null,
    proposed: true,
    ...over,
  };
}

const view: ShortlistView = {
  brief_id: "synthetic-brief",
  brief_version: 2,
  status: "in_review",
  counts: { candidates: 3, by_verdict: { relevant: 1, uncertain: 1, not_relevant: 1 }, on_shortlist: 0, proposed: 1, undecided: 2 },
  precision: {
    value: 0.5,
    kept: 1,
    decided: 2,
    model_relevant: 3,
    undecided: 1,
    target: 0.8,
    meets_target: false,
    label: "user-checked",
    rubric_versions: ["rubric-v1-abc"],
    definition: "accepted among relevant",
  },
  candidates: [
    cand("org-s/lint-a"),
    cand("org-s/maybe-b", { verdict: "uncertain", distance: 1, proposed: false }),
    cand("org-s/other-c", { verdict: "not_relevant", distance: 2, proposed: false, reason: "A web framework." }),
  ],
  reference_cases_to_confirm: [
    cand("x", {
      candidate_ref: "named:reference:0",
      repo_full_name: null,
      url: null,
      panel: "reference",
      named_index: 0,
      named_as: "Synthetic Reference",
      resolution: "unresolved",
      resolution_rule: "no_launch_post_found",
      verdict: null,
      matches: [{ full_name: "org-x/ref-1", url: "https://github.com/org-x/ref-1", description: "one line", stars: 42 }],
    }),
  ],
  brief_warnings: ["field.widening_steps is empty"],
  defaulted_fields: [{ field: "panel.winners", value: 20 }],
};

function setup(v: ShortlistView = view) {
  const onDecide = vi.fn();
  const onAdd = vi.fn();
  const onFinalize = vi.fn();
  render(<ShortlistReview view={v} onDecide={onDecide} onAdd={onAdd} onFinalize={onFinalize} />);
  return { onDecide, onAdd, onFinalize };
}

describe("D7 shortlist review (M22)", () => {
  it("routes /briefs/:id/shortlist", () => {
    expect(matchRoute("/briefs/synthetic-brief/shortlist")).toEqual({ name: "briefShortlist", id: "synthetic-brief" });
    expect(matchRoute("/briefs/synthetic-brief")).toEqual({ name: "brief", id: "synthetic-brief" });
  });

  it("R4.6/R4.7: shows each candidate's source, verdict, reason and distance; precision below target is flagged", () => {
    setup();
    const table = screen.getByRole("table", { name: "Candidates" });
    expect(within(table).getByText("org-s/lint-a")).toBeTruthy();
    expect(within(table).getByText("A web framework.")).toBeTruthy();
    expect(within(table).getAllByText("github_keyword").length).toBe(3);
    expect(screen.getByLabelText("Precision").textContent).toMatch(/50 %/);
    expect(screen.getByRole("alert").textContent).toMatch(/Below the 80 % target/);
  });

  it("R4.7/D7: lists the brief fields still at their default, apart from warnings", () => {
    setup();
    expect(screen.getByText(/Brief fields to confirm \(1 still at their default\)/)).toBeTruthy();
    expect(screen.getByText("panel.winners")).toBeTruthy();
    expect(screen.getByText(/Brief warnings \(1\)/)).toBeTruthy();
  });

  it("filters by verdict, panel and distance", () => {
    setup();
    fireEvent.change(screen.getByLabelText("Verdict"), { target: { value: "uncertain" } });
    const table = screen.getByRole("table", { name: "Candidates" });
    expect(within(table).queryByText("org-s/lint-a")).toBeNull();
    expect(within(table).getByText("org-s/maybe-b")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Verdict"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Distance"), { target: { value: "2" } });
    expect(within(table).getByText("org-s/other-c")).toBeTruthy();
    expect(within(table).queryByText("org-s/maybe-b")).toBeNull();
  });

  it("R4.7: a decision needs a reason; single, selected and bulk decisions send it", () => {
    const { onDecide } = setup();
    const accept = screen.getAllByRole("button", { name: "Accept" })[0] as HTMLButtonElement;
    expect(accept.disabled).toBe(true); // no reason yet
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: "fits the core field" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Accept" })[0] as HTMLButtonElement);
    expect(onDecide).toHaveBeenLastCalledWith({ decision: "accept", reason: "fits the core field", candidates: ["gh:org-s/lint-a"] });
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: "bulk" } });
    fireEvent.click(screen.getByLabelText("select org-s/maybe-b"));
    fireEvent.click(screen.getByLabelText("select org-s/other-c"));
    fireEvent.click(screen.getByRole("button", { name: "Reject selected (2)" }));
    expect(onDecide).toHaveBeenLastCalledWith({ decision: "reject", reason: "bulk", candidates: ["gh:org-s/maybe-b", "gh:org-s/other-c"] });
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: "bulk" } });
    fireEvent.click(screen.getByRole("button", { name: "Accept all undecided relevant" }));
    expect(onDecide).toHaveBeenLastCalledWith({ decision: "accept", reason: "bulk", verdict: "relevant" });
  });

  it("R4.7/R4.11: add by URL and confirm a reference case from its candidate matches", () => {
    const { onAdd } = setup();
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: "missed by search" } });
    fireEvent.change(screen.getByLabelText("GitHub URL"), { target: { value: "https://github.com/org-y/new-tool" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(onAdd).toHaveBeenLastCalledWith({ url: "https://github.com/org-y/new-tool", reason: "missed by search", panel: "field" });
    const refs = screen.getByRole("list", { name: "Named projects to confirm (reference cases and distribution examples)" });
    expect(within(refs).getByText("Synthetic Reference")).toBeTruthy();
    expect(within(refs).getByText(/42 stars · one line/)).toBeTruthy();
    fireEvent.click(within(refs).getByRole("button", { name: "Use this repo" }));
    expect(onAdd).toHaveBeenLastCalledWith({ url: "https://github.com/org-x/ref-1", reason: "missed by search", resolves: "named:reference:0" });
  });

  it("R4.7: finalize is blocked while candidates are undecided and after finalizing", () => {
    const { onFinalize } = setup();
    expect((screen.getByRole("button", { name: "Finalize the shortlist" }) as HTMLButtonElement).disabled).toBe(true);
    cleanup();
    const ready = setup({ ...view, counts: { ...view.counts, undecided: 0 } });
    fireEvent.click(screen.getByRole("button", { name: "Finalize the shortlist" }));
    expect(ready.onFinalize).toHaveBeenCalledTimes(1);
    expect(onFinalize).not.toHaveBeenCalled();
    cleanup();
    setup({ ...view, status: "final", counts: { ...view.counts, undecided: 0 } });
    expect((screen.getByRole("button", { name: "Shortlist is final" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
