"""Nowcasting court terme (6–48 h).

Choix assumé : une **baseline statistique versionnée**, pas un réseau de
neurones.

L'article de référence emploie un LSTM, mais ses métriques sont obtenues sur
données synthétiques normalisées, avec un F1 de détection d'événements extrêmes
qui *augmente* avec l'horizon (0,51 à 6 h → 0,66 à 48 h) — un résultat que
l'article lui-même qualifie de contre-intuitif. Ces chiffres ne démontrent donc
pas qu'un LSTM serait opérationnellement supérieur ici.

La baseline est explicable, mesurable, et constitue la référence que tout modèle
ultérieur devra battre en validation temporelle sur données marocaines réelles.
L'interface `NowcastModel` rend la substitution triviale.

Ce module produit des **prévisions**. Il ne les interprète pas : la conversion
en risque appartient au moteur d'impact agricole.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import InsufficientDataError
from app.domain.enums import DEFAULT_HORIZONS_HOURS, Confidence, DataState
from app.domain.provenance import SOURCE_NOWCAST_BASELINE, DataSourceRef
from app.providers.weather.interface import WeatherSeries

MODEL_VERSION = "baseline-persistance-amortie-1.0.0"


class HorizonForecast(BaseModel):
    """Prévision agrégée sur une fenêtre [maintenant, maintenant + horizon]."""

    model_config = ConfigDict(frozen=True)

    horizon_hours: int
    valid_from: datetime
    valid_to: datetime

    cumulative_precipitation_mm: float | None = None
    max_temperature_c: float | None = None
    min_temperature_c: float | None = None
    max_wind_gust_kmh: float | None = None
    mean_humidity: float | None = None
    min_soil_moisture: float | None = None

    confidence: Confidence
    state: DataState = DataState.FORECAST
    model_version: str = MODEL_VERSION
    source: DataSourceRef = SOURCE_NOWCAST_BASELINE


class NowcastResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    coordinates: tuple[float, float]
    computed_at: datetime
    horizons: tuple[HorizonForecast, ...]
    input_state: DataState = Field(
        description="État des données d'entrée : FORECAST réel ou SIMULATED en démonstration"
    )
    input_source: DataSourceRef

    def at_horizon(self, hours: int) -> HorizonForecast:
        for horizon in self.horizons:
            if horizon.horizon_hours == hours:
                return horizon
        raise InsufficientDataError(f"Aucune prévision disponible à l'horizon {hours} h.")


class NowcastModel(Protocol):
    """Contrat d'un modèle de nowcasting.

    Un GRU entraîné se substituera à la baseline en implémentant ce protocole,
    sans que le moteur de risque n'ait à changer.
    """

    version: str

    def predict(
        self, series: WeatherSeries, *, horizons: tuple[int, ...]
    ) -> tuple[HorizonForecast, ...]: ...


class DampedPersistenceBaseline:
    """Persistance amortie sur les prévisions du fournisseur météo.

    Le fournisseur météo fournit déjà une prévision horaire issue de modèles
    numériques nationaux. La valeur ajoutée d'un nowcast local n'est pas de la
    refaire, mais de l'**agréger sur les fenêtres de décision** et d'y attacher
    une confiance décroissante avec l'horizon.

    Prétendre faire mieux qu'un modèle numérique national avec quelques
    centaines d'observations serait malhonnête. Ce que le produit sait faire,
    c'est traduire cette prévision en signal exploitable pour une décision
    logistique — et c'est là qu'est sa valeur.
    """

    version = MODEL_VERSION

    def predict(
        self, series: WeatherSeries, *, horizons: tuple[int, ...] = DEFAULT_HORIZONS_HOURS
    ) -> tuple[HorizonForecast, ...]:
        now = datetime.now(UTC)
        forecasts: list[HorizonForecast] = []

        for hours in sorted(horizons):
            end = now + timedelta(hours=hours)
            window = series.window(now, end)
            if not window:
                continue

            coverage = len(window) / hours if hours else 0.0
            forecasts.append(
                HorizonForecast(
                    horizon_hours=hours,
                    valid_from=now,
                    valid_to=end,
                    cumulative_precipitation_mm=series.total_precipitation_mm(now, end),
                    max_temperature_c=series.max_of("temperature_c", now, end),
                    min_temperature_c=series.min_of("temperature_c", now, end),
                    max_wind_gust_kmh=series.max_of("wind_gust_kmh", now, end),
                    mean_humidity=_mean(
                        [h.relative_humidity for h in window if h.relative_humidity is not None]
                    ),
                    min_soil_moisture=series.min_of("soil_moisture", now, end),
                    confidence=_confidence_for(hours, coverage),
                )
            )

        if not forecasts:
            raise InsufficientDataError(
                "Aucune donnée météo exploitable sur les horizons demandés. "
                "Aucune prévision n'est produite plutôt qu'une valeur par défaut."
            )
        return tuple(forecasts)


def _confidence_for(horizon_hours: int, coverage: float) -> Confidence:
    """Confiance décroissante avec l'horizon et la lacune de couverture.

    Une prévision à 48 h est structurellement moins fiable qu'à 6 h. L'afficher
    avec la même assurance conduirait l'utilisateur à surréagir à un signal
    lointain.
    """
    if coverage < 0.6:
        return Confidence.LOW
    if horizon_hours <= 12:
        return Confidence.HIGH
    if horizon_hours <= 24:
        return Confidence.MEDIUM if coverage < 0.9 else Confidence.HIGH
    return Confidence.MEDIUM if horizon_hours <= 48 else Confidence.LOW


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


# --- indicateurs dérivés repris de l'article ---------------------------------

def standardized_precipitation_index(
    recent_total_mm: float, historical_totals_mm: list[float]
) -> float | None:
    """SPI simplifié : écart normalisé au régime historique de la même fenêtre.

    Repris de l'article de référence comme indicateur de stress hydrique lent.
    Renvoie None si l'historique est trop court : un SPI calculé sur quelques
    valeurs donnerait une fausse impression de rigueur statistique.
    """
    if len(historical_totals_mm) < 30:
        return None

    mean = sum(historical_totals_mm) / len(historical_totals_mm)
    variance = sum((v - mean) ** 2 for v in historical_totals_mm) / len(historical_totals_mm)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return round((recent_total_mm - mean) / std, 2)


def temperature_anomaly_c(
    observed_mean_c: float, historical_means_c: list[float]
) -> float | None:
    """Anomalie thermique par rapport à la normale de la même période."""
    if len(historical_means_c) < 30:
        return None
    normal = sum(historical_means_c) / len(historical_means_c)
    return round(observed_mean_c - normal, 2)


class NowcastingService:
    """Service applicatif : récupère la météo puis applique le modèle."""

    def __init__(self, model: NowcastModel | None = None) -> None:
        self.model = model or DampedPersistenceBaseline()

    async def nowcast(
        self,
        latitude: float,
        longitude: float,
        *,
        horizons: tuple[int, ...] = DEFAULT_HORIZONS_HOURS,
    ) -> NowcastResult:
        from app.domain.geo import Coordinates
        from app.providers.registry import get_weather_provider

        provider = get_weather_provider()
        series = await provider.get_forecast(
            Coordinates(latitude, longitude), hours_ahead=max(horizons)
        )

        future = [h for h in series.hours if h.timestamp >= datetime.now(UTC)]
        input_state = future[0].state if future else DataState.FORECAST

        return NowcastResult(
            coordinates=(latitude, longitude),
            computed_at=datetime.now(UTC),
            horizons=self.model.predict(series, horizons=horizons),
            input_state=input_state,
            input_source=provider.source,
        )
