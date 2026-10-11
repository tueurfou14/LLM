"""Site public : chaque page répond, a un titre, une description, un seul <h1>, des images avec texte
alternatif et des liens internes valides ; ce qui est modifié dans l'administration apparaît sur le site."""

from markupsafe import escape
from urllib.parse import urlsplit

import pytest

from conftest import analyser

from app.core.framework import REGISTRE
from app.site.contenu import installer_exemples
from app.site.public import PAGES, lien, publies

ADRESSES = [p.adresse for p in PAGES]
EXTERNES = ("http://", "https://", "//", "mailto:", "tel:", "data:", "javascript:", "#")


def _gabarit(adresse: str) -> str:
    page = next((p for p in PAGES if p.adresse == adresse), None)
    return f"app/templates/site/{page.gabarit}" if page else f"la page {adresse}"


def _pages_a_verifier(client, db) -> list[str]:
    """Pages du menu + une page d'actualité + la page 404."""
    actualites = publies(REGISTRE["actualites"], db)
    assert actualites, "les actualités d'exemple de contenu/site.json n'ont pas été installées"
    return ADRESSES + [lien(actualites[0]), "/page-qui-n-existe-pas"]


def test_les_pages_obligatoires_existent():
    for adresse in ("/", "/contact", "/mentions-legales"):
        assert adresse in ADRESSES, f"app/site/public.py : la page {adresse} doit rester dans PAGES"


@pytest.mark.parametrize("adresse", ADRESSES)
def test_page_publique(anonyme, adresse):
    reponse = anonyme.get(adresse)
    assert reponse.status_code == 200, (
        f"{_gabarit(adresse)} : la page {adresse} répond {reponse.status_code}. "
        f"{reponse.text[:800] if reponse.status_code != 500 else 'Erreur 500 : voir la trace ci-dessus.'}")
    page = analyser(reponse.text)
    assert page.lang == "fr", f"{adresse} : la balise <html> doit avoir lang=\"fr\" (app/templates/site/_base.html)"
    assert page.titre.strip(), f"{_gabarit(adresse)} : le <title> est vide. Ajoute {{% set titre = \"...\" %}} en haut."
    assert page.description and page.description.strip(), (
        f"{_gabarit(adresse)} : la meta description est vide. Ajoute {{% set description = \"...\" %}} en haut.")
    assert page.nb_h1 == 1, (
        f"{_gabarit(adresse)} : il faut exactement un <h1> par page, il y en a {page.nb_h1}. "
        "Garde un seul <h1> (le titre principal) et utilise <h2>/<h3> pour les autres titres.")
    for img in page.images:
        assert (img.get("alt") or "").strip(), (
            f"{_gabarit(adresse)} : l'image « {img.get('src')} » n'a pas de texte alternatif (alt=\"...\").")


def test_page_actualite_et_404(anonyme, db):
    article = publies(REGISTRE["actualites"], db)[0]
    for adresse, statut in ((lien(article), 200), ("/page-qui-n-existe-pas", 404)):
        reponse = anonyme.get(adresse)
        assert reponse.status_code == statut, f"{adresse} répond {reponse.status_code} au lieu de {statut}"
        page = analyser(reponse.text)
        assert page.nb_h1 == 1 and page.titre.strip() and page.description, f"{adresse} : titre, description ou <h1> incorrect"
        assert all((img.get("alt") or "").strip() for img in page.images), f"{adresse} : image sans texte alternatif"
    assert str(escape(article.titre)) in anonyme.get(lien(article)).text


def test_liens_internes_valides(anonyme, db):
    vus: dict[str, int] = {}
    for adresse in _pages_a_verifier(anonyme, db):
        page = analyser(anonyme.get(adresse).text)
        for balise, attribut, valeur in page.liens:
            if valeur.startswith(EXTERNES) or not valeur.strip():
                continue
            chemin = urlsplit(valeur).path
            assert chemin.startswith("/"), (
                f"{_gabarit(adresse)} : <{balise} {attribut}=\"{valeur}\"> : utilise une adresse qui commence par /")
            if chemin not in vus:
                vus[chemin] = anonyme.get(chemin, follow_redirects=True).status_code
            assert vus[chemin] == 200, (
                f"{_gabarit(adresse)} : <{balise} {attribut}=\"{valeur}\"> mène à une page en erreur ({vus[chemin]}). "
                "Corrige le lien ou ajoute la page (app/site/public.py, PAGES).")


def test_le_menu_mene_a_toutes_les_pages(anonyme):
    html = anonyme.get("/").text
    for page in PAGES:
        if page.menu:
            assert f'href="{page.adresse}"' in html, f"le menu ne contient pas la page {page.adresse}"


def test_sitemap_et_robots(anonyme, db):
    sitemap = anonyme.get("/sitemap.xml")
    assert sitemap.status_code == 200 and "xml" in sitemap.headers["content-type"]
    for adresse in ADRESSES:
        assert f"{adresse}</loc>" in sitemap.text, f"sitemap.xml ne contient pas {adresse}"
    article = publies(REGISTRE["actualites"], db)[0]
    assert f"{lien(article)}</loc>" in sitemap.text, "sitemap.xml ne contient pas les actualités"
    robots = anonyme.get("/robots.txt").text
    assert "Disallow: /admin/" in robots and "Sitemap: " in robots


def test_url_du_site_dans_sitemap_et_partages(http, anonyme):
    http.post("/admin/parametres", data={"url_site": "https://www.exemple.fr"})
    assert "<loc>https://www.exemple.fr/contact</loc>" in anonyme.get("/sitemap.xml").text
    accueil = anonyme.get("/").text
    assert 'rel="canonical" href="https://www.exemple.fr/"' in accueil
    assert 'property="og:url" content="https://www.exemple.fr/"' in accueil


def test_contenus_d_exemple_installes_une_seule_fois(anonyme, http, db):
    services = publies(REGISTRE["services"], db)
    assert services, "les services d'exemple de contenu/site.json n'ont pas été installés"
    assert str(escape(services[0].titre)) in anonyme.get("/").text
    for service in db.query(REGISTRE["services"].modele).all():
        http.post(f"/admin/services/{service.id}/supprimer")
    db.expire_all()
    assert installer_exemples(db) == [], "les exemples ne doivent pas revenir après que le client a tout supprimé"
    assert db.query(REGISTRE["services"].modele).count() == 0


def test_temoignages_d_exemple_masques(anonyme):
    assert "Exemple d'avis" not in anonyme.get("/").text, (
        "les témoignages d'exemple ne doivent pas être publiés : seuls de vrais avis vont sur le site")


def test_actualite_non_publiee_absente_du_site(http, anonyme, db):
    reponse = http.post("/admin/actualites/nouveau", data={
        "titre": "Brouillon secret", "date": "2026-10-01", "chapo": "Pas encore prêt."}, follow_redirects=False)
    assert reponse.status_code == 303, f"création d'une actualité non publiée refusée : {reponse.text[:500]}"
    module = REGISTRE["actualites"]
    article = db.get(module.modele, int(reponse.headers["location"].rsplit("/", 1)[1]))
    assert article.publie is False
    for adresse in ("/", "/actualites", "/sitemap.xml"):
        assert "Brouillon secret" not in anonyme.get(adresse).text, f"{adresse} montre une actualité non publiée"
    assert anonyme.get(lien(article)).status_code == 404, "une actualité non publiée doit répondre 404"
    http.post(f"/admin/actualites/{article.id}", data={"titre": "Brouillon secret", "date": "2026-10-01",
                                                       "chapo": "Prêt.", "publie": "on"})
    assert "Brouillon secret" in anonyme.get("/actualites").text, "une fois publiée, l'actualité doit apparaître"


def test_adresse_d_actualite_corrigee(anonyme, db):
    article = publies(REGISTRE["actualites"], db)[0]
    reponse = anonyme.get(f"/actualites/{article.id}-ancien-titre", follow_redirects=False)
    assert reponse.status_code == 301 and reponse.headers["location"] == lien(article)


def test_offre_expiree_masquee(http, anonyme):
    http.post("/admin/offres/nouveau", data={"titre": "Offre terminée", "fin": "2020-01-31", "publie": "on"})
    http.post("/admin/offres/nouveau", data={"titre": "Offre en cours", "publie": "on"})
    accueil = anonyme.get("/").text
    assert "Offre en cours" in accueil and "Offre terminée" not in accueil


def test_modification_admin_visible_sur_le_site(http, anonyme, db):
    reponse = http.post("/admin/textes", data={"accueil_titre": "Pneus et entretien à prix justes",
                                               "services_intro": "Tout pour votre véhicule."}, follow_redirects=False)
    assert reponse.status_code == 303, f"/admin/textes refuse l'enregistrement : {reponse.text[:800]}"
    assert "Pneus et entretien à prix justes" in anonyme.get("/").text, "le titre modifié n'apparaît pas sur l'accueil"
    assert "Tout pour votre véhicule." in anonyme.get("/services").text
    http.post("/admin/parametres", data={"telephone": "03 81 11 22 33", "couleur_primaire": "#aa2200",
                                         "facebook": "https://www.facebook.com/exemple"})
    accueil = anonyme.get("/").text
    assert "03 81 11 22 33" in accueil and 'href="tel:0381112233"' in accueil
    assert "--primaire: #aa2200" in accueil, "la couleur modifiée n'est pas appliquée"
    assert "https://www.facebook.com/exemple" in accueil
    service = publies(REGISTRE["services"], db)[0]
    http.post(f"/admin/services/{service.id}", data={"titre": "Montage de pneus", "resume": "En 30 minutes.",
                                                     "position": "1", "publie": "on"})
    assert "Montage de pneus" in anonyme.get("/services").text
    http.post(f"/admin/services/{service.id}", data={"titre": "Montage de pneus", "resume": "En 30 minutes.",
                                                     "position": "1"})
    assert "Montage de pneus" not in anonyme.get("/services").text, "un service décoché doit disparaître du site"


def test_texte_remis_a_l_origine_n_est_pas_stocke(http, db):
    from app.core.modeles import Parametre
    from app.site.contenu import reglages
    origine = next(r["valeur"] for r in reglages("textes") if r["cle"] == "accueil_titre")
    http.post("/admin/textes", data={"accueil_titre": "Autre titre"})
    assert db.get(Parametre, "texte.accueil_titre") is not None
    http.post("/admin/textes", data={"accueil_titre": origine})
    db.expire_all()
    assert db.get(Parametre, "texte.accueil_titre") is None, (
        "un texte identique à contenu/site.json ne doit pas être stocké (le fichier reste la référence)")


def test_texte_saisi_echappe(http, anonyme):
    http.post("/admin/textes", data={"accueil_intro": "<script>alert(1)</script>"})
    assert "<script>alert(1)</script>" not in anonyme.get("/").text, "un texte saisi doit être échappé"
