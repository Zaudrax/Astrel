"""Traduction par l'API DeepL (texte).

Clé : `DEEPL_API_KEY`. Une clé de l'offre GRATUITE finit par « :fx » et
s'adresse à api-free.deepl.com (500 000 caractères par mois, puis refus, jamais
de facture) ; une clé Pro va sur api.deepl.com et est FACTURÉE au caractère.
D'où le plafond par demande (`MAX_CHARS`).

La « voix » passe par la chaîne d'Astrel : Whisper transcrit ce que l'utilisateur
dit, DeepL traduit le texte, la voix le prononce (Rémy, multilingue, lit
l'anglais avec l'accent anglais). L'API vocale de DeepL (flux WebSocket) est
réservée aux abonnements payants : pas branchée ici, voir docs/CONNECTEURS.md.
"""
from __future__ import annotations

import unicodedata

import httpx

from . import connector
from .. import hors_ligne

NAME = "Traduction DeepL"
MAX_CHARS = 5000

# Noms dits à l'oral → codes DeepL. EN et PT seuls sont dépréciés en cible :
# on choisit la variante britannique et européenne.
LANGS = {
    "anglais": "EN-GB", "americain": "EN-US", "anglais americain": "EN-US", "en": "EN-GB",
    "francais": "FR", "allemand": "DE", "espagnol": "ES", "italien": "IT",
    "portugais": "PT-PT", "bresilien": "PT-BR", "portugais bresilien": "PT-BR", "pt": "PT-PT",
    "neerlandais": "NL", "hollandais": "NL", "polonais": "PL", "russe": "RU", "japonais": "JA",
    "chinois": "ZH-HANS", "coreen": "KO", "arabe": "AR", "turc": "TR", "ukrainien": "UK",
    "suedois": "SV", "danois": "DA", "norvegien": "NB", "finnois": "FI", "grec": "EL",
    "tcheque": "CS", "roumain": "RO", "hongrois": "HU", "bulgare": "BG", "slovaque": "SK",
    "slovene": "SL", "estonien": "ET", "letton": "LV", "lituanien": "LT", "indonesien": "ID",
    "vietnamien": "VI", "hebreu": "HE", "thai": "TH",
}
FORMALITY = {"formel": "prefer_more", "vouvoiement": "prefer_more", "informel": "prefer_less",
             "tutoiement": "prefer_less", "": ""}


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.strip().lower()) if unicodedata.category(c) != "Mn")


def lang_code(lang: str, target: bool = True) -> str:
    """« anglais », « en », « EN-US » → code DeepL. Vide reste vide."""
    folded = _fold(lang)
    if not folded:
        return ""
    if folded in LANGS:
        code = LANGS[folded]
    else:
        code = lang.strip().upper()
    if not target:
        return code.split("-")[0]  # la langue SOURCE ne prend pas de variante
    return code


def endpoint(key: str) -> str:
    return "https://api-free.deepl.com" if key.endswith(":fx") else "https://api.deepl.com"


def _post(path: str, payload: dict | None = None, method: str = "POST") -> dict:
    hors_ligne.verifier("traduction (DeepL)")  # 04/11
    key = connector.settings(NAME)["DEEPL_API_KEY"]
    try:
        with httpx.Client(timeout=connector.TIMEOUT_S) as client:
            r = client.request(method, endpoint(key) + path, json=payload,
                               headers={"Authorization": f"DeepL-Auth-Key {key}"})
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as exc:
        raise connector.http_error(NAME, exc) from exc


def translate(text: str, target_lang: str, source_lang: str = "", formality: str = "") -> dict:
    text = text.strip()
    if not text:
        raise ValueError("texte vide")
    if len(text) > MAX_CHARS:
        raise ValueError(f"texte trop long ({len(text)} caractères, {MAX_CHARS} au plus par demande)")
    target = lang_code(target_lang)
    if not target:
        raise ValueError("précise la langue cible (« anglais », « EN-US », « DE »…)")
    payload: dict = {"text": [text], "target_lang": target}
    source = lang_code(source_lang, target=False)
    if source:
        payload["source_lang"] = source
    form = FORMALITY.get(_fold(formality), formality.strip())
    if form:
        payload["formality"] = form
    data = _post("/v2/translate", payload)
    item = (data.get("translations") or [{}])[0]
    return {
        "traduction": item.get("text", ""),
        "langue_source": item.get("detected_source_language", source),
        "langue_cible": target,
        "caracteres": len(text),
    }


def usage() -> dict:
    data = _post("/v2/usage", method="GET")
    used, limit = data.get("character_count", 0), data.get("character_limit", 0)
    key = connector.settings(NAME)["DEEPL_API_KEY"]
    return {
        "offre": "gratuite" if key.endswith(":fx") else "Pro (facturée au caractère)",
        "caracteres_utilises": used,
        "limite": limit,
        "reste": max(0, limit - used) if limit else None,
    }
