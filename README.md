# AtlasAgri Intelligence

**Prévoir. Anticiper. Réacheminer. Décider.**

Plateforme privée de décision pour les chaînes d'approvisionnement agricoles marocaines.

---

## Le problème traité

La question n'est pas « comment construire un assistant conversationnel pour l'agriculture ».
Elle est :

> Comment une entreprise agricole peut-elle anticiper une perturbation et déterminer la
> meilleure action alternative **avant** que cette perturbation ne coûte de l'argent ?

La plupart des outils s'arrêtent à « il y a un risque élevé ». AtlasAgri continue :

> « Il y a un risque élevé. Voici les alternatives. Voici l'effet de chacune. Voici celle
> que nous recommandons. Voici pourquoi. Voici ce qu'elle coûte. »

---

## Chaîne de traitement

```
Donnée → Observation → Prévision → Détection de risque → Analyse d'impact
  → Génération d'alternatives → Évaluation → Optimisation → Explication → Décision humaine
```

Une règle traverse toute l'architecture :

**Les calculs critiques sont déterministes et faits en Python. Claude interprète, compare,
explique et communique. Claude n'invente jamais un itinéraire, un coût, un délai, un stock
ou un score.**

---

## Ce que le produit fait aujourd'hui

| Capacité | État |
|---|---|
| Authentification, rôles, isolation multi-tenant | Opérationnel |
| Météo réelle via Open-Meteo (avec provenance) | Opérationnel |
| Nowcasting 6/12/24/48 h (baseline statistique versionnée) | Opérationnel |
| Risque agro-climatique à seuils configurables et tracés | Opérationnel |
| Exposition d'itinéraire **tronçon par tronçon et dans le temps** | Opérationnel |
| Risque supply chain : couverture, délai, exposition fournisseur | Opérationnel |
| Alternatives multi-classes avec contraintes dures | Opérationnel |
| Optimisation multicritère pondérée par profil produit | Opérationnel |
| Carte opérationnelle couplée à la recommandation | Opérationnel |
| Simulations « et si ? » | Opérationnel |
| Serveur MCP réel (14 outils typés) | Opérationnel |
| Copilote Claude via API réelle | Opérationnel si une clé est configurée |
| Recommandations auditables + décision humaine | Opérationnel |
| Collecte du retour terrain (instrument conversationnel) | Opérationnel |
| Calibration des seuils par quantiles sur historique local | Mécanisme opérationnel, **campagne réelle non exécutée** |
| Score de Brier et courbe de fiabilité | Opérationnel, **sans échantillon suffisant à ce jour** |
| Indicateurs satellite (NDVI/NDWI) via Copernicus | Adaptateur réel, **désactivé sans identifiants** — déclaré indisponible, jamais estimé |

### Ce que le produit ne fait pas

Énoncé explicitement pour éviter toute survente :

- Il ne prédit pas la météo mieux qu'un service météorologique national.
- Il ne fournit pas de guidage routier tour par tour.
- Il ne remplace ni un TMS, ni un ERP, ni un WMS.
- Ses probabilités de perturbation ne sont **pas calibrées** sur un historique d'incidents
  marocains : ce sont des indicateurs comparatifs, utiles pour classer des options entre
  elles, pas des probabilités statistiques.
- Ses seuils agronomiques sont des valeurs de départ **à valider par des agronomes**.
  L'interface le signale sur chaque évaluation concernée.
- Aucune campagne de calibration n'a encore tourné sur l'archive météo réelle,
  et aucun retour terrain n'a été collecté en production : la boucle de
  correction est en place et testée, mais elle n'a pas encore corrigé quoi que
  ce soit.

---

## Démarrage rapide

```bash
# 1. Backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e "backend[dev]"

cp .env.example .env      # puis renseigner au minimum SECRET_KEY

cd backend
uvicorn app.main:app --reload        # http://localhost:8000/api/docs

# 2. Frontend (autre terminal)
cd frontend && npm install && npm run dev    # http://localhost:5173
```

Comptes de démonstration (mot de passe `AtlasAgri2026!`) :

| Compte | Rôle |
|---|---|
| `supply@souss-primeurs.ma` | Responsable supply chain |
| `operations@souss-primeurs.ma` | Responsable opérations |
| `directrice@souss-primeurs.ma` | Direction |
| `analyste@souss-primeurs.ma` | Analyste (lecture seule) |
| `supply@gharb-agro.ma` | Seconde organisation — sert à vérifier l'isolation |

Détail complet : [`docs/setup.md`](docs/setup.md).

---

## Scénario de démonstration

L'expédition **EXP-1842** transporte 180 t de tomate cerise export d'Agadir vers Casablanca.

1. Un épisode pluvieux est attendu sur la section de montagne de l'A7 (Imi n'Tanoute →
   Chichaoua).
2. Le moteur calcule que le camion y sera **pendant** la fenêtre de perturbation — un départ
   plus matinal l'aurait évitée.
3. Douze options sont générées et évaluées : itinéraires, décalages de départ, entrepôts,
   fournisseurs. Quatre sont écartées avec leur motif.
4. La recommandation retenue est d'**avancer le départ de 10 h** : −30 points de risque,
   sans surcoût, échéance respectée avec 28 h 52 de marge.
5. L'itinéraire littoral, alternative apparemment évidente, est **moins bon** : il traverse
   une zone de rafales à 90 km/h.

Ce dernier point est délibéré. Un système qui trouve toujours une réponse évidente n'aide
personne ; celui-ci arbitre entre deux corridors imparfaits, ce qui est la situation réelle
d'un exploitant du Souss.

---

## Architecture

Monolithe modulaire Python (FastAPI) + frontend React. Pas de microservices : ni le volume,
ni la taille d'équipe, ni la stabilité des frontières de domaine ne justifieraient le coût
opérationnel d'un système distribué à ce stade.

```
Frontend React ─→ API FastAPI ─→ Services applicatifs ─→ Domaine (pur)
                                          │                    │
                                          ├─→ Registre d'outils métier
                                          │        ├─→ Serveur MCP (stdio)
                                          │        └─→ Agent Claude
                                          └─→ Repositories ─→ PostgreSQL
                                              Adaptateurs ──→ Open-Meteo · routage · satellite
```

Le registre d'outils est **unique** : le serveur MCP et l'agent consomment les mêmes
définitions. Deux définitions parallèles divergeraient en silence, et l'écart ne se
découvrirait qu'en production.

Détail : [`docs/architecture.md`](docs/architecture.md).

---

## Fondement scientifique

La méthode s'appuie sur Silva-Sosa, *Real-Time Climate Risk Assessment for Supply Chain
Resilience: A Data-Driven Nowcasting Framework for Colombian Agriculture* (UNICIENCIA).

Nous en reprenons le processus en trois étapes, les horizons 6/12/24/48 h, et surtout la
**dérivation de seuils par quantiles (33ᵉ/67ᵉ percentiles)** — une méthode reproductible,
qui permet de produire des seuils marocains sans importer les valeurs colombiennes.

Nous n'en reprenons ni les seuils numériques, ni le choix du LSTM par défaut, ni la
validation sur données synthétiques. La couche itinéraires / alternatives / optimisation est
un développement propre et ne provient pas de l'article.

La boucle de retour terrain s'appuie sur Ahmadi et al., *An Agentic Approach for Active Data
Collection, Travel Behavior Modeling, and Weather-Sensitive Demand Prediction* (McGill).
Nous en reprenons l'instrument à objets question, la discipline d'immuabilité des réponses
brutes et la réservation d'une partition d'évaluation. Nous n'en reprenons pas la conclusion
sur la performance comparée des modèles de langage : leur tâche est une prédiction
comportementale sans modèle calibré disponible, la nôtre est un calcul de coût vérifiable.

Détail — reprises, adaptations, refus, et où le produit va plus loin :
[`docs/methodologie-recherche.md`](docs/methodologie-recherche.md) et
[`docs/calibration-et-fiabilite.md`](docs/calibration-et-fiabilite.md).

---

## Commandes d'exploitation

```bash
python -m app.cli calibrer --region SOUSS_MASSA --annees 10   # seuils depuis l'historique local
python -m app.cli fiabilite                                    # prédictions confrontées au réel
```

---

## Documentation

| Document | Contenu |
|---|---|
| [`architecture.md`](docs/architecture.md) | Choix structurants, modèle de données, risques techniques |
| [`calibration-et-fiabilite.md`](docs/calibration-et-fiabilite.md) | Boucle de correction : calibration, retour terrain, score de Brier |
| [`methodologie-recherche.md`](docs/methodologie-recherche.md) | Article de référence : ce qui est repris, adapté, refusé |
| [`setup.md`](docs/setup.md) | Installation, configuration, dépannage |
| [`api.md`](docs/api.md) | Endpoints REST |
| [`mcp.md`](docs/mcp.md) | Outils MCP et intégration client |
| [`ml.md`](docs/ml.md) | Nowcasting, métriques, validation temporelle |
| [`risk-engine.md`](docs/risk-engine.md) | Seuils, calibration, agrégation |
| [`alternatives.md`](docs/alternatives.md) | Génération, contraintes, optimisation |
| [`security.md`](docs/security.md) | Isolation, autorisation, injection de prompt |
| [`deployment.md`](docs/deployment.md) | Mise en production |

---

## Tests

```bash
cd backend && pytest -q          # 131 tests
cd frontend && npm run typecheck
```

Les tests portent sur du comportement métier : exposition temporelle, contraintes dures,
isolation multi-tenant, refus d'une option inventée par le modèle, et un garde-fou vérifiant
qu'aucun seuil de l'article colombien n'a été repris.

Plusieurs vérifient un **refus de conclure** : calibration sur données simulées,
seuil agronomiquement absurde, score de fiabilité sur échantillon insuffisant.
Un produit qui affiche un chiffre faux avec assurance est plus dangereux qu'un
produit qui dit ne pas savoir.

---

## Licence et statut

Logiciel privé destiné à des déploiements d'entreprise. Prototype d'ingénierie orienté
production : l'architecture est celle d'un produit, les limites de validation des données
sont documentées et affichées dans l'interface.
