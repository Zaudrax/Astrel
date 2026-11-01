#!/usr/bin/env bash
# Installe Astrel (Linux, macOS) : le travail est fait par install.py, commun à
# tous les systèmes. Aucun sudo. Relançable sans risque. Options : --help.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 install.py "$@"
