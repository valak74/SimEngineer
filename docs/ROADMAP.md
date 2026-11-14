# Feuille de route

| Phase | Contenu | Critère de fin |
|---|---|---|
| 0 | Socle : config TOML + env, logging, CLI, ruff/mypy, docs | CLI démarre, outils verts |
| 1 | Sources (live, ibt_replay, synthetic), tours, grille 1 m, stockage | tours synthétiques stockés et relus |
| 2 | Bus d'événements, arbitre de parole, TTS Piper + cache, périphérique audio, gabarits FR/EN | priorités et anti-répétition testés |
| 3 | Spotter : règles pures, hystérésis, cooldowns, scénarios, journal JSONL | 3 scénarios synthétiques verts |
| 4 | Ingénieur : carburant, fenêtre de stand, pneus, météo, commandes de stand | calculs testés (tours/temps/jaune) |
| 5 | Coach : virages, métriques, règles, comparaison, classement | défauts injectés retrouvés |
| 6 | Dialogue et LLM : STT, client multi-fournisseurs, outils, rapport | faux client : chiffres exacts, bascule, mode local |
| 7 | Références : import .ibt/CSV, droits, delta temps réel, sélection | delta testé |
| 8 | Profil pilote et programme d'entraînement, calibration des seuils | tendances testées |
| 9 | Conseiller de setup : diagnostic, avant/après, guide | diagnostics testés |
| 10 | Packaging Windows, mesure CPU/RAM, PERF.md, démarrage avec iRacing | mesure reproductible |

L'avancement réel est dans `docs/PROGRESS.md`.
