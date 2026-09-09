# Calibration des seuils et mesure de fiabilité

Ce document décrit la boucle de correction du produit : comment les seuils
cessent d'être des hypothèses, et comment on vérifie que les prédictions valent
quelque chose.

---

## 1. Pourquoi cette boucle existe

Avant elle, AtlasAgri annonçait des probabilités de perturbation sans jamais
apprendre si elles se vérifiaient. Le système prédisait en permanence et ne se
corrigeait jamais.

Trois limites documentées découlaient toutes de la même absence :

- les probabilités de perturbation n'étaient pas calibrées sur un historique
  d'incidents ;
- les pondérations d'optimisation étaient posées à dire d'expert ;
- les pertes évitées — l'argument commercial réel — n'étaient pas quantifiables.

La boucle se referme en deux temps : la **calibration** apporte la vérité
climatologique, le **retour terrain** apporte la vérité opérationnelle.

---

## 2. Calibration des seuils

### Méthode reprise

Silva-Sosa dérive les catégories de risque des **quantiles de la distribution
empirique locale** plutôt que de valeurs absolues. C'est la contribution la plus
transférable de l'article : une méthode, pas un jeu de chiffres. On la
ré-applique à la distribution marocaine et on obtient des seuils marocains.

Chaque seuil calibré conserve sa fenêtre, sa taille d'échantillon, ses quantiles
et sa source. Un seuil dont on ne peut plus dire d'où il vient redevient une
hypothèse.

### Divergence assumée : les niveaux de quantiles

L'article retient les **33ᵉ et 67ᵉ percentiles**, explicitement « pour créer des
catégories de risque équilibrées ». C'est cohérent avec son objectif : analyser
un phénomène en trois classes de taille comparable.

Un produit d'alerte a l'objectif inverse. Avec 33/67, un tiers des journées
serait classé « modéré » et un tiers « élevé » : l'exploitant recevrait des
alertes en permanence et cesserait de les lire en une semaine. Une alerte doit
signaler ce qui sort de l'ordinaire.

AtlasAgri retient donc les **85ᵉ, 95ᵉ et 99ᵉ percentiles**. La méthode est celle
de l'article ; les niveaux sont ceux d'un outil opérationnel.

### Divergence assumée : valeurs brutes ou anomalies

L'article calibre sur des **anomalies** météorologiques — déficit
pluviométrique, écart de température à la normale. Nous calibrons sur des
**valeurs absolues agrégées** par fenêtre : cumul de pluie sur 48 h, température
maximale sur 24 h, rafale maximale.

Raison : nos seuils alimentent des décisions de transport et de récolte, qui
répondent à des valeurs absolues. Un camion s'arrête sous 25 mm/h de pluie, que
ce soit ou non anormal pour la saison. L'anomalie reste pertinente pour le stress
hydrique lent, où le SPI est déjà implémenté.

### Trois refus de calibrer

La statistique produit une valeur quelle que soit la distribution. Encore
faut-il qu'elle ait un sens.

**Échantillon insuffisant.** Sous 60 observations exploitables, le calibrateur
refuse. Un seuil calibré sur douze valeurs donnerait une fausse impression de
rigueur.

**Aléa inexistant localement.** Agadir ne gèle pratiquement jamais. La
distribution des températures minimales n'y a pas de queue froide, et un
quantile bas renvoie « gel à 9 °C » — une valeur que la méthode calcule sans
broncher et qui déclencherait des alertes de gel toute l'année. Une enveloppe de
plausibilité par type d'aléa rejette ces seuils : le moteur retombe alors sur la
valeur provisoire, qui se déclare comme provisoire.

**Bornes non discriminantes.** Si les trois bornes tombent à moins d'un écart
utile les unes des autres, le seuil ne sépare rien.

Exemple réel, sur distributions marocaines représentatives :

| Zone et grandeur | Modéré | Élevé | Critique | Verdict |
|---|---|---|---|---|
| Pluie 48 h, Souss | 10,8 mm | 17,8 mm | 22,2 mm | retenu |
| T. max, Marrakech | 36,6 °C | 41,1 °C | 44,3 °C | retenu |
| T. max, Agadir | 26,2 °C | 27,8 °C | 29,3 °C | **rejeté** — pas de stress thermique sur ce littoral |
| T. min, Agadir | 9,2 °C | 6,7 °C | 4,0 °C | **rejeté** — zone sans gel |
| T. min, plateau du Saïs | 1,4 °C | −2,1 °C | −4,2 °C | retenu |
| Rafales, littoral atlantique | 54,8 km/h | 68,7 km/h | 92,3 km/h | retenu |

### Refus de calibrer sur données simulées

Une campagne lancée avec `WEATHER_PROVIDER=offline` est **rejetée**. Des seuils
issus d'un jeu de démonstration seraient crédibles en apparence et faux en
pratique ; persistés, ils feraient autorité sur des décisions réelles.

### Exécution

```bash
python -m app.cli calibrer --region SOUSS_MASSA --annees 10
python -m app.cli calibrer                       # toutes les régions actives
```

La calibration modifie les seuils qui fondent toutes les évaluations de
l'organisation. C'est une opération d'administration, exécutable par un
administrateur ou une tâche planifiée — pas un bouton de l'interface.

---

## 3. Collecte des résultats observés

### Instrument

Reprend le patron d'Ahmadi et al. : un questionnaire est une suite d'**objets
question** typés, portant leur validation et leur condition d'affichage.
L'administration est conversationnelle et à état — chaque réponse détermine la
suivante.

Le branchement reste **entièrement déterministe**, et le modèle de langage
n'administre pas le questionnaire. Deux raisons :

1. Un instrument de mesure doit produire la même séquence pour la même
   situation. Un enchaînement décidé par un modèle rendrait les réponses non
   comparables entre deux expéditions, ce qui ruinerait leur usage statistique.
2. La collecte fonctionne sans clé d'API. Le retour terrain est la donnée la
   plus précieuse du produit : la subordonner à un service externe serait un
   mauvais arbitrage.

Le modèle garde un rôle accessoire : reformuler une question mal comprise, coder
une réponse libre. Jamais décider quelle question vient ensuite.

### Trois garanties

**Prédiction figée à la décision.** Une probabilité recalculée après coup n'est
pas celle qui a été montrée à l'utilisateur. Sans cet instantané, aucune
calibration honnête n'est possible.

**Réponses immuables.** Une correction crée une version référençant la
précédente. On ne réécrit pas une mesure de terrain.

**Partition figée à l'écriture.** Chaque résultat est affecté à « calibration »
ou « évaluation » au moment de sa création, par un condensé de l'identifiant
d'expédition. La tirer plus tard, ou à chaque enregistrement, permettrait de
déplacer un résultat gênant vers la partition souhaitée — et une calibration
deviendrait circulaire sans que rien ne le signale.

---

## 4. Mesure de fiabilité

### Score de Brier

Moyenne du carré de l'écart entre probabilité annoncée et événement observé.
Comparé à la référence d'une prévision constante égale au taux de base :

```
référence = taux_observé × (1 − taux_observé)
gain      = (référence − brier) / référence
```

Un système qui ne bat pas cette référence n'apporte rien, quel que soit son score
absolu. Le module le dit explicitement dans ce cas.

### Courbe de fiabilité

Taux observé par tranche de probabilité annoncée. C'est la lecture la plus
parlante pour un non-statisticien : *quand le système annonce 30 %, cela se
produit-il environ trois fois sur dix ?*

### Deux refus de conclure

**Sous 25 couples**, aucun score n'est publié. Un score de Brier calculé sur sept
expéditions n'a aucune valeur informative, et un chiffre affiché finit toujours
par se retrouver dans une présentation commerciale.

**La perte évitée** n'est annoncée que si les deux groupes comparés —
recommandation suivie et non suivie — comptent au moins dix expéditions à risque
chacun. En deçà, les pertes constatées sont affichées telles quelles, sans
montant évité.

Même au-delà, l'écart est présenté comme une **association et non un effet
causal** : les expéditions où la recommandation est suivie peuvent différer par
ailleurs. C'est la mesure la plus demandée commercialement et la plus facile à
falsifier.

### Exécution

```bash
python -m app.cli fiabilite
```

Également exposée en lecture : `GET /api/v1/resultats/fiabilite`.

---

## 5. Ce que cette boucle ne fait pas encore

- Les pondérations d'optimisation restent posées à dire d'expert. Les estimer
  par un modèle de choix discret sur les recommandations acceptées et refusées
  est l'étape suivante, et demande d'abord plusieurs dizaines de décisions.
- Aucune campagne de calibration n'a encore été exécutée sur l'archive réelle :
  l'environnement de développement n'a pas d'accès sortant vers Open-Meteo.
  Le mécanisme est en place et testé ; les seuils marocains restent à produire.
- Les enveloppes de plausibilité sont des valeurs d'ingénierie, à confirmer avec
  des agronomes marocains — comme les seuils provisoires qu'elles encadrent.
