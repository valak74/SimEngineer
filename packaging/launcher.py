"""Point d'entrée de l'exécutable PyInstaller."""

import multiprocessing

from simengineer.cli import app

if __name__ == "__main__":
    multiprocessing.freeze_support()  # requis pour le worker d'analyse dans un .exe
    app()
