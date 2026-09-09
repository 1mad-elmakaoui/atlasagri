# Moteurs de risque

Trois moteurs distincts, volontairement séparés parce qu'ils répondent à des questions
différentes.

| Moteur | Question | Seuils |
|---|---|---|
| `AgriculturalImpactEngine` | La culture au champ va-t-elle souffrir ? | Cumuls longs, par culture |
| `RouteRiskService` | Le camion va-t-il être bloqué ou ralenti ? | Intensité et saturation, par classe de route |
| `SupplyChainRiskEngine` | L'entreprise va-t-elle en pâtir ? | Couverture, délai, exposition |

## Pourquoi risque agricole et risque routier ont des seuils distincts

C'est le point le plus important de ce module, et une erreur corrigée en cours de
développement.

Le risque agricole s'évalue sur des **cumuls longs** : c'est la quantité d'eau reçue sur 48 h
qui abîme un plant de tomate.

Le risque routier s'évalue sur l'**intensité pendant la traversée** (visibilité, aquaplanage)
et sur la **saturation antérieure des sols** (ruissellement, coupures). Un cumul de 60 mm
étalé sur deux jours ne coupe pas une route ; 25 mm en une heure, si.

Comparer le cumul d'une heure de traversée à un seuil agronomique de 48 h sous-estimait
systématiquement le risque routier. Les deux jeux de seuils sont donc séparés :
`domain/crops.py` et `domain/road_risk.py`.

## Calibration des seuils par quantiles

Méthode reprise de l'article de référence : les bornes sont dérivées des **33ᵉ et 67ᵉ
percentiles de la distribution historique locale**, et non fixées en valeur absolue.

C'est ce qui rend la méthode transférable au Maroc : on transfère le procédé, jamais les
nombres. Un déficit pluviométrique de 44 mm/48 h signalant une sécheresse sévère en zone
andine n'a aucune signification transposable dans le Souss semi-aride.

```python
seuil = QuantileThresholdCalibrator().calibrate(
    observations=cumuls_historiques_48h,
    risk_type=RiskType.HEAVY_RAIN,
    variable="precipitation_48h_mm",
    unit="mm",
    source_label_fr="Réanalyse ERA5 via Open-Meteo Archive",
    region_code="SOUSS_MASSA",
    calibration_window="2015-2024",
)
```

Le calibrateur **refuse** de produire un seuil sous 60 observations exploitables. Un seuil
calibré sur douze valeurs donnerait une fausse impression de rigueur statistique.

Les aberrations sont écartées par écart interquartile avec un facteur 3,0 — et non le 1,5
usuel — parce qu'en météorologie les événements extrêmes sont précisément ce que l'on
cherche à détecter : un filtre agressif supprimerait le signal utile.

## Provenance obligatoire

Chaque seuil porte son origine :

| Origine | Signification | Affichage |
|---|---|---|
| `calibrated` | Dérivé de la distribution locale | Percentiles, fenêtre, taille d'échantillon |
| `agronomic_literature` | Issu de la littérature | Source citée |
| `provisional_expert` | Valeur de départ | **« À valider par un agronome »** |

Les seuils livrés aujourd'hui sont majoritairement `provisional_expert`, et l'interface le
signale sur chaque évaluation concernée. C'est le principal chantier de crédibilité du
produit.

## Agrégation de plusieurs facteurs

Une somme serait fausse — deux risques modérés ne font pas un risque critique. Un maximum
seul perdrait l'information de cumul.

Le moteur retient donc le facteur dominant, majoré d'une fraction décroissante des suivants :

```
score = sévérité₁ + Σ (sévérité_i × 0,35 / i)     puis borné à 1,0
```

Deux menaces simultanées aggravent la situation sans la doubler mécaniquement.

## Exposition d'itinéraire

Le risque global d'un itinéraire combine deux lectures :

- la **moyenne pondérée par la distance** — un tronçon critique de 5 km ne compromet pas un
  trajet de 600 km autant qu'un tronçon critique de 200 km ;
- le **pire tronçon ramené à 70 % de sa sévérité** — un col coupé arrête le camion quelle
  que soit sa longueur.

Le maximum des deux est retenu. Une moyenne pure diluerait un point de blocage ponctuel
jusqu'à l'invisibilité.

La vulnérabilité structurelle module ensuite le résultat : une autoroute drainée à chaussées
séparées encaisse ce qui coupe une route régionale de montagne.

## Probabilité de perturbation

L'indicateur exposé est **dérivé de règles explicites** et n'est **pas calibré** sur un
historique d'incidents marocains, faute d'un tel historique.

Son usage légitime est de **classer des itinéraires entre eux**. Il ne doit pas être lu comme
une probabilité statistique. L'API le rappelle dans chaque réponse concernée, et l'interface
l'affiche.

## Risque supply chain

Le raisonnement est celui d'un responsable supply chain : le stock doit couvrir le délai de
réapprovisionnement **plus** la durée de la perturbation.

```
écart = (délai fournisseur + durée perturbation) − couverture utilisable
```

La **couverture utilisable** exclut le stock de sécurité. Le confondre avec de la marge
conduirait à sous-estimer le risque : le stock de sécurité absorbe la variabilité normale de
la demande, pas une perturbation exceptionnelle.

Exemple du jeu de démonstration :

```
Tomate cerise export — Entrepôt central de Casablanca
  Stock            128 t
  Stock de sécurité 60 t
  Demande          40 t/jour
  → couverture totale     3,2 jours
  → couverture utilisable 1,7 jour
  → délai fournisseur     2,0 jours + perturbation 1,5 jour = 3,5 jours requis
  → écart : 1,8 jour  →  risque ÉLEVÉ
```
