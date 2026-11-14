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
