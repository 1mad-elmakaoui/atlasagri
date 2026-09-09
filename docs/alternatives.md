# Moteur d'alternatives et optimisation

## Séparation génération / classement

Deux modules distincts, et c'est délibéré :

- `services/alternatives.py` **génère et filtre** ;
- `services/optimization.py` **classe**.

Les mélanger rendrait impossible de changer de stratégie de classement sans toucher à la
génération, et masquerait la distinction entre *ce qui est possible* et *ce qui est
préférable*.

## Classes d'alternatives

| Classe | Ce qui varie |
|---|---|
| `CURRENT_PLAN` | Rien — sert de référence de comparaison |
| `ALTERNATE_ROUTE` | Le corridor emprunté |
| `DEPARTURE_SHIFT` | L'heure de départ, **en avance comme en retard** |
| `ALTERNATE_SUPPLIER` | L'origine de la marchandise |
| `ALTERNATE_WAREHOUSE` | La destination, avec réacheminement ultérieur |
| `INVENTORY_TRANSFER`, `SPLIT_SHIPMENT` | Prévues, non livrées au MVP |

L'exploration des départs **en avance** est essentielle : face à un front qui arrive, partir
plus tôt est souvent la meilleure réponse, et aucun moteur ne la trouverait s'il n'explorait
que les retards.

## Contraintes dures

Une option qui viole une contrainte dure est marquée `INFEASIBLE`, **conservée avec son
motif**, et exclue du classement. Recommander un plan inapplicable serait pire qu'inutile :
ce serait dangereux.

| Contrainte | Vérification |
|---|---|
| Engagement de service | L'arrivée estimée dépasse-t-elle l'échéance ? |
| Capacité de destination | Le site peut-il absorber le volume ? |
| Chaîne du froid | La destination dispose-t-elle du stockage requis par la culture ? |
| Délai fournisseur | Le fournisseur peut-il livrer avant l'échéance ? |
| Capacité fournisseur | Peut-il produire le volume en un délai raisonnable ? |
| Préparation | Le départ avancé laisse-t-il le temps de charger ? |

Les options écartées restent visibles dans l'interface : l'utilisateur doit pouvoir constater
qu'une piste évidente a bien été examinée, et pourquoi elle a été rejetée.

## Comparabilité des options

Une option qui s'arrête en chemin doit être chiffrée jusqu'au bout.

Un entrepôt intermédiaire ne livre pas la marchandise à destination. Sa durée, sa distance,
son coût et son respect de l'échéance intègrent donc le **trajet de réacheminement** et un
délai de rupture de charge. Sans cela, l'option paraîtrait plus rapide et moins chère que le
trajet direct simplement parce qu'elle s'arrête à mi-parcours — et le classement la
privilégierait à tort.

## Optimisation multicritère

```
score = w_risque·risque + w_coût·coût + w_délai·durée + w_exposition·exposition + w_fiabilité·fiabilité
```

Les valeurs sont normalisées en min-max **sur le lot comparé** : un coût de 45 000 MAD n'est
ni bon ni mauvais dans l'absolu, il l'est par rapport aux autres options disponibles pour
cette expédition.

### Pondérations par profil produit

Il n'existe pas de pondération universelle.

| Profil | Risque | Coût | Délai | Exposition | Fiabilité |
|---|---|---|---|---|---|
| Périssable sous froid | 0,40 | 0,10 | 0,25 | 0,20 | 0,05 |
| Périssable standard | 0,32 | 0,20 | 0,25 | 0,18 | 0,05 |
| Vrac à faible marge | 0,22 | **0,45** | 0,13 | 0,15 | 0,05 |
| Export sous délai strict | 0,28 | 0,10 | **0,34** | 0,16 | 0,12 |

Des tomates réfrigérées et du blé en vrac n'ont pas la même fonction d'objectif : pour les
premières, un surcoût de transport reste très inférieur à la perte d'un lot déclassé ; pour
le second, la marge unitaire ne peut pas absorber un surcoût logistique.

Les pondérations sont **affichées à l'utilisateur**. Savoir que le système a privilégié le
risque à 40 % et le coût à 10 % permet de contester l'arbitrage, ce qui est sain.

## Contreparties

Une recommandation qui ne dit pas ce qu'elle coûte est un argumentaire, pas un conseil. Le
moteur énonce systématiquement le surcoût, le temps supplémentaire et le risque résiduel.

Un écart de coût inférieur à 500 MAD sur une expédition à plus de 40 000 MAD n'est pas
présenté comme un argument : il est dans le bruit du modèle de coût, et l'afficher donnerait
une fausse impression de précision.

## Réserves signalées

Le classement remonte ses propres limites :

- données météo simulées ;
- écart de score inférieur à 0,05 entre les deux premières options — le choix peut alors
  légitimement se faire sur un critère opérationnel non modélisé ;
- option unique — il n'y a pas de véritable arbitrage à opérer ;
- aucune option faisable — une décision humaine devient nécessaire.

## Traçabilité

Chaque recommandation enregistrée conserve ses entrées, les pondérations appliquées, les
options considérées avec leur rang, les options écartées avec leur motif, et les sources de
données. La question « pourquoi cette recommandation est-elle apparue ? » reste donc
répondable plusieurs mois après les faits.
