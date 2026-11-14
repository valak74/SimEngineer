# SimEngineer — règles du projet

Ingénieur de course IA local pour iRacing (Porsche Cup, puis GT3), sur le PC de jeu
Windows 10/11 du pilote. Python 3.12. Ne doit jamais voler de ressources au simulateur.

## Règles non négociables
1. **Lecture seule du sim.** Seules écritures : commandes broadcast du SDK pour le stand,
   derrière `engineer.pit_commands_enabled` (désactivé par défaut). Jamais d'injection,
   jamais de modification de fichiers iRacing.
2. **Spotter et alertes de danger = règles pures**, sans réseau ni LLM, donnée → parole < 200 ms.
3. **Le LLM ne calcule jamais.** Carburant, deltas, distances, fenêtres de stand sont calculés
   en code. Le LLM reformule et répond via des outils qui lisent des données déjà calculées.
   Jamais dans la boucle temps réel du spotter.
4. **Budget** : collecteur < 3 % d'un cœur, < 150 Mo de RAM hors modèles vocaux, aucun GPU.
   Analyses lourdes dans un processus à basse priorité.
5. **Local-first** : données brutes en local (SQLite + NPZ). Seuls des résumés structurés vont
   au LLM (jamais le flux brut, jamais de données personnelles). Mode « tout local » : sans LLM,
   tout fonctionne par gabarits.
6. **Développable sans iRacing** : sources `live`, `ibt_replay`, `synthetic`. Tout doit être
   testable avec `synthetic`.
7. **N'invente aucun nom de variable iRacing.** `dump-vars` écrit `docs/irsdk_vars.md` depuis la
   vraie télémétrie. Toutes les variables passent par la constante unique `VARS`
   (`src/simengineer/core/vars.py`), validée au démarrage. Les noms supposés sont des hypothèses
   marquées « à vérifier » dans `docs/ASSUMPTIONS.md`.
8. **Données de référence** : uniquement des fichiers que le pilote possède. Pas de scraping.
9. **Aucune clé d'API dans le code ni dans git** : variables d'environnement ou `.env` ignoré.

## Limites de conception
- Pas de télémétrie complète des autres pilotes ni de position latérale : voiture arrêtée devant
  = distance annoncée, pas le côté. Le côté ne vient que de `CarLeftRight` (voitures adjacentes).
  Vitesse des autres voitures estimée par la variation de `CarIdxLapDistPct`.
- Comparaison aux pros = tours de référence chargés avant, pas de flux en direct.
- Précision des trajectoires limitée par le SDK : à mesurer, ne pas promettre.

## Priorités de parole (arbitre unique, une seule voix)
P0 danger (coupe tout, < 200 ms) > P1 course > P2 coaching (ligne droite uniquement, jamais en
freinage ou duel) > P3 conversation. Anti-répétition par clé, expiration des messages périmés,
≤ 8 mots pour P0/P2.

## Méthode
- Une phase à la fois (voir `docs/ROADMAP.md`) : tests, implémentation, puis
  `pytest -q && ruff check . && mypy src` verts, mise à jour de `docs/PROGRESS.md`, commit.
- Ambiguïté : option la plus simple et réversible, notée dans `docs/DECISIONS.md`.
- Ce qui exige iRacing réel : code + procédure dans `docs/RUN_ON_PC.md`. Ne jamais marquer une
  hypothèse « vérifiée » sans retour du pilote.
- Pas de push, pas de suppression hors du repo, pas d'installation hors dépendances du projet.

## Commandes
```
pip install -e ".[dev]"
pytest -q && ruff check . && mypy src
simengineer run --source synthetic --no-realtime
```

## Structure
`src/simengineer/{core,sources,telemetry,spotter,engineer,coach,reference,setup,voice,llm,store}`,
`tests/`, `docs/`. Le français est la langue des docs, des messages et des commentaires.
