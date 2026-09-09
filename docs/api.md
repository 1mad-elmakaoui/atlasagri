# API REST

Base : `/api/v1` — toutes les réponses sont en JSON, champs en français.

## Authentification

Jeton porteur dans l'en-tête `Authorization`.

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"supply@souss-primeurs.ma","password":"AtlasAgri2026!"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl http://localhost:8000/api/v1/tableau-de-bord -H "Authorization: Bearer $TOKEN"
```

Chaque appel est borné à l'organisation de l'utilisateur. Une ressource appartenant à une
autre organisation renvoie `404`, jamais `403` : confirmer son existence serait déjà une
divulgation.

## Format d'erreur

```json
{ "code": "introuvable", "message_fr": "L'expédition « EXP-9999 » est introuvable." }
```

| Code | Statut | Signification |
|---|---|---|
| `authentification_requise` | 401 | Jeton absent, invalide ou expiré |
| `acces_refuse` | 403 | Rôle insuffisant |
| `introuvable` | 404 | Ressource inexistante ou hors périmètre |
| `donnees_invalides` | 422 | Entrée non conforme |
| `trop_de_requetes` | 429 | Limite de débit atteinte |
| `fournisseur_indisponible` | 503 | Source externe injoignable |
| `fonctionnalite_desactivee` | 503 | Fonction non configurée (copilote, satellite) |

Les messages sont destinés à être affichés tels quels. Aucun détail technique n'y figure.

## Endpoints

### Supervision

| Méthode | Chemin | Description |
|---|---|---|
| `GET` | `/api/sante` | Health |

### Chaîne d'approvisionnement

| Méthode | Chemin | Description |
|---|---|---|
| `GET` | `/api/v1/alertes` | Alerts |
| `POST` | `/api/v1/alertes/{alert_id}/traiter` | Acknowledge Alert |
| `GET` | `/api/v1/fournisseurs` | Suppliers |
| `GET` | `/api/v1/recommandations` | Recommendations |
| `POST` | `/api/v1/recommandations/{recommendation_id}/decision` | Decide |
| `POST` | `/api/v1/simulations` | Run Simulation |
| `GET` | `/api/v1/simulations/scenarios` | Scenarios |
| `GET` | `/api/v1/stocks` | Inventory |

### Authentification

| Méthode | Chemin | Description |
|---|---|---|
| `POST` | `/api/v1/auth/login` | Login |
| `GET` | `/api/v1/auth/moi` | Me |

### Carte

| Méthode | Chemin | Description |
|---|---|---|
| `GET` | `/api/v1/carte/expedition/{reference}` | Shipment Layers |
| `GET` | `/api/v1/carte/sites` | Sites |

### Copilote IA

| Méthode | Chemin | Description |
|---|---|---|
| `GET` | `/api/v1/copilote/conversations` | Conversations |
| `GET` | `/api/v1/copilote/etat` | Status |
| `POST` | `/api/v1/copilote/question` | Ask |

### Expéditions

| Méthode | Chemin | Description |
|---|---|---|
| `GET` | `/api/v1/expeditions` | List Shipments |
| `GET` | `/api/v1/expeditions/{reference}` | Shipment Detail |

### Vue générale

| Méthode | Chemin | Description |
|---|---|---|
| `GET` | `/api/v1/tableau-de-bord` | Overview |
| `GET` | `/api/v1/tableau-de-bord/regions` | Regions At Risk |

## Réponses notables

### `GET /api/v1/tableau-de-bord`

Le **risque global** est le niveau de l'expédition la plus exposée, pas une moyenne. Une
moyenne diluerait une expédition critique parmi dix expéditions saines et masquerait
précisément ce qu'il faut voir.

Le champ `sources` expose l'état de chaque fournisseur externe : l'utilisateur doit pouvoir
constater qu'une source est en panne pour pondérer sa confiance dans ce qu'il voit.

### `GET /api/v1/expeditions/{reference}`

Réponse complète de décision : contexte, profil d'optimisation appliqué avec ses
pondérations, alternatives classées, options écartées avec leur motif, et recommandation.

Chaque alternative expose :

| Champ | Contenu |
|---|---|
| `criteres` | Valeur brute, valeur normalisée et poids de chaque critère |
| `raisons_fr` / `contreparties_fr` | Justification et ce que l'option coûte |
| `ecart_*` | Écarts au plan actuel : risque, coût, durée |
| `troncons` | Tronçons avec heure de passage prévue et motif d'exposition |
| `trace` | Tracé en (longitude, latitude), prêt pour MapLibre |
| `sources`, `etat_des_donnees` | Provenance et nature épistémique |

La `probabilite_perturbation` est un **indicateur comparatif dérivé de règles explicites**,
non calibré sur un historique d'incidents. L'API le rappelle dans les réponses concernées.

### `GET /api/v1/carte/expedition/{reference}`

Couches GeoJSON prêtes à afficher : points, itinéraires, tronçons exposés, et la légende.

La légende est servie par le serveur pour que couleurs et libellés restent alignés sur les
niveaux réellement produits par le moteur.

### `POST /api/v1/recommandations/{id}/decision`

Enregistre la décision humaine (`ACCEPTED`, `REJECTED`, `MODIFIED`). Réservée aux rôles
décisionnaires.

AtlasAgri ne déclenche aucune action opérationnelle : la mise en œuvre reste du ressort des
équipes du client. La décision est tracée dans le journal d'audit.

### `POST /api/v1/simulations`

Recalcule la décision sous une hypothèse modifiée et compare les deux situations. Aucune
donnée n'est modifiée.

L'indicateur le plus parlant n'est pas le score mais le **nombre d'options restantes** :
perdre onze options sur douze dit mieux qu'un score que la marge de manœuvre s'est effondrée.

### `GET /api/v1/copilote/etat`

À interroger avant d'afficher l'interface du copilote. Sans clé d'API configurée, il répond
`disponible: false` avec un message expliquant que le reste de l'application fonctionne. Le
copilote n'est jamais simulé.

## Limitation de débit

Deux compteurs séparés : général (`RATE_LIMIT_REQUESTS_PER_MINUTE`, défaut 120) et copilote
(`AGENT_RATE_LIMIT_REQUESTS_PER_MINUTE`, défaut 20).

Une question au copilote déclenche plusieurs appels de modèle et d'outils ; la protéger
séparément évite qu'un usage intensif épuise le quota des pages courantes.

## Documentation interactive

`GET /api/docs` — schéma OpenAPI complet, avec essai des endpoints.
