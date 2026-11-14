# Spécification — SimEngineer

## Objectif
Une seule application locale qui joue le rôle d'ingénieur de course pour iRacing :
spotter vocal, stratégie carburant/stand, coach par virage, dialogue vocal push-to-talk,
débriefs, références et conseils de setup. Voir `CLAUDE.md` pour les règles non négociables.

## Architecture

```
            +-----------+   Frame 60 Hz   +----------------------------------------+
 iRacing -> |  sources  | --------------> | boucle principale (un seul thread)      |
 .ibt   ->  | live/ibt/ |                 |  spotter (règles pures)  -> P0          |
 synth  ->  | synthetic |                 |  segmenteur de tours     -> tours       |
            +-----------+                 |  ingénieur (1 Hz + par tour) -> P1      |
                                          |  delta référence (O(1) par image)       |
                                          +------------+---------------------------+
                                                       | énoncés (priorité, clé, TTL)
                                                       v
   processus basse priorité            +---------------------------+     +-----------+
   analyse du tour (coach) ----P2----> |  arbitre de parole unique | --> | TTS Piper |
   thread dialogue (STT→LLM→outils) -P3|  P0 > P1 > P2 > P3        |     | + cache   |
                                       +---------------------------+     +-----------+
```

- **Boucle principale** : ne fait que des opérations O(1)/O(nb voitures) par image. Aucun
  accès réseau, aucun disque synchrone hors fin de tour.
- **Analyse** (rééchantillonnage, métriques par virage, conseils) : processus séparé à basse
  priorité (`coach/worker.py`).
- **Dialogue** : thread séparé ; le LLM ne voit que des sorties d'outils (`llm/tools.py`)
  lisant un instantané `RaceState` déjà calculé.

## Modules
| Module | Rôle |
|---|---|
| `core` | configuration, journalisation, `VARS`, types (`Frame`, `SessionInfo`), bus d'événements |
| `sources` | `live` (pyirsdk), `ibt_replay` (lecteur .ibt maison), `synthetic` (scénarios) |
| `telemetry` | segmentation en tours, validité, rééchantillonnage sur grille de 1 m |
| `store` | SQLite (sessions, tours, références, métriques, setup) + NPZ par tour |
| `voice` | gabarits FR/EN, arbitre de parole, TTS (Piper + cache), STT, push-to-talk |
| `spotter` | règles pures + hystérésis + cooldowns, journal JSONL |
| `engineer` | carburant, fenêtre de stand, pneus, météo, écarts, commandes de stand |
| `coach` | virages, métriques, comparaison, conseils classés, débrief, rapport, profil, programme |
| `reference` | import .ibt/CSV avec métadonnées de droits, delta temps réel, sélection |
| `setup` | diagnostic sous/survirage et pneus, avant/après, guide d'application manuelle |
| `llm` | client multi-fournisseurs, outils validés, dialogue, budget, rapport |
| `ui` | interface web locale en lecture seule : direct (SSE 4 Hz) et historique (processus basse priorité) |

## Données
- `data/simengineer.db` : SQLite.
- `data/laps/<session>/<tour>.npz` : canaux bruts (temps) + grille 1 m.
- `data/refs/*.npz` : tours de référence importés.
- `data/alerts.jsonl` : journal des alertes spotter (avec latence de traitement).
- `data/tts_cache/` : phrases pré-synthétisées.

## Ce qui part vers le LLM
Uniquement : la question transcrite du pilote, le prompt système, les définitions d'outils et
les sorties JSON des outils (chiffres déjà calculés). Jamais de flux brut, de nom de pilote,
d'identifiant iRacing ni de chemin de fichier.
