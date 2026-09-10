"""Fournisseur météo hors ligne — jeu de démonstration.

Ce fournisseur existe pour une seule raison : permettre au produit de
fonctionner et d'être démontré lorsque l'accès réseau au service météo est
bloqué (proxy d'entreprise, environnement d'intégration continue cloisonné).

Trois garde-fous rendent cette substitution honnête :

1. **Chaque valeur produite porte l'état `SIMULATED`** et la source
   « Jeu de démonstration local ». L'interface affiche cette étiquette.
2. Il n'est **jamais** activé automatiquement en cas de panne d'Open-Meteo :
   il faut le choisir explicitement via `WEATHER_PROVIDER=offline`. Une panne
   réelle produit une indisponibilité visible, pas une substitution silencieuse.
3. Il est **refusé en production** : un déploiement client ne peut pas démarrer
   sur des données simulées par inadvertance.

Les normales climatiques utilisées sont des valeurs réelles publiées pour des
stations marocaines ; la variabilité horaire, elle, est synthétique et
déterministe (même point + même heure ⇒ même valeur), ce qui rend les
démonstrations et les tests reproductibles.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.errors import AtlasAgriError
from app.core.logging import get_logger
from app.domain.enums import DataState
from app.domain.geo import Coordinates, haversine_km
from app.domain.provenance import SOURCE_OFFLINE_DEMO, DataSourceRef
from app.providers.weather.interface import HourlyWeather, WeatherProvider, WeatherSeries

logger = get_logger(__name__)


@dataclass(frozen=True)
class ClimateNormals:
    """Normales mensuelles d'une station de référence (janvier → décembre)."""

    name: str
    coordinates: Coordinates
    temp_max_c: tuple[float, ...]
    temp_min_c: tuple[float, ...]
    precipitation_mm_month: tuple[float, ...]


# Normales publiées pour des stations marocaines. Le contraste littoral /
# intérieur est réel et structurant : Essaouira plafonne à 23 °C en août quand
# Marrakech dépasse 37 °C.
STATIONS: tuple[ClimateNormals, ...] = (
    ClimateNormals(
        "Agadir", Coordinates(30.4278, -9.5981),
        (20.5, 21.5, 22.5, 23.0, 23.5, 25.0, 26.0, 26.5, 26.5, 25.5, 23.5, 21.0),
        (7.0, 8.5, 10.0, 11.0, 13.0, 15.5, 17.5, 18.0, 17.0, 14.5, 11.0, 8.0),
        (40, 35, 25, 10, 3, 0.5, 0, 0.5, 4, 20, 40, 50),
    ),
    ClimateNormals(
        "Essaouira", Coordinates(31.5085, -9.7595),
        (19.0, 19.5, 20.0, 20.5, 21.0, 22.0, 23.0, 23.5, 23.5, 23.0, 21.0, 19.5),
        (10.0, 11.0, 12.0, 13.0, 15.0, 17.0, 18.0, 18.5, 18.0, 16.0, 13.0, 11.0),
        (45, 35, 25, 12, 3, 0.5, 0, 0, 2, 18, 45, 50),
    ),
    ClimateNormals(
        "Marrakech", Coordinates(31.6295, -7.9811),
        (18.5, 20.0, 23.0, 25.0, 28.5, 33.0, 37.0, 37.0, 32.5, 28.0, 22.5, 19.0),
        (6.0, 8.0, 10.5, 12.0, 15.0, 18.0, 20.5, 20.5, 18.5, 15.0, 10.5, 7.0),
        (30, 32, 35, 32, 15, 6, 2, 3, 8, 25, 35, 32),
    ),
    ClimateNormals(
        "Safi", Coordinates(32.2994, -9.2372),
        (18.5, 19.5, 21.0, 22.0, 23.5, 25.0, 26.5, 27.0, 26.5, 25.0, 21.5, 19.0),
        (8.0, 9.0, 10.5, 11.5, 14.0, 17.0, 19.0, 19.5, 18.0, 15.5, 11.5, 9.0),
        (55, 45, 38, 25, 10, 2, 0, 0.5, 4, 25, 60, 65),
    ),
    ClimateNormals(
        "Casablanca", Coordinates(33.5731, -7.5898),
        (17.5, 18.5, 20.0, 21.0, 22.5, 24.5, 26.0, 26.5, 26.0, 24.5, 21.0, 18.5),
        (8.0, 9.0, 10.5, 12.0, 14.5, 17.5, 19.5, 20.0, 18.5, 16.0, 12.5, 9.5),
        (65, 55, 45, 35, 15, 4, 0.5, 1, 6, 35, 80, 80),
    ),
    ClimateNormals(
        "Béni Mellal", Coordinates(32.3373, -6.3498),
        (17.0, 19.0, 22.0, 24.0, 28.0, 33.0, 38.0, 38.0, 32.0, 27.0, 21.0, 18.0),
        (5.0, 6.5, 9.0, 11.0, 14.0, 18.0, 21.0, 21.0, 18.0, 14.0, 9.0, 6.0),
        (45, 48, 50, 45, 25, 8, 2, 3, 12, 38, 52, 50),
    ),
    ClimateNormals(
        "Fès", Coordinates(34.0181, -5.0078),
        (16.0, 18.0, 21.0, 23.0, 27.0, 32.0, 36.0, 36.0, 31.0, 26.0, 20.0, 16.5),
        (4.5, 6.0, 8.0, 10.0, 13.0, 17.0, 20.0, 20.5, 18.0, 14.0, 9.0, 5.5),
        (70, 70, 65, 60, 40, 15, 3, 4, 20, 55, 75, 80),
    ),
    ClimateNormals(
        "Tanger", Coordinates(35.7595, -5.8340),
        (16.5, 17.5, 19.0, 20.5, 23.0, 26.0, 29.0, 29.5, 27.0, 23.5, 19.5, 17.0),
        (8.0, 9.0, 10.0, 11.5, 14.0, 17.0, 19.5, 20.0, 18.5, 15.5, 11.5, 9.0),
        (100, 90, 70, 55, 30, 8, 1, 2, 20, 70, 120, 120),
    ),
)


@dataclass(frozen=True)
class SyntheticEvent:
    """Perturbation de démonstration, injectée par-dessus la climatologie.

    Ces événements donnent au scénario de démonstration des arbitrages réels :
    l'itinéraire de montagne est touché par la pluie, l'itinéraire littoral par
    le vent. Aucune option n'est parfaite — c'est ce qui rend la comparaison
    d'alternatives intéressante plutôt que décorative.
    """

    label_fr: str
    center: Coordinates
    radius_km: float
    starts_in_hours: float
    ends_in_hours: float
    peak_precipitation_mm_h: float = 0.0
    peak_wind_gust_kmh: float = 0.0
    temperature_delta_c: float = 0.0

    def intensity_at(self, point: Coordinates, hours_from_now: float) -> float:
        """Intensité dans [0,1] : décroît avec la distance et hors de la fenêtre."""
        if not self.starts_in_hours <= hours_from_now <= self.ends_in_hours:
            return 0.0
        distance = haversine_km(point, self.center)
        if distance >= self.radius_km:
            return 0.0

        spatial = math.cos((distance / self.radius_km) * math.pi / 2)
        duration = self.ends_in_hours - self.starts_in_hours
        phase = (hours_from_now - self.starts_in_hours) / duration if duration else 0.5
        temporal = math.sin(phase * math.pi)  # montée puis retombée
        return max(0.0, spatial * temporal)


# Scénario de démonstration, calé sur le corridor Souss-Massa → Casablanca.
DEMO_EVENTS: tuple[SyntheticEvent, ...] = (
    SyntheticEvent(
        label_fr="Épisode pluvieux sur le tronçon de montagne de l'A7",
        center=Coordinates(31.1719, -8.8506),  # Imi n'Tanoute
        radius_km=95.0,
        starts_in_hours=16.0,
        ends_in_hours=30.0,
        peak_precipitation_mm_h=9.5,
        peak_wind_gust_kmh=62.0,
        temperature_delta_c=-4.0,
    ),
    SyntheticEvent(
        label_fr="Coup de vent sur le littoral atlantique",
        center=Coordinates(31.8, -9.5),  # au large entre Essaouira et Safi
        radius_km=120.0,
        starts_in_hours=10.0,
        ends_in_hours=40.0,
        peak_precipitation_mm_h=1.1,
        peak_wind_gust_kmh=92.0,
        temperature_delta_c=-2.0,
    ),
)


class OfflineWeatherProvider(WeatherProvider):
    """Génère une série météo déterministe, intégralement étiquetée « Simulé »."""

    def __init__(self, *, events: tuple[SyntheticEvent, ...] = DEMO_EVENTS) -> None:
        from app.core.config import settings

        if settings.is_production:
            raise AtlasAgriError(
                "Le fournisseur météo hors ligne est interdit en production : "
                "un déploiement client ne doit jamais reposer sur des données simulées."
            )
        self.events = events
        logger.warning(
            "Fournisseur météo hors ligne actif — toutes les valeurs seront "
            "étiquetées « Simulé » dans l'interface"
        )

    @property
    def label_fr(self) -> str:
        return "Jeu de démonstration local (simulé)"

    @property
    def source(self) -> DataSourceRef:
        return SOURCE_OFFLINE_DEMO

    async def get_forecast(
        self, coordinates: Coordinates, *, hours_ahead: int = 48
    ) -> WeatherSeries:
        now = _floor_hour(datetime.now(UTC))
        hours = [
            self._hour_at(coordinates, now + timedelta(hours=offset), offset)
            for offset in range(-24, hours_ahead + 1)
        ]
        return WeatherSeries(
            coordinates=(coordinates.latitude, coordinates.longitude),
            hours=tuple(hours),
            source=SOURCE_OFFLINE_DEMO,
            retrieved_at=datetime.now(UTC),
        )

    async def get_historical(
        self, coordinates: Coordinates, *, start_date: str, end_date: str
    ) -> WeatherSeries:
        start = datetime.fromisoformat(start_date).replace(tzinfo=UTC)
        end = datetime.fromisoformat(end_date).replace(tzinfo=UTC)
        if end < start:
            raise AtlasAgriError("Période historique invalide : la fin précède le début.")

        now = _floor_hour(datetime.now(UTC))
        total_hours = int((end - start).total_seconds() // 3600) + 1
        # L'historique ne reçoit aucun événement synthétique : il sert à
        # calibrer des seuils, et y injecter des perturbations de démonstration
        # fausserait la calibration.
        hours = [
            self._hour_at(
                coordinates,
                start + timedelta(hours=index),
                (start + timedelta(hours=index) - now).total_seconds() / 3600,
                apply_events=False,
            )
            for index in range(min(total_hours, 24 * 400))
        ]
        return WeatherSeries(
            coordinates=(coordinates.latitude, coordinates.longitude),
            hours=tuple(hours),
            source=SOURCE_OFFLINE_DEMO,
            retrieved_at=datetime.now(UTC),
        )

    async def health(self) -> dict[str, Any]:
        return {
            "fournisseur": self.label_fr,
            "disponible": True,
            "message_fr": (
                "Mode démonstration : les valeurs météo sont simulées et "
                "signalées comme telles. À ne pas utiliser pour une décision réelle."
            ),
        }

    # --- génération ---

    def _hour_at(
        self,
        point: Coordinates,
        stamp: datetime,
        hours_from_now: float,
        *,
        apply_events: bool = True,
    ) -> HourlyWeather:
        normals = _interpolate_normals(point, stamp.month)
        noise = _deterministic_noise(point, stamp)

        # Cycle diurne : minimum vers 6 h, maximum vers 15 h UTC.
        amplitude = (normals["temp_max"] - normals["temp_min"]) / 2
        midpoint = (normals["temp_max"] + normals["temp_min"]) / 2
        diurnal = -math.cos((stamp.hour - 3) / 24 * 2 * math.pi)
        temperature = midpoint + amplitude * diurnal + noise * 2.5

        # La pluie mensuelle est concentrée sur peu d'heures : une répartition
        # uniforme donnerait une bruine permanente, ce qui n'a rien de marocain.
        wet_hour = noise > 0.72
        base_precip = (
            round(normals["precip_month"] / 22.0 * (1 + noise), 2) if wet_hour else 0.0
        )

        humidity = 55 + 25 * (1 - diurnal) / 2 + noise * 12
        wind = 8 + 14 * abs(noise)
        gust = wind * 1.7
        precipitation = base_precip

        if apply_events:
            for event in self.events:
                intensity = event.intensity_at(point, hours_from_now)
                if intensity <= 0:
                    continue
                precipitation += event.peak_precipitation_mm_h * intensity
                gust = max(gust, event.peak_wind_gust_kmh * intensity)
                temperature += event.temperature_delta_c * intensity
                humidity = min(100.0, humidity + 25 * intensity)

        return HourlyWeather(
            timestamp=stamp,
            state=DataState.SIMULATED,
            temperature_c=round(temperature, 1),
            precipitation_mm=round(precipitation, 2),
            precipitation_probability=round(min(100.0, precipitation * 22), 0),
            relative_humidity=round(max(10.0, min(100.0, humidity)), 0),
            wind_speed_kmh=round(wind, 1),
            wind_gust_kmh=round(gust, 1),
            pressure_hpa=round(1015 + noise * 8 - precipitation * 0.6, 1),
            soil_moisture=round(max(0.03, min(0.45, 0.14 + precipitation * 0.012)), 3),
            et0_mm=round(max(0.0, 0.16 * max(0.0, diurnal) * (1 + temperature / 40)), 3),
            shortwave_radiation=round(max(0.0, 720 * max(0.0, diurnal)), 1),
        )


def _floor_hour(stamp: datetime) -> datetime:
    return stamp.replace(minute=0, second=0, microsecond=0)


def _interpolate_normals(point: Coordinates, month: int) -> dict[str, float]:
    """Pondération par l'inverse du carré de la distance aux stations."""
    index = month - 1
    weights: list[float] = []
    for station in STATIONS:
        distance = haversine_km(point, station.coordinates)
        if distance < 1.0:
            return {
                "temp_max": station.temp_max_c[index],
                "temp_min": station.temp_min_c[index],
                "precip_month": station.precipitation_mm_month[index],
            }
        weights.append(1.0 / (distance**2))

    total = sum(weights)
    return {
        "temp_max": sum(
            s.temp_max_c[index] * w for s, w in zip(STATIONS, weights, strict=True)
        ) / total,
        "temp_min": sum(
            s.temp_min_c[index] * w for s, w in zip(STATIONS, weights, strict=True)
        ) / total,
        "precip_month": sum(
            s.precipitation_mm_month[index] * w
            for s, w in zip(STATIONS, weights, strict=True)
        )
        / total,
    }


def _deterministic_noise(point: Coordinates, stamp: datetime) -> float:
    """Bruit reproductible dans [0,1].

    Dérivé d'un condensé du couple (lieu, heure) : la même requête renvoie
    toujours la même valeur, ce qui rend les démonstrations et les tests stables.
    """
    key = f"{point.latitude:.2f}:{point.longitude:.2f}:{stamp:%Y%m%d%H}"
    digest = hashlib.sha256(key.encode()).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
