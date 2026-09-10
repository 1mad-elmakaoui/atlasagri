"""Configuration applicative.

Toute la configuration passe par ici. Aucun module ne lit `os.environ`
directement : cela garantit qu'une variable manquante est détectée au
démarrage et non au milieu d'une requête utilisateur.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---
    atlasagri_env: Literal["development", "staging", "production"] = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"

    # --- Sécurité ---
    secret_key: str = ""
    access_token_ttl_minutes: int = 480

    # --- Base de données ---
    database_url: str = "sqlite+pysqlite:///./data/local/atlasagri.db"

    # --- Agent ---
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    agent_max_tool_iterations: int = 8

    # --- Fournisseurs ---
    weather_provider: Literal["openmeteo", "offline"] = "openmeteo"
    open_meteo_base_url: str = "https://api.open-meteo.com/v1"
    open_meteo_archive_url: str = "https://archive-api.open-meteo.com/v1"
    weather_cache_ttl_seconds: int = 900
    http_timeout_seconds: float = 15.0

    satellite_provider: Literal["none", "copernicus"] = "none"
    copernicus_client_id: str = ""
    copernicus_client_secret: str = ""
    copernicus_base_url: str = "https://sh.dataspace.copernicus.eu"

    routing_provider: Literal["network", "osrm"] = "network"
    osrm_base_url: str = ""
    routing_api_key: str = ""

    # --- Limitation de débit ---
    rate_limit_requests_per_minute: int = 120
    agent_rate_limit_requests_per_minute: int = 20

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @property
    def is_production(self) -> bool:
        return self.atlasagri_env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def agent_enabled(self) -> bool:
        """Le copilote n'est jamais simulé : sans clé, il est désactivé."""
        return bool(self.anthropic_api_key)

    @property
    def satellite_enabled(self) -> bool:
        return self.satellite_provider == "copernicus" and bool(self.copernicus_client_id)

    def effective_secret_key(self) -> str:
        """Clé de signature des jetons.

        En production une clé explicite est obligatoire : on échoue au démarrage
        plutôt que de signer des jetons avec une valeur devinable.
        """
        if self.secret_key:
            return self.secret_key
        if self.is_production:
            raise RuntimeError(
                "SECRET_KEY est obligatoire en production. "
                "Générer une valeur avec: openssl rand -hex 32"
            )
        return "cle-de-developpement-non-securisee"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
