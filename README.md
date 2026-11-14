# SimEngineer

Ingénieur de course IA local pour iRacing : spotter vocal, carburant et stands, coach par
virage, dialogue vocal push-to-talk, références et conseils de setup.

- Règles du projet : [CLAUDE.md](CLAUDE.md)
- Spécification : [docs/SPEC.md](docs/SPEC.md)
- Installation et vérifications sur le PC de jeu : [docs/RUN_ON_PC.md](docs/RUN_ON_PC.md)
- Hypothèses à vérifier : [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md)

```
pip install -e ".[dev]"
simengineer run --source synthetic --no-realtime   # démonstration sans iRacing
pytest -q
```
