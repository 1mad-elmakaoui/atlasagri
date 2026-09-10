"""Contrat WeatherProvider et schémas météo normalisés.

Ces schémas sont ceux que voit le reste de l'application. Aucun champ propre à
Open-Meteo ne les traverse : changer de fournisseur n'impacte que l'adaptateur.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import DataState
from app.domain.geo import Coordinates
from app.domain.provenance import DataSourceRef


class HourlyWeather(BaseModel):
    """Conditions météorologiques à une heure donnée, en un point donné."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    state: DataState = Field(description="OBSERVED pour le passé, FORECAST pour l'avenir")

    temperature_c: float | None = None
    precipitation_mm: float | None = None
    precipitation_probability: float | None = Field(default=None, ge=0, le=100)
    relative_humidity: float | None = Field(default=None, ge=0, le=100)
    wind_speed_kmh: float | None = None
    wind_gust_kmh: float | None = None
    pressure_hpa: float | None = None
    soil_moisture: float | None = Field(default=None, description="m³/m³, 0–7 cm")
    et0_mm: float | None = Field(default=None, description="Évapotranspiration de référence")
    shortwave_radiation: float | None = None

    @property
    def vapor_pressure_deficit_kpa(self) -> float | None:
        """VPD, indicateur de demande évaporative.

        Calculé (donc `DERIVED`) à partir de la température et de l'humidité
        relative via la formule de Tetens. Renvoie None si une des deux entrées
        manque : mieux vaut une absence qu'une valeur inventée.
        """
        if self.temperature_c is None or self.relative_humidity is None:
            return None
        saturation = 0.6108 * pow(
            2.718281828, (17.27 * self.temperature_c) / (self.temperature_c + 237.3)
        )
        return round(saturation * (1 - self.relative_humidity / 100.0), 4)


class WeatherSeries(BaseModel):
    """Série horaire pour un point, accompagnée de sa provenance."""

    model_config = ConfigDict(frozen=True)

    coordinates: tuple[float, float] = Field(description="(latitude, longitude)")
    hours: tuple[HourlyWeather, ...]
    source: DataSourceRef
    retrieved_at: datetime
    timezone: str = "UTC"

    def window(self, start: datetime, end: datetime) -> tuple[HourlyWeather, ...]:
        return tuple(h for h in self.hours if start <= h.timestamp <= end)

    def total_precipitation_mm(self, start: datetime, end: datetime) -> float | None:
        values = [
            h.precipitation_mm for h in self.window(start, end) if h.precipitation_mm is not None
        ]
        return round(sum(values), 2) if values else None

    def max_of(self, attribute: str, start: datetime, end: datetime) -> float | None:
        values = [
            getattr(h, attribute)
            for h in self.window(start, end)
            if getattr(h, attribute) is not None
        ]
        return max(values) if values else None

    def min_of(self, attribute: str, start: datetime, end: datetime) -> float | None:
        values = [
            getattr(h, attribute)
            for h in self.window(start, end)
            if getattr(h, attribute) is not None
        ]
        return min(values) if values else None


class WeatherProvider(ABC):
    """Interface que tout fournisseur météo doit satisfaire."""

    @property
    @abstractmethod
    def label_fr(self) -> str: ...

    @property
    @abstractmethod
    def source(self) -> DataSourceRef: ...

    @abstractmethod
    async def get_forecast(
        self, coordinates: Coordinates, *, hours_ahead: int = 48
    ) -> WeatherSeries:
        """Prévisions horaires à venir."""

    @abstractmethod
    async def get_historical(
        self, coordinates: Coordinates, *, start_date: str, end_date: str
    ) -> WeatherSeries:
        """Historique horaire, utilisé pour calibrer les seuils par quantiles."""

    @abstractmethod
    async def health(self) -> dict[str, Any]: ...
