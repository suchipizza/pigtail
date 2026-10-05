"""BundleBuilder: the single place where research results become Research Bundle objects.

Every higher-level object is attached to Claims; every Claim to EvidenceLinks; every
EvidenceLink to a Source and an immutable SourceFetch. The builder enforces that chain
at construction time so the engine cannot emit unsupported objects.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pigtail import (
    BUNDLE_SCHEMA_VERSION,
    ENGINE_VERSION,
    ONTOLOGY_VERSION,
    RESEARCH_POLICY_VERSION,
    SOURCE_POLICY_VERSION,
)
from pigtail.bundle.models import ResearchBundle
from pigtail.domain.ids import new_id
from pigtail.domain.time import iso, utcnow
from pigtail.policies.models import SourcePolicy

SURFACE_INFO: dict[str, tuple[str, str, str | None]] = {
    "github": ("GitHub", "code_host", "https://github.com"),
    "hacker_news": ("Hacker News", "community", "https://news.ycombinator.com"),
    "reddit": ("Reddit", "community", "https://www.reddit.com"),
    "product_hunt": ("Product Hunt", "launch_surface", "https://www.producthunt.com"),
    "x": ("X (Twitter)", "social", "https://x.com"),
    "web": ("Web", "web", None),
}


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    query = "&".join(
        q for q in parts.query.split("&") if q and not q.lower().startswith(("utm_", "ref=", "fbclid", "gclid"))
    )
    return urlunsplit((scheme, host, path, query, ""))


def sha256_text(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass
class EvidenceSpec:
    source_id: str
    fetch_id: str
    evidence_class: str
    source_directness: str
    locator_kind: str
    locator_value: str
    inference_strength: str = "explicit"
    causal_attribution: str = "unknown"
    excerpt: str | None = None
    notes: str | None = None


@dataclass
class BundleBuilder:
    target: dict
    started_at: str = field(default_factory=lambda: iso(utcnow()) or "")
    cutoff_at: str = field(default_factory=lambda: iso(utcnow()) or "")
    model_provider: str = "none"
    model_id: str = "none"
    discovery_provider: str = "none"
    discovery_version: str | None = None

    def __post_init__(self) -> None:
        self.c: dict[str, list[dict]] = {
            k: []
            for k in (
                "people",
                "repositories",
                "sources",
                "source_fetches",
                "claims",
                "evidence_links",
                "events",
                "launch_episodes",
                "growth_episodes",
                "metric_snapshots",
                "company_stages",
                "strategy_phases",
                "surfaces",
                "surface_presences",
                "tactics",
                "tactic_occurrences",
                "growth_engines",
                "growth_engine_occurrences",
                "outcomes",
                "prerequisites",
                "constraints",
                "conflicts",
                "gaps",
            )
        }
        self.narrative: dict[str, Any] = {
            "thirty_second": None,
            "origin": None,
            "first_users": None,
            "flywheel": None,
            "did_differently": None,
            "key_takeaways": [],
        }
        self._sources_by_url: dict[str, dict] = {}
        self._surfaces: dict[str, dict] = {}
        self._claims: dict[str, dict] = {}
        self._evidence_by_claim: dict[str, list[dict]] = {}
        self.policies_by_source: dict[str, SourcePolicy] = {}
        self.run_id = new_id()

    # ----- sources ----------------------------------------------------------------
    def surface(self, key: str) -> dict:
        if key not in self._surfaces:
            name, cat, url = SURFACE_INFO.get(key, (key.replace("_", " ").title(), "other", None))
            s = {"id": new_id(), "key": key, "name": name, "category": cat, "url": url}
            self._surfaces[key] = s
            self.c["surfaces"].append(s)
        return self._surfaces[key]

    def source_by_url(self, url: str) -> dict | None:
        return self._sources_by_url.get(canonicalize_url(url))

    def add_source(
        self,
        url: str,
        *,
        surface_key: str,
        source_type: str,
        policy: SourcePolicy,
        title: str | None = None,
        author: str | None = None,
        published_at: str | None = None,
    ) -> dict:
        canon = canonicalize_url(url)
        if canon in self._sources_by_url:
            s = self._sources_by_url[canon]
            s["title"] = s["title"] or title
            s["author"] = s["author"] or author
            s["published_at"] = s["published_at"] or published_at
            return s
        self.surface(surface_key)
        s = {
            "id": new_id(),
            "url": url,
            "canonical_url": canon,
            "surface_key": surface_key,
            "source_type": source_type,
            "title": title,
            "author": author,
            "published_at": published_at,
            "discovered_at": iso(utcnow()),
            "policy": {
                "policy_key": policy.key,
                "policy_version": policy.version,
                "coverage_tier": policy.coverage_tier,
                "retention_mode": policy.retention.mode,
                "public_display_mode": policy.public_display.mode,
            },
        }
        self._sources_by_url[canon] = s
        self.policies_by_source[s["id"]] = policy
        self.c["sources"].append(s)
        return s

    def add_fetch(
        self,
        source: dict,
        *,
        status: str,
        http_status: int | None = None,
        content_hash: str | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
        parser_version: str = "pigtail-0.1.0",
        error_code: str | None = None,
        retrieved_at: str | None = None,
    ) -> dict:
        policy = self.policies_by_source[source["id"]]
        f = {
            "id": new_id(),
            "source_id": source["id"],
            "retrieved_at": retrieved_at or iso(utcnow()),
            "status": status,
            "http_status": http_status,
            "content_hash": content_hash,
            "etag": etag,
            "last_modified": last_modified,
            "parser_version": parser_version,
            "retention_mode": policy.retention.mode,
            "retained_artifact_path": None,  # Pigtail never archives third-party content by default.
            "error_code": error_code,
        }
        self.c["source_fetches"].append(f)
        return f

    # ----- claims & evidence ------------------------------------------------------
    def add_claim(
        self,
        statement: str,
        *,
        kind: str,
        time: dict,
        evidence: list[EvidenceSpec],
        subject_ref: dict | None = None,
        certainty: float = 0.9,
        negative: bool = False,
        review_state: str = "machine_extracted",
    ) -> str:
        if not evidence:
            raise ValueError("A claim needs at least one piece of evidence")
        cid = new_id()
        claim = {
            "id": cid,
            "statement": statement.strip(),
            "claim_kind": kind,
            "subject_ref": subject_ref or {"type": "target", "id": self.target["id"]},
            "time": time,
            "review_state": review_state,
            "extraction_certainty": max(0.0, min(1.0, float(certainty))),
            "is_negative_sensitive": negative,
            "status": "active",
        }
        self.c["claims"].append(claim)
        self._claims[cid] = claim
        for ev in evidence:
            self._link({"type": "claim", "id": cid}, cid, ev)
        return cid

    def add_evidence(self, claim_id: str, ev: EvidenceSpec) -> None:
        self._link({"type": "claim", "id": claim_id}, claim_id, ev)

    def _link(self, target_ref: dict, claim_id: str, ev: EvidenceSpec) -> dict:
        policy = self.policies_by_source[ev.source_id]
        link = {
            "id": new_id(),
            "target_ref": target_ref,
            "claim_id": claim_id,
            "source_id": ev.source_id,
            "source_fetch_id": ev.fetch_id,
            "evidence_class": ev.evidence_class,
            "source_directness": ev.source_directness,
            "corroboration": "single_source",
            "inference_strength": ev.inference_strength,
            "causal_attribution": ev.causal_attribution,
            "locator": {"kind": ev.locator_kind, "value": ev.locator_value[:500]},
            "excerpt": policy.clip_excerpt(ev.excerpt),
            "notes": ev.notes,
        }
        self.c["evidence_links"].append(link)
        if target_ref["type"] == "claim":
            self._evidence_by_claim.setdefault(claim_id, []).append(link)
        return link

    def claim(self, cid: str) -> dict:
        return self._claims[cid]

    def has_claim(self, cid: str) -> bool:
        return cid in self._claims

    def evidence_for(self, cid: str) -> list[dict]:
        return self._evidence_by_claim.get(cid, [])

    def attach(
        self,
        ref_type: str,
        obj: dict,
        claim_ids: list[str],
        *,
        inference: str | None = None,
        causal: str | None = None,
    ) -> None:
        """Copy claim evidence onto a higher-level object so it is directly traceable (spec §13)."""
        for cid in claim_ids:
            for ev in self._evidence_by_claim.get(cid, []):
                link = dict(ev)
                link["id"] = new_id()
                link["target_ref"] = {"type": ref_type, "id": obj["id"]}
                if inference:
                    link["inference_strength"] = inference
                if causal:
                    link["causal_attribution"] = causal
                self.c["evidence_links"].append(link)

    # ----- generic add ------------------------------------------------------------
    def add(self, collection: str, obj: dict) -> dict:
        obj.setdefault("id", new_id())
        self.c[collection].append(obj)
        return obj

    def gap(
        self,
        gap_type: str,
        summary: str,
        *,
        severity: str = "minor",
        related_refs: list[dict] | None = None,
        surface_key: str | None = None,
    ) -> dict:
        for g in self.c["gaps"]:
            if g["gap_type"] == gap_type and g["summary"] == summary:
                return g
        return self.add(
            "gaps",
            {
                "gap_type": gap_type,
                "summary": summary,
                "severity": severity,
                "related_refs": related_refs or [],
                "surface_key": surface_key,
            },
        )

    # ----- finalize ---------------------------------------------------------------
    def compute_corroboration(self) -> None:
        for cid, links in self._evidence_by_claim.items():
            sources = {link["source_id"] for link in links}
            value = "multi_source_consistent" if len(sources) > 1 else "single_source"
            if self._claims[cid]["status"] == "contradicted":
                value = "contradicted"
            for link in self.c["evidence_links"]:
                if link["claim_id"] == cid:
                    link["corroboration"] = value

    def build(self, *, status: str, cost: dict, usage: dict, completed_at: str | None = None) -> ResearchBundle:
        self.compute_corroboration()
        data = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "bundle_id": new_id(),
            "generated_at": iso(utcnow()),
            "run": {
                "run_id": self.run_id,
                "status": status,
                "started_at": self.started_at,
                "completed_at": completed_at or iso(utcnow()),
                "source_cutoff_at": self.cutoff_at,
                "engine_version": ENGINE_VERSION,
                "research_policy_version": RESEARCH_POLICY_VERSION,
                "source_policy_version": SOURCE_POLICY_VERSION,
                "ontology_version": ONTOLOGY_VERSION,
                "model": {"provider": self.model_provider, "model": self.model_id},
                "discovery": {
                    "provider": self.discovery_provider,
                    "provider_version": self.discovery_version,
                },
                "cost": cost,
                "usage": usage,
            },
            "target": self.target,
            **self.c,
            "narrative": self.narrative,
        }
        return ResearchBundle.model_validate(data)
