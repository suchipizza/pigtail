# Troubleshooting

Run `pigtail doctor` first. It checks Python, your config, credentials, the output folder, the
source-policy files and network access, without spending money.

## Exit codes

| Code | Meaning | What to do |
|---:|---|---|
| 0 | Success (including "completed with gaps") | Open the report. Gaps are listed at the bottom. |
| 1 | Unexpected failure | Re-run with `--verbose`; please open an issue with the output. |
| 2 | Command or config problem | Check the command and `pigtail.toml`. |
| 3 | Credentials or provider problem | Set `ANTHROPIC_API_KEY`; check `--model`. |
| 4 | Target not recognised or not found | Use `https://github.com/owner/repo` or a domain like `example.com`. |
| 5 | Research failed | Often a temporary API problem; try again. |
| 6 | Research Bundle is invalid | For `validate`: the file breaks a rule listed in the output. During `analyze`: a bug — please report it. |
| 7 | Rendering failed | The bundle was saved; run `pigtail render <bundle>` again and report the error. |
| 8 | Source-policy problem | A file in `source-policies/` is invalid. |

## Common problems

**`Missing model credentials: set ANTHROPIC_API_KEY`**
Export the key in the same terminal (`export ANTHROPIC_API_KEY=...`) or put it in a `.env` file in
the folder where you run Pigtail.

**`Model 'x' is not available to this API key`**
Check the model name: `--model anthropic/claude-sonnet-5-5`. Pigtail never switches to a different
model silently.

**`GitHub API rate limit reached`**
Without a token GitHub allows 60 requests per hour. Set `GITHUB_TOKEN` (no special permissions
needed) for 5,000 per hour.

**`... is not a supported target`**
Pigtail V1 accepts GitHub repository URLs and website domains. Product names like `"Tally"` are not
supported because they are ambiguous.

**The report says "Insufficient public evidence for a useful Pigtail forensic."**
This is a real result, not an error: Pigtail found too little verifiable public information. You
can add sources you know about with `--source <url>`.

**A growth spike says "No high-confidence public event found."**
Pigtail detected the spike but found no public launch, post or release near it. The cause may be a
mention Pigtail cannot read (for example a link-only platform) or something private.

**The report looks wrong after editing the bundle**
Run `pigtail validate research-bundle.json` to find broken references, then `pigtail render`.

**Run takes long or costs more than expected**
Lower `engine.max_sources` and `discovery.max_queries` in `pigtail.toml`. `run.json` shows the cost and request counts of each run.

## Configuration file

Pigtail looks for config in this order: `--config <path>`, `./pigtail.toml`,
`$XDG_CONFIG_HOME/pigtail/config.toml`, the platform config folder, then built-in defaults.

```toml
[engine]
output_root = "./pigtail-output"
open_report_on_success = true
max_sources = 60
max_runtime_minutes = 20

[model]
provider = "anthropic"
model = "claude-sonnet-5-5"
api_key_env = "ANTHROPIC_API_KEY"

[discovery]
provider = "anthropic_web_search"   # or "none" to skip web search
api_key_env = "ANTHROPIC_API_KEY"
max_queries = 12

[github]
token_env = "GITHUB_TOKEN"
request_timeout_seconds = 30

[logging]
level = "INFO"
format = "human"
```

Keys are referenced by environment-variable name. Writing a key directly into the file is rejected.
Unknown sections are rejected so typos do not go unnoticed.
