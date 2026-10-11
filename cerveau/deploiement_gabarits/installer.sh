#!/usr/bin/env bash
# Installation de @@NOM@@ dans son conteneur. Généré par « cerveau deployer » : versionnez-le, ne le modifiez pas
# à la main (il est réécrit à chaque déploiement).
#
#   bash deploiement/installer.sh                 installe ou réinstalle (idempotent)
#   bash deploiement/installer.sh --mise-a-jour   code + dépendances + redémarrage ; refuse de créer des secrets
#   bash deploiement/installer.sh --revenir       remet la version précédente du code en service
#
# À lancer en root DANS le conteneur. Relancé, il ne recrée ni l'utilisateur, ni les secrets, ni les données :
#   code        /opt/@@NOM@@/app          (remplacé à chaque version, la précédente reste dans app.precedent)
#   données     /var/lib/@@NOM@@/data     (base SQLite, fichiers envoyés ; app/data pointe ici)
#   secrets     /etc/@@NOM@@/app.env      (SECRET_KEY, APP_DATABASE_URL ; jamais dans le dépôt)
#   sauvegardes /var/backups/@@NOM@@      (chaque nuit, @@RETENTION@@ jours)
set -euo pipefail
umask 022

NOM="@@NOM@@"
UTILISATEUR="@@UTILISATEUR@@"
PORT="@@PORT@@"
# Vide en production. Les tests y mettent un dossier temporaire pour exécuter le script hors d'un conteneur.
SYS="${CERVEAU_RACINE_SYSTEME:-}"

RACINE="$SYS/opt/$NOM"
CODE="$RACINE/app"
VENV="$RACINE/venv"
DONNEES="$SYS/var/lib/$NOM"
CONF="$SYS/etc/$NOM"
SAUVEGARDES="$SYS/var/backups/$NOM"
SYSTEMD="$SYS/etc/systemd/system"

case ":$PATH:" in *:/usr/local/bin:*) ;; *) PATH="$PATH:/usr/local/bin" ;; esac

etape() { printf '==> %s\n' "$*"; }
echec() { printf 'ÉCHEC : %s\n' "$*" >&2; exit 1; }

MODE="${1:-}"
case "$MODE" in
    ""|--mise-a-jour|--revenir) ;;
    *) echec "option inconnue « $MODE » (attendu : --mise-a-jour ou --revenir)" ;;
esac
[ -n "$SYS" ] || [ "$(id -u)" = 0 ] || echec "à lancer en root dans le conteneur"

export UV_PROJECT_ENVIRONMENT="$VENV" UV_PYTHON_INSTALL_DIR="$SYS/opt/uv/python" UV_CACHE_DIR="$SYS/var/cache/uv"
export UV_NO_PROGRESS=1

synchroniser() {  # $1 = dossier du code : installe ses dépendances dans le venv partagé
    if [ -f "$1/uv.lock" ]; then
        (cd "$1" && uv sync --no-dev --locked) \
            || echec "uv sync a échoué (uv.lock pas à jour ? lance « uv lock » dans le projet puis redéploie)"
    else
        (cd "$1" && uv sync --no-dev) || echec "uv sync a échoué (voir les messages ci-dessus)"
    fi
    chmod -R go+rX "$VENV"
    if [ -d "$SYS/opt/uv/python" ]; then chmod -R go+rX "$SYS/opt/uv/python"; fi
}

if [ "$MODE" = "--revenir" ]; then
    [ -d "$RACINE/app.precedent" ] || echec "pas de version précédente dans $RACINE/app.precedent"
    etape "retour à la version précédente"
    rm -rf "$RACINE/app.echec"
    if [ -d "$CODE" ]; then mv "$CODE" "$RACINE/app.echec"; fi
    mv "$RACINE/app.precedent" "$CODE"
    synchroniser "$CODE"
    ln -sfn "$DONNEES/data" "$CODE/data"
    systemctl restart "$NOM.service"
    echo "version précédente remise en service (la version écartée est dans $RACINE/app.echec)"
    exit 0
fi

etape "paquets système"
manquants=""
for paquet in ca-certificates curl python3 sqlite3; do
    dpkg -s "$paquet" >/dev/null 2>&1 || manquants="$manquants $paquet"
done
if [ -n "$manquants" ]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -q
    # shellcheck disable=SC2086
    apt-get install -y -q --no-install-recommends $manquants
else
    echo "déjà installés"
fi

etape "uv"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf "@@URL_UV@@" | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 INSTALLER_NO_MODIFY_PATH=1 sh
fi
command -v uv >/dev/null 2>&1 || echec "uv introuvable après installation"

etape "utilisateur système $UTILISATEUR (sans droits root, sans shell)"
if ! id -u "$UTILISATEUR" >/dev/null 2>&1; then
    useradd --system --home-dir "/var/lib/$NOM" --no-create-home --shell /usr/sbin/nologin "$UTILISATEUR"
fi
mkdir -p "$RACINE" "$DONNEES/data" "$SAUVEGARDES" "$CONF" "$SYSTEMD"
chown "$UTILISATEUR:$UTILISATEUR" "$DONNEES" "$DONNEES/data" "$SAUVEGARDES"
chmod 750 "$DONNEES" "$DONNEES/data" "$SAUVEGARDES"
chown "root:$UTILISATEUR" "$CONF"
chmod 750 "$CONF"

etape "secrets"
if [ -f "$CONF/app.env" ]; then
    echo "déjà en place ($CONF/app.env) : conservés"
else
    [ "$MODE" != "--mise-a-jour" ] \
        || echec "$CONF/app.env absent : une mise à jour ne crée pas de secrets. Lance un déploiement complet."
    (
        umask 077
        cle="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
        admin="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
        printf 'SECRET_KEY=%s\nAPP_DATABASE_URL=sqlite:///%s/data/app.db\n' "$cle" "/var/lib/$NOM" > "$CONF/app.env.tmp"
        printf 'ADMIN_PASSWORD=%s\n' "$admin" > "$CONF/admin-initial.env"
        chown "root:$UTILISATEUR" "$CONF/app.env.tmp" "$CONF/admin-initial.env"
        chmod 640 "$CONF/app.env.tmp" "$CONF/admin-initial.env"
        mv "$CONF/app.env.tmp" "$CONF/app.env"
        echo "secrets générés dans $CONF (lisibles par root et $UTILISATEUR seulement)"
        echo "MOT_DE_PASSE_ADMIN_INITIAL=$admin"
    )
fi

etape "code et dépendances"
ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NOUVEAU="$CODE"
if [ "$ICI" != "$CODE" ]; then
    NOUVEAU="$RACINE/app.nouveau"
    if [ "$ICI" != "$NOUVEAU" ]; then
        rm -rf "$NOUVEAU"
        cp -a "$ICI" "$NOUVEAU"
        rm -rf "$NOUVEAU/data" "$NOUVEAU/.venv" "$NOUVEAU/.git"
    fi
fi
if [ -e "$NOUVEAU/data" ] && [ ! -L "$NOUVEAU/data" ]; then
    echec "$NOUVEAU/data est un vrai dossier : les données vivent dans $DONNEES/data. Déplace-le puis relance."
fi
# Les dépendances d'abord : si elles échouent, la version en service n'est pas touchée.
synchroniser "$NOUVEAU"
if [ "$NOUVEAU" != "$CODE" ]; then
    rm -rf "$RACINE/app.precedent"
    if [ -d "$CODE" ]; then
        mv "$CODE" "$RACINE/app.precedent"
        echo "version précédente gardée dans $RACINE/app.precedent"
    fi
    mv "$NOUVEAU" "$CODE"
fi
chown -R root:root "$CODE"
chmod -R u=rwX,go=rX "$CODE"
ln -sfn "$DONNEES/data" "$CODE/data"

etape "services systemd"
ecrire_si_change() {  # $1 = fichier ; le contenu arrive sur l'entrée standard
    cat > "$1.tmp"
    if [ -f "$1" ] && cmp -s "$1.tmp" "$1"; then rm -f "$1.tmp"; else mv "$1.tmp" "$1"; fi
}
ecrire_si_change "$SYSTEMD/$NOM.service" <<EOF
[Unit]
Description=@@TITRE@@ (déployé par cerveau)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$UTILISATEUR
Group=$UTILISATEUR
WorkingDirectory=/opt/$NOM/app
EnvironmentFile=/etc/$NOM/app.env
EnvironmentFile=-/etc/$NOM/admin-initial.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/$NOM/venv/bin/@@COMMANDE@@
Restart=always
RestartSec=3
NoNewPrivileges=yes
UMask=0027

[Install]
WantedBy=multi-user.target
EOF
ecrire_si_change "$SYSTEMD/$NOM-sauvegarde.service" <<EOF
[Unit]
Description=Sauvegarde de la base de $NOM

[Service]
Type=oneshot
User=$UTILISATEUR
Group=$UTILISATEUR
ExecStart=/bin/bash /opt/$NOM/app/deploiement/sauvegarde.sh
Nice=10
EOF
ecrire_si_change "$SYSTEMD/$NOM-sauvegarde.timer" <<EOF
[Unit]
Description=Sauvegarde nocturne de la base de $NOM

[Timer]
OnCalendar=*-*-* @@HEURE@@:00
RandomizedDelaySec=15min
Persistent=true

[Install]
WantedBy=timers.target
EOF

etape "démarrage"
systemctl daemon-reload
systemctl enable --quiet "$NOM.service" "$NOM-sauvegarde.timer"
systemctl restart "$NOM.service"
systemctl start "$NOM-sauvegarde.timer"
echo "installation terminée : l'application écoute sur le port $PORT"
