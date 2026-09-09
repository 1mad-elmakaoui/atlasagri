# Couche MCP

## Principe

```
Claude → outils MCP → services applicatifs → logique métier déterministe → base / API externes
```

Claude n'accède jamais directement à la base de données.

## Registre unique

Une capacité métier est définie **une seule fois**, dans `app/tools/registry.py`, avec son
nom, sa description, son schéma d'entrée, ses rôles autorisés et son comportement d'erreur.

Deux consommateurs partagent ce registre :

- `app/mcp_server.py` — serveur stdio pour un client MCP externe ;
- `app/agent/service.py` — agent applicatif via l'API Anthropic.

Définir les outils deux fois produirait deux versions qui divergent en silence. L'écart ne
se découvrirait qu'en production, quand le modèle appellerait un outil dont le comportement
n'est plus celui qu'on croyait.

## Outils disponibles

### Météo et environnement

| Outil | Décision soutenue |
|---|---|
| `get_weather_nowcast` | Ce qui est attendu, et de combien de temps on dispose |
| `get_satellite_observation` | État de la végétation — déclare son indisponibilité si non configuré |

### Risque

| Outil | Décision soutenue |
|---|---|
| `get_crop_risk` | Anticiper une perte de qualité ou de rendement |
| `get_at_risk_regions` | Repérer où porter l'attention en priorité |

### Supply chain

| Outil | Décision soutenue |
|---|---|
| `get_inventory_status` | Combien de temps on tient sans réapprovisionnement |
| `calculate_stock_coverage` | Faut-il réapprovisionner ou changer de source |
| `get_supplier_exposure` | Quel fournisseur risque de ne pas livrer |

### Transport

| Outil | Décision soutenue |
|---|---|
| `get_shipment` | Situer l'expédition avant analyse |
| `get_route_options` | Comparer des itinéraires sur des critères homogènes |
| `get_route_risk` | Comprendre où et quand le trajet est menacé |

### Décision

| Outil | Décision soutenue |
|---|---|
| `generate_alternatives` | Passer du constat de risque à une décision argumentée |
| `simulate_scenario` | Éprouver un plan avant de s'y engager |
| `create_recommendation` | Formaliser une proposition traçable |
| `create_alert` | Porter un risque à l'attention des équipes |

## Garanties de sécurité

### Le tenant n'est jamais un paramètre du modèle

Il vient du contexte d'exécution authentifié. Aucun schéma d'outil n'expose `tenant_id`,
`user_id` ni équivalent — un test le vérifie sur l'ensemble du registre. Claude ne peut donc
pas franchir une frontière d'organisation, même si un message le lui demandait explicitement.

### Aucune action irréversible

L'outil le plus engageant, `create_recommendation`, crée une proposition **en attente de
validation humaine**. Rien n'est exécuté côté opérationnel.

Il refuse par ailleurs un identifiant d'option qui ne figure pas parmi celles réellement
évaluées, et enregistre les valeurs d'impact issues des moteurs plutôt que celles proposées
par le modèle : une recommandation dont les chiffres viendraient du modèle ne serait pas
vérifiable après coup.

### Autorisation avant exécution

Les outils d'écriture sont réservés aux rôles décisionnaires. Un analyste ne les voit même
pas dans la liste : le filtrage se fait à la déclaration, ce qui évite au modèle de tenter
un appel puis d'avoir à interpréter un refus.

### Sorties structurées

Aucun outil ne renvoie un bloc de texte libre. Chaque réponse est une structure typée,
accompagnée de sa provenance, ce qui permet à l'interface de construire le panneau
« Sources et preuves » sans que le modèle ait à recopier des chiffres.

### Journalisation

Chaque appel est journalisé et audité : outil, tenant, durée, issue. Aucun secret n'y figure,
un filtre masquant systématiquement les clés et jetons.
