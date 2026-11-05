#!/usr/bin/env bash
# SearXNG LOCAL du moteur local d'Astrel (05/11/2026, lot 7 partie A, docs/chantiers/HYBRIDE-QWEN.md).
#   tools/searxng.sh --installer   (ré)installe la venv à part .venv-searxng, à la version FIXÉE ci-dessous,
#                                  écrit runtime/searxng/settings.yml (0600, clé tirée au hasard), et la venv
#                                  à part .venv-trafilatura (lecture des pages, astrel/web_local.py)
#   tools/searxng.sh --lancer      lance SearXNG au premier plan, sur 127.0.0.1:$PORT SEULEMENT
#   tools/searxng.sh --etat        installé ? version ? répond ? une recherche JSON d'essai (code 1 sinon)
#   tools/searxng.sh --service     écrit ~/.config/systemd/user/astrel-searxng.service (sans l'activer)
# Sans sudo. SearXNG (AGPL-3.0) est un PROGRAMME séparé : Astrel lui parle en HTTP local, ne l'importe jamais
# (comme Piper). Rien n'est lancé tout seul par ce script hors de --lancer.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv-searxng"
SRC="$VENV/src"
REQS="$ROOT/tools/searxng.requirements.txt"
LECTURE="$ROOT/.venv-trafilatura"
REQS_LECTURE="$ROOT/tools/trafilatura.requirements.txt"
MODELE="$ROOT/tools/searxng-settings.yml"
DOSSIER="$ROOT/runtime/searxng"
SETTINGS="$DOSSIER/settings.yml"
PORT="${ASTREL_SEARXNG_PORT:-8899}"
# Version FIXÉE (05/11) : SearXNG ne publie pas de paquet, seulement un dépôt git en continu ; on épingle un
# commit (master du 02/11/2026, « 2026.11.2+cccbc9e », correctifs récents des moteurs qwant et brave).
# Changer de version = changer ces deux lignes, puis --installer (et refaire l'essai réel).
DEPOT="https://github.com/searxng/searxng.git"
COMMIT="cccbc9e158b0ac3bb2158e9841a647a5bec57b57"

case "$PORT" in
    ''|*[!0-9]*) echo "ASTREL_SEARXNG_PORT invalide : $PORT" >&2; exit 2 ;;
esac

ecrire_settings() {
    mkdir -p "$DOSSIER"
    chmod 700 "$DOSSIER"
    local cle=""
    # Clé gardée d'une installation à l'autre (sessions de la page de contrôle), sinon tirée au hasard.
    if [ -f "$SETTINGS" ]; then
        cle="$(sed -n 's/^  secret_key: "\([A-Za-z0-9]\{32,\}\)"$/\1/p' "$SETTINGS" | head -n1)"
    fi
    [ -n "$cle" ] || cle="$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 48)"
    local tmp="$SETTINGS.tmp"
    ( umask 077; sed -e "s/@SECRET@/$cle/" -e "s/@PORT@/$PORT/" "$MODELE" > "$tmp" )
    mv -f "$tmp" "$SETTINGS"
    chmod 600 "$SETTINGS"
    # Limiteur coupé (settings.yml) : fichier vide, valeurs par défaut du schéma (évite l'avertissement « missing »).
    [ -f "$DOSSIER/limiter.toml" ] || ( umask 077; echo "# limiteur coupé dans settings.yml (instance locale)" \
        > "$DOSSIER/limiter.toml" )
}

installer() {
    mkdir -p "$VENV/.tmp"
    export TMPDIR="$VENV/.tmp" UV_CACHE_DIR="$VENV/.cache" PIP_NO_INPUT=1
    if [ ! -d "$SRC/.git" ]; then
        git clone --quiet --filter=blob:none --no-checkout "$DEPOT" "$SRC"
    fi
    git -C "$SRC" fetch --quiet --filter=blob:none origin "$COMMIT" 2>/dev/null || git -C "$SRC" fetch --quiet origin
    git -C "$SRC" -c advice.detachedHead=false checkout --quiet --force "$COMMIT"
    [ "$(git -C "$SRC" rev-parse HEAD)" = "$COMMIT" ] || { echo "commit inattendu" >&2; exit 1; }
    if command -v uv >/dev/null 2>&1; then
        [ -x "$VENV/bin/python" ] || uv venv --allow-existing --python /usr/bin/python3 "$VENV"
        uv pip install --python "$VENV/bin/python" -r "$REQS"
    else
        [ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
        "$VENV/bin/python" -m pip install --no-cache-dir --disable-pip-version-check -r "$REQS"
    fi
    # Version figée dans le paquet (searx/version_frozen.py, comme l'image officielle) : sinon SearXNG lance
    # `git` à chaque démarrage dans son dossier courant (l'arbre d'Astrel) pour retrouver sa version.
    ( cd "$SRC" && GITHUB_REPOSITORY=searxng/searxng GITHUB_REF_NAME=master "$VENV/bin/python" -m searx.version freeze )
    if command -v uv >/dev/null 2>&1; then
        uv pip install --python "$VENV/bin/python" --reinstall-package searxng --no-build-isolation --no-deps "$SRC"
    else
        "$VENV/bin/python" -m pip install --no-cache-dir --disable-pip-version-check --force-reinstall \
            --no-build-isolation --no-deps "$SRC"
    fi
    rm -rf "$VENV/.cache" "$VENV/.tmp"
    ecrire_settings
    echo "SearXNG installé ($COMMIT), réglages : $SETTINGS"
    installer_lecture
}

# trafilatura dans SA venv (05/11) : ni dans .venv (piège 4 : httpcore/h11 épinglés, paquets du système), ni avec
# SearXNG (ses versions suivent les siennes). Lancé par web_local.py en process enfant borné, jamais importé.
installer_lecture() {
    mkdir -p "$LECTURE/.tmp"
    export TMPDIR="$LECTURE/.tmp" UV_CACHE_DIR="$LECTURE/.cache" PIP_NO_INPUT=1
    if command -v uv >/dev/null 2>&1; then
        [ -x "$LECTURE/bin/python" ] || uv venv --allow-existing --python /usr/bin/python3 "$LECTURE"
        uv pip install --python "$LECTURE/bin/python" -r "$REQS_LECTURE"
    else
        [ -x "$LECTURE/bin/python" ] || python3 -m venv "$LECTURE"
        "$LECTURE/bin/python" -m pip install --no-cache-dir --disable-pip-version-check -r "$REQS_LECTURE"
    fi
    rm -rf "$LECTURE/.cache" "$LECTURE/.tmp"
    echo "trafilatura installé : $LECTURE"
}

lancer() {
    if [ ! -x "$VENV/bin/granian" ]; then
        echo "SearXNG absent : lance tools/searxng.sh --installer" >&2
        exit 3
    fi
    # granian ouvre son port avec SO_REUSEPORT : une 2e instance le PARTAGERAIT en silence (réponses mêlées de
    # deux réglages, vu le 05/11). Port déjà pris = rien n'est lancé. Arrêt : par le service, ou `kill` du pid
    # (titre du process : « granian wsgi 127.0.0.1:PORT searx.webapp:app »).
    if "$VENV/bin/python" -I -c "import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1', $PORT)) == 0 else 1)"; then
        echo "port 127.0.0.1:$PORT déjà pris (SearXNG déjà lancé ?) : rien n'est lancé" >&2
        exit 4
    fi
    [ -f "$SETTINGS" ] || ecrire_settings
    # Le port du fichier suit ASTREL_SEARXNG_PORT (réécrit s'il a changé).
    grep -q "^  port: $PORT\$" "$SETTINGS" || ecrire_settings
    export SEARXNG_SETTINGS_PATH="$SETTINGS"
    # Aucune variable de mandataire héritée : SearXNG sort en direct (comme le reste d'Astrel).
    unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
    cd "$VENV"
    # Journal sans les recherches : SearXNG écrit l'adresse COMPLÈTE d'une requête qui échoue vers un moteur
    # (« GET https://…/search?q=<la recherche> ») ; tout ce qui suit un « ? » est masqué avant le journal.
    exec > >(sed -u -E 's#(https?://[^ ?"]*)\?[^ "]*#\1?…#g') 2>&1
    exec "$VENV/bin/granian" --interface wsgi --host 127.0.0.1 --port "$PORT" --workers 1 \
        --blocking-threads 4 --no-ws --log-level warning searx.webapp:app
}

etat() {
    if [ ! -x "$VENV/bin/python" ]; then
        echo "SearXNG : non installé (tools/searxng.sh --installer)"
        exit 1
    fi
    local v
    v="$("$VENV/bin/python" -I -c 'import importlib.metadata as m; print(m.version("searxng"))' 2>/dev/null || true)"
    echo "SearXNG : installé ${v:-?} (attendu : commit ${COMMIT:0:7}), réglages ${SETTINGS#"$ROOT"/}"
    if [ -x "$LECTURE/bin/python" ]; then
        v="$("$LECTURE/bin/python" -I -c 'import importlib.metadata as m; print(m.version("trafilatura"))' 2>/dev/null || true)"
        echo "trafilatura : installé ${v:-?}"
    else
        echo "trafilatura : non installé (lecture de secours, moins propre) : tools/searxng.sh --installer"
    fi
    "$VENV/bin/python" -I - "$PORT" <<'PY'
import json, sys, urllib.parse, urllib.request
port = sys.argv[1]
url = f"http://127.0.0.1:{port}/search?" + urllib.parse.urlencode({"q": "wikipedia", "format": "json"})
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # jamais de mandataire
try:
    with opener.open(url, timeout=15) as r:
        d = json.load(r)
except Exception as exc:
    print(f"SearXNG : ne répond pas sur 127.0.0.1:{port} ({type(exc).__name__})")
    sys.exit(1)
res = d.get("results") or []
mortes = sorted({e[0] for e in d.get("unresponsive_engines") or []})
print(f"SearXNG : répond sur 127.0.0.1:{port}, {len(res)} résultats JSON"
      + (f", moteurs sans réponse : {', '.join(mortes)}" if mortes else ""))
sys.exit(0 if res else 1)
PY
}

service() {
    local dest="$HOME/.config/systemd/user/astrel-searxng.service"
    mkdir -p "$(dirname "$dest")"
    sed -e "s#@ROOT@#$ROOT#g" -e "s#@PORT@#$PORT#g" "$ROOT/systemd/astrel-searxng.service" > "$dest"
    echo "écrit : $dest"
    echo "pour l'activer (une fois, par l'utilisateur) : systemctl --user daemon-reload && systemctl --user enable --now astrel-searxng.service"
}

case "${1:-}" in
    --installer) installer ;;
    --lancer) lancer ;;
    --etat) etat ;;
    --service) service ;;
    *) echo "usage : tools/searxng.sh --installer | --lancer | --etat | --service" >&2; exit 2 ;;
esac
