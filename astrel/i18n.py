"""Langue de l'interface : français (défaut) ou anglais (20/10, demande de l'utilisateur : « avoir
une traduction complète de l'application en anglais et avoir le choix de passer de anglais à
français »).

Le code continue d'écrire ses textes d'écran EN FRANÇAIS (journal, voix, WhatsApp, tests
inchangés). La traduction se fait à l'AFFICHAGE, par le catalogue `astrel/locales/en.json` :
- clé = le texte français tel que le code le compose, valeur = l'anglais ;
- `{nom}` dans une clé = une partie variable (f-string) : « Voix {name} revenue. » →
  « {name} voice is back. » ; la partie capturée est elle-même traduite si elle est connue ;
- texte inconnu : rendu tel quel (repli sur le français, jamais d'erreur).
Le HUD fait la même chose en JavaScript (`hud/src/i18n.js`, même catalogue, même règle) ;
la bulle et le mini-chat passent par `tr()`.

Réglage : `ui_language` de la page Réglages (`runtime/settings.json`, `ASTREL_UI_LANGUAGE`
prioritaire), lu à chaque appel de `lang()` (fichier relu s'il a changé) : à chaud, sans
relance. Ni la voix, ni la langue des réponses d'Astrel, ni son prompt n'en dépendent.
Inventaire de ce qui reste à traduire : `tools/i18n_inventaire.py`.

23/10 (plan P31, demande de l'utilisateur) : le produit s'appelle « Astrel » (nom CONFIGURABLE, réglage
`assistant_name`, `assistant_name()`). Dans un texte d'écran CONNU du catalogue (clé exacte ou modèle),
`tr()` remplace l'ancien nom par ce nom, dans les deux langues, et seulement dans les parties FIXES du
modèle : jamais dans une partie variable (commande, chemin, texte de Claude), ni un identifiant (nom du
paquet, variables, service, dossier, alias `hey_jarvis`). Depuis le 25/10 (plan P36, mot d'éveil
« Astrel »), l'ancien mot d'éveil devient « Hey Astrel ». Même règle dans le HUD (`hud/src/i18n.js`).
Depuis le 28/10 (plan P42), le code écrit « Astrel » : ce nom par défaut est remplacé de la même façon
par un nom choisi (« d'Astrel » → « de Nova »).
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

LANGS = ("fr", "en")
DEFAULT = "fr"
CATALOGUE = Path(__file__).resolve().parent / "locales" / "en.json"
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
LETTER = re.compile(r"[A-Za-zÀ-ÿ]")
# Après traduction : nombres et unités à l'anglaise (« 4,3 Go » → « 4.3 GB »).
UNITS = ((re.compile(r"(\d),(\d)"), r"\1.\2"), (re.compile(r"(\d) ?Go\b"), r"\1 GB"),
         (re.compile(r"(\d) ?Mo\b"), r"\1 MB"), (re.compile(r"(\d) ?Ko\b"), r"\1 KB"))
# Repli : un texte composé de morceaux connus (lignes, « a · b », « a ; b ») est traduit morceau par
# morceau (séparateur français → anglais). « a, b et c » (descriptions jointes du garde-fou) : seulement
# si TOUS les morceaux sont connus (`strict`), jamais une phrase à moitié traduite pour une virgule.
SEPARATORS = (("\n", "\n", False), (" · ", " · ", False), (" ; ", "; ", False), (" et ", " and ", True),
              (", ", ", ", True))

_lock = threading.Lock()
_cat: dict[str, str] | None = None
_patterns: list[tuple[str, str, re.Pattern, list[str], str, str]] = []
_lang_cache: tuple[tuple, str] = ((), DEFAULT)
_values_cache: tuple[tuple, dict] = ((), {})


# --------------------------------------------------------------------------- langue choisie

def _settings_file() -> Path:
    from . import config
    return Path(config.ASTREL_SETTINGS_FILE)


def _values() -> dict:
    """Valeurs choisies de `runtime/settings.json`, relues seulement si le fichier a changé."""
    global _values_cache
    path = _settings_file()
    try:
        st = path.stat()
    except OSError:
        return {}
    # inode + date + taille : deux écritures dans la même milliseconde (os.replace) se distinguent
    stamp = (st.st_ino, st.st_mtime_ns, st.st_size)
    if stamp == _values_cache[0]:
        return _values_cache[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        values = data.get("values") if isinstance(data, dict) else None
        values = values if isinstance(values, dict) else {}
    except (OSError, ValueError, AttributeError):
        values = {}
    _values_cache = (stamp, values)
    return values


def lang() -> str:
    """« fr » ou « en » : variable `ASTREL_UI_LANGUAGE`, sinon Réglages, sinon français."""
    env = os.environ.get("ASTREL_UI_LANGUAGE", "").strip().lower()
    if env in LANGS:
        return env
    value = str(_values().get("ui_language") or DEFAULT)
    return value if value in LANGS else DEFAULT


# --------------------------------------------------------------------------- nom affiché (P31)

NAME_MAX = 32
_TAG = re.compile(r"<[^<>]*>")
# 25/10 (plan P36) : l'ancien mot d'éveil (« Hey … », « OK … ») n'est plus protégé : il suit le nom
# (« Hey Astrel »). L'alias de transition s'écrit par son identifiant (`hey_jarvis`), jamais remplacé.
# 28/10 (plan P42, renommage) : les textes du code écrivent désormais « Astrel », le nom par défaut, au
# lieu de l'ancien ; il sert de repère comme l'ancien (nom choisi posé à sa place), élision comprise
# (« d'Astrel » → « de Nova »). L'ancien repère reste lu : textes du HUD et du cœur pas encore renommés.
# UNE seule passe pour les deux repères : deux substitutions de suite relisaient le nom posé par la
# première (« Mon Astrel » devenait « Mon Mon Astrel » dans un texte encore en ancien nom).
_NAME_RX = re.compile(r"(?<![\w/.-])Astrel(?![\w/-]|\.\w)")
_VOWEL = re.compile(r"[aeiouy\u00e0-\u00e6\u00e8-\u00ef\u00f2-\u00f6\u00f9-\u00fc]", re.I)


def clean_name(raw: object) -> str:
    """Nom sur une ligne, sans balise ni chevron ni caractère de contrôle, `NAME_MAX` caractères au
    plus ; vide = « Astrel » (« <b>Nova</b> » → « Nova »)."""
    from . import config
    text = _TAG.sub("", str(raw or ""))
    text = re.sub(r"[\x00-\x1f\x7f<>]", " ", text)
    return " ".join(text.split())[:NAME_MAX].strip() or config.ASSISTANT_NAME_DEFAULT


def assistant_name() -> str:
    """Nom affiché : `ASTREL_ASSISTANT_NAME`, sinon réglage `assistant_name` (relu s'il a changé : la
    bulle suit à chaud), sinon `config.ASSISTANT_NAME` (« Astrel »)."""
    from . import config
    if os.environ.get("ASTREL_ASSISTANT_NAME", "").strip():
        return clean_name(os.environ["ASTREL_ASSISTANT_NAME"])
    return clean_name(_values().get("assistant_name") or config.ASSISTANT_NAME)


def nom(text: str, name: str | None = None, language: str | None = None) -> str:
    """Ancien nom ou « Astrel » → nom affiché ; en français, élision devant une voyelle (« de Astrel » →
    « d'Astrel ») et inverse devant une consonne (« d'Astrel » → « de Nova »)."""
    if not text or not isinstance(text, str) or "Astrel" not in text:
        return text
    name = name or assistant_name()
    out = _NAME_RX.sub(lambda _m: name, text)
    if (language or lang()) == "fr":
        if _VOWEL.match(name):
            out = re.sub(rf"(?<!\w)([DdJj]|[Qq]u|[Ll]orsqu|[Pp]uisqu|[Jj]usqu)e\s(?={re.escape(name)}(?![\w-]))", r"\1'", out)
        elif name != "Astrel" and "Astrel" in text:
            # « d'Astrel » écrit dans le code, nom choisi qui commence par une consonne : « de Nova »
            out = re.sub(rf"(?<!\w)([DdJj]|[Qq]u|[Ll]orsqu|[Pp]uisqu|[Jj]usqu)['’](?={re.escape(name)}(?![\w-]))",
                         r"\1e ", out)
    return out


# --------------------------------------------------------------------------- catalogue

def _compile(key: str) -> tuple[re.Pattern, list[str], str]:
    names: list[str] = []
    out, pos = [], 0
    for m in PLACEHOLDER.finditer(key):
        out.append(re.escape(key[pos:m.start()]))
        out.append("(.*?)")
        names.append(m.group(1))
        pos = m.end()
    out.append(re.escape(key[pos:]))
    literal = PLACEHOLDER.sub("", key)
    return re.compile("".join(out), re.S), names, literal


def catalogue() -> dict[str, str]:
    """{français: anglais} (clés « _… » = commentaires, ignorées)."""
    global _cat, _patterns
    with _lock:
        if _cat is not None:
            return _cat
        try:
            raw = json.loads(CATALOGUE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        cat = {k: v for k, v in raw.items() if isinstance(v, str) and v and not k.startswith("_")}
        pats = []
        for k, v in cat.items():
            if not PLACEHOLDER.search(k):
                continue
            rx, names, literal = _compile(k)
            prefix = k[:PLACEHOLDER.search(k).start()]
            if not LETTER.search(prefix) and len(LETTER.findall(literal)) < 4:
                continue  # « {a}, {b} de {c} » prendrait n'importe quoi (« avant de » → « avant of »)
            pats.append((prefix, literal, rx, names, v, k))
        pats.sort(key=lambda p: -len(p[1]))  # le plus précis d'abord
        _cat, _patterns = cat, pats
        return cat


def reload() -> None:
    """Relit le catalogue (tests, ou après l'avoir modifié)."""
    global _cat
    with _lock:
        _cat = None
    catalogue()


def covered(text: str) -> bool:
    """Le modèle `text` (texte ou f-string de l'inventaire) a-t-il sa traduction anglaise ?"""
    return bool(catalogue().get(text))


def _render(text: str, depth: int, language: str, name: str) -> str | None:
    """Texte CONNU du catalogue rendu dans `language` (« fr » : le texte lui-même), le nom affiché posé
    dans ses parties fixes ; None si inconnu."""
    english = language == "en"
    cat = catalogue()
    hit = cat.get(text)
    if hit is not None:
        return nom(hit if english else text, name, language)
    stripped = text.strip()
    if stripped != text and stripped:
        inner = _render(stripped, depth, language, name)
        if inner is not None:
            start = text[:len(text) - len(text.lstrip())]
            end = text[len(text.rstrip()):]
            return start + inner + end
        return None
    for prefix, _literal, rx, names, en, fr in _patterns:
        if prefix and not text.startswith(prefix):
            continue
        m = rx.fullmatch(text)
        if not m:
            continue
        values = {}
        for key, value in zip(names, m.groups()):
            values[key] = (_render(value, depth + 1, language, name) or value) if depth < 2 and value else value
        template = nom(en if english else fr, name, language)  # le nom dans le modèle, jamais dans les valeurs
        return PLACEHOLDER.sub(lambda p: values.get(p.group(1), p.group(0)), template)
    if depth < 3:
        for sep, en_sep, strict in SEPARATORS:
            if sep in text:
                parts = text.split(sep)
                done = [_render(p, depth + 1, language, name) for p in parts]
                if (all if strict else any)(d is not None for d in done):
                    return (en_sep if english else sep).join(d if d is not None else p for d, p in zip(done, parts))
    return None


def _english(text: str, depth: int) -> str | None:
    return _render(text, depth, "en", assistant_name())


def tr(text: str, language: str | None = None) -> str:
    """Le texte d'écran `text` (écrit en français par le code) dans la langue de l'interface, avec le
    nom affiché (P31) s'il est connu du catalogue."""
    if not text or not isinstance(text, str):
        return text
    language = language or lang()
    if language != "en":
        name = assistant_name()
        if "Astrel" not in text and ("Astrel" not in text or name == "Astrel"):
            return text
        out = _render(text, 0, "fr", name)
        return text if out is None else out
    out = _render(text, 0, "en", assistant_name())
    if out is None:
        return text
    for rx, rep in UNITS:
        out = rx.sub(rep, out)
    return out
