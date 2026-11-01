"""Socle commun des connecteurs externes (agenda, transports, traduction,
domotique) : lecture des identifiants et erreurs HTTP lisibles.

Les identifiants viennent de `config.secret` : l'environnement d'abord, puis
`config/secrets.json` (ignoré par Git). Jamais dans le code. Un connecteur dont
il manque une clé ne plante pas : il dit laquelle ajouter, et où.

Guide pour obtenir chaque clé : `docs/CONNECTEURS.md`.
"""
from __future__ import annotations

import httpx

from .. import config

# Connecteur → (clés obligatoires, clés facultatives). `mcp_config` transmet
# celles posées dans l'environnement au serveur MCP, qui n'hérite de presque rien.
CONNECTORS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "Home Assistant": (("HOMEASSISTANT_URL", "HOMEASSISTANT_TOKEN"), ()),
    "Agenda CalDAV": (("CALDAV_URL", "CALDAV_USERNAME", "CALDAV_PASSWORD"), ("CALDAV_CALENDAR",)),
    "Transports Navitia": (("NAVITIA_API_KEY",), ("NAVITIA_BASE_URL", "NAVITIA_COVERAGE")),
    "Traduction DeepL": (("DEEPL_API_KEY",), ()),
}
ALL_KEYS = tuple(k for req, opt in CONNECTORS.values() for k in (*req, *opt))
GUIDE = "docs/CONNECTEURS.md"
TIMEOUT_S = 20.0


class NotConfigured(RuntimeError):
    """Il manque un identifiant : le message dit lequel et où le mettre."""


def settings(name: str) -> dict[str, str]:
    """Les réglages d'un connecteur, ou `NotConfigured` s'il en manque un."""
    required, optional = CONNECTORS[name]
    values = {k: config.secret(k) for k in (*required, *optional)}
    missing = [k for k in required if not values[k]]
    if missing:
        raise NotConfigured(
            f"Connecteur {name} non configuré : ajoute {', '.join(missing)} dans "
            f"config/secrets.json (ou dans l'environnement). Marche à suivre : {GUIDE}."
        )
    return values


def status() -> dict[str, dict]:
    """Pour chaque connecteur : actif ou non, et ce qui manque."""
    out = {}
    for name, (required, _optional) in CONNECTORS.items():
        missing = [k for k in required if not config.secret(k)]
        out[name] = {"actif": not missing, "manque": missing}
    return out


def http_error(name: str, exc: Exception) -> RuntimeError:
    """Traduit une erreur réseau en phrase compréhensible (et sans la clé)."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        detail = ""
        try:
            data = exc.response.json()
            err = data.get("error") if isinstance(data, dict) else None
            detail = (err.get("message") if isinstance(err, dict) else err) or data.get("message") or ""
        except ValueError:
            detail = exc.response.text[:200]
        if code in (401, 403):
            return RuntimeError(f"{name} refuse l'identifiant (HTTP {code}) : vérifie la clé ou le mot de passe. {detail}".strip())
        if code == 404:
            return RuntimeError(f"{name} : ressource introuvable (HTTP 404). {detail}".strip())
        if code == 429:
            return RuntimeError(f"{name} : trop de requêtes, réessaie dans un moment (HTTP 429).")
        if code == 456:
            return RuntimeError(f"{name} : quota de caractères du mois épuisé (HTTP 456).")
        return RuntimeError(f"{name} a répondu HTTP {code}. {detail}".strip())
    if isinstance(exc, httpx.TimeoutException):
        return RuntimeError(f"{name} ne répond pas (délai dépassé).")
    if isinstance(exc, httpx.HTTPError):
        return RuntimeError(f"{name} injoignable : {type(exc).__name__} ({exc}).")
    return RuntimeError(f"{name} : {exc}")
