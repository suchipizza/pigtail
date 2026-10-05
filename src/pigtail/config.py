"""TOML configuration (spec §26). Secrets are referenced by environment-variable name only."""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pigtail.errors import UsageError

SECRET_KEYS = {"api_key", "token", "secret", "password", "apikey"}


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EngineConfig(_Section):
    output_root: str = "./pigtail-output"
    open_report_on_success: bool = True
    max_sources: int = Field(default=60, ge=1, le=500)
    max_runtime_minutes: int = Field(default=20, ge=1)


class ModelConfig(_Section):
    provider: str = "anthropic"
    model: str = "claude-opus-5-5"
    api_key_env: str = "ANTHROPIC_API_KEY"


class DiscoveryConfig(_Section):
    provider: str = "anthropic_web_search"
    api_key_env: str = "ANTHROPIC_API_KEY"
    max_queries: int = Field(default=12, ge=0, le=100)


class GitHubConfig(_Section):
    token_env: str = "GITHUB_TOKEN"
    request_timeout_seconds: int = Field(default=30, ge=1)


class ResearchConfig(_Section):
    policy_version: str = "0.1.0"
    source_cutoff: str = "now"
    prefer_first_party: bool = True
    allow_third_party_synthesis: bool = True


class RendererConfig(_Section):
    theme: str = "default"
    embed_assets: bool = True


class LoggingConfig(_Section):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["human", "json"] = "human"


class Config(_Section):
    engine: EngineConfig = Field(default_factory=EngineConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    renderer: RendererConfig = Field(default_factory=RendererConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    source_path: str | None = Field(default=None, exclude=True)

    def secret(self, env_name: str) -> str | None:
        value = os.environ.get(env_name, "").strip()
        return value or None


def user_config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "pigtail" / "config.toml"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "pigtail" / "config.toml"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", Path.home())) / "pigtail" / "config.toml"
    return Path.home() / ".config" / "pigtail" / "config.toml"


def candidate_paths(explicit: str | None) -> list[Path]:
    if explicit:
        return [Path(explicit)]
    paths = [Path("./pigtail.toml")]
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        paths.append(Path(xdg) / "pigtail" / "config.toml")
    paths.append(user_config_path())
    return paths


def _reject_literal_secrets(data: dict, prefix: str = "") -> None:
    for k, v in data.items():
        if isinstance(v, dict):
            _reject_literal_secrets(v, f"{prefix}{k}.")
        elif k.lower() in SECRET_KEYS:
            raise UsageError(
                f"Config key `{prefix}{k}` looks like a literal secret.",
                hint=f'Store secrets in environment variables and reference them, e.g. `{k}_env = "MY_VAR"`.',
            )


def load_config(explicit: str | None = None) -> Config:
    for path in candidate_paths(explicit):
        if path.exists():
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as exc:
                raise UsageError(f"Could not parse {path}: {exc}") from exc
            _reject_literal_secrets(data)
            try:
                cfg = Config.model_validate(data)
            except ValidationError as exc:
                problems = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors())
                raise UsageError(f"Invalid config {path}: {problems}") from exc
            cfg.source_path = str(path)
            return cfg
        if explicit:
            raise UsageError(f"Config file not found: {path}")
    return Config()


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load KEY=VALUE lines from a local .env without overriding the real environment."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
