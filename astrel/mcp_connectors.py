"""Serveur MCP « connecteurs » : les services extérieurs de l'utilisateur — agenda
CalDAV, transports (SNCF / Navitia), traduction DeepL. La domotique passe par
le serveur MCP OFFICIEL de Home Assistant, déclaré à côté (`mcp_config`).

Pensé pour un futur bracelet connecté : ce qu'on demande en marchant, loin de
l'écran. Chaque outil marche sans rien casser tant que ses identifiants
manquent : il répond « connecteur non configuré, ajoute X » (docs/CONNECTEURS.md).

Pourquoi un serveur à part plutôt que dans « astrel » : le garde-fou
(`guard.MCP_POLICY`) laisse passer sans question tout outil du serveur
« astrel » qu'il ne cite pas, même en mode prudent. Un serveur inconnu, lui,
est jugé sur le NOM de ses outils : lire l'agenda ou créer un événement est
« sensible » (libre en autonome, confirmé en prudent), exactement comme les
connecteurs claude.ai. Et `guard.py` est un fichier protégé.
"""
from __future__ import annotations

import functools
import sys

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .tools import agenda, connector, transport, translate


class _Server(MCPServer):
    """Même règle que `mcp_server._Server` : toute exception devient un `ToolError`
    avec son vrai message, sinon Claude ne reçoit que « Error executing tool ».
    Un connecteur non configuré rend sa consigne telle quelle."""

    def tool(self, *args, **kwargs):
        register = super().tool(*args, **kwargs)

        def decorator(fn):
            @functools.wraps(fn)
            def wrapped(*a, **kw):
                try:
                    return fn(*a, **kw)
                except ToolError:
                    raise
                except connector.NotConfigured as exc:
                    raise ToolError(str(exc)) from exc
                except Exception as exc:
                    raise ToolError(f"{type(exc).__name__} : {exc}") from exc

            return register(wrapped)

        return decorator


server = _Server(
    "connecteurs",
    log_level="WARNING",
    instructions=(
        "Services extérieurs de l'utilisateur : son agenda (CalDAV), les horaires de train et de "
        "transports en commun (SNCF / Navitia), la traduction (DeepL). La domotique passe par le "
        "serveur « homeassistant » ; s'il est absent, dis que le connecteur Home Assistant n'est pas "
        "configuré (HOMEASSISTANT_URL et HOMEASSISTANT_TOKEN dans config/secrets.json). "
        "Si un outil répond « non configuré », répète à l'utilisateur, en une phrase, ce qu'il doit ajouter."
    ),
)


@server.tool()
def connectors_status() -> dict:
    """Quels connecteurs sont prêts (agenda, transports, traduction, domotique) et, pour les autres,
    quels identifiants manquent. Ne contacte aucun service."""
    return connector.status()


# --- agenda ---

@server.tool()
def calendar_upcoming(days: int = 7, limit: int = 20) -> dict:
    """Prochains événements de l'agenda de l'utilisateur, sur `days` jours à partir de maintenant
    (récurrences comprises). Chaque événement a un champ « quand » prêt à être dit."""
    return agenda.upcoming(days, limit)


@server.tool()
def calendar_create_event(title: str, start: str, end: str = "", duration_minutes: int = 60,
                          location: str = "", description: str = "", reminder_minutes: int = 0) -> dict:
    """Ajoute un événement à l'agenda. `start` : date/heure ISO locale (« 2026-10-02T14:00 »), ou une
    date seule (« 2026-10-02 ») pour une journée entière. `end` facultatif, sinon `duration_minutes`.
    `reminder_minutes` > 0 ajoute une alerte avant l'événement."""
    return agenda.create(title, start, end, duration_minutes, location, description, reminder_minutes)


@server.tool()
def calendar_list_calendars() -> list[dict]:
    """Calendriers du compte CalDAV (pour choisir lequel utiliser avec CALDAV_CALENDAR)."""
    return agenda.calendars()


# --- transports ---

@server.tool()
def transport_search_stop(query: str, count: int = 5) -> list[dict]:
    """Cherche une gare ou un arrêt par son nom (« Lille Part-Dieu », « Toulouse »). Rend les
    identifiants à passer aux autres outils quand un nom est ambigu."""
    return transport.search_places(query, count)


@server.tool()
def transport_departures(station: str, count: int = 6, when: str = "") -> dict:
    """Prochains départs depuis une gare ou un arrêt (nom ou identifiant « stop_area:… »), en temps
    réel : heure, retard, ligne, direction, perturbation. `when` : date/heure ISO locale, sinon maintenant."""
    return transport.departures(station, count, when)


@server.tool()
def transport_journey(origin: str, destination: str, when: str = "", arrive_by: bool = False,
                      count: int = 3) -> dict:
    """Itinéraires entre deux lieux (gares, arrêts, adresses, villes) : heures, durée, correspondances,
    trains ou lignes empruntés. `when` : date/heure ISO locale ; `arrive_by` : true pour une heure d'arrivée."""
    return transport.journeys(origin, destination, when, arrive_by, count)


# --- traduction ---

@server.tool()
def translate_text(text: str, target_lang: str, source_lang: str = "", formality: str = "") -> dict:
    """Traduit un texte avec DeepL. `target_lang` : nom (« anglais », « allemand ») ou code
    (« EN-US », « DE »). `source_lang` : vide = détection automatique. `formality` : « formel » ou
    « informel » (vouvoiement / tutoiement), si la langue le permet. Pour une traduction à voix
    haute, dis simplement la traduction : la voix d'Astrel lit les langues étrangères."""
    return translate.translate(text, target_lang, source_lang, formality)


@server.tool()
def translate_usage() -> dict:
    """Caractères DeepL consommés ce mois-ci et limite de l'offre."""
    return translate.usage()


def main() -> int:
    server.run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
