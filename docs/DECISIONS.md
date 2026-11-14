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
