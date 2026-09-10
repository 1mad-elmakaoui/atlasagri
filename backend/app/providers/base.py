"""Contrats communs aux fournisseurs externes.

Un fournisseur ne doit jamais laisser fuir ses structures propres dans le
domaine : chaque adaptateur normalise sa réponse vers les schémas internes.
Cela permet de remplacer Open-Meteo par un autre service sans toucher au moteur
de risque.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol

import httpx

from app.core.config import settings
from app.core.errors import ProviderUnavailableError
from app.core.logging import get_logger

logger = get_logger(__name__)


class TtlCache:
    """Cache mémoire à durée de vie.

    Suffisant pour un monolithe mono-processus. Une installation multi-instance
    devra le remplacer par un cache partagé — l'interface est volontairement
    étroite pour rendre ce remplacement trivial.
    """

    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._entries: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() > expires_at:
            self._entries.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._entries[key] = (value, time.monotonic() + self.ttl_seconds)

    def clear(self) -> None:
        self._entries.clear()


async def fetch_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    provider_label_fr: str,
    retries: int = 2,
) -> dict[str, Any]:
    """Appel HTTP JSON avec délai d'attente et reprises bornées.

    Les reprises ne concernent que les erreurs transitoires (réseau, 5xx, 429).
    Une erreur 4xx traduit un défaut de notre requête : la réessayer ne ferait
    que retarder le diagnostic.
    """
    last_error: Exception | None = None

    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        for attempt in range(retries + 1):
            try:
                response = await client.get(url, params=params, headers=headers)
                if response.status_code >= 500 or response.status_code == 429:
                    raise httpx.HTTPStatusError(
                        f"statut {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status is not None and 400 <= status < 500 and status != 429:
                    logger.error(
                        "Requête rejetée par le fournisseur",
                        context={"fournisseur": provider_label_fr, "statut": status, "url": url},
                    )
                    raise ProviderUnavailableError(
                        f"{provider_label_fr} a rejeté la requête."
                    ) from exc
                last_error = exc
            except (httpx.RequestError, ValueError) as exc:
                last_error = exc

            if attempt < retries:
                await asyncio.sleep(0.5 * (2**attempt))

    logger.warning(
        "Fournisseur injoignable",
        context={"fournisseur": provider_label_fr, "url": url, "erreur": str(last_error)},
    )
    raise ProviderUnavailableError(
        f"{provider_label_fr} est momentanément injoignable. "
        "Les données correspondantes sont affichées comme indisponibles."
    ) from last_error


class HealthReportable(Protocol):
    """Un fournisseur sait dire s'il est configuré et joignable."""

    @property
    def label_fr(self) -> str: ...

    async def health(self) -> dict[str, Any]: ...
