"""Évaluation du risque d'un itinéraire.

Apport central du produit, absent de l'article de référence : l'exposition est
calculée **segment par segment et dans le temps**.

Un itinéraire n'est pas exposé parce qu'il traverse une région où il pleuvra
dans 30 heures. Il est exposé si le véhicule se trouve dans la zone **pendant**
la fenêtre de perturbation. Un camion qui franchit le col dans 2 h ne rencontre
pas l'orage prévu pour dans 30 h.

Sans cette dimension temporelle, le système surestimerait massivement le risque,
déclencherait des alertes sur des trajets déjà terminés, et l'utilisateur
cesserait de le croire au bout de quelques jours.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from app.core.logging import get_logger
from app.domain.enums import Confidence, DataState, RiskLevel, TransportMode
from app.domain.geo import Coordinates, interpolate
from app.domain.provenance import SOURCE_RISK_ENGINE, DataSourceRef
from app.domain.road_risk import (
    FROST,
    RAIN_ACCUMULATION,
    RAIN_INTENSITY,
    WIND_GUST,
    vulnerability_of,
)
from app.domain.transport import CostBreakdown, estimate_cost
from app.providers.registry import get_weather_provider
from app.providers.routing.interface import RouteGeometry
from app.providers.weather.interface import WeatherSeries

logger = get_logger(__name__)

# Ralentissement du trafic selon la sévérité des conditions. Valeurs
# volontairement prudentes : une pluie forte ralentit réellement un poids lourd,
# mais surestimer ce ralentissement fabriquerait de faux dépassements de SLA.
MAX_SPEED_REDUCTION = 0.35

# Grille d'échantillonnage météo. Interroger le fournisseur à chaque point du
# tracé serait coûteux et inutile : les modèles météo ont eux-mêmes une
# résolution de l'ordre du quart de degré.
SAMPLING_GRID_DEGREES = 0.25


class SegmentExposure(BaseModel):
    """Exposition d'un tronçon, sur la fenêtre où le véhicule l'emprunte."""

    model_config = ConfigDict(frozen=True)

    index: int
    from_name_fr: str
    to_name_fr: str
    from_coordinates: tuple[float, float]
    to_coordinates: tuple[float, float]
    road_ref: str
    distance_km: float

    entry_at: datetime
    exit_at: datetime
    hours_from_departure: float

    rain_intensity_mm_h: float | None = Field(
        default=None, description="Intensité moyenne pendant la traversée"
    )
    antecedent_rain_24h_mm: float | None = Field(
        default=None, description="Cumul des 24 h précédant le passage (saturation des sols)"
    )
    max_wind_gust_kmh: float | None = None
    min_temperature_c: float | None = None

    level: RiskLevel
    severity: float = Field(ge=0, le=1)
    reasons_fr: tuple[str, ...]

    @property
    def is_exposed(self) -> bool:
        return self.level.rank >= RiskLevel.MODERATE.rank


class RouteAssessment(BaseModel):
    """Évaluation complète d'un itinéraire pour une expédition donnée."""

    model_config = ConfigDict(frozen=True)

    route_id: str
    label_fr: str
    node_codes: tuple[str, ...]
    coordinates_path: list[tuple[float, float]]

    distance_km: float
    nominal_duration_hours: float
    adjusted_duration_hours: float = Field(
        description="Durée majorée du ralentissement attendu — valeur estimée, non observée"
    )
    departure_at: datetime
    estimated_arrival_at: datetime

    risk_score: float = Field(ge=0, le=1)
    risk_level: RiskLevel
    disruption_probability: float = Field(
        ge=0, le=1,
        description=(
            "Indicateur dérivé de règles explicites, destiné à comparer des "
            "itinéraires entre eux. Non calibré sur un historique d'incidents."
        ),
    )
    reliability: float

    cost_mad: float
    cost_breakdown: list[dict[str, float | str]]
    trucks_required: int

    sla_deadline_at: datetime | None
    sla_compliant: bool
    sla_margin_hours: float | None

    segments: tuple[SegmentExposure, ...]
    exposed_segment_indexes: tuple[int, ...]
    main_risk_factors_fr: tuple[str, ...]

    confidence: Confidence
    data_state: DataState
    sources: tuple[DataSourceRef, ...]
    computed_at: datetime

    @property
    def exposed_distance_km(self) -> float:
        return round(sum(s.distance_km for s in self.segments if s.is_exposed), 1)

    @property
    def exposure_fraction(self) -> float:
        """Part du trajet réellement exposée — plus parlant qu'un score abstrait."""
        if self.distance_km == 0:
            return 0.0
        return round(self.exposed_distance_km / self.distance_km, 3)


class RouteRiskService:
    """Calcule l'exposition d'un itinéraire aux conditions attendues."""

    async def assess(
        self,
        route: RouteGeometry,
        *,
        departure_at: datetime,
        volume_tonnes: float,
        transport_mode: TransportMode,
        sla_deadline_at: datetime | None = None,
    ) -> RouteAssessment:
        weather_by_cell = await self._sample_weather(route)

        segments: list[SegmentExposure] = []
        cumulative_hours = 0.0

        for index, segment in enumerate(route.segments):
            entry_at = departure_at + timedelta(hours=cumulative_hours)
            exit_at = entry_at + timedelta(hours=segment.duration_hours)

            midpoint = interpolate(
                Coordinates(*segment.from_coordinates), Coordinates(*segment.to_coordinates), 0.5
            )
            series = weather_by_cell.get(_cell_key(midpoint))

            exposure = self._evaluate_segment(
                index=index,
                segment=segment,
                series=series,
                entry_at=entry_at,
                exit_at=exit_at,
                hours_from_departure=cumulative_hours,
            )
            segments.append(exposure)

            # Le ralentissement du tronçon affecte l'heure d'arrivée sur les
            # tronçons suivants : il faut cumuler la durée ajustée, pas la durée
            # nominale, sinon la position du véhicule dans le temps est fausse.
            cumulative_hours += segment.duration_hours * (
                1 + MAX_SPEED_REDUCTION * exposure.severity
            )

        return self._build_assessment(
            route=route,
            segments=tuple(segments),
            departure_at=departure_at,
            adjusted_duration_hours=round(cumulative_hours, 2),
            volume_tonnes=volume_tonnes,
            transport_mode=transport_mode,
            sla_deadline_at=sla_deadline_at,
            weather_state=_dominant_state(weather_by_cell),
            weather_source=get_weather_provider().source,
        )

    # --- échantillonnage météo ---

    async def _sample_weather(self, route: RouteGeometry) -> dict[str, WeatherSeries]:
        provider = get_weather_provider()
        cells: dict[str, Coordinates] = {}

        for segment in route.segments:
            midpoint = interpolate(
                Coordinates(*segment.from_coordinates), Coordinates(*segment.to_coordinates), 0.5
            )
            cells.setdefault(_cell_key(midpoint), midpoint)

        results: dict[str, WeatherSeries] = {}
        for key, point in cells.items():
            try:
                results[key] = await provider.get_forecast(point, hours_ahead=48)
            except Exception as exc:  # noqa: BLE001 - dégradation contrôlée
                # Un tronçon sans météo est signalé comme non évalué, jamais
                # comme sûr : l'absence de donnée n'est pas une absence de risque.
                logger.warning(
                    "Météo indisponible pour un tronçon",
                    context={"cellule": key, "erreur": str(exc)},
                )
        return results

    # --- évaluation d'un tronçon ---

    def _evaluate_segment(
        self,
        *,
        index: int,
        segment,
        series: WeatherSeries | None,
        entry_at: datetime,
        exit_at: datetime,
        hours_from_departure: float,
    ) -> SegmentExposure:
        base = {
            "index": index,
            "from_name_fr": segment.from_name_fr,
            "to_name_fr": segment.to_name_fr,
            "from_coordinates": segment.from_coordinates,
            "to_coordinates": segment.to_coordinates,
            "road_ref": segment.road_ref,
            "distance_km": segment.distance_km,
            "entry_at": entry_at,
            "exit_at": exit_at,
            "hours_from_departure": round(hours_from_departure, 2),
        }

        if series is None:
            return SegmentExposure(
                **base,
                level=RiskLevel.LOW,
                severity=0.0,
                reasons_fr=(
                    "Conditions non évaluées : données météo indisponibles pour ce tronçon.",
                ),
            )

        # Fenêtre élargie d'une demi-heure : un véhicule n'entre pas dans un
        # tronçon à la seconde près.
        window_start = entry_at - timedelta(minutes=30)
        window_end = exit_at + timedelta(minutes=30)
        window_hours = max(0.5, (window_end - window_start).total_seconds() / 3600)

        traversal_rain = series.total_precipitation_mm(window_start, window_end)
        intensity = (traversal_rain / window_hours) if traversal_rain is not None else None
        antecedent = series.total_precipitation_mm(entry_at - timedelta(hours=24), entry_at)
        gust = series.max_of("wind_gust_kmh", window_start, window_end)
        temperature_min = series.min_of("temperature_c", window_start, window_end)

        measured = (
            (RAIN_INTENSITY, intensity),
            (RAIN_ACCUMULATION, antecedent),
            (WIND_GUST, gust),
            (FROST, temperature_min),
        )

        severity = 0.0
        reasons: list[str] = []

        for criterion, value in measured:
            if value is None:
                continue
            if criterion.evaluate(value) is RiskLevel.LOW:
                continue

            severity = max(severity, criterion.severity(value))
            reasons.append(
                f"{criterion.label_fr} : {round(value, 1)} {criterion.unit} "
                f"lors du passage prévu entre {entry_at:%d/%m %H:%M} et {exit_at:%H:%M} "
                f"(seuil de vigilance {criterion.moderate_at} {criterion.unit})."
            )

        if severity > 0:
            # La vulnérabilité de l'axe amplifie ou atténue l'effet : une route
            # régionale de montagne se coupe là où une autoroute drainée tient.
            severity = min(1.0, severity * vulnerability_of(segment.road_class))
            severity = min(1.0, severity * (1 + (1 - segment.reliability)))
            if segment.notes_fr:
                reasons.append(segment.notes_fr + ".")

        return SegmentExposure(
            **base,
            rain_intensity_mm_h=round(intensity, 2) if intensity is not None else None,
            antecedent_rain_24h_mm=round(antecedent, 1) if antecedent is not None else None,
            max_wind_gust_kmh=round(gust, 0) if gust is not None else None,
            min_temperature_c=round(temperature_min, 1) if temperature_min is not None else None,
            level=RiskLevel.from_score(severity),
            severity=round(severity, 3),
            reasons_fr=tuple(reasons),
        )

    # --- agrégation ---

    def _build_assessment(
        self,
        *,
        route: RouteGeometry,
        segments: tuple[SegmentExposure, ...],
        departure_at: datetime,
        adjusted_duration_hours: float,
        volume_tonnes: float,
        transport_mode: TransportMode,
        sla_deadline_at: datetime | None,
        weather_state: DataState,
        weather_source: DataSourceRef,
    ) -> RouteAssessment:
        risk_score = _aggregate_route_risk(segments)
        arrival = departure_at + timedelta(hours=adjusted_duration_hours)

        cost: CostBreakdown = estimate_cost(
            distance_km=route.total_distance_km,
            duration_hours=adjusted_duration_hours,
            volume_tonnes=volume_tonnes,
            mode=transport_mode,
        )

        sla_margin = None
        sla_compliant = True
        if sla_deadline_at is not None:
            sla_margin = round((sla_deadline_at - arrival).total_seconds() / 3600, 2)
            sla_compliant = sla_margin >= 0

        exposed = tuple(s.index for s in segments if s.is_exposed)
        factors = _main_factors(segments)

        return RouteAssessment(
            route_id=route.id,
            label_fr=route.label_fr,
            node_codes=route.node_codes,
            coordinates_path=route.coordinates_path,
            distance_km=route.total_distance_km,
            nominal_duration_hours=route.total_duration_hours,
            adjusted_duration_hours=adjusted_duration_hours,
            departure_at=departure_at,
            estimated_arrival_at=arrival,
            risk_score=risk_score,
            risk_level=RiskLevel.from_score(risk_score),
            disruption_probability=_disruption_probability(risk_score, route.average_reliability),
            reliability=round(route.average_reliability, 3),
            cost_mad=cost.total_mad,
            cost_breakdown=cost.as_lines_fr(),
            trucks_required=cost.trucks_required,
            sla_deadline_at=sla_deadline_at,
            sla_compliant=sla_compliant,
            sla_margin_hours=sla_margin,
            segments=segments,
            exposed_segment_indexes=exposed,
            main_risk_factors_fr=factors,
            confidence=_confidence(segments, weather_state),
            data_state=weather_state,
            sources=(weather_source, SOURCE_RISK_ENGINE),
            computed_at=datetime.now(UTC),
        )


# --- fonctions d'agrégation -------------------------------------------------

def _aggregate_route_risk(segments: tuple[SegmentExposure, ...]) -> float:
    """Risque global d'un itinéraire.

    Pondéré par la distance : un tronçon critique de 5 km ne compromet pas un
    trajet de 600 km autant qu'un tronçon critique de 200 km. Mais une moyenne
    pure diluerait un point de blocage ponctuel jusqu'à l'invisibilité — un col
    coupé arrête le camion quelle que soit sa longueur.

    On combine donc les deux lectures : la moyenne pondérée, et le pire tronçon
    ramené à son poids. Le maximum des deux est retenu.
    """
    if not segments:
        return 0.0

    total_km = sum(s.distance_km for s in segments) or 1.0
    weighted = sum(s.severity * s.distance_km for s in segments) / total_km
    worst = max((s.severity for s in segments), default=0.0)

    # Le pire tronçon compte pour au moins 70 % de sa sévérité, même court.
    return round(min(1.0, max(weighted, worst * 0.7)), 3)


def _disruption_probability(risk_score: float, reliability: float) -> float:
    """Indicateur de perturbation dérivé de règles explicites.

    Ce n'est **pas** une probabilité calibrée sur un historique d'incidents
    marocains : nous n'en disposons pas. C'est un indicateur comparatif, dont
    l'usage légitime est de classer des itinéraires entre eux.

    La documentation et l'interface le disent explicitement plutôt que de
    laisser croire à une probabilité statistique.
    """
    base = risk_score * 0.85
    fragility = (1 - reliability) * 0.5
    return round(min(0.95, base + fragility * risk_score + 0.02), 3)


def _main_factors(segments: tuple[SegmentExposure, ...]) -> tuple[str, ...]:
    """Trois raisons principales, les plus sévères d'abord."""
    ordered = sorted(
        (s for s in segments if s.is_exposed), key=lambda s: s.severity, reverse=True
    )
    factors: list[str] = []
    for segment in ordered:
        for reason in segment.reasons_fr:
            entry = f"{segment.from_name_fr} → {segment.to_name_fr} ({segment.road_ref}) : {reason}"
            if entry not in factors:
                factors.append(entry)
        if len(factors) >= 3:
            break
    return tuple(factors[:3])


def _confidence(segments: tuple[SegmentExposure, ...], state: DataState) -> Confidence:
    if state is DataState.SIMULATED:
        # Une donnée simulée ne peut pas fonder une confiance élevée.
        return Confidence.LOW
    unevaluated = sum(1 for s in segments if s.rain_intensity_mm_h is None)
    if not segments or unevaluated / len(segments) > 0.3:
        return Confidence.LOW
    return Confidence.MEDIUM if unevaluated else Confidence.HIGH


def _dominant_state(weather_by_cell: dict[str, WeatherSeries]) -> DataState:
    for series in weather_by_cell.values():
        future = [h for h in series.hours if h.timestamp >= datetime.now(UTC)]
        if future:
            return future[0].state
    return DataState.FORECAST


def _cell_key(point: Coordinates) -> str:
    lat = round(point.latitude / SAMPLING_GRID_DEGREES) * SAMPLING_GRID_DEGREES
    lon = round(point.longitude / SAMPLING_GRID_DEGREES) * SAMPLING_GRID_DEGREES
    return f"{lat:.2f}:{lon:.2f}"
