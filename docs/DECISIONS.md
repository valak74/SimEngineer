# Décisions (ambiguïtés tranchées : option la plus simple et réversible)

| # | Décision | Raison |
|---|---|---|
| D01 | Stockage des tours en NPZ (pas Parquet) | numpy suffit, aucune dépendance en plus ; Parquet ajoutable plus tard |
| D02 | Lecteur/écrivain `.ibt` maison (`sources/ibt_format.py`) plutôt que `irsdk.IBT` | testable sous Linux, aller-retour vérifié par les tests ; format à confirmer sur un vrai fichier (ASSUMPTIONS) |
| D03 | `Frame` typé + dictionnaire `slow` pour pneus/météo lus à 1 Hz | budget CPU, typage mypy |
| D04 | Surcharges d'environnement `SIMENGINEER__SECTION__CLE` (valeur JSON ou chaîne) | simple, sans dépendance |
| D05 | Clés d'API : variables d'environnement, ou fichier `.env` local ignoré par git | règle 9 |
| D06 | Tout le code est exécuté sous Python 3.12+ ; CI locale faite ici en 3.13 | seul interpréteur outillé dans le conteneur de dev |
| D07 | Infos de session `.ibt` extraites par expressions régulières (pas de PyYAML) | une dépendance en moins ; quelques clés seulement |
| D08 | Rejeu accéléré : l'arbitre de parole utilise le temps de session (cooldowns cohérents) ; en temps réel, l'horloge système | rejeu rapide réaliste |
| D09 | Sans référence importée, le meilleur tour valide de la séance sert de référence | coach utile dès la 1re séance |
| D10 | Voiture arrêtée : annonce à la 1re détection (≤ 400 m) puis un rappel à ≤ 150 m ; distances arrondies à 50 m (phrases pré-synthétisées) | latence et anti-répétition |
| D11 | Trafic évalué à 20 Hz, côtés/drapeaux à 60 Hz | budget CPU (PERF.md) |
| D12 | Garde-fou LLM : tout chiffre de la réponse doit figurer dans une sortie d'outil ou la question ; sinon une relance, puis gabarit | règle 3 vérifiable par test |
| D13 | Écarts en piste = distance convertie en temps avec le temps au tour moyen du pilote | iRacing ne donne pas mieux pour les autres |
| D14 | Indices de setup relatifs (rapport de direction 12:1, empattement 2,46 m configurables), signes volant/latéral estimés par corrélation | ne pas supposer de convention |
| D15 | Calibration des seuils du coach = max(base, 1,5 × dispersion), plafonnée à 3 × base, appliquée à la séance suivante | réversible, explicable |
| D16 | Démarrage avec iRacing : raccourci dans « Démarrage » de Windows + attente passive (sondage 2 s) | plus simple qu'un service Windows |
| D17 | Interface = page web locale servie par l'application (stdlib `http.server`, HTML/CSS/JS sans dépendance ni CDN), lecture seule | aucune dépendance, utilisable sur un second écran/tablette, hors ligne |
| D18 | Direct par Server-Sent Events à 4 Hz (temps de session), instantané publié par affectation atomique | coût minimal dans la boucle, pas de verrou |
| D19 | Historique de l'interface calculé dans un processus séparé à basse priorité | règle 4, même si l'on consulte en roulant |
| D20 | Style sombre futuriste inspiré de Go Setups, sans logo, nom ni ressource de cette marque ; police Bahnschrift (fournie avec Windows) | identité propre, rien à télécharger |
| D21 | Interface en français uniquement (les textes générés suivent `language`) | simplicité ; traduction ajoutable plus tard |
| D22 | Rappels d'entraînement en P2, durée de vie 2,5 s, déclenchés ~3,5 s avant le freinage de la référence et jamais avant la sortie du virage précédent ; virage ignoré si la ligne droite fait moins de 2 s | un rappel en retard est pire qu'aucun rappel |
| D23 | Virages ciblés : programme d'entraînement (profil), sinon points faibles, sinon pires virages du dernier tour ; 3 au maximum | un seul thème par séance |
| D24 | Traces : grille de 2 m, référence = tour importé, sinon meilleur tour de la séance (2e meilleur si l'on affiche le meilleur) ; tours invalides traçables | lisibilité et poids du JSON (~150 Ko) |
