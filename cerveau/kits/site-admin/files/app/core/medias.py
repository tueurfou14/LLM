"""Images envoyées depuis l'administration.

Contrôle (PNG, JPG ou WEBP reconnus à leur contenu, 5 Mo au plus), stockage sous un nom aléatoire dans
data/uploads/ (ou le dossier APP_UPLOADS_DIR), adresse publique /medias/<fichier>.
"""

import os
import re
import secrets
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from app.core.database import DATA

DOSSIER = Path(os.environ.get("APP_UPLOADS_DIR") or DATA / "uploads")
TAILLE_MAX = 5 * 1024 * 1024
EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
TYPES_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}
NOM_SUR = re.compile(r"[a-f0-9]{32}\.(png|jpg|webp)")


@dataclass
class ImageRecue:
    """Image envoyée et contrôlée, pas encore écrite sur le disque."""
    contenu: bytes
    extension: str


def format_image(contenu: bytes) -> str | None:
    """Format réel du fichier, d'après ses premiers octets (l'extension du nom ne suffit pas)."""
    if contenu.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if contenu.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if contenu[:4] == b"RIFF" and contenu[8:12] == b"WEBP":
        return ".webp"
    return None


def lire(fichier) -> ImageRecue | None:
    """Fichier d'un formulaire → ImageRecue ; None si aucun fichier n'a été choisi.
    Lève ValueError avec un message pour l'utilisateur si le fichier est refusé."""
    if fichier is None or isinstance(fichier, str) or not getattr(fichier, "filename", ""):
        return None
    extension = Path(fichier.filename).suffix.lower()
    fichier.file.seek(0)
    contenu = fichier.file.read(TAILLE_MAX + 1)
    if len(contenu) > TAILLE_MAX:
        raise ValueError("image trop lourde : 5 Mo au plus")
    format_ = format_image(contenu)
    if extension not in EXTENSIONS or format_ is None:
        raise ValueError("format refusé : PNG, JPG ou WEBP seulement")
    return ImageRecue(contenu, format_)


def enregistrer(image: ImageRecue) -> str:
    """Écrit l'image sous un nom aléatoire et renvoie ce nom."""
    DOSSIER.mkdir(parents=True, exist_ok=True)
    nom = secrets.token_hex(16) + image.extension
    (DOSSIER / nom).write_bytes(image.contenu)
    return nom


def supprimer(noms) -> None:
    """Efface des images devenues inutiles (remplacées, retirées, ou objet supprimé)."""
    for nom in noms:
        if nom and NOM_SUR.fullmatch(nom):
            try:
                (DOSSIER / nom).unlink(missing_ok=True)
            except OSError:
                pass   # Windows : fichier encore ouvert par une lecture en cours ; il restera sur le disque


def chemin(nom: str) -> Path | None:
    """Fichier d'une image enregistrée, ou None (nom invalide ou image absente)."""
    if not NOM_SUR.fullmatch(nom or ""):
        return None
    fichier = DOSSIER / nom
    return fichier if fichier.is_file() else None


def type_mime(nom: str) -> str:
    return TYPES_MIME[Path(nom).suffix.lower()]


def png_exemple(largeur: int = 8, hauteur: int = 6, couleur: tuple[int, int, int] = (31, 61, 54)) -> bytes:
    """Petite image PNG valide, pour les tests."""
    def bloc(genre: bytes, donnees: bytes) -> bytes:
        return (struct.pack(">I", len(donnees)) + genre + donnees
                + struct.pack(">I", zlib.crc32(genre + donnees) & 0xFFFFFFFF))
    lignes = (b"\x00" + bytes(couleur) * largeur) * hauteur
    return (b"\x89PNG\r\n\x1a\n" + bloc(b"IHDR", struct.pack(">IIBBBBB", largeur, hauteur, 8, 2, 0, 0, 0))
            + bloc(b"IDAT", zlib.compress(lignes)) + bloc(b"IEND", b""))
