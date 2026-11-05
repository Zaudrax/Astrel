"""Faits EXACTS calculés par le cœur pour le moteur local (05/11/2026, lot 8, recommandation R2 du rapport
docs/chantiers/RECHERCHE-QWEN-2026-11-05.md : faire calculer un programme bat le raisonnement du modèle, PAL).

`faits(demande, langue)` rend les lignes « [Contrôle d'Astrel : …] » ajoutées au message du LOCAL seulement
(jamais à Claude, jamais gardées pour « Envoyer à Claude ») :
- IBAN, carte, NIR : `sensible.faits` (lot du 05/11) ;
- carte dont la clé de Luhn est FAUSSE, quand la demande parle de carte ou de Luhn ;
- jour de la semaine d'une date écrite en entier, nombre de jours entre deux dates ;
- date du jour quand la demande en a besoin (« aujourd'hui », « quel jour »…) : le modèle ne la connaît pas ;
- calcul simple écrit en toutes lettres ou en chiffres (« 17 fois 23 », « 15 % de 240 », « 12 × 12 »).
Rien n'est deviné : ce qui n'est pas reconnu exactement ne donne aucune ligne."""
from __future__ import annotations

import datetime as dt
import re
from typing import Callable

from . import sensible

MAX_CALCULS = 4

_MOIS = {"janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
         "août": 8, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12}
_MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"), 1)}
_JOURS = {"fr": ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"),
          "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")}
_MOIS_NOM = {"fr": ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
                    "novembre", "décembre"),
             "en": tuple(m.capitalize() for m in _MONTHS)}
_DATE_FR = re.compile(r"\b(1er|[0-3]?\d)\s+(" + "|".join(_MOIS) + r")\s+(\d{3,4})\b", re.I)
_DATE_EN = re.compile(r"\b(?:(" + "|".join(_MONTHS) + r")\s+([0-3]?\d)(?:st|nd|rd|th)?,?\s+(\d{3,4})|([0-3]?\d)"
                      r"(?:st|nd|rd|th)?\s+(" + "|".join(_MONTHS) + r")\s+(\d{3,4}))\b", re.I)
_AUJOURDHUI = re.compile(r"aujourd['’]hui|\bdemain\b|\bhier\b|quel jour (?:sommes|est-on|on est)|quelle date|"
                         r"date du jour|\btoday\b|\btomorrow\b|\byesterday\b|what day is it|what(?:'s| is) the date",
                         re.I)
# Un calcul n'est fait que si la demande en est un (« le port 8080 / 443 » n'est pas une division).
_CALCUL_MOT = re.compile(r"\bcombien\b|\bcalcul|\bfont\b|\bfait\b|\bégal|=|résultat|how much|what is|\bcompute|"
                         r"\bequals?\b|\bcalculate", re.I)
_CARTE_MOT = re.compile(r"\bcarte|luhn|\bcard\b", re.I)

_NB = r"(\d+(?:[.,]\d+)?)"
_OPS: list[tuple[re.Pattern, str, Callable[[float, float], float]]] = [
    (re.compile(_NB + r"\s*(?:%|pour ?cent|percent)\s+(?:de|of)\s+" + _NB, re.I), "%", lambda a, b: a * b / 100),
    (re.compile(_NB + r"\s+(?:fois|x|×|\*|multiplié par|multiplied by|times)\s+" + _NB, re.I), "×", lambda a, b: a * b),
    (re.compile(_NB + r"\s+(?:divisé par|/|÷|divided by)\s+" + _NB, re.I), "÷", lambda a, b: a / b if b else float("nan")),
    (re.compile(_NB + r"\s+(?:plus|\+)\s+" + _NB, re.I), "+", lambda a, b: a + b),
    (re.compile(_NB + r"\s+(?:moins|−|-|minus)\s+" + _NB, re.I), "−", lambda a, b: a - b),
]

_TEXTES = {
    "fr": {"tete": "Contrôle d'Astrel :", "jour": "le {d} est un {j}", "ecart": "{n} jours entre le {a} et le {b}",
           "auj": "aujourd'hui, nous sommes le {j} {d}", "carte_ko": "numéro de carte …{m} : clé de Luhn INVALIDE",
           "calc": "{a} {op} {b} = {r}"},
    "en": {"tete": "Astrel check:", "jour": "{d} is a {j}", "ecart": "{n} days between {a} and {b}",
           "auj": "today is {j}, {d}", "carte_ko": "card number …{m}: INVALID Luhn check digit",
           "calc": "{a} {op} {b} = {r}"},
}


def _date_txt(d: dt.date, lang: str) -> str:
    mois = _MOIS_NOM[lang][d.month - 1]
    if lang == "en":
        return f"{mois} {d.day}, {d.year}"
    return f"{'1er' if d.day == 1 else d.day} {mois} {d.year}"


def _dates(texte: str) -> list[dt.date]:
    out: list[tuple[int, dt.date]] = []
    for m in _DATE_FR.finditer(texte):
        jour = 1 if m.group(1).lower() == "1er" else int(m.group(1))
        try:
            out.append((m.start(), dt.date(int(m.group(3)), _MOIS[m.group(2).lower()], jour)))
        except ValueError:
            continue
    for m in _DATE_EN.finditer(texte):
        mois, jour, an = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(5), m.group(4), m.group(6))
        try:
            out.append((m.start(), dt.date(int(an), _MONTHS[mois.lower()], int(jour))))
        except ValueError:
            continue
    return [d for _, d in sorted(out, key=lambda x: x[0])]


def _nombre(brut: str) -> float:
    return float(brut.replace(",", "."))


def _ecrire(x: float, lang: str) -> str:
    if x != x:  # nan : division par zéro
        return "indéfini" if lang == "fr" else "undefined"
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    s = f"{x:.6g}"
    return s.replace(".", ",") if lang == "fr" else s


def _calculs(texte: str, lang: str) -> list[str]:
    t, out, pris = _TEXTES[lang], [], []
    if not _CALCUL_MOT.search(texte):
        return out
    for motif, op, f in _OPS:
        for m in motif.finditer(texte):
            if any(a < m.end() and m.start() < b for a, b in pris):
                continue
            a, b = _nombre(m.group(1)), _nombre(m.group(2))
            r = f(a, b)
            if op == "%":
                out.append(t["calc"].format(a=m.group(1) + " %", op="de" if lang == "fr" else "of", b=m.group(2),
                                            r=_ecrire(r, lang)))
            else:
                out.append(t["calc"].format(a=m.group(1), op=op, b=m.group(2), r=_ecrire(r, lang)))
            pris.append(m.span())
            if len(out) >= MAX_CALCULS:
                return out
    return out


def _cartes_fausses(texte: str, lang: str) -> list[str]:
    if not _CARTE_MOT.search(texte):
        return []
    out = []
    for m in sensible._CARTE.finditer(texte):
        s = re.sub(r"[ -]", "", m.group(1))
        if 13 <= len(s) <= 19 and s[0] in "3456" and len(set(s)) > 1 and not sensible._luhn(s) \
                and not sensible._iban_valide(m.group(1)):
            out.append(_TEXTES[lang]["carte_ko"].format(m=s[-4:]))
    return out


def faits(demande: str, langue: str = "fr", aujourdhui: dt.date | None = None) -> str:
    """Lignes « [Contrôle d'Astrel : …] » pour le message du LOCAL ; "" si rien n'est reconnu."""
    lang = langue if langue in _TEXTES else "fr"
    texte = str(demande or "")
    t = _TEXTES[lang]
    lignes = []
    base = sensible.faits(texte, lang)
    if base:
        lignes.append(base)
    calc = _cartes_fausses(texte, lang)
    dates = _dates(texte)
    for d in dates[:3]:
        calc.append(t["jour"].format(d=_date_txt(d, lang), j=_JOURS[lang][d.weekday()]))
    if len(dates) >= 2:
        a, b = dates[0], dates[1]
        calc.append(t["ecart"].format(n=abs((b - a).days), a=_date_txt(a, lang), b=_date_txt(b, lang)))
    if _AUJOURDHUI.search(texte):
        auj = aujourdhui or dt.date.today()
        calc.append(t["auj"].format(j=_JOURS[lang][auj.weekday()], d=_date_txt(auj, lang)))
    calc += _calculs(texte, lang)
    if calc:
        lignes.append(f"[{t['tete']} " + " ; ".join(calc) + ".]")
    return "\n".join(lignes)
