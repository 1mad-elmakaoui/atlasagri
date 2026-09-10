"""Sélection des fournisseurs selon la configuration.

Point d'entrée unique : le reste de l'application demande « le fournisseur
météo » sans savoir lequel est actif. Changer de fournisseur est une décision
de configuration, pas une modification de code.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.core.config import settings
from app.core.errors import FeatureDisabledError, ValidationError
from app.core.logging import get_logger
from app.providers.routing.interface import RoutingProvider
from app.providers.routing.network import NetworkRoutingProvider
from app.providers.routing.osrm import OsrmRoutingProvider
from app.providers.satellite.copernicus import CopernicusSatelliteProvider
from app.providers.satellite.interface import SatelliteProvider
from app.providers.satellite.providers import UnavailableSatelliteProvider
from app.providers.weather.interface import WeatherProvider
from app.providers.weather.offline import OfflineWeatherProvider
from app.providers.weather.open_meteo import OpenMeteoProvider

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def get_weather_provider() -> WeatherProvider:
    if settings.weather_provider == "offline":
        return OfflineWeatherProvider()
    return OpenMeteoProvider()


@lru_cache(maxsize=1)
def get_routing_provider() -> RoutingProvider:
    if settings.routing_provider == "osrm":
        try:
            provider = OsrmRoutingProvider()
        except ValidationError as exc:
            # Configuration incomplète : on le dit et on retombe sur le graphe
            # de référence, plutôt que d'empêcher l'application de démarrer.
            logger.warning(
                "OSRM demandé mais mal configuré, repli sur le réseau de référence",
                context={"motif": exc.message_fr},
            )
            return NetworkRoutingProvider()

        logger.info(
            "Routage OSRM actif", context={"serveur": provider.base_url}
        )
        return provider
    return NetworkRoutingProvider()


@lru_cache(maxsize=1)
def get_satellite_provider() -> SatelliteProvider:
    if not settings.satellite_enabled:
        return UnavailableSatelliteProvider()

    try:
        provider = CopernicusSatelliteProvider()
    except FeatureDisabledError as exc:
        # Configuration incomplète : on le dit dans les journaux et l'interface
        # affichera « non configurée ». Un démarrage bloqué serait une réaction
        # disproportionnée pour une fonctionnalité secondaire.
        logger.warning(
            "Copernicus demandé mais identifiants absents",
            context={"motif": exc.message_fr},
        )
        return UnavailableSatelliteProvider()

    logger.info("Fournisseur satellite Copernicus actif")
    return provider


async def providers_health() -> list[dict[str, Any]]:
    """État des sources externes, affiché dans l'interface.

    L'utilisateur doit pouvoir constater qu'une source est en panne : c'est ce
    qui lui permet de pondérer sa confiance dans ce qu'il voit à l'écran.
    """
    return [
        await get_weather_provider().health(),
        await get_routing_provider().health(),
        await get_satellite_provider().health(),
    ]
