"""Administration sous /admin : connexion, droits, utilisateurs, journal, textes, paramètres, liens et formulaires."""

from urllib.parse import urlsplit

from conftest import ADMIN, analyser

from app.core.modeles import Journal


def test_sante(anonyme):
    assert anonyme.get("/sante").json() == {"ok": True}


def test_admin_sans_barre_finale(anonyme):
    reponse = anonyme.get("/admin", follow_redirects=False)
    assert reponse.status_code in (303, 307) and reponse.headers["location"].endswith("/admin/")


def test_admin_reserve_aux_connectes(anonyme):
    for page in ("/admin/", "/admin/textes", "/admin/parametres", "/admin/utilisateurs", "/admin/journal",
                 "/admin/mon-compte"):
        reponse = anonyme.get(page, follow_redirects=False)
        assert reponse.status_code == 303, f"{page} est accessible sans connexion"
        assert reponse.headers["location"].startswith("/admin/connexion?suite="), f"{page} : mauvaise redirection"
    reponse = anonyme.post("/admin/textes", data={"accueil_titre": "Piraté"}, follow_redirects=False)
    assert reponse.status_code == 303, "/admin/textes accepte un envoi sans connexion"


def test_connexion_ramene_a_la_page_demandee(anonyme):
    reponse = anonyme.post("/admin/connexion", data={"identifiant": "admin", "mot_de_passe": "admin-mot-de-passe",
                                                     "suite": "/admin/textes"}, follow_redirects=False)
    assert reponse.headers["location"] == "/admin/textes"


def test_mauvais_mot_de_passe(anonyme):
    reponse = anonyme.post("/admin/connexion", data={"identifiant": "admin", "mot_de_passe": "faux"})
    assert reponse.status_code == 401 and "incorrect" in reponse.text


def test_freinage_apres_cinq_echecs(anonyme):
    for _ in range(5):
        anonyme.post("/admin/connexion", data={"identifiant": "admin", "mot_de_passe": "faux"})
    reponse = anonyme.post("/admin/connexion", data={"identifiant": "admin", "mot_de_passe": "admin-mot-de-passe"})
    assert "Réessayez dans une minute" in reponse.text


def test_redirection_apres_connexion_reste_sur_le_site(anonyme):
    for suite in ("//exemple.com", "https://exemple.com", "/\\exemple.com"):
        reponse = anonyme.post("/admin/connexion", data={"identifiant": "admin", "mot_de_passe": "admin-mot-de-passe",
                                                         "suite": suite}, follow_redirects=False)
        assert reponse.headers["location"] == "/admin/", f"redirection ouverte vers {suite}"


def test_cookie_de_session_limite_a_l_admin(anonyme):
    public = anonyme.get("/")
    assert "set-cookie" not in public.headers, "le site public ne doit déposer aucun cookie"
    reponse = anonyme.post("/admin/connexion", data={"identifiant": "admin", "mot_de_passe": "admin-mot-de-passe"},
                           follow_redirects=False)
    assert "path=/admin" in reponse.headers["set-cookie"].lower()


def test_cookie_d_une_autre_application_locale_sans_effet(anonyme):
    """Sur 127.0.0.1, une autre application peut avoir déposé son propre cookie « session » (chemin /)."""
    anonyme.cookies.set("session", "cookie-d-une-autre-application", domain="testserver", path="/")
    anonyme.post("/admin/connexion", data={"identifiant": "admin", "mot_de_passe": "admin-mot-de-passe"})
    assert anonyme.get("/admin/", follow_redirects=False).status_code == 200, "la connexion ne tient pas"


def test_deconnexion(http):
    reponse = http.get("/admin/deconnexion", follow_redirects=False)
    assert reponse.headers["location"] == "/admin/connexion"
    assert http.get("/admin/", follow_redirects=False).status_code == 303


def test_pages_administration(http):
    for page in ("/admin/", "/admin/utilisateurs", "/admin/utilisateurs/nouveau", "/admin/journal", "/admin/textes",
                 "/admin/parametres", "/admin/mon-compte"):
        assert http.get(page).status_code == 200, f"{page} en erreur"


def test_liens_et_formulaires_de_l_admin_restent_sous_admin(http):
    """Parcourt l'administration depuis le tableau de bord : chaque lien interne répond et chaque formulaire
    envoie vers /admin/… (un lien oublié sans le préfixe /admin donnerait une page introuvable)."""
    a_voir, vues = ["/admin/"], set()
    while a_voir and len(vues) < 150:
        page = a_voir.pop()
        if page in vues:
            continue
        vues.add(page)
        reponse = http.get(page)
        assert reponse.status_code == 200, f"{page} répond {reponse.status_code}"
        if not reponse.headers["content-type"].startswith("text/html"):
            continue
        html = analyser(reponse.text)
        for form in html.formulaires:
            action = form.get("action", "")
            assert action.startswith("/admin/") or (form.get("method", "get").lower() == "get" and action == ""), (
                f"{page} : formulaire envoyé vers « {action} » au lieu de /admin/…")
        for balise, _, lien in html.liens:
            chemin = urlsplit(lien).path
            if balise != "a" or not chemin.startswith("/") or lien.startswith("//"):
                continue
            if chemin.startswith("/admin/"):
                if not any(fin in chemin for fin in ("/deconnexion", "export.csv")):
                    a_voir.append(chemin)
            else:
                assert chemin == "/" or chemin.startswith(("/medias/", "/static/", "/actualites/")), (
                    f"{page} : lien « {lien} » hors de /admin (oubli du préfixe {{{{ ADMIN }}}} ou module.adresse ?)")


def test_utilisateur_redaction(http, anonyme):
    http.post("/admin/utilisateurs/nouveau", data={"identifiant": "marc", "mot_de_passe": "motdepasse-marc",
                                                   "roles": "redaction", "actif": "on"})
    http.get("/admin/deconnexion")
    anonyme.post("/admin/connexion", data={"identifiant": "marc", "mot_de_passe": "motdepasse-marc"})
    assert anonyme.get("/admin/").status_code == 200
    assert anonyme.get("/admin/textes").status_code == 200, "la rédaction doit pouvoir modifier les textes"
    assert anonyme.get("/admin/services/").status_code == 200
    for page in ("/admin/utilisateurs", "/admin/parametres", "/admin/journal"):
        assert anonyme.get(page).status_code == 403, f"{page} doit être réservé aux administrateurs"


def test_changement_de_mot_de_passe(http):
    reponse = http.post("/admin/mon-compte", data={"actuel": "admin-mot-de-passe", "nouveau": "nouveau-mot-de-passe",
                                                   "confirmation": "nouveau-mot-de-passe"}, follow_redirects=False)
    assert reponse.status_code == 303 and reponse.headers["location"] == "/admin/mon-compte"


def test_journal_des_actions(http, db):
    http.post("/admin/services/nouveau", data={"titre": "Vidange", "resume": "Rapide", "position": "1"})
    assert db.query(Journal).filter_by(action="création", module="services").count() == 1
    http.post("/admin/textes", data={"accueil_titre": "Nouveau titre"})
    assert db.query(Journal).filter_by(module="textes").count() == 1


def test_saisie_invalide_reaffiche_le_formulaire(http):
    reponse = http.post("/admin/services/nouveau", data={"titre": "X", "resume": "Y", "position": "abc"})
    assert reponse.status_code == 422 and "nombre entier attendu" in reponse.text


def test_parametre_invalide_refuse(http):
    reponse = http.post("/admin/parametres", data={"couleur_primaire": "rouge", "email": "pas-un-email",
                                                   "facebook": "facebook.com/x"})
    assert reponse.status_code == 422
    for message in ("couleur attendue", "adresse email invalide", "commençant par https://"):
        assert message in reponse.text, f"/admin/parametres : message « {message} » absent"


def test_page_admin_inconnue(http):
    reponse = http.get("/admin/nimporte-quoi")
    assert reponse.status_code == 404 and "introuvable" in reponse.text.lower()
