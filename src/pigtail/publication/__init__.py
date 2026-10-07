"""Publication gate for Pigtail-hosted reports (internal tool, see docs/publication-gate.md).

Normal `pigtail analyze` output is not affected. The gate turns a valid Research Bundle into a
minimized public projection, records every change in an audit, and refuses publication while a
blocking finding is unresolved.
"""

POLICY_VERSION = "0.1.0"
SANITIZER_VERSION = "0.1.0"
PROJECTION_VERSION = "0.1.0"
SUPPORTED_POLICY_VERSIONS = frozenset({POLICY_VERSION})

PUBLIC_EXCERPT_MAX_WORDS = 15

INDEPENDENCE_NOTICE = (
    "Independent analysis based on public sources. Pigtail is not affiliated with or endorsed by {target}."
)
THIRD_PARTY_NOTICE = (
    "Pigtail's software license applies to Pigtail code. Third-party names, source material, excerpts and "
    "linked data remain subject to their respective rights and are not relicensed by Pigtail."
)
MACHINE_GENERATED_NOTICE = (
    "Machine-generated analysis of public sources that passed Pigtail's automated publication checks. It can "
    "contain mistakes; check the linked sources before relying on a claim."
)

# Files that make up a published example directory, and files that must never be in one.
PUBLIC_EXAMPLE_FILES = ("report.html", "public-report-bundle.json", "publication-manifest.json", "metadata.json")
FORBIDDEN_EXAMPLE_FILES = (
    "research-bundle.json",
    "research-bundle.invalid.json",
    "source-index.json",
    "run.json",
    "publication-audit.json",
    "publication-review.yaml",
)
NOTICE_FILE = "NOTICE.md"
