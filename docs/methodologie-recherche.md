# Fondement méthodologique — de l'article scientifique au produit

Article de référence :
**Silva-Sosa, H. J.** — *Real-Time Climate Risk Assessment for Supply Chain Resilience:
A Data-Driven Nowcasting Framework for Colombian Agriculture*
(Corporación Universitaria de Ciencia y Desarrollo — UNICIENCIA, Bogotá).

Ce document dit précisément **ce que nous reprenons**, **ce que nous adaptons** et
**ce que nous refusons de reprendre**. Il existe pour qu'aucune affirmation du produit ne
puisse s'appuyer abusivement sur l'autorité d'un article dont le périmètre est plus étroit.

---

## 1. Ce que l'article apporte réellement

L'article propose un processus de traduction en trois étapes :

```
1. Nowcasting climatique court terme (6–48 h) sur séries temporelles de stations au sol
2. Cartographie d'impact climat → agriculture via relations empiriques et seuils
3. Traduction en signaux de décision supply chain (inventaire, sourcing, transport)
```

Il revendique un temps d'anticipation moyen de **38 heures** pour les alertes de niveau élevé,
et une structure catégorielle **Faible / Modéré / Élevé** justifiée par le fait qu'elle
s'intègre mieux aux protocoles de décision existants qu'une prévision probabiliste.

---

## 2. Ce que nous reprenons

### 2.1 La dérivation de seuils par quantiles — la contribution la plus utile

L'article définit ses catégories de risque aux **33ᵉ et 67ᵉ percentiles** de la distribution
empirique locale des anomalies météorologiques.

C'est le point méthodologique que nous adoptons intégralement, parce que c'est une **méthode
reproductible et non un jeu de valeurs**. Elle est transférable au Maroc précisément parce
qu'elle ne transfère aucun chiffre colombien : on ré-applique le procédé à la distribution
historique marocaine et on obtient des seuils marocains.

Implémentation : `app/domain/thresholds.py` → `QuantileThresholdCalibrator`.
Chaque seuil calibré conserve sa provenance : variable, région, culture, fenêtre de
calibration, taille d'échantillon, quantiles utilisés, date de calibration.

### 2.2 Les horizons et le temps d'anticipation

Horizons 6 h / 12 h / 24 h / 48 h, configurables.

Surtout, nous reprenons l'idée que le **temps disponible pour agir** est une sortie de premier
rang, et pas un détail. C'est ce qui alimente la timeline de décision de l'interface :
un risque élevé à +36 h et un risque élevé à +4 h n'appellent pas les mêmes options.

### 2.3 Les indicateurs dérivés

SPI (Standardized Precipitation Index) et anomalies de température, calculés à partir des
données brutes pour représenter les stress lents. Étiquetés `DERIVED`, jamais `OBSERVED`.

### 2.4 Le contrôle qualité des données

Détection d'aberrations par écart interquartile (IQR), imputation par interpolation temporelle,
contrôles de cohérence. Implémenté dans la normalisation des séries météo.

### 2.5 La validation temporelle stricte

Partition chronologique entraînement / validation / test, sans aucun mélange aléatoire.
Métriques MAE, RMSE et F1 sur détection d'événements extrêmes.

### 2.6 La sensibilité différenciée par culture

L'article établit des corrélations climat-rendement par culture (café r = 0,38 ;
fleurs r = 0,66 ; riz r = 0,09). Nous reprenons **le principe** — chaque culture a ses propres
moteurs climatiques et sa propre force de couplage — sans reprendre les cultures ni les
coefficients.

---

## 3. Ce que nous adaptons

| Article (Colombie) | AtlasAgri (Maroc) |
|---|---|
| Café, riz, fleurs | Tomate, agrumes, maraîchage, céréales, olive |
| Ceinture caféière andine, zone rizicole, région inter-andine | Souss-Massa, Doukkala-Abda, Gharb, Saïs, Tadla |
| Stations IDEAM + statistiques AGRONET | Open-Meteo (réanalyse + prévision), extension prévue vers les données nationales marocaines |
| Résolution régionale agrégée | Site (ferme, entrepôt, hub) géolocalisé, agrégé par région |
| Signal de décision catégoriel | Signal de décision **puis** alternatives évaluées et classées |

**Le climat marocain n'est pas le climat colombien.** La Colombie andine est un régime
équatorial d'altitude à deux saisons des pluies ; le Maroc agricole est un régime méditerranéen
semi-aride à forte contrainte hydrique et à risque de chergui (vent chaud et sec). Un seuil de
déficit pluviométrique de 44,4 mm sur 48 h, pertinent en zone andine, n'a aucune signification
transposable dans le Souss. C'est exactement pourquoi nous reprenons la méthode des quantiles
et pas les nombres.

---

## 4. Ce que nous refusons de reprendre

### 4.1 Les seuils numériques colombiens

Table III de l'article (déficit pluviométrique 29,6 / 44,4 mm ; anomalie thermique 1,9 / 2,0 °C)
concerne la ceinture caféière andine. Ces valeurs **ne figurent nulle part** dans notre code.

### 4.2 Le LSTM comme choix par défaut

L'article rapporte MAE 0,58–0,60 mm et RMSE 0,73–0,75 mm, quasi constants sur les quatre
horizons, et un F1 de détection d'événements extrêmes qui **augmente** avec l'horizon
(0,51 à 6 h → 0,66 à 48 h). L'article qualifie lui-même ce dernier résultat de
contre-intuitif.

Deux lectures sont possibles : soit le modèle capte un signal saisonnier dominant, soit les
données synthétiques normalisées rendent la tâche artificiellement stable. L'article étant
validé sur données synthétiques, on ne peut pas trancher.

Conclusion d'ingénierie : ces chiffres **ne constituent pas une preuve** qu'un LSTM est
opérationnellement supérieur ici. Nous démarrons donc par une baseline statistique versionnée,
mesurable, et nous ne passerons à un modèle récurrent que s'il bat cette baseline sur données
marocaines réelles en validation temporelle. L'interface `NowcastModel` rend cette substitution
triviale.

### 4.3 Les données synthétiques comme fondement de validation

L'article valide son prototype sur des données synthétiques calibrées. C'est méthodologiquement
défendable pour une preuve de concept, mais ce n'est pas une validation opérationnelle, et
l'article le dit clairement dans ses limites.

Notre produit utilise l'API météo réelle. Lorsque le réseau ne le permet pas, le jeu de données
local est **étiqueté `SIMULATED` sur chaque valeur** et affiché comme tel dans l'interface.

### 4.4 Le routage comme apport de l'article

L'article évoque le reroutage comme *signal de décision* (« reroute supplies ») mais ne fournit
**aucune** fonctionnalité de calcul d'itinéraire, aucune géométrie routière, aucun modèle de
coût de transport.

Toute la couche itinéraires / alternatives / optimisation multicritère d'AtlasAgri est un
développement propre et ne doit jamais être présentée comme issue de l'article.

---

## 5. Où nous allons plus loin que l'article

C'est ici que se situe la valeur produit.

L'article s'arrête au signal de décision. Sa Table III se termine littéralement par une colonne
« Decision Signal » dont la valeur la plus élevée est *« Activate contingency plans; reroute
supplies »*.

Cette phrase décrit **une intention, pas une décision**. Elle ne dit pas quel plan de secours,
quel itinéraire, à quel coût, avec quel effet sur le SLA, ni pourquoi celui-là plutôt qu'un autre.

AtlasAgri prend le relais exactement à ce point :

```
        ┌──────────────── périmètre de l'article ─────────────────┐
Données → Nowcasting → Impact agricole → Signal de risque
                                              │
        └──────────────────────────────────────┼──────────────────┘
                                               ▼
                                    Exposition supply chain
                                               ▼
                                  Génération d'alternatives
                                               ▼
                                Filtrage par contraintes dures
                                               ▼
                              Optimisation multicritère pondérée
                                               ▼
                                Recommandation + arbitrages
                                               ▼
                              Explication visuelle + décision humaine
        └────────────── périmètre propre à AtlasAgri ─────────────┘
```

Ajouts qui ne figurent pas dans l'article :

1. **Exposition temporelle par segment** — un itinéraire n'est exposé que si le véhicule se
   trouve dans la zone pendant la fenêtre de risque. L'article raisonne au niveau régional
   agrégé et ne modélise pas le déplacement.
2. **Moteur d'alternatives multi-classes** — itinéraire, fournisseur, entrepôt, horaire,
   transfert de stock, fractionnement.
3. **Contraintes dures avant classement** — une option infaisable est écartée, jamais classée.
4. **Optimisation multicritère pondérée par profil produit.**
5. **Couplage stock / délai fournisseur / SLA** — le risque n'est opérationnel que rapporté à
   la couverture de stock et au délai de réapprovisionnement.
6. **Traçabilité décisionnelle** — entrées, sources, alternatives écartées et motif conservés.
7. **Couche d'explication en langage métier** avec distinction stricte
   observé / prévu / dérivé / inféré / simulé.

---

## 6. Limites que nous héritons et assumons

L'article énonce honnêtement ses limites. Plusieurs nous concernent encore :

- Les seuils ne sont pas validés contre des historiques réels de perturbation.
- Le cadre ne modélise ni les ravageurs, ni les pratiques culturales, ni l'irrigation.
- La résolution reste régionale pour les signaux climatiques.
- Aucun retour terrain d'exploitants n'a encore été intégré.
- Le lien causal entre signal émis et résilience réellement améliorée reste à établir sur
  plusieurs cycles annuels.

Nous ajoutons une limite propre : nos probabilités de perturbation sont dérivées de règles
explicites et **ne sont pas calibrées** sur un historique d'incidents logistiques marocains.
Elles sont utilisables pour **comparer des options entre elles**, ce qui est l'usage réel du
produit. Elles ne doivent pas être lues comme des probabilités absolues.

Le produit affiche ces limites plutôt que de les masquer. En B2B, une plateforme qui surestime
sa propre certitude perd la confiance de son utilisateur au premier échec.
