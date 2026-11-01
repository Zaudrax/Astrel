#!/usr/bin/env bash
# Bulle flottante. Indépendante de l'assistant : peut être fermée et relancée.
cd "$(dirname "$0")"
exec ./.venv/bin/python -m astrel.bubble "$@"
