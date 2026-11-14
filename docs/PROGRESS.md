# Avancement

| Phase | État | Notes |
|---|---|---|
| 0 Socle | fait | config, logging, VARS, CLI, docs |
| 1 Sources et tours | fait | synthetic, ibt_replay (lecteur .ibt maison, aller-retour testé), live (non testé sans iRacing) ; tours, validité, grille 1 m, SQLite + NPZ |
| 2 Parole | fait | bus d'événements, arbitre P0-P3, gabarits FR/EN, Piper + cache mémoire/disque, choix du périphérique ; son réel non testé (pas de carte audio ici) |
| 3 Spotter | fait | règles pures + hystérésis + cooldowns ; scénarios synthétiques (arrêtée à 300 m, dépassement, accident) ; journal JSONL avec latence de traitement |
| 4 Ingénieur | fait | carburant (moyenne glissante de tours propres, marge, réserve), fenêtre de stand (tours / temps / jaune longue / multi-arrêts), pneus, météo, écarts, commandes de stand derrière option désactivée |
| 5 Coach | fait | détection de virages, 13 métriques par virage, règles seuillées, comparaison à une référence, classement par gain, 1-2 conseils P2, worker basse priorité, rapport de session par gabarit ; défauts injectés retrouvés et chiffrés |
| 6 Dialogue et LLM | à faire | |
| 7 Références | à faire | |
| 8 Profil et programme | à faire | |
| 9 Setup | à faire | |
| 10 Packaging et perf | à faire | |
