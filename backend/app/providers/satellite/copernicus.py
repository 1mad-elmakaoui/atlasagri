"""Adaptateur Copernicus Data Space — NDVI et NDWI depuis Sentinel-2.

Utilise l'API statistique de Sentinel Hub plutôt que l'API de traitement
d'images. La différence est décisive : nous voulons une **valeur agrégée** sur
une parcelle, pas une image à analyser ensuite. Télécharger un raster pour en
recalculer la moyenne côté serveur ajouterait du transfert, du traitement et
une source d'erreur, pour exactement le même résultat.

Deux exigences d'honnêteté gouvernent ce module :

1. **Un NDVI sous les nuages n'est pas un NDVI.** Sentinel-2 traverse
   l'atmosphère, pas les nuages. Une acquisition couverte donne des valeurs
   d'apparence normale et totalement fausses. Les pixels nuageux sont donc
   masqués via la bande de classification, et une acquisition dont il reste
   trop peu de pixels valides est écartée plutôt que moyennée.
2. **Aucune valeur par défaut.** Si aucune acquisition exploitable n'existe sur
   la fenêtre demandée, le module dit pourquoi. Il ne renvoie ni la dernière
   valeur connue, ni une moyenne saisonnière.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import FeatureDisabledError, ProviderUnavailableError
from app.core.logging import get_logger
from app.domain.geo import Coordinates
from app.domain.provenance import DataSourceRef
from app.providers.base import TtlCache
from app.providers.satellite.interface import (
    SatelliteAvailability,
    SatelliteProvider,
    VegetationObservation,
)

logger = get_logger(__name__)

TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu"
    "/auth/realms/CDSE/protocol/openid-connect/token"
)

SOURCE_COPERNICUS = DataSourceRef(
    id="copernicus-sentinel2",
    label_fr="Copernicus Sentinel-2 (L2A)",
    kind="satellite",
    detail="Indices NDVI et NDWI calculés sur acquisition optique, résolution 10–20 m",
)

# Demi-côté de la zone analysée, en degrés. 0,02° ≈ 2,2 km : assez large pour
# couvrir une exploitation, assez étroit pour ne pas noyer la parcelle dans son
# environnement.
AOI_HALF_SIZE_DEGREES = 0.02

# Part minimale de pixels valides (hors nuage, hors ombre) pour qu'une
# acquisition soit exploitable. En dessous, la moyenne porte sur trop peu de
# surface pour représenter la parcelle.
MINIMUM_VALID_FRACTION = 0.35

# Marge de sécurité sur l'expiration du jeton : on renouvelle avant la fin
# plutôt que d'essuyer un 401 en pleine requête.
TOKEN_REFRESH_MARGIN_SECONDS = 60

# Évalue NDVI et NDWI, et construit le masque de validité.
#
# SCL (Scene Classification Layer) code le type de chaque pixel. On écarte :
#   3 = ombre de nuage, 8 = nuage probabilité moyenne,
#   9 = nuage probabilité haute, 10 = cirrus fin, 11 = neige.
EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B03", "B04", "B08", "SCL", "dataMask"] }],
    output: [
      { id: "ndvi", bands: 1, sampleType: "FLOAT32" },
      { id: "ndwi", bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1 }
    ]
  };
}

function evaluatePixel(s) {
  var ndvi = (s.B08 + s.B04) === 0 ? 0 : (s.B08 - s.B04) / (s.B08 + s.B04);
  var ndwi = (s.B03 + s.B08) === 0 ? 0 : (s.B03 - s.B08) / (s.B03 + s.B08);
  var nuageux = (s.SCL === 3 || s.SCL === 8 || s.SCL === 9 || s.SCL === 10 || s.SCL === 11);
  return {
    ndvi: [ndvi],
    ndwi: [ndwi],
    dataMask: [nuageux ? 0 : s.dataMask]
  };
}
"""


class CopernicusSatelliteProvider(SatelliteProvider):
    """Interroge Copernicus Data Space via l'API statistique Sentinel Hub."""

    def __init__(self) -> None:
        if not (settings.copernicus_client_id and settings.copernicus_client_secret):
            raise FeatureDisabledError(
                "Identifiants Copernicus absents : renseigner COPERNICUS_CLIENT_ID "
                "et COPERNICUS_CLIENT_SECRET."
            )
        self.base_url = settings.copernicus_base_url.rstrip("/")
        self._cache = TtlCache(ttl_seconds=6 * 3600)
        self._token: str | None = None
        self._token_expiry: float = 0.0

    @property
    def label_fr(self) -> str:
        return "Copernicus Data Space (Sentinel-2)"

    # --- authentification ---

    async def _access_token(self) -> str:
        """Jeton OAuth2, renouvelé avant expiration.

        Les jetons Copernicus sont de courte durée. On les conserve en mémoire
        plutôt que d'en demander un à chaque appel : une authentification par
        requête multiplierait la latence et la charge sur le service d'identité.
        """
        if self._token and time.monotonic() < self._token_expiry:
            return self._token

        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            try:
                reponse = await client.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": settings.copernicus_client_id,
                        "client_secret": settings.copernicus_client_secret,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            except httpx.RequestError as exc:
                raise ProviderUnavailableError(
                    "Service d'identité Copernicus injoignable."
                ) from exc

        if reponse.status_code == 401:
            raise ProviderUnavailableError(
                "Identifiants Copernicus refusés. Vérifier COPERNICUS_CLIENT_ID et "
                "COPERNICUS_CLIENT_SECRET dans le tableau de bord Copernicus."
            )
        if reponse.status_code >= 400:
            # Le corps peut contenir des éléments d'identification : on ne le
            # propage pas, on le journalise.
            logger.error(
                "Échec d'authentification Copernicus",
                context={"statut": reponse.status_code},
            )
            raise ProviderUnavailableError(
                f"Authentification Copernicus en échec (statut {reponse.status_code})."
            )

        corps = reponse.json()
        jeton = corps.get("access_token")
        if not jeton:
            raise ProviderUnavailableError(
                "Copernicus n'a pas renvoyé de jeton d'accès exploitable."
            )

        duree = int(corps.get("expires_in", 600))
        self._token = jeton
        self._token_expiry = time.monotonic() + max(
            30, duree - TOKEN_REFRESH_MARGIN_SECONDS
        )
        return jeton

    # --- observation ---

    async def get_vegetation(
        self, coordinates: Coordinates, *, days_back: int = 30
    ) -> VegetationObservation | SatelliteAvailability:
        cle = f"{coordinates.latitude:.3f}:{coordinates.longitude:.3f}:{days_back}"
        if (cache := self._cache.get(cle)) is not None:
            return cache

        fin = date.today()
        debut = fin - timedelta(days=max(5, days_back))

        try:
            payload = await self._statistics(coordinates, debut, fin)
        except ProviderUnavailableError as exc:
            return SatelliteAvailability(
                available=False,
                reason_fr=f"Copernicus indisponible : {exc.message_fr}",
                remediation_fr="Réessayer plus tard ou vérifier la configuration.",
            )

        resultat = _latest_usable(payload)
        if resultat is None:
            indisponible = SatelliteAvailability(
                available=False,
                reason_fr=(
                    f"Aucune acquisition Sentinel-2 exploitable sur les "
                    f"{days_back} derniers jours pour cette zone : couverture "
                    "nuageuse trop importante ou absence de passage satellite. "
                    "Aucune valeur n'est estimée à la place."
                ),
                remediation_fr=(
                    "Élargir la fenêtre temporelle, ou attendre la prochaine "
                    "acquisition dégagée. Sentinel-2 repasse tous les cinq jours."
                ),
            )
            # Mise en cache courte : inutile de réinterroger toutes les minutes
            # une zone durablement couverte.
            self._cache.set(cle, indisponible)
            return indisponible

        observation = VegetationObservation(
            acquired_on=resultat["date"],
            ndvi=round(resultat["ndvi"], 3),
            ndwi=round(resultat["ndwi"], 3),
            masked_share_percent=round((1 - resultat["valid_fraction"]) * 100, 1),
            source=SOURCE_COPERNICUS,
            retrieved_at=datetime.now(UTC),
        )
        self._cache.set(cle, observation)
        return observation

    async def _statistics(
        self, coordinates: Coordinates, debut: date, fin: date
    ) -> dict[str, Any]:
        jeton = await self._access_token()
        demi = AOI_HALF_SIZE_DEGREES

        corps = {
            "input": {
                "bounds": {
                    "bbox": [
                        coordinates.longitude - demi,
                        coordinates.latitude - demi,
                        coordinates.longitude + demi,
                        coordinates.latitude + demi,
                    ],
                    "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
                },
                "data": [
                    {
                        "type": "sentinel-2-l2a",
                        "dataFilter": {"mosaickingOrder": "leastCC"},
                    }
                ],
            },
            "aggregation": {
                "timeRange": {
                    "from": f"{debut.isoformat()}T00:00:00Z",
                    "to": f"{fin.isoformat()}T23:59:59Z",
                },
                # Une agrégation par jour laisse chaque passage satellite
                # distinct : moyenner sur dix jours mélangerait une acquisition
                # dégagée avec une acquisition nuageuse.
                "aggregationInterval": {"of": "P1D"},
                "evalscript": EVALSCRIPT,
                "resx": 20,
                "resy": 20,
            },
        }

        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds * 3) as client:
            try:
                reponse = await client.post(
                    f"{self.base_url}/api/v1/statistics",
                    json=corps,
                    headers={
                        "Authorization": f"Bearer {jeton}",
                        "Content-Type": "application/json",
                    },
                )
            except httpx.RequestError as exc:
                raise ProviderUnavailableError(
                    "API statistique Copernicus injoignable."
                ) from exc

        if reponse.status_code == 401:
            # Jeton périmé plus tôt qu'annoncé : on le jette et on abandonne
            # proprement plutôt que de boucler sur un renouvellement.
            self._token = None
            self._token_expiry = 0.0
            raise ProviderUnavailableError(
                "Jeton Copernicus refusé. Nouvelle tentative au prochain appel."
            )
        if reponse.status_code >= 400:
            logger.error(
                "Requête statistique Copernicus rejetée",
                context={"statut": reponse.status_code, "corps": reponse.text[:400]},
            )
            raise ProviderUnavailableError(
                f"Copernicus a rejeté la requête (statut {reponse.status_code})."
            )

        return reponse.json()

    async def health(self) -> dict[str, Any]:
        try:
            await self._access_token()
        except (ProviderUnavailableError, FeatureDisabledError) as exc:
            return {
                "fournisseur": self.label_fr,
                "disponible": False,
                "message_fr": str(getattr(exc, "message_fr", exc)),
            }
        return {
            "fournisseur": self.label_fr,
            "disponible": True,
            "message_fr": (
                "Authentification établie. Les indices de végétation sont calculés "
                "sur acquisitions Sentinel-2, pixels nuageux exclus."
            ),
        }


def _latest_usable(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Acquisition la plus récente dont la couverture valide est suffisante.

    Parcourt les intervalles du plus récent au plus ancien et retient le premier
    exploitable. Une moyenne sur toutes les acquisitions mélangerait des dates
    et des conditions différentes : ce qui intéresse l'exploitant, c'est l'état
    de la parcelle maintenant, pas sa moyenne du mois.
    """
    intervalles = payload.get("data") or []

    for intervalle in sorted(
        intervalles, key=lambda i: i.get("interval", {}).get("from", ""), reverse=True
    ):
        sorties = intervalle.get("outputs") or {}
        ndvi_stats = _stats(sorties, "ndvi")
        ndwi_stats = _stats(sorties, "ndwi")
        if ndvi_stats is None or ndwi_stats is None:
            continue

        echantillons = ndvi_stats.get("sampleCount") or 0
        sans_donnee = ndvi_stats.get("noDataCount") or 0
        if echantillons <= 0:
            continue

        part_valide = (echantillons - sans_donnee) / echantillons
        if part_valide < MINIMUM_VALID_FRACTION:
            continue

        moyenne_ndvi = ndvi_stats.get("mean")
        moyenne_ndwi = ndwi_stats.get("mean")
        if moyenne_ndvi is None or moyenne_ndwi is None:
            continue

        brut = intervalle.get("interval", {}).get("from", "")
        try:
            acquise_le = datetime.fromisoformat(brut.replace("Z", "+00:00")).date()
        except ValueError:
            continue

        return {
            "date": acquise_le,
            "ndvi": float(moyenne_ndvi),
            "ndwi": float(moyenne_ndwi),
            "valid_fraction": part_valide,
        }

    return None


def _stats(sorties: dict[str, Any], nom: str) -> dict[str, Any] | None:
    """Extrait le bloc de statistiques d'une sortie de l'évalscript."""
    bandes = (sorties.get(nom) or {}).get("bands") or {}
    for bande in bandes.values():
        stats = bande.get("stats")
        if isinstance(stats, dict):
            return stats
    return None
