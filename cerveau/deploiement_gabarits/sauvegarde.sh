#!/usr/bin/env bash
# Sauvegarde à chaud de la base SQLite de @@NOM@@. Généré par « cerveau deployer ».
# Lancé chaque nuit par @@NOM@@-sauvegarde.timer (utilisateur @@UTILISATEUR@@) ; à la main :
#   systemctl start @@NOM@@-sauvegarde.service
# « sqlite3 .backup » passe par l'API de sauvegarde de SQLite : copie cohérente même pendant que
# l'application écrit (mode WAL). La copie est contrôlée (integrity_check), compressée, et les
# sauvegardes de plus de @@RETENTION@@ jours sont effacées.
set -euo pipefail
umask 027

NOM="@@NOM@@"
RETENTION_JOURS="${RETENTION_JOURS:-@@RETENTION@@}"
SYS="${CERVEAU_RACINE_SYSTEME:-}"
DONNEES="$SYS/var/lib/$NOM/data"
DEST="$SYS/var/backups/$NOM"
BASE="$DONNEES/app.db"
horodatage="$(date +%Y-%m-%d_%H%M%S)"

mkdir -p "$DEST"
tmp="$DEST/.app-$horodatage.db"
trap 'rm -f "$tmp" "$tmp.gz" "$DEST/.fichiers-$horodatage.tgz"' EXIT

if [ -f "$BASE" ]; then
    sqlite3 "$BASE" ".backup '$tmp'"
    controle="$(sqlite3 "$tmp" 'PRAGMA integrity_check;')"
    if [ "$controle" != "ok" ]; then
        echo "ÉCHEC : la copie ne passe pas le contrôle d'intégrité : $controle" >&2
        exit 1
    fi
    gzip -9 "$tmp"
    mv "$tmp.gz" "$DEST/app-$horodatage.db.gz"
    echo "base sauvegardée : $DEST/app-$horodatage.db.gz"
else
    echo "pas encore de base ($BASE) : rien à sauvegarder"
fi

if [ -d "$DONNEES/uploads" ]; then
    tar -czf "$DEST/.fichiers-$horodatage.tgz" -C "$DONNEES" uploads
    mv "$DEST/.fichiers-$horodatage.tgz" "$DEST/fichiers-$horodatage.tgz"
    echo "fichiers envoyés sauvegardés : $DEST/fichiers-$horodatage.tgz"
fi

find "$DEST" -maxdepth 1 -type f \( -name 'app-*.db.gz' -o -name 'fichiers-*.tgz' \) \
    -mtime "+$((RETENTION_JOURS - 1))" -print -delete | sed 's/^/supprimée (plus de '"$RETENTION_JOURS"' jours) : /'
