"""Envoi de mails par le serveur SMTP réglé dans Paramètres.
Le mot de passe SMTP peut venir de la variable d'environnement SMTP_PASSWORD (préférable en production)."""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from sqlalchemy.orm import Session

from app.core.modeles import lire_parametres


def configure(parametres: dict[str, str]) -> bool:
    return bool(parametres.get("smtp_hote") and parametres.get("smtp_expediteur"))


def envoyer(db: Session, destinataire: str, sujet: str, texte: str,
            pieces: list[tuple[str, bytes, str]] | None = None) -> None:
    """Envoie un mail. Lève ValueError avec un message clair si l'envoi est impossible."""
    parametres = lire_parametres(db)
    if not configure(parametres):
        raise ValueError("l'envoi de mails n'est pas configuré : renseignez le serveur SMTP dans Paramètres")
    if "@" not in destinataire:
        raise ValueError("adresse du destinataire invalide")
    message = EmailMessage()
    message["From"] = formataddr((parametres.get("societe_nom") or "", parametres["smtp_expediteur"]))
    message["To"] = destinataire
    message["Subject"] = sujet
    message["Message-ID"] = make_msgid(domain=parametres["smtp_expediteur"].split("@")[-1])
    message.set_content(texte)
    for nom, contenu, type_mime in pieces or []:
        principal, _, secondaire = type_mime.partition("/")
        message.add_attachment(contenu, maintype=principal, subtype=secondaire, filename=nom)
    port = int(parametres.get("smtp_port") or 587)
    mot_de_passe = os.environ.get("SMTP_PASSWORD") or parametres.get("smtp_mot_de_passe") or ""
    contexte = ssl.create_default_context()
    try:
        if port == 465:
            serveur = smtplib.SMTP_SSL(parametres["smtp_hote"], port, context=contexte, timeout=30)
        else:
            serveur = smtplib.SMTP(parametres["smtp_hote"], port, timeout=30)
        with serveur:
            if port != 465:
                serveur.starttls(context=contexte)
            if parametres.get("smtp_utilisateur"):
                serveur.login(parametres["smtp_utilisateur"], mot_de_passe)
            serveur.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise ValueError(f"envoi impossible : {exc}") from None
