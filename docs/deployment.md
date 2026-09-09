# Déploiement

## Cible

Monolithe modulaire : un processus applicatif, une base PostgreSQL, des ressources statiques
servies par un serveur web.

```
Navigateur → CDN / Nginx (statique React)
                 │
                 └→ Reverse proxy → Uvicorn (FastAPI) → PostgreSQL
                                          └→ Open-Meteo (sortant HTTPS)
                                          └→ API Anthropic (sortant HTTPS)
```

## Prérequis

- PostgreSQL 15 ou plus, avec sauvegardes automatiques
- Terminaison TLS au niveau du proxy
- Accès HTTPS sortant vers `api.open-meteo.com` et `api.anthropic.com`

Le second point mérite attention : de nombreux réseaux d'entreprise filtrent le trafic
sortant. Si `api.open-meteo.com` est bloqué, le produit affichera l'indisponibilité de la
source — il ne fabriquera pas de valeurs de remplacement. Prévoir l'ouverture avec l'équipe
réseau du client **avant** la mise en service.

## Configuration de production

```bash
ATLASAGRI_ENV=production
SECRET_KEY=<openssl rand -hex 32>
DATABASE_URL=postgresql+psycopg://user:motdepasse@hote:5432/atlasagri
CORS_ORIGINS=https://atlasagri.client.ma
WEATHER_PROVIDER=openmeteo
ANTHROPIC_API_KEY=<clé>
LOG_LEVEL=INFO
```

Contrôles appliqués au démarrage en production :

| Contrôle | Comportement |
|---|---|
| `SECRET_KEY` absent | Démarrage refusé |
| `WEATHER_PROVIDER=offline` | Refusé — pas de données simulées chez un client |
| Jeu de démonstration | Non inséré |

## Lancement

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

Le nombre de workers dépend du profil de charge. Le calcul d'exposition d'itinéraire est le
poste dominant ; il est borné par le cache météo.

**Attention en multi-worker** : le cache météo et le compteur de limitation de débit sont en
mémoire de processus. Avec plusieurs workers, chacun a les siens — le cache est moins
efficace et la limite de débit devient approximative. Passer à Redis avant de dépasser un
seul worker en production.

## Frontend

```bash
cd frontend && npm ci && npm run build      # produit dist/
```

Servir `dist/` en statique et relayer `/api` vers l'application. Le lot dépasse 1 Mo,
principalement à cause de MapLibre ; un découpage par route est la première optimisation à
envisager si le temps de premier affichage devient un sujet.

## Base de données

Les tables sont créées au démarrage via `create_all()`, ce qui convient à un prototype.

**Avant la première mise en production**, introduire Alembic : sans migrations versionnées,
toute évolution de schéma sur une base contenant des données clientes devient une opération
manuelle et risquée.

## Supervision

`GET /api/sante` renvoie l'état de l'application et de chaque source externe. À utiliser
comme sonde applicative — une sonde qui vérifierait seulement que le processus répond
manquerait une panne de fournisseur météo.

Journaux au format JSON en production, exploitables par un agrégateur.

Éléments à suivre : latence par endpoint, taux d'erreur des fournisseurs externes, durée et
nombre d'appels d'outils, appels au modèle, décisions humaines acceptées ou rejetées.

Ce dernier indicateur est le plus important commercialement : c'est lui qui permettra, à
terme, de mesurer si les recommandations suivies évitent réellement des pertes.

## Sauvegarde

Sauvegarde quotidienne de PostgreSQL, restauration testée. Les tables `recommendations`,
`audit_logs` et `simulations` portent la traçabilité décisionnelle : leur perte rendrait
impossible de justifier a posteriori une décision opérationnelle.

## Mise à l'échelle

Dans l'ordre, quand la charge l'impose :

1. Cache météo partagé (Redis) — supprime la duplication entre workers.
2. Précalcul périodique du risque par expédition — aujourd'hui calculé à la demande.
3. Réplique de lecture PostgreSQL.
4. File d'attente pour les recalculs de masse.

Ne pas passer aux microservices sans raison de domaine claire. Les frontières actuelles sont
stables mais jeunes ; les figer prématurément dans un réseau de services coûterait cher.
