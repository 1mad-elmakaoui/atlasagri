# Nowcasting

## Choix : une baseline avant tout réseau de neurones

L'article de référence emploie un LSTM. Ce produit démarre sur une **baseline statistique
versionnée**. Ce n'est pas une facilité, c'est une position d'ingénierie.

Les métriques de l'article (MAE 0,58–0,60 mm, RMSE 0,73–0,75 mm) sont quasi constantes sur
les quatre horizons, et son F1 de détection d'événements extrêmes **augmente** avec
l'horizon (0,51 à 6 h → 0,66 à 48 h). L'article qualifie lui-même ce résultat de
contre-intuitif.

Deux lectures sont possibles : soit le modèle capte un signal saisonnier dominant, soit les
données synthétiques normalisées rendent la tâche artificiellement stable. L'article étant
validé sur données synthétiques, on ne peut pas trancher.

Conclusion : ces chiffres **ne démontrent pas** qu'un LSTM serait opérationnellement
supérieur ici. Un modèle neuronal invérifiable est un risque commercial, pas un atout.

## Ce que fait la baseline

Le fournisseur météo produit déjà une prévision horaire issue de modèles numériques
nationaux. La valeur ajoutée d'un nowcast local n'est pas de la refaire — prétendre faire
mieux qu'un service météorologique national avec quelques centaines d'observations serait
malhonnête.

Ce que le produit sait faire, c'est **agréger cette prévision sur les fenêtres de décision**
et y attacher une confiance décroissante avec l'horizon. C'est là qu'est sa valeur.

| Horizon | Confiance par défaut |
|---|---|
| 6 h, 12 h | Élevée |
| 24 h | Élevée si la couverture est complète, sinon Moyenne |
| 48 h | Moyenne |
| Couverture < 60 % | Faible, quel que soit l'horizon |

Une prévision à 48 h affichée avec la même assurance qu'une prévision à 6 h conduirait
l'utilisateur à surréagir à un signal lointain.

## Grandeurs produites

Par horizon : cumul de précipitations, températures extrêmes, rafale maximale, humidité
moyenne, humidité du sol minimale.

Indicateurs dérivés disponibles :

- **SPI** (Standardized Precipitation Index) — écart normalisé au régime historique.
  Renvoie `None` sous 30 observations : un SPI calculé sur quelques valeurs donnerait une
  fausse impression de rigueur statistique.
- **Anomalie thermique** — écart à la normale de la même période, mêmes garde-fous.
- **VPD** (déficit de pression de vapeur) — calculé par la formule de Tetens, `None` si la
  température ou l'humidité manque.

Tous sont étiquetés `DERIVED`, jamais `OBSERVED`.

## Substituer un modèle

L'interface `NowcastModel` est un protocole :

```python
class NowcastModel(Protocol):
    version: str
    def predict(self, series: WeatherSeries, *, horizons: tuple[int, ...]) -> tuple[HorizonForecast, ...]: ...
```

Un GRU entraîné s'y substitue sans que le moteur de risque n'ait à changer. La version du
modèle circule jusqu'à l'interface, ce qui permet de savoir quelle version a produit une
prévision donnée.

## Règles de validation

Non négociables, reprises de l'article :

1. **Aucun mélange aléatoire des séries temporelles.** Découpage strictement chronologique.
2. **Aucune fuite temporelle** dans la construction des variables.
3. Métriques : MAE, RMSE, et F1 sur détection d'événements extrêmes — une erreur moyenne
   faible peut masquer une incapacité totale à détecter les épisodes qui comptent.
4. Traçabilité : version, horodatage, entrées, horizon, métriques.

## Condition de passage à un modèle neuronal

Un modèle récurrent ne remplacera la baseline que s'il la **bat sur données marocaines
réelles en validation temporelle**, sur les métriques ci-dessus, avec un historique couvrant
plusieurs saisons.

Tant que cette condition n'est pas remplie, la baseline reste en place. Elle a l'avantage
d'être explicable, mesurable, et de constituer une référence honnête.

## Contrôle qualité des données

Repris de l'article : détection d'aberrations par écart interquartile, imputation par
interpolation temporelle, contrôles de cohérence. Le facteur IQR est porté à 3,0 pour ne pas
supprimer les événements extrêmes, qui sont le signal recherché.
