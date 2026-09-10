"""Erreurs applicatives.

Chaque erreur porte un message **en français destiné à l'utilisateur** et un
code stable exploitable par le frontend. Les détails techniques restent dans les
journaux : ils ne remontent jamais dans une réponse HTTP, pour ne pas exposer la
structure interne du système.
"""

from __future__ import annotations


class AtlasAgriError(Exception):
    """Erreur métier. Toutes les erreurs applicatives en héritent."""

    code = "erreur_interne"
    http_status = 500
    message_fr = "Une erreur interne est survenue."

    def __init__(self, message_fr: str | None = None, *, detail: str | None = None) -> None:
        self.message_fr = message_fr or self.message_fr
        self.detail = detail
        super().__init__(self.message_fr)


class NotFoundError(AtlasAgriError):
    code = "introuvable"
    http_status = 404
    message_fr = "La ressource demandée est introuvable."


class ValidationError(AtlasAgriError):
    code = "donnees_invalides"
    http_status = 422
    message_fr = "Les données fournies sont invalides."


class AuthenticationError(AtlasAgriError):
    code = "authentification_requise"
    http_status = 401
    message_fr = "Authentification requise."


class AuthorizationError(AtlasAgriError):
    code = "acces_refuse"
    http_status = 403
    message_fr = "Vous n'avez pas les droits nécessaires pour cette action."


class TenantIsolationError(AtlasAgriError):
    """Tentative d'accès à une ressource appartenant à une autre organisation.

    Traitée comme un 404 côté client : confirmer l'existence d'une ressource
    d'un autre tenant serait déjà une fuite d'information.
    """

    code = "introuvable"
    http_status = 404
    message_fr = "La ressource demandée est introuvable."


class ProviderUnavailableError(AtlasAgriError):
    """Un fournisseur externe est indisponible.

    Le produit affiche l'indisponibilité honnêtement plutôt que de substituer
    une valeur fabriquée.
    """

    code = "fournisseur_indisponible"
    http_status = 503
    message_fr = "Une source de données externe est momentanément indisponible."


class FeatureDisabledError(AtlasAgriError):
    """Fonctionnalité non configurée (agent sans clé, satellite sans identifiants)."""

    code = "fonctionnalite_desactivee"
    http_status = 503
    message_fr = "Cette fonctionnalité n'est pas configurée sur cette installation."


class RateLimitError(AtlasAgriError):
    code = "trop_de_requetes"
    http_status = 429
    message_fr = "Trop de requêtes. Merci de réessayer dans un instant."


class InsufficientDataError(AtlasAgriError):
    """Données insuffisantes pour produire un résultat défendable.

    Utilisée quand renvoyer un résultat serait trompeur : mieux vaut dire
    « je ne sais pas » qu'afficher un chiffre non fondé.
    """

    code = "donnees_insuffisantes"
    http_status = 422
    message_fr = "Les données disponibles sont insuffisantes pour produire ce résultat."
