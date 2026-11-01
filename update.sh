#!/usr/bin/env bash
# Met Astrel à jour (git pull --ff-only, dépendances si besoin, relance) :
# le travail est fait par update.py, commun à tous les systèmes.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 update.py "$@"
