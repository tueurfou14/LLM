"""Modules métier : un fichier par module, chargé automatiquement.

Ajouter un module = créer app/modules/<nom>.py qui déclare `module = Module(...)`.
Retirer un module = supprimer son fichier (ses données restent en base).
Les fichiers qui commencent par _ sont des aides partagées, pas des modules.
"""

import importlib
import pkgutil


def charger() -> None:
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if not info.name.startswith("_"):
            importlib.import_module(f"{__name__}.{info.name}")
