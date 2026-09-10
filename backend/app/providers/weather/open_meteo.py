"""Adaptateur Open-Meteo.

Open-Meteo agrège les modèles météorologiques nationaux et ne demande pas de
clé d'API, ce qui en fait un point de départ raisonnable. La dépendance à un
fournisseur unique reste un risque identifié : c'est précisément pourquoi tout
passe par `WeatherProvider`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.config import settings
from app.core.errors import ProviderUnavailableError
from app.core.logging import get_logger
from app.domain.enums import DataState
from app.domain.geo import Coordinates
from app.domain.provenance import (
    SOURCE_OPEN_METEO,
    SOURCE_OPEN_METEO_ARCHIVE,
    DataSourceRef,
)
from app.providers.base import TtlCache, fetch_json
from app.providers.weather.interface import HourlyWeather, WeatherProvider, WeatherSeries

logger = get_logger(__name__)

# Variables demandées à Open-Meteo, dans l'ordre du contrat interne.
HOURLY_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "precipitation_probability",
    "surface_pressure",
    "wind_speed_10m",
    "wind_gusts_10m",
    "shortwave_radiation",
    "et0_fao_evapotranspiration",
    "soil_moisture_0_to_7cm",
]

# L'archive ne propose ni probabilité de précipitation ni rafales sur toute la
# période : demander une variable absente ferait échouer toute la requête.
ARCHIVE_HOURLY_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "surface_pressure",
    "wind_speed_10m",
    "shortwave_radiation",
    "et0_fao_evapotranspiration",
    "soil_moisture_0_to_7cm",
]


class OpenMeteoProvider(WeatherProvider):
    def __init__(self) -> None:
        self._cache = TtlCache(settings.weather_cache_ttl_seconds)

    @property
    def label_fr(self) -> str:
        return "Open-Meteo"

    @property
    def source(self) -> DataSourceRef:
        return SOURCE_OPEN_METEO

    async def get_forecast(
        self, coordinates: Coordinates, *, hours_ahead: int = 48
    ) -> WeatherSeries:
        days = max(1, min(16, (hours_ahead + 23) // 24))
        cache_key = f"prev:{coordinates.latitude:.3f}:{coordinates.longitude:.3f}:{days}"
        if (cached := self._cache.get(cache_key)) is not None:
            return cached

        payload = await fetch_json(
            f"{settings.open_meteo_base_url}/forecast",
            params={
                "latitude": round(coordinates.latitude, 4),
                "longitude": round(coordinates.longitude, 4),
                "hourly": ",".join(HOURLY_VARIABLES),
                "forecast_days": days,
                "past_days": 1,  # contexte récent, utile au nowcasting
                "timezone": "UTC",
            },
            provider_label_fr=self.label_fr,
        )

        series = self._parse(payload, source=SOURCE_OPEN_METEO)
        self._cache.set(cache_key, series)
        return series

    async def get_historical(
        self, coordinates: Coordinates, *, start_date: str, end_date: str
    ) -> WeatherSeries:
        cache_key = (
            f"hist:{coordinates.latitude:.3f}:{coordinates.longitude:.3f}:{start_date}:{end_date}"
        )
        if (cached := self._cache.get(cache_key)) is not None:
            return cached

        payload = await fetch_json(
            f"{settings.open_meteo_archive_url}/archive",
            params={
                "latitude": round(coordinates.latitude, 4),
                "longitude": round(coordinates.longitude, 4),
                "start_date": start_date,
                "end_date": end_date,
                "hourly": ",".join(ARCHIVE_HOURLY_VARIABLES),
                "timezone": "UTC",
            },
            provider_label_fr=f"{self.label_fr} (archive)",
        )

        series = self._parse(payload, source=SOURCE_OPEN_METEO_ARCHIVE, force_observed=True)
        self._cache.set(cache_key, series)
        return series

    async def health(self) -> dict[str, Any]:
        try:
            await self.get_forecast(Coordinates(33.5731, -7.5898), hours_ahead=6)
        except ProviderUnavailableError as exc:
            return {
                "fournisseur": self.label_fr,
                "disponible": False,
                "message_fr": str(exc),
            }
        return {"fournisseur": self.label_fr, "disponible": True, "message_fr": "Opérationnel"}

    # --- normalisation ---

    def _parse(
        self, payload: dict[str, Any], *, source: DataSourceRef, force_observed: bool = False
    ) -> WeatherSeries:
        hourly = payload.get("hourly")
        if not isinstance(hourly, dict) or "time" not in hourly:
            # On refuse une réponse inexploitable plutôt que de produire une
            # série vide qui passerait pour « pas de risque ».
            raise ProviderUnavailableError(
                f"{self.label_fr} a renvoyé une réponse inexploitable."
            )

        timestamps: list[str] = hourly["time"]
        now = datetime.now(UTC)

        def column(name: str) -> list[float | None]:
            values = hourly.get(name)
            if not isinstance(values, list) or len(values) != len(timestamps):
                return [None] * len(timestamps)
            return values

        columns = {name: column(name) for name in HOURLY_VARIABLES}

        hours: list[HourlyWeather] = []
        for index, raw_time in enumerate(timestamps):
            try:
                stamp = datetime.fromisoformat(raw_time).replace(tzinfo=UTC)
            except ValueError:
                continue

            hours.append(
                HourlyWeather(
                    timestamp=stamp,
                    state=(
                        DataState.OBSERVED
                        if force_observed or stamp <= now
                        else DataState.FORECAST
                    ),
                    temperature_c=columns["temperature_2m"][index],
                    precipitation_mm=columns["precipitation"][index],
                    precipitation_probability=columns["precipitation_probability"][index],
                    relative_humidity=columns["relative_humidity_2m"][index],
                    wind_speed_kmh=columns["wind_speed_10m"][index],
                    wind_gust_kmh=columns["wind_gusts_10m"][index],
                    pressure_hpa=columns["surface_pressure"][index],
                    soil_moisture=columns["soil_moisture_0_to_7cm"][index],
                    et0_mm=columns["et0_fao_evapotranspiration"][index],
                    shortwave_radiation=columns["shortwave_radiation"][index],
                )
            )

        if not hours:
            raise ProviderUnavailableError(
                f"{self.label_fr} n'a renvoyé aucune donnée horaire exploitable."
            )

        return WeatherSeries(
            coordinates=(payload.get("latitude", 0.0), payload.get("longitude", 0.0)),
            hours=tuple(hours),
            source=source,
            retrieved_at=now,
        )
