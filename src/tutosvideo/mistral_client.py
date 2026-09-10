"""Client Mistral compatible selon la version du SDK installé."""

from __future__ import annotations

from typing import Any


def mistral_client(api_key: str) -> Any:
    """Retourne un client Mistral (SDK 1.x ``mistralai.Mistral`` ou ``mistralai.client``)."""
    try:
        from mistralai import Mistral

        return Mistral(api_key=api_key)
    except ImportError:
        from mistralai.client import Mistral

        return Mistral(api_key=api_key)
