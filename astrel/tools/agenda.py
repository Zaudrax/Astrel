"""Agenda auto-hébergé par CalDAV (Nextcloud, Radicale, Baïkal, SOGo…).

Réglages : `CALDAV_URL`, `CALDAV_USERNAME`, `CALDAV_PASSWORD` ; facultatif :
`CALDAV_CALENDAR` (nom affiché ou fin d'adresse du calendrier voulu).
`CALDAV_URL` peut être l'adresse du serveur DAV (Astrel trouve les calendriers
seul) ou directement celle d'un calendrier.

Pourquoi pas la bibliothèque `caldav` : sa version 3 tire une douzaine de
paquets (niquests, urllib3-future…) dans une venv partagée avec le système
(piège 4 du CLAUDE.md). Les deux opérations utiles — lire une plage, déposer
un événement — tiennent en deux requêtes HTTP : REPORT et PUT.

Les récurrences sont dépliées PAR LE SERVEUR (`<C:expand>`, RFC 4791) ; si
le serveur l'ignore, `dateutil.rrule` prend le relais.
"""
from __future__ import annotations

import re
import uuid
import xml.etree.ElementTree as ET
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urljoin, unquote
from zoneinfo import ZoneInfo

import httpx

from . import connector

NAME = "Agenda CalDAV"
DAV, CAL = "DAV:", "urn:ietf:params:xml:ns:caldav"
NS = {"d": DAV, "c": CAL}
JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre")

_cache: dict[tuple, dict] = {}   # réglages → calendrier trouvé (le serveur MCP vit longtemps)


# --- HTTP / WebDAV ---

def _client(cfg: dict) -> httpx.Client:
    return httpx.Client(auth=(cfg["CALDAV_USERNAME"], cfg["CALDAV_PASSWORD"]), timeout=connector.TIMEOUT_S,
                        follow_redirects=True, headers={"User-Agent": "Astrel/1.0"})


def _dav(client: httpx.Client, method: str, url: str, body: str, depth: str) -> list[tuple[str, ET.Element]]:
    """PROPFIND/REPORT → [(href absolu, <d:prop> des propriétés trouvées)]."""
    try:
        r = client.request(method, url, content=body.encode("utf-8"),
                           headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"})
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise connector.http_error(NAME, exc) from exc
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError as exc:
        raise RuntimeError(f"{NAME} : réponse illisible ({url} est-il bien un serveur CalDAV ?)") from exc
    out = []
    for resp in root.findall("d:response", NS):
        href = urljoin(str(r.url), (resp.findtext("d:href", "", NS) or "").strip())
        merged = ET.Element("prop")
        for ps in resp.findall("d:propstat", NS):
            if " 200 " in f" {ps.findtext('d:status', '', NS)} ":
                prop = ps.find("d:prop", NS)
                if prop is not None:
                    merged.extend(list(prop))
        out.append((href, merged))
    return out


def _href(prop: ET.Element, path: str) -> str:
    return (prop.findtext(path, "", NS) or "").strip()


_PROPFIND = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:resourcetype/><d:displayname/><d:current-user-principal/>
    <c:calendar-home-set/><c:supported-calendar-component-set/></d:prop>
</d:propfind>"""


def _is_calendar(prop: ET.Element) -> bool:
    return prop.find("d:resourcetype/c:calendar", NS) is not None


def _has_events(prop: ET.Element) -> bool:
    comps = prop.findall("c:supported-calendar-component-set/c:comp", NS)
    return not comps or any(c.get("name", "").upper() == "VEVENT" for c in comps)


def _name(href: str, prop: ET.Element) -> str:
    return (prop.findtext("d:displayname", "", NS) or "").strip() or unquote(href.rstrip("/").rsplit("/", 1)[-1])


def _list_calendars(client: httpx.Client, url: str) -> list[dict]:
    first = _dav(client, "PROPFIND", url, _PROPFIND, "0")
    if not first:
        raise RuntimeError(f"{NAME} : aucune réponse exploitable de {url}")
    href, prop = first[0]
    if _is_calendar(prop):
        return [{"nom": _name(href, prop), "url": href}]
    home = _href(prop, "c:calendar-home-set/d:href")
    if not home:
        principal = _href(prop, "d:current-user-principal/d:href")
        if not principal:
            raise RuntimeError(f"{NAME} : {url} n'annonce ni calendrier ni utilisateur ; donne l'adresse DAV du serveur")
        found = _dav(client, "PROPFIND", urljoin(href, principal), _PROPFIND, "0")
        home = _href(found[0][1], "c:calendar-home-set/d:href") if found else ""
        if not home:
            raise RuntimeError(f"{NAME} : le serveur n'indique pas où sont les calendriers")
    home = urljoin(href, home)
    return [{"nom": _name(h, p), "url": h}
            for h, p in _dav(client, "PROPFIND", home, _PROPFIND, "1")
            if _is_calendar(p) and _has_events(p)]


def _calendar(cfg: dict, client: httpx.Client) -> dict:
    key = (cfg["CALDAV_URL"], cfg["CALDAV_USERNAME"], cfg["CALDAV_CALENDAR"])
    if key in _cache:
        return _cache[key]
    cals = _list_calendars(client, cfg["CALDAV_URL"])
    if not cals:
        raise LookupError(f"{NAME} : aucun calendrier d'événements sur ce compte")
    wanted = cfg["CALDAV_CALENDAR"].strip().casefold()
    if wanted:
        match = [c for c in cals if c["nom"].casefold() == wanted
                 or c["url"].rstrip("/").rsplit("/", 1)[-1].casefold() == wanted]
        if not match:
            raise LookupError(f"calendrier « {cfg['CALDAV_CALENDAR']} » introuvable ; "
                              f"calendriers disponibles : {', '.join(c['nom'] for c in cals)}")
        cals = match
    _cache[key] = cals[0]
    return cals[0]


def calendars() -> list[dict]:
    cfg = connector.settings(NAME)
    with _client(cfg) as client:
        return [{"nom": c["nom"]} for c in _list_calendars(client, cfg["CALDAV_URL"])]


# --- iCalendar : lecture ---

def _unfold(ics: str) -> list[str]:
    return re.sub(r"\r?\n[ \t]", "", ics).splitlines()


def _unescape(v: str) -> str:
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


def _split(line: str) -> tuple[str, dict[str, str], str]:
    """« DTSTART;TZID=Europe/Paris:20261001T090000 » → (nom, paramètres, valeur)."""
    head, _, value = line.partition(":")
    # Un paramètre entre guillemets peut contenir « : » (TZID="…") : recoller.
    while head.count('"') % 2:
        more, _, value = value.partition(":")
        head += ":" + more
    name, *params = head.split(";")
    return name.upper(), {k.upper(): v.strip('"') for k, _, v in (p.partition("=") for p in params)}, value


# Heure locale : toujours par `.astimezone()` (sans argument), qui applique le
# décalage du MOMENT considéré. Un fuseau pris « maintenant » (+02:00 en été)
# décalerait d'une heure tous les rendez-vous de l'hiver.


def _parse_dt(value: str, params: dict[str, str]) -> datetime | date:
    value = value.strip()
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value[:8], "%Y%m%d").date()
    naive = datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        return naive.replace(tzinfo=timezone.utc).astimezone()
    tzid = params.get("TZID", "")
    try:
        return naive.replace(tzinfo=ZoneInfo(tzid)).astimezone() if tzid else naive.astimezone()
    except (KeyError, ValueError):
        return naive.astimezone()   # fuseau Windows (« Romance Standard Time ») : heure locale


def _zone(value: str, params: dict[str, str]) -> ZoneInfo | timezone | None:
    """Fuseau dans lequel une récurrence se déplie (RFC 5545) : celui du TZID, UTC pour « …Z », None pour
    une heure « flottante » (heure locale). 03/11 (CI Windows, machine en UTC) : déplier dans le fuseau de
    la MACHINE décalait d'une heure, après un changement d'heure, un rendez-vous hebdomadaire de Paris."""
    if value.strip().endswith("Z"):
        return timezone.utc
    try:
        return ZoneInfo(params["TZID"]) if params.get("TZID") else None
    except (KeyError, ValueError):
        return None


def _parse_duration(value: str) -> timedelta:
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip())
    if not m:
        return timedelta(0)
    sign, w, d, h, mi, s = m.groups()
    delta = timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0), minutes=int(mi or 0), seconds=int(s or 0))
    return -delta if sign == "-" else delta


def parse_events(ics: str) -> list[dict]:
    """Les VEVENT d'un texte iCalendar (les VALARM imbriqués sont ignorés)."""
    events, current, depth = [], None, 0
    for line in _unfold(ics):
        name, params, value = _split(line)
        if name == "BEGIN":
            if value.upper() == "VEVENT" and current is None:
                current, depth = {"exdates": []}, 0
            elif current is not None:
                depth += 1
            continue
        if name == "END":
            if current is not None and depth:
                depth -= 1
            elif current is not None and value.upper() == "VEVENT":
                events.append(current)
                current = None
            continue
        if current is None or depth:
            continue
        if name in ("DTSTART", "DTEND", "RECURRENCE-ID"):
            current[name] = _parse_dt(value, params)
            if name == "DTSTART":
                current["_fuseau"] = _zone(value, params)
        elif name == "EXDATE":
            current["exdates"] += [_parse_dt(v, params) for v in value.split(",") if v]
        elif name in ("SUMMARY", "LOCATION", "DESCRIPTION", "UID", "RRULE", "DURATION", "STATUS"):
            current[name] = _unescape(value) if name in ("SUMMARY", "LOCATION", "DESCRIPTION") else value
    for ev in events:
        start = ev.get("DTSTART")
        if "DTEND" not in ev and start is not None:
            if "DURATION" in ev:
                ev["DTEND"] = start + _parse_duration(ev["DURATION"])
            else:
                ev["DTEND"] = start + timedelta(days=1) if not isinstance(start, datetime) else start
    return [e for e in events if e.get("DTSTART") is not None]


def _as_dt(v: datetime | date) -> datetime:
    return v if isinstance(v, datetime) else datetime.combine(v, time.min).astimezone()


def _expand(ev: dict, start: datetime, end: datetime) -> list[dict]:
    """Occurrences d'un événement dans [start, end[ quand le serveur n'a pas déplié."""
    if "RRULE" not in ev:
        return [ev]
    from dateutil.rrule import rrulestr

    first = ev["DTSTART"]
    fuseau = ev.get("_fuseau")
    length = _as_dt(ev["DTEND"]) - _as_dt(first)
    anchor = _as_dt(first)
    rule = ev["RRULE"]
    if isinstance(first, date) and not isinstance(first, datetime):
        anchor = anchor.replace(tzinfo=None)
        excluded = {_as_dt(x).replace(tzinfo=None) for x in ev["exdates"]}
        lo, hi = (start - length).replace(tzinfo=None), end.replace(tzinfo=None)
    else:
        # Déplié à l'heure murale du fuseau de l'événement (heure locale s'il n'en a pas), puis rendu en
        # heure locale : 18 h à Paris reste 18 h à Paris après le changement d'heure, où que soit la machine.
        def murale(v: datetime) -> datetime:
            return (v.astimezone(fuseau) if fuseau else v.astimezone()).replace(tzinfo=None)
        # dateutil refuse un UNTIL en UTC face à une date de départ locale : on l'aligne.
        rule = re.sub(r"UNTIL=(\d{8}T\d{6})Z", lambda m: "UNTIL=" + murale(_parse_dt(m.group(1) + "Z", {}))
                      .strftime("%Y%m%dT%H%M%S"), rule)
        anchor = murale(anchor)
        excluded = {murale(_as_dt(x)) for x in ev["exdates"]}
        lo, hi = murale(start - length), murale(end)
    out = []
    for occ in rrulestr(rule, dtstart=anchor).between(lo, hi, inc=True):
        if occ in excluded:
            continue
        inst = dict(ev)
        if isinstance(first, datetime):
            inst["DTSTART"] = occ.replace(tzinfo=fuseau).astimezone() if fuseau else occ.astimezone()
        else:
            inst["DTSTART"] = occ.date()
        inst["DTEND"] = inst["DTSTART"] + length if isinstance(first, datetime) else occ.date() + length
        inst["RECURRENCE-ID"] = inst["DTSTART"]
        out.append(inst)
        if len(out) >= 200:
            break
    return out


def _say(v: datetime | date) -> str:
    """« mercredi 1 octobre à 14 h 30 » : fait pour être dit."""
    day = f"{JOURS[v.weekday()]} {v.day} {MOIS[v.month - 1]}"
    if isinstance(v, datetime):
        return f"{day} à {v.hour} h {v.minute:02d}" if v.minute else f"{day} à {v.hour} h"
    return day


def upcoming(days: int = 7, limit: int = 20) -> dict:
    cfg = connector.settings(NAME)
    days = max(1, min(int(days), 366))
    now = datetime.now().astimezone()
    end = now + timedelta(days=days)
    fmt = "%Y%m%dT%H%M%SZ"
    lo, hi = now.astimezone(timezone.utc).strftime(fmt), end.astimezone(timezone.utc).strftime(fmt)
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><c:calendar-data><c:expand start="{lo}" end="{hi}"/></c:calendar-data></d:prop>
  <c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">
    <c:time-range start="{lo}" end="{hi}"/>
  </c:comp-filter></c:comp-filter></c:filter>
</c:calendar-query>"""
    with _client(cfg) as client:
        cal = _calendar(cfg, client)
        found = _dav(client, "REPORT", cal["url"], body, "1")
    raw = [ev for _href_, prop in found for ev in parse_events(prop.findtext("c:calendar-data", "", NS) or "")]
    # Une occurrence modifiée (RECURRENCE-ID) remplace celle que la règle aurait produite.
    overrides = {(e.get("UID"), _as_dt(e["RECURRENCE-ID"])) for e in raw if "RECURRENCE-ID" in e and "RRULE" not in e}
    events = []
    for ev in raw:
        for inst in _expand(ev, now, end):
            if "RRULE" in ev and (inst.get("UID"), _as_dt(inst["RECURRENCE-ID"])) in overrides:
                continue
            if _as_dt(inst["DTEND"]) <= now or _as_dt(inst["DTSTART"]) >= end:
                continue
            if inst.get("STATUS", "").upper() == "CANCELLED":
                continue
            events.append(inst)
    events.sort(key=lambda e: _as_dt(e["DTSTART"]))
    out = []
    for e in events[:max(1, limit)]:
        all_day = not isinstance(e["DTSTART"], datetime)
        item = {"titre": e.get("SUMMARY", "(sans titre)"), "quand": _say(e["DTSTART"]),
                "debut": e["DTSTART"].isoformat(timespec="minutes") if not all_day else e["DTSTART"].isoformat(),
                "journee_entiere": all_day}
        if not all_day:
            item["fin"] = _as_dt(e["DTEND"]).strftime("%H:%M")
        if e.get("LOCATION"):
            item["lieu"] = e["LOCATION"]
        if e.get("DESCRIPTION"):
            item["note"] = " ".join(e["DESCRIPTION"].split())[:200]
        out.append(item)
    return {"calendrier": cal["nom"], "jours": days, "evenements": out, "total": len(events)}


# --- iCalendar : écriture ---

def _escape(v: str) -> str:
    return v.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545 : 75 octets par ligne au plus, suite précédée d'une espace."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n ".join(out)


def build_event(uid: str, title: str, start: datetime | date, end: datetime | date,
                location: str = "", description: str = "", reminder_minutes: int = 0) -> str:
    utc = "%Y%m%dT%H%M%SZ"

    def stamp(name: str, v: datetime | date) -> str:
        if isinstance(v, datetime):
            return f"{name}:{v.astimezone(timezone.utc).strftime(utc)}"
        return f"{name};VALUE=DATE:{v.strftime('%Y%m%d')}"

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Astrel//Astrel//FR", "CALSCALE:GREGORIAN",
             "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{datetime.now(timezone.utc).strftime(utc)}",
             stamp("DTSTART", start), stamp("DTEND", end), f"SUMMARY:{_escape(title)}"]
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    if reminder_minutes > 0:
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_escape(title)}",
                  f"TRIGGER:-PT{int(reminder_minutes)}M", "END:VALARM"]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def _parse_when(raw: str) -> datetime | date:
    raw = raw.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return date.fromisoformat(raw)
    dt = datetime.fromisoformat(raw)
    return dt if dt.tzinfo else dt.astimezone()


def create(title: str, start: str, end: str = "", duration_minutes: int = 60, location: str = "",
           description: str = "", reminder_minutes: int = 0) -> dict:
    title = " ".join(title.split())[:200]
    if not title:
        raise ValueError("titre vide")
    begin = _parse_when(start)
    if end:
        finish = _parse_when(end)
        if type(finish) is not type(begin):
            raise ValueError("début et fin doivent être tous deux des dates, ou tous deux des dates avec heure")
    elif isinstance(begin, datetime):
        finish = begin + timedelta(minutes=max(1, duration_minutes))
    else:
        finish = begin + timedelta(days=1)   # journée entière : fin EXCLUSIVE, le lendemain
    if finish <= begin:
        raise ValueError("la fin doit être après le début")
    cfg = connector.settings(NAME)
    uid = f"{uuid.uuid4()}@astrel"
    ics = build_event(uid, title, begin, finish, location.strip(), description.strip(), reminder_minutes)
    with _client(cfg) as client:
        cal = _calendar(cfg, client)
        target = cal["url"].rstrip("/") + f"/{uid.split('@')[0]}.ics"
        try:
            r = client.put(target, content=ics.encode("utf-8"),
                           headers={"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise connector.http_error(NAME, exc) from exc
    return {"cree": title, "quand": _say(begin), "calendrier": cal["nom"], "id": uid}
