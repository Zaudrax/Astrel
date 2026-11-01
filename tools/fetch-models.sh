#!/usr/bin/env bash
# Télécharge les modèles locaux (exclus de Git : ~550 Mo). Ne retélécharge pas
# ce qui est déjà là. Le travail est fait par tools/fetch_models.py (aussi
# utilisé par install.py, Windows compris).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/.venv/bin/python" "$ROOT/tools/fetch_models.py"
