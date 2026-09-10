"""Instrument de collecte structurée.

Reprend le patron de l'article d'Ahmadi et al. : un questionnaire est une suite
d'**objets question** typés, portant leur propre validation et leur condition
d'affichage. L'administration est conversationnelle et *stateful* — chaque
réponse détermine la question suivante — mais la logique de branchement reste
**entièrement déterministe**.

Ce point est délibéré. L'article définit un agent comme un composant qui « peut
combiner un LLM avec des règles déterministes » ; il n'impose pas que le LLM
pilote l'enchaînement. Ici il ne le pilote pas :

1. Un instrument de mesure doit produire la même séquence pour la même
   situation. Un enchaînement décidé par un modèle rendrait les réponses non
   comparables entre deux expéditions, ce qui ruinerait leur usage statistique.
2. La collecte fonctionne sans clé d'API. Le retour terrain est la donnée la
   plus précieuse du produit : la subordonner à la disponibilité d'un service
   externe serait un mauvais arbitrage.

Le modèle de langage garde un rôle utile mais accessoire : reformuler une
question mal comprise, ou coder une réponse libre en catégorie. Jamais décider
quelle question vient ensuite.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.errors import ValidationError

AnswerKind = Literal["boolean", "choice", "number", "text"]


@dataclass(frozen=True)
class Option:
    """Modalité d'une question fermée."""

    code: str
    label_fr: str


@dataclass(frozen=True)
class Question:
    """Objet question.

    `depends_on` porte la condition d'affichage : une fonction des réponses
    déjà collectées. C'est ce qui rend l'instrument adaptatif sans le rendre
    imprévisible.
    """

    code: str
    text_fr: str
    kind: AnswerKind
    help_fr: str = ""
    options: tuple[Option, ...] = ()
    required: bool = True
    minimum: float | None = None
    maximum: float | None = None
    max_length: int = 500
    depends_on: Callable[[dict[str, Any]], bool] | None = field(default=None, compare=False)

    def applies_to(self, answers: dict[str, Any]) -> bool:
        return self.depends_on is None or self.depends_on(answers)

    def validate(self, raw: Any) -> Any:
        """Valide et normalise une réponse.

        Échoue avec un message exploitable plutôt que d'accepter une valeur
        approximative : une donnée de terrain douteuse contamine ensuite toute
        la calibration qui s'appuiera dessus.
        """
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if self.required:
                raise ValidationError(f"« {self.text_fr} » : une réponse est attendue.")
            return None

        if self.kind == "boolean":
            if isinstance(raw, bool):
                return raw
            texte = str(raw).strip().lower()
            if texte in {"oui", "true", "1", "o", "yes"}:
                return True
            if texte in {"non", "false", "0", "n", "no"}:
                return False
            raise ValidationError(f"« {self.text_fr} » : répondre par oui ou non.")

        if self.kind == "choice":
            codes = {o.code for o in self.options}
            valeur = str(raw).strip().upper()
            if valeur not in codes:
                libelles = ", ".join(f"{o.code} ({o.label_fr})" for o in self.options)
                raise ValidationError(
                    f"« {self.text_fr} » : réponse hors liste. Valeurs possibles : {libelles}."
                )
            return valeur

        if self.kind == "number":
            try:
                valeur = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    f"« {self.text_fr} » : une valeur numérique est attendue."
                ) from exc
            if self.minimum is not None and valeur < self.minimum:
                raise ValidationError(
                    f"« {self.text_fr} » : la valeur doit être au moins {self.minimum}."
                )
            if self.maximum is not None and valeur > self.maximum:
                raise ValidationError(
                    f"« {self.text_fr} » : la valeur doit être au plus {self.maximum}."
                )
            return valeur

        texte = str(raw).strip()
        return texte[: self.max_length]

    def as_payload(self) -> dict[str, Any]:
        """Représentation destinée à l'interface et aux outils."""
        return {
            "code": self.code,
            "texte_fr": self.text_fr,
            "aide_fr": self.help_fr,
            "type": self.kind,
            "obligatoire": self.required,
            "options": [{"code": o.code, "libelle_fr": o.label_fr} for o in self.options],
            "minimum": self.minimum,
            "maximum": self.maximum,
        }


# ---------------------------------------------------------------------------
# Instrument : que s'est-il réellement passé sur cette expédition ?
# ---------------------------------------------------------------------------

TYPES_PERTURBATION = (
    Option("PLUIE", "Fortes pluies ou visibilité réduite"),
    Option("INONDATION", "Chaussée inondée ou coupée"),
    Option("VENT", "Vent fort"),
    Option("NEIGE_GEL", "Neige ou verglas"),
    Option("TRAFIC", "Congestion ou accident"),
    Option("VEHICULE", "Panne ou incident véhicule"),
    Option("CHARGEMENT", "Retard au chargement ou au déchargement"),
    Option("CONTROLE", "Contrôle routier ou administratif"),
    Option("AUTRE", "Autre cause"),
)

MOTIFS_NON_LIVRAISON = (
    Option("ANNULEE", "Expédition annulée"),
    Option("REPORTEE", "Expédition reportée"),
    Option("EN_COURS", "Toujours en cours"),
    Option("REFUSEE", "Refusée à la livraison"),
)


def _a_ete_livree(reponses: dict[str, Any]) -> bool:
    return reponses.get("livree") is True


def _perturbation_signalee(reponses: dict[str, Any]) -> bool:
    return _a_ete_livree(reponses) and reponses.get("perturbation") is True


SHIPMENT_OUTCOME_SURVEY: tuple[Question, ...] = (
    Question(
        code="livree",
        text_fr="L'expédition a-t-elle été livrée ?",
        kind="boolean",
        help_fr="Répondre non si elle a été annulée, reportée, ou si elle roule encore.",
    ),
    Question(
        code="motif_non_livraison",
        text_fr="Pourquoi n'a-t-elle pas été livrée ?",
        kind="choice",
        options=MOTIFS_NON_LIVRAISON,
        depends_on=lambda r: r.get("livree") is False,
    ),
    Question(
        code="ecart_arrivee_h",
        text_fr="De combien d'heures l'arrivée s'écarte-t-elle de l'heure prévue ?",
        kind="number",
        help_fr=(
            "Une valeur négative signifie une arrivée en avance. "
            "0 si l'arrivée était à l'heure."
        ),
        minimum=-48,
        maximum=336,
        depends_on=_a_ete_livree,
    ),
    Question(
        code="itineraire_suivi",
        text_fr="L'itinéraire prévu a-t-il été suivi ?",
        kind="boolean",
        depends_on=_a_ete_livree,
    ),
    Question(
        code="itineraire_reel_fr",
        text_fr="Quel itinéraire a été emprunté à la place ?",
        kind="text",
        help_fr="Indiquer les villes traversées, par exemple « par Essaouira et Safi ».",
        required=False,
        depends_on=lambda r: _a_ete_livree(r) and r.get("itineraire_suivi") is False,
    ),
    Question(
        code="perturbation",
        text_fr="Une perturbation a-t-elle affecté le trajet ?",
        kind="boolean",
        help_fr="Toute cause ayant ralenti, dévié ou compliqué le transport.",
        depends_on=_a_ete_livree,
    ),
    Question(
        code="type_perturbation",
        text_fr="Quelle en était la cause principale ?",
        kind="choice",
        options=TYPES_PERTURBATION,
        depends_on=_perturbation_signalee,
    ),
    Question(
        code="lieu_perturbation_fr",
        text_fr="Où la perturbation s'est-elle produite ?",
        kind="text",
        help_fr="Le tronçon ou la ville la plus proche.",
        required=False,
        depends_on=_perturbation_signalee,
    ),
    Question(
        code="retard_perturbation_h",
        text_fr="Combien d'heures de retard cette perturbation a-t-elle causées ?",
        kind="number",
        minimum=0,
        maximum=336,
        depends_on=_perturbation_signalee,
    ),
    Question(
        code="qualite_affectee",
        text_fr="La qualité de la marchandise a-t-elle été affectée ?",
        kind="boolean",
        depends_on=_a_ete_livree,
    ),
    Question(
        code="perte_estimee_mad",
        text_fr="Quelle perte estimez-vous, en dirhams ?",
        kind="number",
        help_fr=(
            "Marchandise déclassée, pénalité client, surcoût de transport. "
            "Une estimation suffit."
        ),
        minimum=0,
        maximum=100_000_000,
        depends_on=lambda r: _a_ete_livree(r) and r.get("qualite_affectee") is True,
    ),
    Question(
        code="commentaire_fr",
        text_fr="Souhaitez-vous ajouter une précision ?",
        kind="text",
        required=False,
        max_length=1000,
        depends_on=lambda r: r.get("livree") is not None,
    ),
)


class SurveyInstrument:
    """Administre un questionnaire en tenant l'état des réponses."""

    def __init__(self, questions: tuple[Question, ...] = SHIPMENT_OUTCOME_SURVEY) -> None:
        self.questions = questions
        self._index = {q.code: q for q in questions}

    def question(self, code: str) -> Question:
        if code not in self._index:
            raise ValidationError(f"Question inconnue : « {code} ».")
        return self._index[code]

    def next_question(self, answers: dict[str, Any]) -> Question | None:
        """Première question applicable non encore répondue, ou None si terminé."""
        for question in self.questions:
            if question.code in answers:
                continue
            if question.applies_to(answers):
                return question
        return None

    def applicable_questions(self, answers: dict[str, Any]) -> tuple[Question, ...]:
        return tuple(q for q in self.questions if q.applies_to(answers))

    def accept(self, answers: dict[str, Any], code: str, raw: Any) -> dict[str, Any]:
        """Valide une réponse et renvoie un **nouveau** dictionnaire.

        Les réponses ne sont jamais mutées sur place : chaque étape produit un
        état distinct, ce qui rend la session rejouable et vérifiable.
        """
        question = self.question(code)
        if not question.applies_to(answers):
            raise ValidationError(
                f"La question « {question.text_fr} » ne s'applique pas aux réponses déjà données."
            )

        valeur = question.validate(raw)
        suivant = dict(answers)
        suivant[code] = valeur

        # Une réponse qui invalide une branche efface les réponses devenues
        # hors sujet. Les conserver produirait un enregistrement incohérent —
        # par exemple une cause de perturbation alors qu'aucune n'est signalée.
        for autre in self.questions:
            if autre.code in suivant and autre.code != code and not autre.applies_to(suivant):
                suivant.pop(autre.code)
        return suivant

    def is_complete(self, answers: dict[str, Any]) -> bool:
        return self.next_question(answers) is None

    def progress(self, answers: dict[str, Any]) -> tuple[int, int]:
        """Questions répondues sur questions applicables, pour l'affichage."""
        applicables = self.applicable_questions(answers)
        repondues = sum(1 for q in applicables if q.code in answers)
        return repondues, len(applicables)


SHIPMENT_OUTCOME_INSTRUMENT = SurveyInstrument()
