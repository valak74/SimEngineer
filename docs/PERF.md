# Performance — budget du collecteur (règle 4)

Budget : collecteur **< 3 % d'un cœur**, **< 150 Mo de RAM** hors modèles vocaux, aucun GPU.
Analyses lourdes dans un processus séparé à basse priorité (`coach/worker.py`,
`IDLE_PRIORITY_CLASS` sous Windows, `nice 10` ailleurs).

## Mesure reproductible

```
simengineer perf --seconds 180 [--source live] [--json perf.json] [--check]
```

Fait tourner l'application **en temps réel** (60 Hz) avec voix « null » (hors modèles
vocaux) et mesure le temps CPU et la RSS du processus principal, plus le worker à part.
Scénario par défaut : `perf` (4 tours, 20 voitures en trafic dense, un jaune).
`--check` renvoie le code 1 hors budget.

## Résultats dans l'environnement de développement (simulé, Linux)

Mesure du 2026-11-14, source `synthetic`, 180 s, 10 800 images, Python 3.13,
Intel Xeon 2,1 GHz (4 cœurs logiques, machine virtuelle) :

| Mesure | Valeur | Budget |
|---|---|---|
| CPU du collecteur | **1,74 % d'un cœur** | < 3 % |
| RAM du collecteur (RSS) | **80 Mo** | < 150 Mo |
| Temps par image | 0,11 ms en moyenne, 11 ms au pire (fin de tour : écriture SQLite) | 16,7 ms |
| Worker d'analyse | 0,6 s CPU cumulées pour 3 tours, 63 Mo | processus séparé |

Optimisations appliquées après une première mesure à 2,27 % :
- trafic (voitures lentes devant, accident) évalué à 20 Hz au lieu de 60 Hz ; côtés
  (`CarLeftRight`), drapeaux et hors-piste restent à 60 Hz ;
- variables lentes (pneus, météo) lues à 1 Hz seulement ;
- ingénieur recalculé à 1 Hz et à chaque tour, jamais par image ;
- rejeu temps réel : colonnes gardées en tableaux numpy (mémoire minimale).

**À confirmer sur le PC de jeu** avec `--source live` (RUN_ON_PC.md, points 23 à 25) :
la source live fait un appel pyirsdk par variable et par image, coût non mesuré ici.

## Avec l'interface (navigateur)

Le serveur de l'interface tourne dans le processus du collecteur : son coût est compté.
`simengineer perf --seconds 120 --with-ui` (serveur démarré + un client abonné au flux direct) :

| Mesure | Valeur | Budget |
|---|---|---|
| CPU du collecteur | **1,51 % d'un cœur** | < 3 % |
| RAM du collecteur (RSS) | **87 Mo** | < 150 Mo |

L'instantané direct est construit 4 fois par seconde (temps de session) à partir de données
déjà calculées ; les pages d'historique (analyse, profil, setup) sont calculées dans un
processus séparé à basse priorité. Le rendu dans le navigateur consomme du CPU/GPU côté
navigateur : sur le PC de jeu, préférer un second écran, une tablette ou un téléphone
(`ui.host = "0.0.0.0"`), et vérifier les FPS (RUN_ON_PC.md, point 31).

## Hors budget du collecteur (mesurés à part sur le PC)

- **TTS Piper** : thread dédié ; phrases P0 pré-synthétisées au démarrage (cache disque).
- **STT faster-whisper `small` int8** : chargé au premier appui, 2 threads CPU, actif
  uniquement après relâchement du bouton push-to-talk. Si trop gourmand : interface
  `STTBackend` pour passer à une API de transcription.
- **LLM** : appels réseau dans le thread de dialogue, jamais dans la boucle 60 Hz.
