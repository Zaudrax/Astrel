#!/usr/bin/env bash
# Ouvre le HUD d'Astrel (qui doit déjà tourner).
cd "$(dirname "$0")"
exec ./.venv/bin/python -m astrel --hud
