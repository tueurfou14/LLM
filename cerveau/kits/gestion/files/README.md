# __TITRE__

Application de gestion : FastAPI, SQLite, pages HTML.

## Lancer

```
uv run uvicorn app.main:app --reload --port 8000
```

Ouvrir http://127.0.0.1:8000. Au premier démarrage, le compte `admin` est créé : son mot de passe
s'affiche dans la console et dans `data/admin-initial.txt` (ou fixé par la variable `ADMIN_PASSWORD`).
La base est le fichier `data/app.db`.

## Fonctions communes à tous les modules

Liste avec recherche et pagination, export CSV, import CSV ou Excel (bouton Importer : tout ou rien,
liens donnés par leur nom), journal des actions. Modules imprimables : impression, PDF, envoi par mail
(serveur SMTP dans Paramètres ; mot de passe aussi possible par la variable SMTP_PASSWORD).
Factures : numérotation continue, verrouillage une fois émises, PDF Factur-X (profil BASIC, XML vérifié
contre le schéma officiel ; faire valider un exemplaire avant la mise en production), page Impayés
avec relance par mail.

## Tester

```
uv run pytest -q
```

Chaque module est testé automatiquement (`tests/test_modules.py`) : création, modification, liste,
recherche, export, impression, planning, actions, suppression. Les règles métier se testent à part.

## Structure

```
app/config.py           titre et rôles
app/modules/<nom>.py    un fichier par module métier, chargé automatiquement
app/modules/_*.py       aides partagées (ex. _documents.py : lignes, totaux, copie de document)
app/core/               socle : moteur des modules, connexion, utilisateurs, journal, paramètres
app/templates/          pages HTML génériques
tests/                  tests
```

## Déclarer un module

```python
from datetime import date
from app.core.framework import Champ, Module

module = Module(
    nom="vehicules", libelle="Véhicules", objet="véhicule", ordre=15, titre="immatriculation",
    champs=[
        Champ("immatriculation", "Immatriculation", requis=True),
        Champ("client", "Client", type="ref", ref="clients", requis=True),
        Champ("mise_en_circulation", "Mise en circulation", type="date"),
        Champ("kilometrage", "Kilométrage", type="entier"),
        Champ("carburant", "Carburant", type="choix", choix=["Diesel", "Essence", "Électrique"]),
    ],
)
```

Types : `texte`, `texte_long`, `entier`, `decimal`, `monnaie`, `date`, `booleen`, `choix` (avec `choix=[...]`),
`email`, `telephone`, `ref` (lien, `ref="<module>"` ; en Python `objet.client` est l'objet lié et
`objet.client_id` son numéro), `numero` (attribué à la création, `format="BC-{annee}-{n:04d}"`),
`calcul` (lecture seule, `calcul=lambda obj: ...`, `affichage="monnaie"`).

Options de `Module` : `feminin=True` (accords « Nouvelle facture »), `ordre` (place dans le menu),
`titre` (champ qui nomme l'objet), `tri="-date"`, `lecture=[rôles]`, `ecriture=[rôles]`,
`suppression=False`, `imprimable=True`, `planning="<champ date>"`, `lignes=Lignes(...)` ou
`lignes_standard()` avec `*champs_totaux()` pour un document chiffré, `actions=[Action(...)]`,
`avant_enregistrement=fonction(objet, db)` qui lève `ValueError("message")` pour refuser,
`verrouille=lambda obj: ...` et `modifiable_si_verrouille=[champs]`, `journal_consultations=True`
(données sensibles : chaque consultation est inscrite au journal).

Un champ ajouté à un module existant est ajouté seul à la base au démarrage. Un champ retiré reste en base.
