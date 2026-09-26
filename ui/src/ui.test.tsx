import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { EvidenceRecord, LaunchModeWindow } from "./api";
import { LaunchModeList } from "./components/LaunchModeStrip";
import { PreviewBanner } from "./components/PreviewBanner";
import { SnapshotLink } from "./components/SnapshotLink";
import { linePath } from "./components/Timeline";
import { matchRoute } from "./router";

afterEach(cleanup);

const ev = (over: Partial<EvidenceRecord> = {}): EvidenceRecord => ({
  id: "ev_000000000000000000000001",
  source: "synthetic",
  url: "https://example.org/x",
  fetched_at: "2026-09-20T00:00:00Z",
  content_hash: "a".repeat(64),
  content_type: "text/plain",
  http_status: 200,
  reliability: "high",
  terms_basis: "synthetic",
  retention_class: "project_level",
  deletion_state: "present",
  collector_version: "test/1",
  case_id: null,
  repo_id: null,
  run_id: null,
  snapshot: { available: true, href: `/api/snapshots/${"a".repeat(64)}?evidence=ev_1`, state: "present" },
  ...over,
});

describe("D1 preview", () => {
  it("labels every page as an uncoded preview", () => {
    render(<PreviewBanner />);
    expect(screen.getByRole("note").textContent).toMatch(/Uncoded preview/);
    expect(screen.getByRole("note").textContent).toMatch(/raw captures, not coded/);
  });

  it("R13.2: opens a snapshot in one click, in a new tab without opener", () => {
    render(<SnapshotLink ev={ev()} />);
    const a = screen.getByRole("link", { name: "Open snapshot" });
    expect(a.getAttribute("href")).toContain("/api/snapshots/");
    expect(a.getAttribute("target")).toBe("_blank");
    expect(a.getAttribute("rel")).toContain("noopener");
  });

  it("R13.2: shows the retention state instead of a dead link once raw bytes are gone", () => {
    render(<SnapshotLink ev={ev({ deletion_state: "raw_dropped", snapshot: { available: false, href: null, state: "raw_dropped" } })} />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText(/raw dropped \(hash kept\)/)).toBeTruthy();
  });

  it("breaks timeline lines at unknown values and unscanned gaps", () => {
    const id = (v: number) => v;
    const d = linePath(
      [
        { t: 0, v: 1 },
        { t: 1, v: 2 },
        { t: 2, v: null },
        { t: 3, v: 4 },
        { t: 5, v: 5 },
      ],
      id,
      id,
      1,
    );
    expect(d).toBe("M0.0,1.0L1.0,2.0M3.0,4.0M5.0,5.0");
  });

  it("D1: the launch-mode strip lists active windows and links a tracked project's open case", () => {
    const w = (over: Partial<LaunchModeWindow>): LaunchModeWindow => ({
      id: 1,
      scope: "tracked_project",
      brief_id: null,
      repo_id: "github:1",
      repo_full_name: "org-a/repo-1",
      case_ids: ["case_00000000000000000001"],
      starts_at: "2026-09-25T00:00:00Z",
      ends_at: "2026-10-09T00:00:00Z",
      source: "detected",
      ...over,
    });
    render(<LaunchModeList items={[w({}), w({ id: 2, scope: "brief", brief_id: "b1", repo_id: null, repo_full_name: null, case_ids: [] })]} />);
    expect(screen.getByRole("link", { name: "org-a/repo-1" }).getAttribute("href")).toBe("/cases/case_00000000000000000001");
    expect(screen.getByText("brief b1")).toBeTruthy();
    cleanup();
    render(<LaunchModeList items={[]} />);
    expect(screen.getByText(/No project is in launch mode/)).toBeTruthy();
  });

  it("routes /cases, /cases/:id and /evidence/:id", () => {
    expect(matchRoute("/")).toEqual({ name: "cases" });
    expect(matchRoute("/cases/case_00000000000000000001")).toEqual({ name: "case", id: "case_00000000000000000001" });
    expect(matchRoute("/evidence/ev_1")).toEqual({ name: "evidence", id: "ev_1" });
    expect(matchRoute("/cases/../x")).toEqual({ name: "notFound" });
  });
});
