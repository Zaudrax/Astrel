"""Connecteurs (agenda CalDAV, transports Navitia, DeepL, Home Assistant) :
aucun appel réseau réel, les services sont simulés par `httpx.MockTransport`."""
import functools
import json
import os
import stat
import time
from datetime import date, datetime, timedelta

import httpx
import pytest

from astrel import config, guard, mcp_config
from astrel.tools import agenda, connector, translate, transport

_REAL_CLIENT = httpx.Client


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Aucun vrai secret, heure de Paris, pas de cache de calendrier entre tests."""
    monkeypatch.setattr(config, "SECRETS_FILE", tmp_path / "secrets.json")
    for key in connector.ALL_KEYS:
        monkeypatch.delenv(key, raising=False)
    tz_avant = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Europe/Paris")
    time.tzset()
    agenda._cache.clear()
    yield
    # 30/10 : TZ remise À LA MAIN, jamais `monkeypatch.undo()` : il défaisait aussi l'isolation du
    # conftest (même objet) AVANT sa fin, et `passerelle.stop()` effaçait le VRAI runtime/passerelle-jeton.
    if tz_avant is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = tz_avant
    time.tzset()


def secrets(tmp_path, **values):
    (tmp_path / "secrets.json").write_text(json.dumps(values), encoding="utf-8")


def mock_http(monkeypatch, handler):
    """Toutes les requêtes httpx des connecteurs passent par `handler`."""
    seen = []

    def record(request):
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(httpx, "Client", functools.partial(_REAL_CLIENT, transport=httpx.MockTransport(record)))
    return seen


# --- configuration absente ---

@pytest.mark.parametrize("call, key", [
    (lambda: translate.translate("bonjour", "anglais"), "DEEPL_API_KEY"),
    (lambda: transport.departures("Lille"), "NAVITIA_API_KEY"),
    (lambda: agenda.upcoming(), "CALDAV_URL"),
    (lambda: agenda.create("Dentiste", "2030-01-02T10:00"), "CALDAV_PASSWORD"),
])
def test_non_configure_dit_quoi_ajouter(call, key):
    with pytest.raises(connector.NotConfigured) as err:
        call()
    msg = str(err.value)
    assert "non configuré" in msg and key in msg and "config/secrets.json" in msg


def test_statut_lit_secrets_et_environnement(tmp_path, monkeypatch):
    secrets(tmp_path, DEEPL_API_KEY="abc:fx")
    monkeypatch.setenv("NAVITIA_API_KEY", "k")
    st = connector.status()
    assert st["Traduction DeepL"]["actif"] and st["Transports Navitia"]["actif"]
    assert not st["Agenda CalDAV"]["actif"]
    assert st["Home Assistant"]["manque"] == ["HOMEASSISTANT_URL", "HOMEASSISTANT_TOKEN"]


def test_outil_mcp_rend_la_consigne_telle_quelle():
    import asyncio

    from mcp.server.mcpserver.exceptions import ToolError

    from astrel import mcp_connectors

    with pytest.raises(ToolError, match="ajoute DEEPL_API_KEY"):
        asyncio.run(mcp_connectors.server.call_tool("translate_text", {"text": "a", "target_lang": "DE"}))


# --- DeepL ---

def test_deepl_codes_et_offre():
    assert translate.lang_code("Anglais") == "EN-GB"
    assert translate.lang_code("américain") == "EN-US"
    assert translate.lang_code("de") == "DE"
    assert translate.lang_code("EN-US", target=False) == "EN"
    assert translate.endpoint("xxx:fx") == "https://api-free.deepl.com"
    assert translate.endpoint("xxx") == "https://api.deepl.com"


def test_deepl_traduit(tmp_path, monkeypatch):
    secrets(tmp_path, DEEPL_API_KEY="cle-test:fx")

    def handler(req):
        assert req.url.host == "api-free.deepl.com" and req.url.path == "/v2/translate"
        assert req.headers["Authorization"] == "DeepL-Auth-Key cle-test:fx"
        body = json.loads(req.content)
        assert body == {"text": ["Bonjour"], "target_lang": "EN-GB", "formality": "prefer_more"}
        return httpx.Response(200, json={"translations": [{"detected_source_language": "FR", "text": "Hello"}]})

    mock_http(monkeypatch, handler)
    out = translate.translate("Bonjour", "anglais", formality="formel")
    assert out["traduction"] == "Hello" and out["langue_source"] == "FR"


def test_deepl_cle_refusee_et_texte_trop_long(tmp_path, monkeypatch):
    secrets(tmp_path, DEEPL_API_KEY="mauvaise")
    mock_http(monkeypatch, lambda req: httpx.Response(403, json={"message": "Wrong key"}))
    with pytest.raises(RuntimeError, match="refuse l'identifiant"):
        translate.translate("Bonjour", "DE")
    with pytest.raises(ValueError, match="trop long"):
        translate.translate("x" * (translate.MAX_CHARS + 1), "DE")


# --- Navitia ---

def test_navitia_departs_temps_reel(tmp_path, monkeypatch):
    secrets(tmp_path, NAVITIA_API_KEY="cle")
    today = datetime.now().strftime("%Y%m%d")

    def handler(req):
        assert req.url.path.startswith("/v1/coverage/sncf/")
        assert req.headers["Authorization"].startswith("Basic ")
        if req.url.path.endswith("/places"):
            assert req.url.params["q"] == "Toulouse" and req.url.params.get_list("type[]") == ["stop_area"]
            return httpx.Response(200, json={"places": [
                {"id": "stop_area:SNCF:87611004", "name": "Toulouse Matabiau (Toulouse)", "embedded_type": "stop_area"}]})
        assert req.url.path.endswith("/stop_areas/stop_area:SNCF:87611004/departures")
        return httpx.Response(200, json={
            "departures": [{
                "display_informations": {"commercial_mode": "TER", "trip_short_name": "871234",
                                         "direction": "Albi-Ville (Albi)",
                                         "links": [{"type": "disruption", "id": "d1"}]},
                "stop_date_time": {"departure_date_time": f"{today}T081200",
                                   "base_departure_date_time": f"{today}T080200"},
            }],
            "disruptions": [{"id": "d1", "messages": [{"text": "<p>Retard dû à un <b>incident</b></p>"}]}],
        })

    mock_http(monkeypatch, handler)
    out = transport.departures("Toulouse")
    assert out["gare"].startswith("Toulouse Matabiau")
    dep = out["departs"][0]
    assert dep == {"heure": "08:12", "retard_min": 10, "ligne": "TER 871234",
                   "direction": "Albi-Ville (Albi)", "perturbation": "Retard dû à un incident"}


def test_navitia_pas_de_trajet(tmp_path, monkeypatch):
    secrets(tmp_path, NAVITIA_API_KEY="cle", NAVITIA_BASE_URL="https://api.navitia.io/v1/", NAVITIA_COVERAGE="fr-se")

    def handler(req):
        assert req.url.host == "api.navitia.io" and "/coverage/fr-se/journeys" in req.url.path
        assert req.url.params["from"] == "stop_area:A" and req.url.params["datetime_represents"] == "arrival"
        return httpx.Response(404, json={"error": {"id": "no_solution", "message": "no solution found"}})

    mock_http(monkeypatch, handler)
    with pytest.raises(LookupError, match="aucun trajet"):
        transport.journeys("stop_area:A", "stop_area:B", when="2030-01-02T09:00", arrive_by=True)


# --- CalDAV : lecture iCalendar ---

ICS = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
    "BEGIN:VEVENT\r\nUID:1\r\nDTSTART:20270115T080000Z\r\nDTEND:20270115T090000Z\r\n"
    "SUMMARY:Réunion\\, point Studio\r\nLOCATION:Bureau\r\nDESCRIPTION:Ligne 1\\nLigne\r\n  2\r\n"
    "BEGIN:VALARM\r\nTRIGGER:-PT10M\r\nDESCRIPTION:ne pas lire\r\nEND:VALARM\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:2\r\nDTSTART;VALUE=DATE:20270120\r\nSUMMARY:Congé\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:3\r\nDTSTART;TZID=\"Europe/Paris\":20270710T140000\r\nDURATION:PT1H30M\r\n"
    "SUMMARY:Été\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def test_ical_lecture():
    ev = {e["UID"]: e for e in agenda.parse_events(ICS)}
    # 08:00 UTC en janvier = 9 h à Paris (heure d'hiver), même lu en été.
    assert ev["1"]["DTSTART"].hour == 9
    assert ev["1"]["SUMMARY"] == "Réunion, point Studio"
    assert ev["1"]["DESCRIPTION"] == "Ligne 1\nLigne 2"
    assert ev["2"]["DTSTART"] == date(2027, 1, 20) and ev["2"]["DTEND"] == date(2027, 1, 21)
    assert ev["3"]["DTSTART"].hour == 14 and ev["3"]["DTEND"].hour == 15 and ev["3"]["DTEND"].minute == 30
    assert agenda._say(ev["3"]["DTSTART"]) == "samedi 10 juillet à 14 h"


def test_recurrence_depliee_si_le_serveur_ne_le_fait_pas():
    ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:r\r\nDTSTART;TZID=Europe/Paris:20261005T180000\r\n"
           "DTEND;TZID=Europe/Paris:20261005T190000\r\nRRULE:FREQ=WEEKLY;COUNT=6\r\n"
           "EXDATE;TZID=Europe/Paris:20261012T180000\r\nSUMMARY:Sport\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    ev = agenda.parse_events(ics)[0]
    lo = datetime(2026, 10, 1).astimezone()
    occ = agenda._expand(ev, lo, lo + timedelta(days=60))
    days = [o["DTSTART"].date().isoformat() for o in occ]
    assert days == ["2026-10-05", "2026-10-19", "2026-10-26", "2026-11-02", "2026-11-09"]
    # Après le passage à l'heure d'hiver (25/10), toujours 18 h.
    assert all(o["DTSTART"].hour == 18 for o in occ)


def test_ical_ecriture_pliee_et_echappee():
    start = datetime(2027, 1, 15, 9, 0).astimezone()
    ics = agenda.build_event("u@astrel", "Dîner ; chez Léa, " + "très " * 30, start, start + timedelta(hours=2),
                             reminder_minutes=15)
    for line in ics.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75
    assert "DTSTART:20270115T080000Z" in ics and "TRIGGER:-PT15M" in ics
    back = agenda.parse_events(ics)[0]
    assert back["SUMMARY"].startswith("Dîner ; chez Léa, très")


# --- CalDAV : découverte, lecture, création (serveur simulé façon Nextcloud) ---

def _ms(*responses):
    body = "".join(
        f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>{props}</d:prop>"
        f"<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>" for href, props in responses)
    return httpx.Response(207, content=(
        '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        f"{body}</d:multistatus>").encode())


def nextcloud(created):
    def handler(req):
        path = req.url.path
        if req.method == "PROPFIND" and path == "/remote.php/dav":
            return _ms((path, "<d:resourcetype><d:collection/></d:resourcetype>"
                              "<d:current-user-principal><d:href>/remote.php/dav/principals/users/utilisateur/</d:href>"
                              "</d:current-user-principal>"))
        if req.method == "PROPFIND" and path.endswith("/principals/users/utilisateur/"):
            return _ms((path, "<c:calendar-home-set><d:href>/remote.php/dav/calendars/utilisateur/</d:href></c:calendar-home-set>"))
        if req.method == "PROPFIND" and path == "/remote.php/dav/calendars/utilisateur/":
            assert req.headers["Depth"] == "1"
            return _ms((path, "<d:resourcetype><d:collection/></d:resourcetype>"),
                       (path + "personal/", "<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>"
                                            "<d:displayname>Personnel</d:displayname>"
                                            "<c:supported-calendar-component-set><c:comp name=\"VEVENT\"/>"
                                            "</c:supported-calendar-component-set>"),
                       (path + "tasks/", "<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>"
                                         "<d:displayname>Tâches</d:displayname>"
                                         "<c:supported-calendar-component-set><c:comp name=\"VTODO\"/>"
                                         "</c:supported-calendar-component-set>"))
        if req.method == "REPORT":
            assert b"<c:expand" in req.content and req.headers["Depth"] == "1"
            soon = (datetime.now() + timedelta(days=1)).replace(hour=14, minute=30, second=0)
            local = soon.strftime("%Y%m%dT%H%M%S")
            ics = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:a\n"
                   f"DTSTART;TZID=Europe/Paris:{local}\nDURATION:PT1H\nSUMMARY:Kiné\nEND:VEVENT\nEND:VCALENDAR\n")
            return _ms((path + "a.ics", f"<c:calendar-data>{ics}</c:calendar-data>"))
        if req.method == "PUT":
            assert req.headers["If-None-Match"] == "*"
            created.append((path, req.content.decode()))
            return httpx.Response(201)
        return httpx.Response(405)

    return handler


def test_caldav_bout_en_bout(tmp_path, monkeypatch):
    secrets(tmp_path, CALDAV_URL="https://cloud.example.org/remote.php/dav", CALDAV_USERNAME="utilisateur",
            CALDAV_PASSWORD="mdp-appli")
    created = []
    mock_http(monkeypatch, nextcloud(created))
    assert agenda.calendars() == [{"nom": "Personnel"}]   # le calendrier de tâches est écarté
    out = agenda.upcoming(days=3)
    assert out["calendrier"] == "Personnel"
    assert [e["titre"] for e in out["evenements"]] == ["Kiné"]
    assert out["evenements"][0]["quand"].endswith("à 14 h 30") and out["evenements"][0]["fin"] == "15:30"
    res = agenda.create("Dentiste", "2030-01-02T10:00", duration_minutes=30, location="Albi")
    assert res["calendrier"] == "Personnel" and res["quand"] == "mercredi 2 janvier à 10 h"
    path, ics = created[0]
    assert path.startswith("/remote.php/dav/calendars/utilisateur/personal/") and path.endswith(".ics")
    assert "DTSTART:20300102T090000Z" in ics and "DTEND:20300102T093000Z" in ics and "LOCATION:Albi" in ics


def test_caldav_calendrier_demande_introuvable(tmp_path, monkeypatch):
    secrets(tmp_path, CALDAV_URL="https://cloud.example.org/remote.php/dav", CALDAV_USERNAME="utilisateur",
            CALDAV_PASSWORD="x", CALDAV_CALENDAR="Travail")
    mock_http(monkeypatch, nextcloud([]))
    with pytest.raises(LookupError, match="Personnel"):
        agenda.upcoming()


def test_caldav_creation_refuse_fin_avant_debut(tmp_path):
    secrets(tmp_path, CALDAV_URL="https://x", CALDAV_USERNAME="u", CALDAV_PASSWORD="p")
    with pytest.raises(ValueError, match="après le début"):
        agenda.create("X", "2030-01-02T10:00", end="2030-01-02T09:00")


# --- déclaration MCP et garde-fou ---

def test_mcp_json_home_assistant_et_droits(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MCP_CONFIG_FILE", tmp_path / "mcp.json")
    monkeypatch.setattr(config, "BROWSER_ENABLED", False)
    data = mcp_config.write()["mcpServers"]
    assert "connecteurs" in data and "homeassistant" not in data

    secrets(tmp_path, HOMEASSISTANT_URL="http://homeassistant.local:8123/", HOMEASSISTANT_TOKEN="jeton")
    monkeypatch.setenv("DEEPL_API_KEY", "env:fx")
    data = mcp_config.write()["mcpServers"]
    assert data["homeassistant"] == {"type": "http", "url": "http://homeassistant.local:8123/api/mcp",
                                     "headers": {"Authorization": "Bearer jeton"}}
    assert data["connecteurs"]["env"]["DEEPL_API_KEY"] == "env:fx"
    assert stat.S_IMODE(os.stat(config.MCP_CONFIG_FILE).st_mode) == 0o600


def test_garde_fou_connecteurs_sensibles(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "MODE_FILE", tmp_path / "autonomy.json")
    monkeypatch.setattr(guard, "GRANTS_FILE", tmp_path / "grants.json")
    for tool in ("calendar_create_event", "calendar_upcoming", "transport_journey", "translate_text"):
        name = f"mcp__connecteurs__{tool}"
        assert guard.classify(name, {}).level == guard.SENSITIVE, tool
        assert not guard.assess(name, {}, mode="autonome").needs_confirm
        assert guard.assess(name, {}, mode="prudent").needs_confirm
    assert guard.classify("mcp__homeassistant__HassTurnOn", {}).level == guard.SENSITIVE
