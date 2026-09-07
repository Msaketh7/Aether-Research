"""Provider adapters.

One module per provider, each translating the shared interface in
``app.models.base`` into that vendor's API and its exceptions into
``app.models.errors``. Nothing outside this package imports a vendor SDK - which
is what makes "swap the provider" a configuration change rather than a search
across the codebase (ADR 0007).
"""

from __future__ import annotations

from app.models.providers.anthropic import AnthropicProvider
from app.models.providers.ollama import OllamaProvider
from app.models.providers.openai import OpenAIProvider

__all__ = ["AnthropicProvider", "OllamaProvider", "OpenAIProvider"]
