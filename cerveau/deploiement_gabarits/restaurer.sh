#!/usr/bin/env bash
# Restaure une sauvegarde de la base de @@NOM@@. Généré par « cerveau deployer ». En root dans le conteneur :
#   bash /opt/@@NOM@@/app/deploiement/restaurer.sh                       liste les sauvegardes
#   bash /opt/@@NOM@@/app/deploiement/restaurer.sh /var/backups/@@NOM@@/app-AAAA-MM-JJ_HHMMSS.db.gz
# La sauvegarde est contrôlée avant de toucher à quoi que ce soit ; la base actuelle est gardée à côté
# (app.db.avant-restauration-<date>) ; l'application est arrêtée le temps de l'échange.
set -euo pipefail

NOM="@@NOM@@"
UTILISATEUR="@@UTILISATEUR@@"
SYS="${CERVEAU_RACINE_SYSTEME:-}"
DONNEES="$SYS/var/lib/$NOM/data"
DEST="$SYS/var/backups/$NOM"

echec() { printf 'ÉCHEC : %s\n' "$*" >&2; exit 1; }

if [ $# -ne 1 ]; then
    echo "usage : bash $0 <sauvegarde .db.gz ou .db>"
    echo "sauvegardes disponibles (les plus récentes d'abord) :"
    ls -1t "$DEST"/app-*.db.gz 2>/dev/null || echo "  aucune dans $DEST"
    exit 2
fi
SOURCE="$1"
[ -f "$SOURCE" ] || echec "fichier introuvable : $SOURCE"

tmp="$DONNEES/.restauration.db"
trap 'rm -f "$tmp"' EXIT
case "$SOURCE" in
    *.gz) gzip -dc "$SOURCE" > "$tmp" ;;
    *) cp "$SOURCE" "$tmp" ;;
esac
[ "$(sqlite3 "$tmp" 'PRAGMA integrity_check;')" = "ok" ] || echec "la sauvegarde est abîmée : rien n'a été modifié"

systemctl stop "$NOM.service"
if [ -f "$DONNEES/app.db" ]; then
    garde="$DONNEES/app.db.avant-restauration-$(date +%Y-%m-%d_%H%M%S)"
    sqlite3 "$DONNEES/app.db" ".backup '$garde'"
    echo "base actuelle gardée : $garde"
fi
rm -f "$DONNEES/app.db-wal" "$DONNEES/app.db-shm"
mv "$tmp" "$DONNEES/app.db"
chown "$UTILISATEUR:$UTILISATEUR" "$DONNEES/app.db"
chmod 640 "$DONNEES/app.db"
systemctl start "$NOM.service"
echo "base restaurée depuis $SOURCE, application redémarrée"
