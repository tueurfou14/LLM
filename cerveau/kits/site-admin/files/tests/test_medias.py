"""Images envoyées depuis l'administration : contrôle, stockage, adresse /medias, remplacement, suppression."""

from app.core import medias
from app.core.framework import REGISTRE
from app.site.public import publies

PNG = medias.png_exemple()
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32


def _realisation(http, image=("photo.png", PNG, "image/png"), **donnees):
    donnees = {"titre": "Atelier", "date": "2026-10-01", "publie": "on", **donnees}
    return http.post("/admin/realisations/nouveau", data=donnees, files={"image": image} if image else None,
                     follow_redirects=False)


def _objet(db, reponse):
    module = REGISTRE["realisations"]
    objet = db.get(module.modele, int(reponse.headers["location"].rsplit("/", 1)[1]))
    db.refresh(objet)
    return objet


def test_image_acceptee_et_servie(http, anonyme, db):
    reponse = _realisation(http)
    assert reponse.status_code == 303, f"image PNG refusée : {reponse.text[:800]}"
    objet = _objet(db, reponse)
    assert medias.NOM_SUR.fullmatch(objet.image), f"nom de fichier non aléatoire : {objet.image}"
    assert (medias.DOSSIER / objet.image).is_file(), "l'image n'est pas écrite dans le dossier des images"
    servie = anonyme.get(f"/medias/{objet.image}")
    assert servie.status_code == 200 and servie.content == PNG
    assert servie.headers["content-type"] == "image/png"
    assert servie.headers["x-content-type-options"] == "nosniff"
    assert f"/medias/{objet.image}" in anonyme.get("/realisations").text, "l'image n'apparaît pas sur le site"


def test_jpg_et_webp_acceptes_avec_le_bon_type(http, anonyme, db):
    for nom, contenu, extension, mime in (("photo.jpeg", JPG, ".jpg", "image/jpeg"),
                                          ("photo.webp", WEBP, ".webp", "image/webp")):
        reponse = _realisation(http, image=(nom, contenu, mime))
        assert reponse.status_code == 303, f"{nom} refusé : {reponse.text[:500]}"
        objet = _objet(db, reponse)
        assert objet.image.endswith(extension)
        assert anonyme.get(f"/medias/{objet.image}").headers["content-type"] == mime


def test_mauvais_type_refuse(http):
    for nom, contenu in (("page.html", b"<script>alert(1)</script>"), ("image.svg", b"<svg></svg>"),
                         ("faux.png", b"ceci n'est pas une image"), ("programme.exe", PNG)):
        reponse = _realisation(http, image=(nom, contenu, "image/png"))
        assert reponse.status_code == 422, f"{nom} aurait dû être refusé"
        assert "PNG, JPG ou WEBP" in reponse.text
    assert not any(medias.DOSSIER.glob("*.svg")) and not any(medias.DOSSIER.glob("*.html"))


def test_image_trop_lourde_refusee(http):
    lourde = PNG + b"\x00" * (medias.TAILLE_MAX + 1)
    reponse = _realisation(http, image=("lourde.png", lourde, "image/png"))
    assert reponse.status_code == 422 and "5 Mo au plus" in reponse.text


def test_image_gardee_si_aucun_nouveau_fichier(http, db):
    objet = _objet(db, _realisation(http))
    image = objet.image
    reponse = http.post(f"/admin/realisations/{objet.id}", data={"titre": "Atelier modifié", "date": "2026-10-01",
                                                                 "publie": "on", "image": "../../secret.txt"},
                        follow_redirects=False)
    assert reponse.status_code == 303
    db.refresh(objet)
    assert objet.titre == "Atelier modifié" and objet.image == image, "l'image doit rester quand aucun fichier n'est choisi"


def test_image_remplacee_et_ancienne_supprimee(http, db):
    objet = _objet(db, _realisation(http))
    ancienne = objet.image
    reponse = http.post(f"/admin/realisations/{objet.id}", data={"titre": "Atelier", "date": "2026-10-01"},
                        files={"image": ("nouvelle.webp", WEBP, "image/webp")}, follow_redirects=False)
    assert reponse.status_code == 303
    db.refresh(objet)
    assert objet.image != ancienne and objet.image.endswith(".webp")
    assert not (medias.DOSSIER / ancienne).exists(), "l'ancienne image doit être supprimée quand elle est remplacée"
    assert (medias.DOSSIER / objet.image).is_file()


def test_image_retiree(http, anonyme, db):
    objet = _objet(db, _realisation(http))
    ancienne = objet.image
    http.post(f"/admin/realisations/{objet.id}", data={"titre": "Atelier", "date": "2026-10-01", "publie": "on",
                                                       "image__retirer": "on"})
    db.refresh(objet)
    assert objet.image is None and not (medias.DOSSIER / ancienne).exists()
    assert "/static/site/img/realisation-" in anonyme.get("/realisations").text, "sans image, l'illustration par défaut s'affiche"


def test_image_supprimee_avec_l_objet(http, db):
    objet = _objet(db, _realisation(http))
    image = objet.image
    http.post(f"/admin/realisations/{objet.id}/supprimer")
    assert not (medias.DOSSIER / image).exists(), "l'image d'un objet supprimé doit être effacée"


def test_formulaire_refuse_garde_l_image_actuelle(http, db):
    objet = _objet(db, _realisation(http))
    image = objet.image
    avant = set(medias.DOSSIER.iterdir())
    reponse = http.post(f"/admin/realisations/{objet.id}", data={"titre": "", "date": "2026-10-01"},
                        files={"image": ("autre.png", PNG, "image/png")})
    assert reponse.status_code == 422
    db.refresh(objet)
    assert objet.image == image and (medias.DOSSIER / image).is_file()
    assert f"/medias/{image}" in reponse.text, "l'aperçu de l'image actuelle doit rester affiché"
    assert set(medias.DOSSIER.iterdir()) == avant, "un formulaire refusé ne doit écrire aucune image"


def test_apercu_dans_le_formulaire(http, db):
    objet = _objet(db, _realisation(http))
    page = http.get(f"/admin/realisations/{objet.id}").text
    assert f'src="/medias/{objet.image}"' in page and "Retirer l'image" in page


def test_medias_refuse_les_noms_douteux(anonyme):
    for nom in ("../app.db", "..%2Fapp.db", "secret.key", "0123456789abcdef0123456789abcdef.svg",
                "0123456789abcdef0123456789abcdef.png"):
        assert anonyme.get(f"/medias/{nom}").status_code == 404, f"/medias/{nom} doit répondre 404"


def test_logo_et_photo_d_accueil_dans_les_parametres(http, anonyme):
    reponse = http.post("/admin/parametres", files={"logo": ("logo.png", PNG, "image/png"),
                                                    "image_accueil": ("accueil.jpg", JPG, "image/jpeg")},
                        data={"nom": "Garage Martin"}, follow_redirects=False)
    assert reponse.status_code == 303, f"/admin/parametres refuse les images : {reponse.text[:800]}"
    accueil = anonyme.get("/").text
    assert "Garage Martin" in accueil
    assert accueil.count("/medias/") >= 2, "le logo et la photo d'accueil doivent venir des images envoyées"
    refus = http.post("/admin/parametres", files={"logo": ("logo.gif", b"GIF89a", "image/gif")})
    assert refus.status_code == 422 and "PNG, JPG ou WEBP" in refus.text


def test_logo_remplace_supprime_l_ancien(http, db):
    from app.site.contenu import valeurs
    http.post("/admin/parametres", files={"logo": ("logo.png", PNG, "image/png")})
    ancien = valeurs(db, "parametres")["logo"]
    http.post("/admin/parametres", files={"logo": ("logo2.png", medias.png_exemple(couleur=(200, 0, 0)), "image/png")})
    db.expire_all()
    nouveau = valeurs(db, "parametres")["logo"]
    assert nouveau != ancien and not (medias.DOSSIER / ancien).exists()


def test_publies_ne_montre_pas_les_images_d_objets_masques(http, anonyme, db):
    objet = _objet(db, _realisation(http, publie=""))
    assert objet not in publies(REGISTRE["realisations"], db)
    assert f"/medias/{objet.image}" not in anonyme.get("/realisations").text
