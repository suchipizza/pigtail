"""Research briefs (PRD F18, D7; M12).

A brief describes the user's project, its neighbourhood (field boundaries), what success means
and the budget. Briefs are private: they live only under `PIGTAIL_DATA_DIR/briefs`
(R18.9), every edit creates a new immutable version (R18.4), and `schemas/brief/v1.json` is the
public, versioned schema (R18.1).

Modules:
    model     pydantic model, validation with field-named messages, JSON Schema, content hash
    store     private on-disk store: immutable versions, multiple briefs per install
    diff      structured and textual diff between two versions
    estimate  cost estimate before a run (GitHub requests per bucket, LLM calls and tokens,
              share of the weekly subscription allowance, money) (R18.5, ADR-053)
    budget    `BudgetGuard`: the hard stop other stages call (R18.5, ADR-053)
    cache     brief-run provenance and cache reuse on re-runs (R18.4, R18.6)
"""
