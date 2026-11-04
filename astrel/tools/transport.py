"""Horaires et trajets en transports en commun, par l'API Navitia.

Deux portes vers le même moteur, au choix de l'utilisateur :
- l'API SNCF (défaut) : `https://api.sncf.com/v1`, couverture « sncf » —
  trains seulement (TGV, TER, Intercités, Transilien), clé gratuite, 5 000
  requêtes par jour ;
- navitia.io (Hove) : `https://api.navitia.io/v1`, couvertures régionales
  (« fr-idf », « fr-se »…) qui incluent bus, tram et métro.

Clé : `NAVITIA_API_KEY` ; facultatifs : `NAVITIA_BASE_URL`, `NAVITIA_COVERAGE`.
L'authentification est la même partout : la clé en nom d'utilisateur, mot de
passe vide. Les heures rendues par Navitia sont celles du réseau (heure de
Paris), sans fuseau.
"""
from __future__ import annotations

import re
from datetime import datetime

import httpx

from . import connector
from .. import hors_ligne

NAME = "Transports Navitia"
DEFAULT_BASE = "https://api.sncf.com/v1"
DEFAULT_COVERAGE = "sncf"
_NAVITIA_ID = re.compile(r"^(stop_area|stop_point|admin|address|poi):|^-?\d+(\.\d+)?;-?\d+(\.\d+)?$")


def _get(path: str, params: list[tuple[str, str]] | None = None) -> dict:
    hors_ligne.verifier("transports (SNCF, Navitia)")  # 04/11
    cfg = connector.settings(NAME)
    base = (cfg["NAVITIA_BASE_URL"] or DEFAULT_BASE).rstrip("/")
    coverage = cfg["NAVITIA_COVERAGE"] or DEFAULT_COVERAGE
    url = f"{base}/coverage/{coverage}/{path.lstrip('/')}"
    try:
        with httpx.Client(timeout=connector.TIMEOUT_S) as client:
            r = client.get(url, params=params or [], auth=(cfg["NAVITIA_API_KEY"], ""))
            if r.status_code == 404:
                # Navitia répond 404 « no_solution » quand il n'y a pas de trajet : pas une panne.
                err = (r.json() if r.headers.get("content-type", "").startswith("application/json") else {}).get("error") or {}
                if err.get("id") in ("no_solution", "no_origin", "no_destination", "no_origin_nor_destination",
                                     "date_out_of_bounds", "unknown_object"):
                    raise LookupError(_NO_SOLUTION.get(err["id"], err.get("message", "aucun résultat")))
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as exc:
        raise connector.http_error(NAME, exc) from exc


_NO_SOLUTION = {
    "no_solution": "aucun trajet trouvé pour cette demande",
    "no_origin": "point de départ introuvable",
    "no_destination": "destination introuvable",
    "no_origin_nor_destination": "départ et destination introuvables",
    "date_out_of_bounds": "cette date est hors des horaires publiés",
    "unknown_object": "gare ou arrêt inconnu",
}


def _nav_time(raw: str) -> str:
    """« 20260930T143000 » → « 14:30 », ou « 01/10 14:30 » si ce n'est pas aujourd'hui."""
    try:
        dt = datetime.strptime(raw, "%Y%m%dT%H%M%S")
    except (TypeError, ValueError):
        return raw or ""
    return dt.strftime("%H:%M") if dt.date() == datetime.now().date() else dt.strftime("%d/%m %H:%M")


def _delay_min(real: str, base: str) -> int:
    try:
        diff = datetime.strptime(real, "%Y%m%dT%H%M%S") - datetime.strptime(base, "%Y%m%dT%H%M%S")
    except (TypeError, ValueError):
        return 0
    return round(diff.total_seconds() / 60)


def _nav_datetime(when: str) -> str:
    """ISO local (« 2026-10-01T08:30 ») → format Navitia."""
    return datetime.fromisoformat(when).strftime("%Y%m%dT%H%M%S")


def search_places(query: str, count: int = 5, stops_only: bool = True) -> list[dict]:
    query = query.strip()
    if not query:
        raise ValueError("nom de gare ou d'arrêt vide")
    params = [("q", query), ("count", str(max(1, min(count, 20))))]
    types = ("stop_area",) if stops_only else ("stop_area", "address", "administrative_region", "poi")
    params += [("type[]", t) for t in types]
    data = _get("places", params)
    return [
        {"id": p.get("id", ""), "nom": p.get("name", ""), "type": p.get("embedded_type", "")}
        for p in data.get("places") or []
    ]


def _resolve(place: str, stops_only: bool) -> dict:
    place = place.strip()
    if _NAVITIA_ID.match(place):
        return {"id": place, "nom": place}
    found = search_places(place, 1, stops_only)
    if not found:
        raise LookupError(f"aucune gare ni aucun arrêt ne correspond à « {place} »")
    return found[0]


def _line(info: dict) -> str:
    """« TER 17532 », « TGV INOUI 6605 », « Métro 1 »."""
    mode = info.get("commercial_mode") or info.get("physical_mode") or ""
    num = info.get("trip_short_name") or info.get("headsign") or ""
    code = info.get("code") or ""
    parts = [mode]
    if code and code not in mode:
        parts.append(code)
    if num and num != code:
        parts.append(num)
    return " ".join(p for p in parts if p).strip()


def _disruptions(data: dict) -> dict[str, str]:
    out = {}
    for d in data.get("disruptions") or []:
        msgs = [m.get("text", "") for m in d.get("messages") or [] if m.get("text")]
        effect = (d.get("severity") or {}).get("effect", "")
        text = re.sub(r"<[^>]+>", " ", msgs[0]) if msgs else effect
        out[d.get("id", "")] = " ".join(text.split())[:240]
    return out


def departures(station: str, count: int = 6, when: str = "") -> dict:
    stop = _resolve(station, stops_only=True)
    params = [("count", str(max(1, min(count, 20)))), ("data_freshness", "realtime")]
    if when:
        params.append(("from_datetime", _nav_datetime(when)))
    data = _get(f"stop_areas/{stop['id']}/departures", params)
    disruptions = _disruptions(data)
    out = []
    for dep in data.get("departures") or []:
        info = dep.get("display_informations") or {}
        sdt = dep.get("stop_date_time") or {}
        real, base = sdt.get("departure_date_time", ""), sdt.get("base_departure_date_time", "")
        notes = [disruptions[l["id"]] for l in info.get("links") or []
                 if l.get("type") == "disruption" and disruptions.get(l.get("id"))]
        out.append({
            "heure": _nav_time(real),
            "retard_min": _delay_min(real, base) if base else 0,
            "ligne": _line(info),
            "direction": info.get("direction") or info.get("headsign", ""),
            "perturbation": notes[0] if notes else "",
        })
    return {"gare": stop["nom"], "departs": out}


def journeys(origin: str, destination: str, when: str = "", arrive_by: bool = False, count: int = 3) -> dict:
    start = _resolve(origin, stops_only=False)
    end = _resolve(destination, stops_only=False)
    params = [("from", start["id"]), ("to", end["id"]), ("count", str(max(1, min(count, 5)))),
              ("data_freshness", "realtime")]
    if when:
        params += [("datetime", _nav_datetime(when)),
                   ("datetime_represents", "arrival" if arrive_by else "departure")]
    data = _get("journeys", params)
    out = []
    for j in data.get("journeys") or []:
        legs = []
        for s in j.get("sections") or []:
            if s.get("type") != "public_transport":
                continue
            info = s.get("display_informations") or {}
            legs.append({
                "ligne": _line(info),
                "depart": f"{_nav_time(s.get('departure_date_time', ''))} {(s.get('from') or {}).get('name', '')}".strip(),
                "arrivee": f"{_nav_time(s.get('arrival_date_time', ''))} {(s.get('to') or {}).get('name', '')}".strip(),
                "direction": info.get("direction", ""),
            })
        out.append({
            "depart": _nav_time(j.get("departure_date_time", "")),
            "arrivee": _nav_time(j.get("arrival_date_time", "")),
            "duree_min": round((j.get("duration") or 0) / 60),
            "correspondances": j.get("nb_transfers", 0),
            "etat": j.get("status", ""),  # ex. SIGNIFICANT_DELAYS, NO_SERVICE ; vide = normal
            "etapes": legs,
        })
    return {"de": start["nom"], "vers": end["nom"], "trajets": out}
