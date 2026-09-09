# Installation et configuration

## Prérequis

- Python 3.11 ou plus
- Node.js 20 ou plus
- PostgreSQL 15 ou plus en production (SQLite accepté en développement local)

---

## 1. Backend

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows : .venv\Scripts\activate
pip install -e "backend[dev]"
pip install -e "backend[postgres]"   # si PostgreSQL
```

### Configuration

```bash
cp .env.example .env
```

Variables à renseigner en priorité :

| Variable | Rôle | Obligatoire |
|---|---|---|
| `SECRET_KEY` | Signature des jetons. `openssl rand -hex 32` | **Oui en production** |
| `DATABASE_URL` | Connexion base | Oui |
| `ANTHROPIC_API_KEY` | Active le copilote IA | Non — sans elle le copilote est désactivé |
| `WEATHER_PROVIDER` | `openmeteo` (réel) ou `offline` (démonstration) | Non, défaut `openmeteo` |
| `CORS_ORIGINS` | Origines autorisées du frontend | Oui |

Le démarrage échoue volontairement si `SECRET_KEY` est absent en production : signer des
jetons avec une valeur devinable serait pire qu'un arrêt.

### Lancement

```bash
cd backend
uvicorn app.main:app --reload
```

- API : http://localhost:8000
- Documentation interactive : http://localhost:8000/api/docs
- État des sources externes : http://localhost:8000/api/sante

Hors production, la base est créée et peuplée automatiquement au premier démarrage. En
production, aucune donnée de démonstration n'est insérée.

---

## 2. Frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

Le serveur de développement relaie `/api` vers `http://localhost:8000` : aucune variable
d'environnement côté navigateur, et pas de question de CORS en local.

---

## 3. Comptes de démonstration

Mot de passe commun : `AtlasAgri2026!`

| Compte | Rôle | Peut décider |
|---|---|---|
| `admin@souss-primeurs.ma` | Administrateur | Oui |
| `supply@souss-primeurs.ma` | Responsable supply chain | Oui |
| `operations@souss-primeurs.ma` | Responsable opérations | Oui |
| `directrice@souss-primeurs.ma` | Direction | Non |
| `analyste@souss-primeurs.ma` | Analyste | Non |
| `supply@gharb-agro.ma` | Seconde organisation | Oui, sur ses propres données |

Le compte Gharb Agro existe pour vérifier l'isolation : connecté avec lui, aucune donnée de
Souss Primeurs n'est visible, et l'accès direct à `EXP-1842` renvoie « introuvable ».

---

## 4. Serveur MCP

Le serveur MCP expose les capacités métier à un client compatible (Claude Desktop,
Claude Code).

```bash
# Obtenir un jeton
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"supply@souss-primeurs.ma","password":"AtlasAgri2026!"}'

# Lancer le serveur avec ce jeton
ATLASAGRI_MCP_TOKEN=<jeton> python -m app.mcp_server
```

Le serveur **refuse de démarrer sans jeton**. Un serveur MCP anonyme ayant accès à la base
contournerait entièrement l'isolation multi-tenant.

Configuration côté client :

```json
{
  "mcpServers": {
    "atlasagri": {
      "command": "/chemin/vers/.venv/bin/python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/chemin/vers/atlasagri/backend",
      "env": { "ATLASAGRI_MCP_TOKEN": "<jeton>", "DATABASE_URL": "<url>" }
    }
  }
}
```

---

## 5. Intégrations externes optionnelles

Aucune de ces intégrations n'est nécessaire pour faire tourner le produit : sans elles, la
fonctionnalité concernée se déclare indisponible plutôt que de produire une valeur inventée.
Les activer améliore la qualité des données, pas le fonctionnement.

### 5.1 Copilote IA — clé Anthropic

1. Créer une clé sur https://console.anthropic.com → *API Keys*
2. `ANTHROPIC_API_KEY=sk-ant-...` dans `backend/.env`
3. Redémarrer le backend ; vérifier sur `/api/sante`

Sans clé, toute l'analyse déterministe (risques, itinéraires, alternatives, simulations)
reste disponible : seule la formulation en langage naturel disparaît.

### 5.2 Indicateurs satellite — Copernicus Data Space

Le compte Copernicus Data Space et le tableau de bord Sentinel Hub sont **deux inscriptions
distinctes qui partagent le même identifiant**. Avoir créé le compte ne suffit pas : il faut
encore générer un client OAuth.

1. Se connecter sur https://dataspace.copernicus.eu
2. Ouvrir le tableau de bord Sentinel Hub :
   https://shapps.dataspace.copernicus.eu/dashboard/
   (accessible aussi depuis le menu du compte, entrée « Sentinel Hub »)
3. Onglet **User settings**
4. Section **OAuth clients** → bouton **Create new**
5. Donner un nom au client, par exemple `atlasagri-prod`, puis valider
6. Copier immédiatement les deux valeurs :
   - **Client ID** → `COPERNICUS_CLIENT_ID`
   - **Client secret** → `COPERNICUS_CLIENT_SECRET`

> Le *client secret* n'est affiché **qu'une seule fois**. S'il est perdu, il faut supprimer
> le client et en créer un autre — il n'existe aucun moyen de le réafficher.

Puis dans `backend/.env` :

```bash
SATELLITE_PROVIDER=copernicus
COPERNICUS_CLIENT_ID=votre-client-id
COPERNICUS_CLIENT_SECRET=votre-client-secret
```

Redémarrer le backend et vérifier `/api/sante` : la ligne « Copernicus Data Space
(Sentinel-2) » doit passer à `disponible: true`. Si elle reste `false`, le message indique
la cause exacte (identifiants refusés, service injoignable).

**Quota.** Le compte gratuit accorde 30 000 unités de traitement par mois. Une requête NDVI
sur une parcelle en coûte quelques-unes : le prototype ne s'en approche pas. Les réponses
sont mises en cache six heures, ce qui suffit largement — Sentinel-2 ne repasse au même
endroit que tous les cinq jours.

**Ce que le produit fait de ces données.** Il calcule NDVI et NDWI sur une zone d'environ
2 km de côté autour du point demandé, en **écartant les pixels nuageux** (bande de
classification SCL). Si moins de 35 % de la zone est exploitable, l'acquisition est rejetée
et le produit remonte l'acquisition dégagée la plus récente. Si aucune ne l'est, il déclare
l'indisponibilité et dit pourquoi : un NDVI mesuré sur des nuages a l'apparence d'une valeur
normale et n'en est pas une.

### 5.3 Géométrie routière réelle — OSRM

```bash
ROUTING_PROVIDER=osrm
OSRM_BASE_URL=https://router.project-osrm.org
```

Le serveur public de démonstration n'offre **aucune garantie de disponibilité** et interdit
un usage de production. Pour un déploiement réel, héberger sa propre instance OSRM avec
l'extrait OpenStreetMap du Maroc (https://download.geofabrik.de/africa/morocco.html).

Sans cette variable, le produit utilise son graphe routier de référence : villes et distances
routières réelles, mais tracé simplifié entre villes. Aucune dépendance réseau.

### 5.4 Fond de carte

Le frontend utilise par défaut les tuiles OpenFreeMap, gratuites et sans clé. Pour un autre
fournisseur, renseigner `VITE_MAP_STYLE_URL` dans `frontend/.env`. Si le fond distant est
inaccessible, la carte bascule automatiquement sur un fond neutre au bout de six secondes :
les itinéraires, zones de risque et sites restent affichés.

---

## 6. Tests

```bash
cd backend && pytest -q
cd frontend && npm run typecheck && npm run build
```

---

## Dépannage

### « Le copilote IA n'est pas configuré »

Comportement normal sans `ANTHROPIC_API_KEY`. Le copilote est **désactivé**, jamais simulé.
Toutes les analyses déterministes — risques, itinéraires, alternatives, simulations —
restent disponibles.

### « Une source de données externe est momentanément indisponible »

Le fournisseur météo est injoignable. Causes fréquentes : coupure réseau, proxy d'entreprise
bloquant `api.open-meteo.com`.

Le produit **n'invente pas** de valeurs de remplacement. Pour faire fonctionner une
démonstration hors ligne :

```bash
WEATHER_PROVIDER=offline
```

Chaque valeur produite est alors étiquetée « Simulé » dans l'interface, un bandeau permanent
le rappelle, et le mode est **refusé en production**.

### La carte reste vide

Vérifier que l'expédition a bien une origine et une destination distinctes rattachées à des
nœuds du réseau routier, et consulter la console du navigateur. Les tracés viennent du
service de routage : le frontend n'en fabrique aucun.

### Erreur « no such table » avec SQLite en mémoire

Utiliser `sqlite+pysqlite:///:memory:` sans chemin de fichier. Le moteur bascule alors sur
un pool à connexion unique, sans lequel chaque connexion ouvrirait sa propre base vide.
