#!/usr/bin/env bash
# Lance Astrel (ou un de ses outils : ./run.sh --help).
cd "$(dirname "$0")"
exec ./.venv/bin/python -m astrel "$@"
