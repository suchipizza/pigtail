"""Model provider selection. Never substitutes a different provider silently (PRD E3)."""

from __future__ import annotations

from pigtail.config import Config
from pigtail.errors import CredentialsError
from pigtail.providers.base import Meter, ModelProvider

SUPPORTED_MODEL_PROVIDERS = ("anthropic",)


def make_model_provider(cfg: Config, meter: Meter) -> ModelProvider:
    if cfg.model.provider == "anthropic":
        from pigtail.providers.models.anthropic import AnthropicModelProvider

        return AnthropicModelProvider(cfg.model.model, cfg.secret(cfg.model.api_key_env), meter)
    raise CredentialsError(
        f"Model provider {cfg.model.provider!r} is not supported.",
        hint=f"Supported providers: {', '.join(SUPPORTED_MODEL_PROVIDERS)}.",
    )
