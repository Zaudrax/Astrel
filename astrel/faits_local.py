"""Faits EXACTS calculés par le cœur pour le moteur local (05/11/2026, lot 8, recommandation R2 du rapport
docs/chantiers/RECHERCHE-QWEN-2026-11-05.md : faire calculer un programme bat le raisonnement du modèle, PAL).

`faits(demande, langue)` rend les lignes « [Contrôle d'Astrel : …] » ajoutées au message du LOCAL seulement
(jamais à Claude, jamais gardées pour « Envoyer à Claude ») :
- IBAN, carte, NIR : `sensible.faits` (lot du 05/11) ;
- carte dont la clé de Luhn est FAUSSE, quand la demande parle de carte ou de Luhn ;
- jour de la semaine d'une date écrite en entier, nombre de jours entre deux dates ;
- 05/11 (essai de l'utilisateur, 19:28 : « combien de jours entre le 1er janvier et le 14 juillet ? » → 203, faux : 194) :
  deux dates SANS année (« du 3 mars au 10 mai », « January 1 and July 14 ») : l'écart pour l'année en cours (ou
  celle d'une date écrite en entier dans la demande), en comptant ou non les deux dates, et l'effet d'une année
  bissextile quand février est entre les deux ;
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
# 05/11 : date SANS année (jamais suivie d'une année : celle-là est lue par `_DATE_FR`/`_DATE_EN`) ; en anglais, le
# mois avec sa majuscule seulement (« you may 2 », « march 3 miles » ne sont pas des dates).
_SANS_AN_FR = re.compile(r"\b(1er|[0-3]?\d)\s+(" + "|".join(_MOIS) + r")\b(?!\s+\d)", re.I)
_MONTHS_CAP = "|".join(m.capitalize() for m in _MONTHS)
_SANS_AN_EN = re.compile(r"\b(?:(" + _MONTHS_CAP + r")\s+([0-3]?\d)(?:st|nd|rd|th)?\b(?!,?\s+\d)|([0-3]?\d)"
                         r"(?:st|nd|rd|th)?\s+(" + _MONTHS_CAP + r")\b(?!,?\s+\d))")
# Écart entre dates sans année : seulement si la demande porte sur une durée (« 14 juillet et 15 août, fériés »
# ne demande rien).
_ECART_MOT = re.compile(r"\bcombien|\bjours?\b|écart|\bdurée|\bsépar|how (?:many|long)|\bdays?\b|\bbetween\b", re.I)
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
           "calc": "{a} {op} {b} = {r}",
           # 05/11 : dates sans année
           "ecart_an": "{n} jours du {a} au {b} en {an} ({n1} en comptant les deux dates)",
           "ecart_an2": "{n} jours du {a} au {b} ({n1} en comptant les deux dates)",
           "bis": "une année bissextile : {m} jours ({m1} en comptant les deux dates)",
           "ord": "une année non bissextile : {m} jours ({m1} en comptant les deux dates)"},
    "en": {"tete": "Astrel check:", "jour": "{d} is a {j}", "ecart": "{n} days between {a} and {b}",
           "auj": "today is {j}, {d}", "carte_ko": "card number …{m}: INVALID Luhn check digit",
           "calc": "{a} {op} {b} = {r}",
           "ecart_an": "{n} days from {a} to {b} in {an} ({n1} counting both dates)",
           "ecart_an2": "{n} days from {a} to {b} ({n1} counting both dates)",
           "bis": "in a leap year: {m} days ({m1} counting both dates)",
           "ord": "in a non-leap year: {m} days ({m1} counting both dates)"},
}


def _date_txt(d: dt.date, lang: str, an: bool = True) -> str:
    mois = _MOIS_NOM[lang][d.month - 1]
    if lang == "en":
        return f"{mois} {d.day}, {d.year}" if an else f"{mois} {d.day}"
    return f"{'1er' if d.day == 1 else d.day} {mois}" + (f" {d.year}" if an else "")


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


def _dates_pos(texte: str) -> list[tuple[int, int, int, int | None]]:
    """(position, mois, jour, année ou None) de chaque date de la demande, dans l'ordre du texte."""
    out: list[tuple[int, int, int, int | None]] = []
    for m in _DATE_FR.finditer(texte):
        out.append((m.start(), _MOIS[m.group(2).lower()], 1 if m.group(1).lower() == "1er" else int(m.group(1)),
                    int(m.group(3))))
    for m in _DATE_EN.finditer(texte):
        mois, jour, an = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(5), m.group(4), m.group(6))
        out.append((m.start(), _MONTHS[mois.lower()], int(jour), int(an)))
    for m in _SANS_AN_FR.finditer(texte):
        out.append((m.start(), _MOIS[m.group(2).lower()], 1 if m.group(1).lower() == "1er" else int(m.group(1)), None))
    for m in _SANS_AN_EN.finditer(texte):
        mois, jour = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        out.append((m.start(), _MONTHS[mois.lower()], int(jour), None))
    return sorted(out)


def _ecart(ma: int, da: int, mb: int, db: int, an: int, an_b: int | None = None) -> int:
    """Jours de (ma, da) de l'année `an` à (mb, db) : l'année `an_b`, sinon la même, ou la suivante si la seconde
    date tombe avant la première dans l'année. Lève ValueError (29 février d'une année ordinaire, 31 avril)."""
    a = dt.date(an, ma, da)
    b = dt.date(an if an_b is None else an_b, mb, db)
    if an_b is None and b < a:
        b = dt.date(an + 1, mb, db)
    return (b - a).days


def _ecart_sans_an(texte: str, lang: str, auj: dt.date) -> list[str]:
    """05/11 : écart entre les deux premières dates quand au moins une n'a pas d'année (l'année en cours, ou celle
    d'une date écrite en entier) ; effet d'une année bissextile si février tombe entre les deux. Rien sinon."""
    if not _ECART_MOT.search(texte):
        return []
    dates = _dates_pos(texte)
    if len(dates) < 2 or all(d[3] is not None for d in dates[:2]) or len([d for d in dates if d[3]]) >= 2:
        return []
    t = _TEXTES[lang]
    (_, ma, da, ya), (_, mb, db, yb) = dates[0], dates[1]
    an = ya or yb or auj.year
    try:
        if ya is None and yb is not None:  # « du 1er décembre au 10 janvier 2027 » : la première l'année d'avant
            an = yb if (ma, da) <= (mb, db) else yb - 1
        n = _ecart(ma, da, mb, db, an, yb)
        a = dt.date(an, ma, da)
        b = a + dt.timedelta(days=n)
        # même dates une année ordinaire (2025 → 2026) et une année bissextile (février 2024 dans l'intervalle)
        ordinaire = _ecart(ma, da, mb, db, 2025)
        bissextile = max(_ecart(ma, da, mb, db, x) for x in (2023, 2024))
    except ValueError:
        return []
    meme_an = a.year == b.year and ya is None and yb is None
    ligne = (t["ecart_an"] if meme_an else t["ecart_an2"]).format(
        n=n, n1=n + 1, an=an, a=_date_txt(a, lang, not meme_an), b=_date_txt(b, lang, not meme_an))
    out = [ligne]
    if ordinaire != bissextile and ya is None and yb is None:  # une année écrite tranche
        autre = ordinaire if n == bissextile else bissextile
        out.append(t["ord" if n == bissextile else "bis"].format(m=autre, m1=autre + 1))
    return out


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
    auj = aujourdhui or dt.date.today()
    calc += _ecart_sans_an(texte, lang, auj)
    if _AUJOURDHUI.search(texte):
        calc.append(t["auj"].format(j=_JOURS[lang][auj.weekday()], d=_date_txt(auj, lang)))
    calc += _calculs(texte, lang)
    if calc:
        lignes.append(f"[{t['tete']} " + " ; ".join(calc) + ".]")
    return "\n".join(lignes)
