"""Seuils de risque agro-climatique et leur calibration.

Méthode reprise de l'article de référence : les catégories de risque sont
définies par **quantiles de la distribution historique locale** (33ᵉ et 67ᵉ
percentiles par défaut), et non par des valeurs absolues importées.

C'est le seul moyen honnête de transférer la méthode colombienne au Maroc :
on transfère le procédé, jamais les nombres. Un déficit pluviométrique de
44 mm/48 h qui signale une sécheresse sévère en zone andine n'a aucune
signification transposable dans le Souss semi-aride.

Chaque seuil conserve sa provenance : d'où il vient, sur quelle fenêtre il a été
calibré, avec combien d'observations. Un seuil sans provenance n'est pas
utilisable dans une décision auditable.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import RiskLevel, RiskType

ThresholdOrigin = Literal["calibrated", "agronomic_literature", "provisional_expert"]

Direction = Literal["above", "below"]


class RiskThreshold(BaseModel):
    """Seuil à trois bornes définissant Faible / Modéré / Élevé pour une variable.

    `direction` indique le sens du danger : « above » pour les excès
    (précipitations, température), « below » pour les déficits (humidité du sol).
    """

    model_config = ConfigDict(frozen=True)

    risk_type: RiskType
    variable: str = Field(description="Variable évaluée, ex. 'precipitation_48h_mm'")
    unit: str
    direction: Direction = "above"

    moderate_at: float = Field(description="Borne d'entrée en risque modéré")
    high_at: float = Field(description="Borne d'entrée en risque élevé")
    critical_at: float | None = Field(
        default=None,
        description="Borne d'entrée en risque critique. Absente si non documentée.",
    )

    # --- Provenance (obligatoire pour l'auditabilité) ---
    origin: ThresholdOrigin
    source_label_fr: str
    region_code: str | None = None
    crop_code: str | None = None
    calibration_window: str | None = Field(
        default=None, description="Fenêtre historique utilisée, ex. '2015-2024'"
    )
    calibration_sample_size: int | None = None
    calibrated_on: date | None = None
    quantiles_used: tuple[float, float] | None = None

    @model_validator(mode="after")
    def _check_monotonic(self) -> RiskThreshold:
        bounds = [self.moderate_at, self.high_at]
        if self.critical_at is not None:
            bounds.append(self.critical_at)

        ordered = bounds == sorted(bounds) if self.direction == "above" else bounds == sorted(
            bounds, reverse=True
        )
        if not ordered:
            raise ValueError(
                f"Seuils incohérents pour {self.variable} (direction={self.direction}) : "
                f"{bounds}. Les bornes doivent progresser vers le danger."
            )
        return self

    def evaluate(self, value: float) -> RiskLevel:
        """Classe une valeur mesurée dans un niveau de risque."""
        crossed = (
            (lambda bound: value >= bound)
            if self.direction == "above"
            else (lambda bound: value <= bound)
        )
        if self.critical_at is not None and crossed(self.critical_at):
            return RiskLevel.CRITICAL
        if crossed(self.high_at):
            return RiskLevel.HIGH
        if crossed(self.moderate_at):
            return RiskLevel.MODERATE
        return RiskLevel.LOW

    def normalized_severity(self, value: float) -> float:
        """Sévérité continue dans [0,1], pour combiner plusieurs facteurs.

        Interpolation linéaire entre les bornes. Au-delà de la borne la plus
        haute, la sévérité sature à 1.0 : on ne prétend pas distinguer un
        dépassement massif d'un dépassement extrême sans données pour le faire.
        """
        top = self.critical_at if self.critical_at is not None else self.high_at
        if self.direction == "above":
            if value <= self.moderate_at:
                span = self.moderate_at if self.moderate_at > 0 else 1.0
                return max(0.0, min(0.25, 0.25 * value / span))
            if value >= top:
                return 1.0
            return _interp(value, self.moderate_at, top, 0.25, 1.0)

        if value >= self.moderate_at:
            return 0.0 if self.moderate_at == 0 else max(0.0, min(0.25, 0.25))
        if value <= top:
            return 1.0
        return _interp(value, self.moderate_at, top, 0.25, 1.0)

    @property
    def provenance_fr(self) -> str:
        """Phrase affichable expliquant d'où vient ce seuil."""
        if self.origin == "calibrated" and self.quantiles_used:
            q_low, q_high = self.quantiles_used
            sample = self.calibration_sample_size or 0
            return (
                f"Seuil calibré sur la distribution historique locale "
                f"(percentiles {q_low:.0%} / {q_high:.0%}, {sample} observations, "
                f"fenêtre {self.calibration_window}). Source : {self.source_label_fr}."
            )
        if self.origin == "agronomic_literature":
            return f"Seuil issu de la littérature agronomique. Source : {self.source_label_fr}."
        return (
            f"Seuil provisoire à valider par un agronome. Source : {self.source_label_fr}. "
            f"À ne pas considérer comme une référence agronomique établie."
        )


def _interp(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    if x1 == x0:
        return y1
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


class QuantileThresholdCalibrator:
    """Dérive des seuils depuis une distribution historique locale.

    Implémente la méthode de l'article : bornes aux 33ᵉ et 67ᵉ percentiles de la
    distribution empirique, ce qui produit des catégories équilibrées et
    reproductibles pour n'importe quelle région.

    Le calibrateur **refuse** de produire un seuil sur un échantillon trop petit
    plutôt que de renvoyer une valeur peu fiable : un seuil calibré sur douze
    observations donnerait une fausse impression de rigueur.
    """

    MIN_SAMPLE_SIZE = 60

    def __init__(
        self,
        low_quantile: float = 0.33,
        high_quantile: float = 0.67,
        critical_quantile: float | None = 0.90,
    ) -> None:
        if not 0.0 < low_quantile < high_quantile < 1.0:
            raise ValueError("Quantiles invalides : attendu 0 < bas < haut < 1")
        self.low_quantile = low_quantile
        self.high_quantile = high_quantile
        self.critical_quantile = critical_quantile

    def calibrate(
        self,
        *,
        observations: list[float],
        risk_type: RiskType,
        variable: str,
        unit: str,
        source_label_fr: str,
        direction: Direction = "above",
        region_code: str | None = None,
        crop_code: str | None = None,
        calibration_window: str | None = None,
    ) -> RiskThreshold:
        usable = [v for v in observations if v is not None and not _is_nan(v)]
        if len(usable) < self.MIN_SAMPLE_SIZE:
            raise InsufficientHistoryError(
                f"Calibration impossible pour {variable} : {len(usable)} observations "
                f"exploitables, minimum requis {self.MIN_SAMPLE_SIZE}. "
                f"Un seuil calibré sur un échantillon insuffisant serait trompeur."
            )

        usable = _remove_outliers_iqr(usable)

        if direction == "above":
            q_lo, q_hi, q_crit = self.low_quantile, self.high_quantile, self.critical_quantile
        else:
            # Pour un déficit, le danger est dans la queue basse : on inverse.
            q_lo = 1.0 - self.low_quantile
            q_hi = 1.0 - self.high_quantile
            q_crit = 1.0 - self.critical_quantile if self.critical_quantile else None

        return RiskThreshold(
            risk_type=risk_type,
            variable=variable,
            unit=unit,
            direction=direction,
            moderate_at=quantile(usable, q_lo),
            high_at=quantile(usable, q_hi),
            critical_at=quantile(usable, q_crit) if q_crit is not None else None,
            origin="calibrated",
            source_label_fr=source_label_fr,
            region_code=region_code,
            crop_code=crop_code,
            calibration_window=calibration_window,
            calibration_sample_size=len(usable),
            calibrated_on=date.today(),
            quantiles_used=(self.low_quantile, self.high_quantile),
        )


class InsufficientHistoryError(ValueError):
    """Historique insuffisant pour produire un seuil défendable."""


def quantile(values: list[float], q: float) -> float:
    """Quantile par interpolation linéaire (méthode 7, celle de numpy/R par défaut)."""
    if not values:
        raise ValueError("Quantile indéfini sur une série vide")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * max(0.0, min(1.0, q))
    lower = int(pos)
    upper = min(lower + 1, len(ordered) - 1)
    weight = pos - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _remove_outliers_iqr(values: list[float], factor: float = 3.0) -> list[float]:
    """Écarte les aberrations par écart interquartile.

    Le facteur 3.0 (au lieu du 1.5 usuel) est délibéré : en météorologie, les
    événements extrêmes sont précisément ce que l'on cherche à détecter. Un
    filtre agressif supprimerait le signal utile. On ne retire ici que les
    valeurs manifestement erronées.
    """
    if len(values) < 4:
        return values
    q1, q3 = quantile(values, 0.25), quantile(values, 0.75)
    iqr = q3 - q1
    if iqr == 0:
        return values
    lo, hi = q1 - factor * iqr, q3 + factor * iqr
    kept = [v for v in values if lo <= v <= hi]
    return kept if len(kept) >= max(4, len(values) // 2) else values


def _is_nan(v: float) -> bool:
    return v != v
