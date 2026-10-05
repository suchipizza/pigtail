"""Critical known facts for reviewed examples, checked by golden tests.

Each fact was checked against the cited first-party source, not against generated prose.
"""

KNOWN_FACTS: dict[str, dict] = {
    "tally": {
        # blog.tally.so/product-hunt-launch-results; blog.tally.so year-one post ($1K MRR at the March 2021 launch)
        "sources_include": ["blog.tally.so/product-hunt-launch-results"],
        "event_types": ["product_hunt_launch"],
        "metrics": [("mrr", 1000, "2021-03")],
        "claims_mention": ["Product Hunt", "very disappointed"],
    },
    "plausible": {
        "repo_created": "2018-12-04",
        "event_types": ["show_hn", "release"],
        "claims_mention": ["Marko"],
    },
    "hatchet": {
        "repo_created": "2023-12-15",
        "event_types": ["show_hn", "launch_hn"],
        "claims_mention": ["Y Combinator"],
    },
    "pocketbase": {
        "repo_created": "2022-07-05",
        "event_types": ["show_hn", "release"],
    },
    "superhuman": {
        "sources_include": ["review.firstround.com/how-superhuman-built-an-engine-to-find-product-market-fit"],
        "claims_mention": ["very disappointed", "Grammarly"],
    },
}
