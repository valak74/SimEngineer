# Avancement

| Phase | État | Notes |
|---|---|---|
| 0 Socle | fait | config, logging, VARS, CLI, docs |
| 1 Sources et tours | fait | synthetic, ibt_replay (lecteur .ibt maison, aller-retour testé), live (non testé sans iRacing) ; tours, validité, grille 1 m, SQLite + NPZ |
| 2 Parole | fait | bus d'événements, arbitre P0-P3, gabarits FR/EN, Piper + cache mémoire/disque, choix du périphérique ; son réel non testé (pas de carte audio ici) |
| 3 Spotter | fait | règles pures + hystérésis + cooldowns ; scénarios synthétiques (arrêtée à 300 m, dépassement, accident) ; journal JSONL avec latence de traitement |
| 4 Ingénieur | fait | carburant (moyenne glissante de tours propres, marge, réserve), fenêtre de stand (tours / temps / jaune longue / multi-arrêts), pneus, météo, écarts, commandes de stand derrière option désactivée |
| 5 Coach | fait | détection de virages, 13 métriques par virage, règles seuillées, comparaison à une référence, classement par gain, 1-2 conseils P2, worker basse priorité, rapport de session par gabarit ; défauts injectés retrouvés et chiffrés |
| 6 Dialogue et LLM | fait | STT faster-whisper + push-to-talk (clavier/volant), client OpenAI-compatible Qwen -> DeepSeek (délai 8 s, 1 relance, bascule, plafond quotidien), 9 outils validés par pydantic, garde-fou « chiffres sourcés », mode tout local par gabarits, commentaire de rapport ; appels réels non testés (pas de clé) |
| 7 Références | fait | import .ibt et CSV (unités auto, temps reconstruit), provenance + droits obligatoires, delta temps réel O(1), sélection automatique même circuit/même voiture |
| 8 Profil et programme | fait | tendances par virage multi-séances (pente, défaut dominant), un thème par séance avec exercices et objectif chiffré, calibration des seuils sur la dispersion du pilote (appliquée à la séance suivante) |
| 9 Setup | fait | indice de sous-virage et contre-braquages par phase (signes estimés, pas supposés), températures/pressions pneus, pistes de réglage, avant/après sur tours propres avec verdict, guide d'application manuelle |
| 10 Packaging et perf | fait | scripts Windows (installation, démarrage auto, PyInstaller), `perf` : 1,74 % d'un cœur et 80 Mo en simulé, `measure-position`, attente et reconnexion iRacing, source live testée avec un faux pyirsdk ; tout reste à vérifier sur le PC (RUN_ON_PC.md) |
| 11 Interface | fait | interface web locale sombre/futuriste : Direct (delta, tour, carburant, stand, carte du circuit avec voitures, radio, coach, écarts, pneus, météo), Séances, Pilote, Setup, Références ; 1,5 % d'un cœur et 87 Mo avec un client connecté ; rendu vérifié par captures (Chromium), pas encore sur le PC |
| 12 Entraînement + traces | fait | mode entraînement (`run --training`) : rappel vocal ≤ 8 mots dans la ligne droite ~3,5 s avant le freinage des virages du programme, chiffré par le dernier tour ; traces superposées tour/référence dans Séances (delta, vitesse, gaz, frein, volant), zoom par virage, réticule |
| 13 Fiabilité et GT3 | fait | CI Windows + Ubuntu ; confirmation vocale des commandes de stand ; virages nommés par circuit (`track export`) ; alerte « voiture plus rapide derrière » (écart en temps) ; README complet avec captures |
| 14 Ingénieur complet | fait | économie de carburant chiffrée (lever de pied par virage, vérification, renfort, abandon) ; conscience de course (incidents, stands, position, écarts officiels et tendance, derniers tours) ; briefing et débrief ; qualification (tour lancé, secteurs) ; tendance par virage ; bouton qui coupe l'ingénieur ; interface (position, incidents, plan d'économie) |

## Synthèse

- 11 phases committées ; `pytest -q`, `ruff check .`, `mypy src` (strict) verts.
- Aucune hypothèse iRacing, voix ou LLM n'est vérifiée : 29 points à faire sur le PC,
  listés dans `docs/RUN_ON_PC.md`.
