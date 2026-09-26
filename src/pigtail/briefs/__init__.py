"""Research briefs (PRD F18, D7; M12).

A brief describes the user's project, its neighbourhood (field boundaries), what success means
and the budget. Briefs are private: they live outside git in `PIGTAIL_BRIEFS_DIR` (default
`~/.pigtail/briefs`, R18.9, ADR-071.3) and are included in the encrypted backup, every edit
creates a new immutable version (R18.4), and `schemas/brief/v1.2.json` is the public, versioned
schema (R18.1).

Modules:
    model     pydantic model, validation with field-named messages, JSON Schema, content hash
    store     private on-disk store: immutable versions, multiple briefs per install
    diff      structured and textual diff between two versions
    estimate  cost estimate before a run (GitHub requests per bucket, LLM calls, tokens and USD
              per stage and model, against the brief's and the monthly cap) (R18.5, R15.11)
    budget    `BudgetGuard`: the hard stop other stages call (R18.5, R15.11, ADR-072.4)
    backup    the briefs directory in the encrypted backup (R18.9, ADR-071.3)
    cache     brief-run provenance and cache reuse on re-runs (R18.4, R18.6)
"""
