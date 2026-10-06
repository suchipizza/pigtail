import examples from "@/data/examples.json";

export type Example = {
  slug: string;
  title: string;
  description: string;
  kind: "repository" | "product" | "company";
  featured: boolean;
  published_at: string;
  bundle_schema_version: string;
  reviewed_by: "ai" | "human";
  target_url: string;
  summary: string | null;
  counts: { sources: number; claims: number; events: number; tactics: number; gaps: number };
  stars_now: number | null;
  sparkline: { t: string; v: number }[];
  generated_at: string;
};

export const EXAMPLES = examples as Example[];
export const GITHUB = "https://github.com/suchipizza/pigtail";
export const BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

export function fmt(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, "")}M`;
  if (n >= 1e4) return `${Math.round(n / 1e3)}K`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1).replace(/\.0$/, "")}K`;
  return String(n);
}
