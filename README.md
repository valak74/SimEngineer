# SimEngineer

Ingénieur de course IA local pour iRacing : spotter vocal, carburant et stands, coach par
virage, dialogue vocal push-to-talk, références et conseils de setup.

- Règles du projet : [CLAUDE.md](CLAUDE.md)
- Spécification : [docs/SPEC.md](docs/SPEC.md)
- Installation et vérifications sur le PC de jeu : [docs/RUN_ON_PC.md](docs/RUN_ON_PC.md)
- Hypothèses à vérifier : [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md)

Interface : `http://127.0.0.1:8765/` (sombre, futuriste ; ouverte par `simengineer run`).

```
pip install -e ".[dev]"
simengineer run --source synthetic --scenario race --speed 6 --null-voice --keep-ui   # démo avec interface
pytest -q
```
