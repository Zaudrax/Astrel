#!/usr/bin/env bash
# Lit un document LOCAL (PDF, Word .docx, Excel .xlsx, HTML…) et
# l'écrit en Markdown sur stdout, tronqué à ~60 000 caractères (plan P12, lot 1).
#   tools/lire-document.sh <chemin>       lire un fichier
#   tools/lire-document.sh --installer    (ré)installer la venv à versions fixées
# markitdown vit dans sa venv À PART (.venv-markitdown, comme .venv-passerelle) ;
# jamais d'URL : seul `convert_local` est appelé. Cache et temporaires dans la
# venv, sous /data (/ n'a que quelques Go libres).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv-markitdown"
REQS="$ROOT/tools/lire-document.requirements.txt"
MAX="${ASTREL_LIRE_DOCUMENT_MAX:-60000}"

if [ "${1:-}" = "--installer" ]; then
    mkdir -p "$VENV/.tmp"
    export TMPDIR="$VENV/.tmp" UV_CACHE_DIR="$VENV/.cache" PIP_NO_INPUT=1
    if command -v uv >/dev/null 2>&1; then
        [ -x "$VENV/bin/python" ] || uv venv --python /usr/bin/python3 "$VENV"
        uv pip install --python "$VENV/bin/python" -r "$REQS"
    else
        [ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
        "$VENV/bin/python" -m pip install --no-cache-dir --disable-pip-version-check -r "$REQS"
    fi
    rm -rf "$VENV/.cache" "$VENV/.tmp"
    exit 0
fi

if [ "$#" -ne 1 ] || [ -z "$1" ]; then
    echo "usage : tools/lire-document.sh <chemin d'un fichier local>" >&2
    exit 2
fi
case "$1" in
    *://*|www.*)
        echo "refusé : une URL n'est pas lue ici, seulement un fichier local." >&2
        exit 2 ;;
esac
if [ ! -f "$1" ]; then
    echo "refusé : $1 n'existe pas ou n'est pas un fichier." >&2
    exit 2
fi
if [ ! -x "$VENV/bin/python" ]; then
    echo "markitdown absent : lance tools/lire-document.sh --installer" >&2
    exit 3
fi

exec "$VENV/bin/python" -I - "$(realpath -- "$1")" "$MAX" <<'PY'
import sys

path, limit = sys.argv[1], int(sys.argv[2])
try:
    from markitdown import MarkItDown
    text = MarkItDown(enable_plugins=False).convert_local(path).text_content or ""
except Exception as exc:  # format inconnu, fichier abîmé : message court, pas de trace
    print(f"lecture impossible : {type(exc).__name__} : {str(exc)[:300]}", file=sys.stderr)
    sys.exit(1)
if len(text) > limit:
    text = (text[:limit] + f"\n\n[… tronqué à {limit} caractères sur {len(text)} : "
            "demande une partie précise du document.]")
sys.stdout.write(text if text.endswith("\n") else text + "\n")
PY
