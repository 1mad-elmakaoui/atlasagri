"""Schémas d'entrée et de sortie de l'API.

Ils constituent le contrat avec le frontend. Les noms de champs sont en
français, comme dans les outils MCP : une seule terminologie du domaine à la
base jusqu'à l'écran.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    utilisateur: dict[str, Any]


class ErrorResponse(BaseModel):
    code: str
    message_fr: str


class AgentQuestion(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    conversation_id: str | None = None


class AgentResponse(BaseModel):
    reponse_fr: str
    conclusion: dict[str, Any] | None
    outils_utilises: list[dict[str, Any]]
    iterations: int
    modele: str
    conversation_id: str | None = None


class DecisionRequest(BaseModel):
    """Acceptation, modification ou rejet d'une recommandation par un humain."""

    decision: str = Field(description="ACCEPTED | REJECTED | MODIFIED")
    note: str | None = Field(default=None, max_length=1000)


class SimulationRequest(BaseModel):
    scenario: str
    shipment_reference: str
    parameter_value: float = Field(default=48.0, ge=0, le=720)
    blocked_node_code: str | None = None


class AlertAcknowledgement(BaseModel):
    acknowledged: bool = True


class MapFilters(BaseModel):
    """Filtres de la carte, appliqués côté serveur.

    Filtrer côté serveur évite d'envoyer l'ensemble des données au navigateur
    puis de les masquer : la donnée non autorisée ne quitte pas le serveur.
    """

    region_code: str | None = None
    product: str | None = None
    minimum_risk: str | None = None
    shipment_reference: str | None = None


class RegionSummary(BaseModel):
    code: str
    nom_fr: str
    latitude: float
    longitude: float
    profil_agricole_fr: str
    cultures: list[str]
    niveau_risque: str | None = None
    score_risque: float | None = None
