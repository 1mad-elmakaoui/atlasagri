# Architecture — AtlasAgri Intelligence

> Document de référence produit par l'équipe d'ingénierie avant l'implémentation.
> Il fixe les choix structurants et explique **pourquoi** ils ont été retenus.
> Il est honnête sur ce qui est opérationnel et sur ce qui ne l'est pas encore.

---

## 1. Principe directeur

AtlasAgri ne s'arrête pas à « il y a un risque ». Le produit va jusqu'à
« voici les alternatives, voici la meilleure, voici pourquoi ».

La chaîne de valeur implémentée est :

```
Donnée → Observation → Prévision → Détection de risque → Analyse d'impact
  → Génération d'alternatives → Évaluation → Optimisation → Explication → Décision humaine
```

Une règle non négociable traverse toute l'architecture :

**Les calculs critiques sont déterministes et faits en Python. Claude interprète, compare, explique et communique. Claude n'invente jamais une route, un coût, un délai, un stock ou un score.**

---

## 1 bis. Ancrage scientifique

La méthode s'appuie sur Silva-Sosa, *Real-Time Climate Risk Assessment for Supply Chain
Resilience: A Data-Driven Nowcasting Framework for Colombian Agriculture* (UNICIENCIA).

Nous en reprenons le processus de traduction en trois étapes (nowcasting → impact agricole →
signal supply chain), les horizons 6/12/24/48 h, le temps d'anticipation comme sortie de
premier rang, et surtout la **dérivation de seuils par quantiles (33ᵉ/67ᵉ percentiles)**, qui
est une méthode reproductible et non un jeu de valeurs — c'est ce qui permet de produire des
seuils marocains sans importer les seuils colombiens.

Nous n'en reprenons ni les valeurs numériques, ni le choix du LSTM par défaut, ni la validation
sur données synthétiques. La couche itinéraires / alternatives / optimisation est un
développement propre à AtlasAgri et ne provient pas de l'article.

Le détail complet — reprises, adaptations, refus, et où le produit va plus loin — est dans
[`methodologie-recherche.md`](./methodologie-recherche.md).

---

## 2. Architecture proposée

### 2.1 Vue d'ensemble

Monolithe modulaire Python (FastAPI) + frontend React. Pas de microservices pour le MVP :
le produit n'a ni le volume, ni l'équipe, ni les frontières de domaine stables qui
justifieraient le coût opérationnel d'un système distribué.

```
┌────────────────────────────────────────────────────────────────┐
│  Frontend React / TypeScript  (carte, tableaux de bord, copilote) │
└───────────────────────────┬────────────────────────────────────┘
                            │ REST/JSON (JWT, portée par tenant)
┌───────────────────────────▼────────────────────────────────────┐
│  API FastAPI                                                    │
│  auth · autorisation · isolation tenant · validation · audit    │
└───────────────────────────┬────────────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────────────┐
│  Services applicatifs                                           │
│  Weather · Nowcasting · AgriculturalImpact · SupplyChainRisk    │
│  Route · Alternative · Optimization · Recommendation · Agent    │
└──────┬──────────────────────────────────┬──────────────────────┘
       │                                  │
┌──────▼──────────────┐          ┌────────▼─────────────────────┐
│ Domaine (pur)       │          │ Registre d'outils métier      │
│ règles, seuils,     │          │ (schémas typés, autorisation) │
│ scoring, contrats   │          └────────┬──────────────┬───────┘
└──────┬──────────────┘                   │              │
       │                          ┌───────▼──────┐  ┌────▼────────┐
┌──────▼──────────────┐           │ Serveur MCP  │  │ Agent Claude│
│ Repositories (SQLA) │           │   (stdio)    │  │ (API réelle)│
└──────┬──────────────┘           └──────────────┘  └─────────────┘
       │
┌──────▼─────────────────────────────────────────────────────────┐
│ Infrastructure : PostgreSQL · adaptateurs fournisseurs externes │
│ WeatherProvider · RoutingProvider · SatelliteProvider           │
└────────────────────────────────────────────────────────────────┘
```

### 2.2 Décision structurante : un seul registre d'outils, deux consommateurs

Le piège classique est d'écrire les outils métier deux fois : une fois pour le serveur MCP,
une fois pour la boucle d'appel d'outils de l'agent. On obtient alors deux définitions qui
divergent silencieusement.

Ici, `app/tools/registry.py` détient **une seule** définition par capacité métier
(nom, description, schéma d'entrée Pydantic, schéma de sortie, autorisation, journalisation).

- Le serveur MCP (`app/mcp_server.py`) expose ce registre en stdio pour Claude Desktop / Claude Code.
- L'agent applicatif (`app/agent/`) consomme le même registre en processus via l'API Anthropic.

Un outil ajouté est donc immédiatement disponible aux deux, avec la même validation et le même
contrôle d'accès. C'est la conséquence directe de la règle « ne pas dupliquer les calculs ».

---

## 3. Choix technologiques définitifs

| Couche | Choix | Justification |
|---|---|---|
| Backend | Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2.0 | Imposé par la spécification, et adapté : typage fort aux frontières, écosystème scientifique proche des moteurs de risque. |
| Base | PostgreSQL (SQLite accepté en développement) | Relationnel, contraintes d'intégrité, `tenant_id` indexé partout. PostGIS n'est **pas** requis pour le MVP (voir §12). |
| Frontend | React 18 + TypeScript + Vite | Standard, typage partagé avec les contrats d'API. |
| Style | Tailwind CSS | Cohérence visuelle sans CSS ad hoc dispersé. |
| Carte | MapLibre GL JS | Open source, sans dépendance à un fournisseur propriétaire, gère bien les couches multiples (routes, zones, marqueurs) nécessaires ici. |
| Graphiques | Recharts | Suffisant pour les comparaisons, timelines et barres avant/après. |
| Agent | API Anthropic officielle (SDK `anthropic`) | Exigence produit : pas de LLM simulé. Sans clé, le copilote est **désactivé avec un message explicite**, jamais simulé. |
| MCP | SDK `mcp` officiel, transport stdio | Couche applicative réelle, pas une façade. |
| Tests | pytest | Tests de comportement sur les calculs, pas de théâtre de tests. |

### Dépendances volontairement écartées du MVP

- **PostGIS** : les opérations géométriques utiles ici (distance haversine, exposition d'un
  segment à une zone circulaire) tiennent en quelques dizaines de lignes déterministes et
  testables. Introduire PostGIS ajouterait une contrainte de déploiement sans bénéfice immédiat.
  À reconsidérer dès que des polygones de zones administratives réels seront intégrés (V2).
- **Celery / broker de messages** : aucun traitement long n'est nécessaire au MVP. Le recalcul
  de risque est déclenché à la demande et mis en cache.
- **PyTorch** : voir §9, un modèle neuronal n'est pas justifiable tant que la baseline n'est
  pas battue sur données réelles.

---

## 4. Structure du dépôt

```
atlasagri/
├── docs/                       # documentation française
├── backend/
│   ├── app/
│   │   ├── core/               # config, logging, erreurs, sécurité, dépendances
│   │   ├── domain/             # énumérations, schémas, seuils, scoring — sans I/O
│   │   ├── db/                 # modèles SQLAlchemy, session, seed
│   │   ├── providers/          # adaptateurs externes (weather / routing / satellite)
│   │   ├── services/           # services applicatifs, un fichier par capacité
│   │   ├── tools/              # registre d'outils métier (source unique)
│   │   ├── agent/              # agent Claude : prompt, boucle d'outils, garde-fous
│   │   ├── api/v1/             # routers HTTP
│   │   ├── mcp_server.py       # serveur MCP stdio
│   │   └── main.py
│   └── tests/
└── frontend/
    └── src/{components,pages,lib,types}
```

Règle de dépendance : `api → services → domain`, et `services → providers/db`.
Le domaine ne dépend de rien (ni de SQLAlchemy, ni de FastAPI, ni d'un fournisseur).
C'est ce qui rend les seuils et le scoring testables sans base ni réseau.

---

## 5. Modèle de données

Entités retenues (aucune table créée uniquement pour « faire entreprise ») :

**Multi-tenant & accès** — `Tenant`, `User` (rôle + `tenant_id`), `AuditLog`.

**Géographie & agriculture** — `Region` (régions marocaines réelles), `Site`
(table unique polymorphe : ferme, entrepôt, hub, client — ces objets partagent
coordonnées, région et capacité ; trois tables séparées auraient dupliqué la même colonne
de géolocalisation), `Crop`, `Product`.

**Chaîne d'approvisionnement** — `Supplier`, `Inventory`, `Shipment`, `RouteRecord`.

**Décision** — `RiskAssessment`, `Recommendation` (avec alternatives considérées, entrées et
sources sérialisées → auditabilité §37), `Alert`, `Simulation`, `AgentConversation`.

**Provenance** — chaque valeur exposée porte un `DataState`
(`OBSERVED` / `FORECAST` / `DERIVED` / `INFERRED` / `SIMULATED`) et une `source`.
C'est l'implémentation directe de la règle d'honnêteté : l'UI affiche l'étiquette,
et une valeur simulée ne peut pas être confondue avec une mesure.

Les routes et segments ne sont **pas** stockés comme géométrie figée : ils sont calculés par
le `RoutingProvider` à partir d'un graphe routier de référence, et seul le plan retenu
(`RouteRecord`) est persisté avec l'expédition.

---

## 6. Architecture MCP

Chaque outil déclare : nom, description orientée décision, schéma d'entrée Pydantic,
schéma de sortie typé, rôles autorisés, et comportement en cas d'erreur.

Familles d'outils :

| Famille | Outils |
|---|---|
| Météo & environnement | `get_weather_forecast`, `get_weather_nowcast`, `get_satellite_observation` |
| Risque | `get_crop_risk`, `get_supply_chain_risk`, `get_at_risk_regions`, `get_at_risk_products` |
| Supply chain | `get_inventory_status`, `calculate_stock_coverage`, `get_supplier_exposure`, `get_alternative_suppliers` |
| Transport | `get_shipment`, `get_route_options`, `get_route_risk`, `compare_routes` |
| Décision | `generate_alternatives`, `rank_alternatives`, `simulate_scenario`, `create_recommendation`, `create_alert` |

Les sorties sont des structures typées, jamais des blocs de texte. Toute réponse d'outil
est encapsulée avec sa provenance et ses sources, ce qui permet au frontend de construire
le panneau « Sources et preuves » sans que Claude ait à recopier des chiffres.

**Sécurité MCP** : le `tenant_id` n'est jamais un paramètre d'outil fourni par le modèle.
Il est injecté depuis le contexte d'exécution authentifié. Claude ne peut donc pas franchir
une frontière de tenant, même si on le lui demandait explicitement.

---

## 7. Architecture de l'agent IA

Boucle bornée (`AGENT_MAX_TOOL_ITERATIONS`, défaut 8) :

```
question métier
  → Claude choisit les outils
  → exécution validée côté serveur (schéma + autorisation + journal)
  → résultats structurés réinjectés
  → … (max N itérations)
  → réponse finale structurée en français
```

La réponse finale n'est pas un paragraphe libre : l'agent doit produire un bloc structuré
(décision, raisons, preuves, alternatives, arbitrages, confiance) que le frontend transforme
en cartes, badges et actions carte. Le paragraphe seul est explicitement interdit (§48).

**Défense contre l'injection de prompt** : les contenus externes (résultats d'API météo,
champs texte saisis par un utilisateur, données importées) sont transmis au modèle comme
**données** dans une enveloppe balisée, avec une consigne système stipulant qu'aucune
instruction contenue dans ces données ne modifie les règles. Les outils ne portent aucune
action irréversible : `create_recommendation` crée une proposition en attente d'approbation
humaine, jamais une exécution.

---

## 8. Architecture route & alternatives

**Génération et classement sont séparés** — c'est le point le plus important de ce module.

1. `RouteService` obtient des itinéraires candidats auprès du `RoutingProvider`
   (graphe routier de référence, ou OSRM si configuré). Distance et géométrie viennent du
   fournisseur, jamais d'une estimation du modèle.
2. Pour chaque segment, le service calcule l'exposition aux zones de risque actives
   (météo, agricole) sur la fenêtre temporelle où le véhicule y circulera réellement —
   un segment traversé dans 2 h n'est pas exposé à un orage prévu dans 30 h.
3. `AlternativeEngine` génère des candidats de plusieurs classes : itinéraire, fournisseur,
   entrepôt, horaire de départ, transfert de stock, expédition fractionnée.
4. Les **contraintes dures** filtrent avant tout classement : capacité insuffisante,
   fournisseur incapable de livrer dans le délai, entrepôt saturé → `INFEASIBLE`, écarté.
5. `OptimizationService` classe les alternatives faisables par score multicritère pondéré :

   ```
   score = w_risque·risque + w_coût·coût_normalisé + w_délai·durée_normalisée + w_exposition·exposition
   ```

   Les poids sont **par profil produit** et configurables : tomates réfrigérées → risque et
   délai dominants ; céréales en vrac → coût dominant. Aucune pondération universelle codée en dur.
6. Le résultat conserve la trace des alternatives écartées et de la raison — auditabilité.

---

## 9. Nowcasting : une baseline avant tout réseau de neurones

La spécification autorise LSTM/GRU. Le choix retenu pour le MVP est **une baseline
statistique versionnée** (persistance + tendance amortie + variance climatologique par horizon),
et non un réseau de neurones.

Raison : sans historique horaire marocain validé sur plusieurs saisons, un LSTM produirait des
prévisions dont on ne pourrait ni mesurer ni défendre la qualité. La baseline est honnête,
explicable, et fournit la référence que tout modèle ultérieur devra battre en validation
temporelle. L'interface `NowcastModel` est en place pour brancher un GRU en V2 sans toucher
au moteur de risque.

Règle absolue respectée : **aucun mélange aléatoire des séries temporelles**. Découpage
strictement chronologique, métriques MAE/RMSE + détection d'événements.

---

## 10. Architecture de l'information frontend

```
Vue générale   → risque global, alertes critiques, expositions, opportunités d'action
Risques        → registre des risques détectés, filtres, horizon
Carte          → surface opérationnelle : régions, sites, routes, zones, alternatives
Expéditions    → liste puis détail (carte + alternatives + recommandation + preuves)
Stocks         → couverture, points de commande, ruptures potentielles
Fournisseurs   → exposition, délais, alternatives de sourcing
Alternatives   → comparaison visuelle des options pour une perturbation
Simulations    → what-if, situation actuelle vs situation simulée
Copilote IA    → interface langage naturel du moteur de décision (pas la page d'accueil)
Alertes        → QUOI / OÙ / QUAND / IMPACT / ACTION
```

Le copilote n'est pas la page d'accueil (§49). La carte et la recommandation sont couplées :
quand l'IA recommande une option, la carte met en évidence l'itinéraire retenu, atténue les
autres et affiche la zone de risque à l'origine de la décision (§47).

---

## 11. APIs externes et limites réelles

| Fournisseur | Usage | État |
|---|---|---|
| **Open-Meteo** | prévisions horaires, historique, sol, ET0 | Adaptateur réel implémenté, aucune clé requise. |
| **Copernicus Data Space (Sentinel-2)** | NDVI / NDWI via l'API statistique Sentinel Hub | Adaptateur réel implémenté (OAuth2 `client_credentials`, masquage des pixels nuageux par la bande SCL). **Désactivé par défaut** : sans identifiants, les indicateurs satellite sont déclarés *indisponibles*, jamais inventés. Le trajet réseau n'a pas pu être rejoué depuis l'environnement de développement — voir la note ci-dessous. |
| **OSRM** | géométrie routière réelle | Adaptateur réel implémenté (`ROUTING_PROVIDER=osrm`). Par défaut le produit utilise un **graphe routier de référence** de villes et axes marocains réels (coordonnées réelles, distances routières de référence), sans dépendance réseau. |

**Ce qui n'a pas pu être vérifié.** Les adaptateurs Copernicus et OSRM sont écrits contre
la documentation publique de ces services, et leur logique de décision est couverte par des
tests (sélection de l'acquisition exploitable, découpage en segments). En revanche, l'aller-retour
réseau réel — échange OAuth, forme exacte de la réponse — n'a pas pu être exécuté : la politique
réseau de l'environnement de développement bloque les hôtes tiers. La première exécution contre
les services réels reste donc à faire côté client. Le produit dégrade proprement en cas d'échec :
identifiants refusés ou service injoignable produisent un message explicite, jamais une valeur
de remplacement.

**Limite à connaître** : dans un environnement réseau restreint (proxy d'entreprise, sandbox
CI), `api.open-meteo.com` peut être inaccessible. Le produit ne fabrique alors pas de fausses
mesures : `WEATHER_PROVIDER=offline` active un jeu de données local dont **chaque valeur est
étiquetée `SIMULATED`** et affichée comme telle dans l'interface. Un décideur ne peut jamais
confondre une simulation de démonstration avec une observation.

---

## 12. Périmètre MVP (livré)

Authentification et isolation multi-tenant · données supply chain marocaines · météo réelle
Open-Meteo · nowcasting baseline · moteur d'impact agricole à seuils configurables · moteur de
risque supply chain · calcul d'itinéraires et exposition par segment · moteur d'alternatives
multi-classes · optimisation multicritère pondérée par profil produit · recommandations
explicables et auditables · carte opérationnelle · simulation what-if · alertes in-app ·
serveur MCP réel · copilote Claude via API réelle.

## 13. Périmètre V2 (non livré, architecture prête)

Modèle GRU entraîné sur historique marocain validé · Sentinel-2 en production · OSRM
auto-hébergé avec trafic · polygones administratifs réels + PostGIS · intégrations ERP/TMS/WMS
· quantification des pertes évitées · automatisation contrôlée de certaines actions ·
notifications e-mail/SMS · déploiement multi-pays.

---

## 14. Risques techniques identifiés

1. **Qualité des seuils agronomiques.** Les seuils marocains initiaux sont des valeurs de
   départ documentées, pas une vérité agronomique validée. Ils sont donc externalisés,
   versionnés et porteurs de leur source. À calibrer avec des agronomes marocains — c'est le
   principal chantier de crédibilité du produit.
2. **Dépendance à un fournisseur météo unique.** Mitigé par l'abstraction `WeatherProvider`,
   mais un second fournisseur reste à intégrer avant une vente ferme.
3. **Distances routières de référence.** Le graphe embarqué donne des distances réalistes mais
   pas de géométrie routière fine. Acceptable pour comparer des options ; insuffisant pour du
   guidage. Résolu par OSRM en V2.
4. **Coût et latence de l'agent.** Chaque question déclenche plusieurs appels d'outils. Mitigé
   par une boucle bornée, du cache, et un rate limit dédié au copilote.
5. **Injection de prompt indirecte.** Traité par balisage des données externes et absence
   d'action irréversible côté outils, mais reste un risque à auditer à chaque nouvel outil.
6. **Absence de données historiques de perturbation.** Les probabilités de perturbation sont
   dérivées de règles explicites, pas apprises. Le produit le dit ; il ne présente pas ces
   valeurs comme des probabilités calibrées statistiquement.

---

## 15. Améliorations recommandées (au-delà de la spécification)

| Proposition | Pourquoi | Quand |
|---|---|---|
| Registre d'outils unique partagé MCP/agent | Évite deux définitions divergentes des mêmes capacités métier. | **MVP** — implémenté. |
| Exposition temporelle segment-par-segment | Un risque n'expose une expédition que si le véhicule est dans la zone pendant la fenêtre. Sans cela, on surestime massivement le risque et l'utilisateur perd confiance. | **MVP** — implémenté. |
| Contraintes dures séparées des préférences | Classer une option infaisable est pire qu'inutile : c'est une recommandation dangereuse. | **MVP** — implémenté. |
| Poids d'optimisation par profil produit | Une seule pondération ne peut pas servir des tomates réfrigérées et du blé en vrac. | **MVP** — implémenté. |
| Étiquetage de provenance sur chaque valeur | Condition de crédibilité en B2B : un décideur doit savoir ce qui est mesuré et ce qui est déduit. | **MVP** — implémenté. |
| Baseline statistique avant LSTM | Un modèle neuronal invérifiable est un risque commercial, pas un atout. | **MVP** — implémenté. |
| Quantification des pertes évitées | C'est l'argument de vente réel. Nécessite l'historique des décisions acceptées/refusées, que l'audit trail commence à collecter dès maintenant. | V2 |
| Second fournisseur météo | Supprime un point de défaillance unique avant engagement contractuel. | V2 |

---

## 16. Ce que ce système ne fait pas

Énoncé explicitement pour éviter toute survente :

- Il ne prédit pas la météo mieux qu'un service météorologique national.
- Il ne fournit pas de guidage routier tour par tour.
- Il ne remplace pas un TMS ni un ERP.
- Ses probabilités de perturbation ne sont pas calibrées sur un historique d'incidents réels.
- Ses seuils agronomiques ne sont pas encore validés par des agronomes marocains.
