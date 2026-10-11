# Application de gestion (kit gestion)
mots-clés: erp, crm, gestion, facture, facturation, devis, client, produit, stock, gmao, planning, module, commande, livraison, intervention, chantier, fournisseur, inventaire, logiciel
description: Construire ou étendre une application de gestion à partir du kit gestion.

Dossier vide : `create_project` kit « gestion ». La base a déjà connexion et rôles, utilisateurs, journal, paramètres société, clients, catalogue, devis → factures, interventions avec planning. Tout est testé : tu ne réécris rien de tout ça.

Méthode, un module à la fois :
1. Liste les modules métier demandés et leurs champs. Un module de base inutile (ex. interventions pour un négoce) : `delete_file` sur `app/modules/<nom>.py`.
2. Chaque nouveau module : `add_module` (nom au pluriel, champs typés, `ref` vers un module existant, `document=true` pour un document chiffré avec lignes et totaux, `planning` sur un champ date). Crée d'abord les modules dont les autres dépendent.
3. Règles métier (calculs, contrôles, transformation d'un document en un autre) : lis le module, puis `edit_file`. Contrôle : `avant_enregistrement=fonction(objet, db)` qui lève `ValueError("message")`. Bouton : `actions=[Action("nom", "Libellé", fonction(objet, db, utilisateur) -> adresse)]`. Exemples dans `app/modules/devis.py` et `interventions.py`. Une règle métier = un test dans `tests/test_<module>.py`.
4. `verify_project` après chaque module. Corrige jusqu'à OK avant le suivant.

À savoir :
- En Python, un champ ref `client` donne `objet.client` (l'objet lié) et `objet.client_id`.
- Montants : `Decimal`, jamais de float. Totaux de documents : `app/modules/_documents.py`.
- Rôles : `app/config.py` ; un module se restreint avec `lecture=[...]`, `ecriture=[...]`.
- Mot de passe admin : affiché au démarrage (server_logs) et dans `data/admin-initial.txt`.
- Ne modifie `app/core/` que si c'est indispensable, et relance alors tous les tests.
