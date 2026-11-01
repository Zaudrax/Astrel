"""Thème d'Astrel : Premium, clair ou sombre (21/10, plan P17 ; demande de l'utilisateur : « changer
le thème de l'app Astrel ainsi que sa bulle entièrement via les réglages, un thème premium simple
et sobre, vraiment comme Apple pourrait le faire »). 25/10 (plan P35, demande de l'utilisateur) : thème
Classique abandonné, Premium SEUL ; sa palette reste dans config/themes.json (non sélectionnable)
pour les icônes (`tools/logo.py`).

- Liste des thèmes, modes, accents et couleurs : `config/themes.json`, source UNIQUE (le HUD
  l'importe au build, `hud/src/theme.js` ; `tests/test_themes.py` la compare à `hud/styles.css`).
- Choix : réglages `theme_mode`, `theme_accent` de la page Réglages (`runtime/settings.json`),
  relus à chaud (`current()` ne relit le fichier que s'il a changé : un `stat` par appel). Une clé
  `theme` restée dans le fichier et `ASTREL_THEME` n'ont plus d'effet (P35).
- `palette()` : couleurs d'un thème dans un mode ; `state_colors()` : couleurs des états de la
  bulle ; `css_gtk()` : CSS GTK de la carte de confirmation et du mini-chat (ASCII : le
  fournisseur CSS de GTK reçoit des octets).
- Mode « auto » : suit le système. La bulle le résout par `Gio.Settings` (`color-scheme`), le
  HUD par `matchMedia`.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

THEMES_FILE = Path(__file__).resolve().parent.parent / "config" / "themes.json"
THEMES = ("premium",)  # thèmes sélectionnables (« classique » : palette gardée, plus choisie ; P35)
MODES = ("auto", "light", "dark")
ACCENTS = ("bleu", "graphite", "vert", "orange")
DEFAULT = {"theme": "premium", "mode": "auto", "accent": "bleu"}
KEYS = {"mode": "theme_mode", "accent": "theme_accent"}

_lock = threading.Lock()
_data: dict | None = None
_cache: tuple[tuple, dict] = ((), dict(DEFAULT))


def data() -> dict:
    """Contenu de config/themes.json (lu une fois)."""
    global _data
    with _lock:
        if _data is None:
            _data = json.loads(THEMES_FILE.read_text(encoding="utf-8"))
        return _data


def _settings_file() -> Path:
    from . import config
    return Path(config.ASTREL_SETTINGS_FILE)


def _clean(raw: dict) -> dict:
    out = dict(DEFAULT)  # thème : toujours Premium (P35)
    mode = str(raw.get("mode") or "").strip().lower()
    accent = str(raw.get("accent") or "").strip().lower()
    if mode in MODES:
        out["mode"] = mode
    if accent in ACCENTS:
        out["accent"] = accent
    return out


def current() -> dict:
    """Choix en cours {theme: "premium", mode, accent} d'après les Réglages. Le fichier n'est relu
    que s'il a changé (inode, date, taille), comme `i18n.lang()`."""
    global _cache
    path = _settings_file()
    try:
        st = path.stat()
        stamp = (st.st_ino, st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = ()
    key = stamp
    if key == _cache[0]:
        return dict(_cache[1])
    raw: dict[str, Any] = {}
    if stamp:
        try:
            values = json.loads(path.read_text(encoding="utf-8")).get("values") or {}
            raw = {name: values.get(k) for name, k in KEYS.items()} if isinstance(values, dict) else {}
        except (OSError, ValueError, AttributeError):
            raw = {}
    value = _clean(raw)
    _cache = (key, value)
    return dict(value)


def resolved_mode(choice: dict | None = None, system_dark: bool | None = None) -> str:
    """« dark » ou « light » : « auto » suit le système (inconnu : sombre, comme le bureau de
    l'utilisateur)."""
    choice = choice or current()
    mode = choice.get("mode", "auto")
    if mode in ("dark", "light"):
        return mode
    return "light" if system_dark is False else "dark"


def _hex(color: str) -> tuple[float, float, float]:
    c = color.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def palette(theme: str = "premium", mode: str = "dark", accent: str = "bleu") -> dict[str, str]:
    """Couleurs (hexadécimal « #rrggbb » ou triplet « r, g, b ») d'un thème dans un mode,
    accent compris (`accent`, `accent_ink`) ; Total et levée ajoutés (`total`, `levee`).
    « classique » reste lisible (icônes, `tools/logo.py`) ; thème inconnu : Premium."""
    d = data()
    t = d["themes"].get(theme) or d["themes"]["premium"]
    modes = t["palettes"]
    pal = dict(modes.get(mode) or modes.get("dark") or next(iter(modes.values())))
    accents = t.get("accents") or {}
    if accents:
        a = accents.get(accent) or accents[next(iter(accents))]
        m = "light" if mode == "light" else "dark"
        pal["accent"], pal["accent_ink"] = a[m], a[f"{m}_ink"]
    pal["total"] = d["semantics"]["total"]
    pal["levee"] = pal[d["semantics"]["levee"]]
    return pal


def state_colors(theme: str = "premium", mode: str = "dark", accent: str = "bleu") -> dict[str, tuple]:
    """Couleur principale et secondaire de chaque état de la bulle, en flottants 0..1 (cairo).
    Premium : une seule teinte par état (l'accent, l'ambre pour une confirmation, le gris coupé).
    Toujours Premium depuis P35 : `theme` n'est gardé que pour l'appelant (`bubble_premium.py`) ;
    l'ancienne branche Classique (paires de couleurs de `classique.states`) est retirée."""
    t = data()["themes"]["premium"]
    pal = palette("premium", mode, accent)
    return {s: (_hex(pal[ref]), _hex(pal[ref])) for s, ref in t["states"].items()}


def rgb(color: str) -> tuple[float, float, float]:
    """« #rrggbb » ou « r, g, b » → flottants 0..1."""
    if color.startswith("#"):
        return _hex(color)
    r, g, b = (int(x) for x in color.split(","))
    return (r / 255, g / 255, b / 255)


def _css_color(color: str, alpha: float | None = None) -> str:
    r, g, b = (round(v * 255) for v in rgb(color))
    return f"rgba({r}, {g}, {b}, {alpha})" if alpha is not None else f"rgb({r}, {g}, {b})"


def css_gtk(mode: str = "dark", accent: str = "bleu") -> bytes:
    """CSS GTK (ASCII) de la carte de confirmation et du mini-chat (thème Premium)."""
    p = palette("premium", mode, accent)
    dark = mode != "light"
    surface = _css_color(p["glass"], 0.97)
    line = _css_color(p["line"], 0.14 if dark else 0.1)
    fill = _css_color(p["hi"], 0.08 if dark else 0.05)
    fill_hover = _css_color(p["hi"], 0.14 if dark else 0.09)
    ink, ink2, accent_c = p["ink"], p["ink_2"], p["accent"]
    # « Oui » : teinte d'accent et texte clair en sombre (contraste), accent plein en clair
    yes_bg, yes_ink = (_css_color(accent_c, 0.24), ink) if dark else (accent_c, "#ffffff")
    css = f"""
/* Theme Premium (plan P17) : surfaces sobres, filet fin, pas de lueur. ASCII seulement. */
.astrel-card {{ background: {surface}; color: {ink}; border: 1px solid {_css_color(p["warn"], 0.7)};
               border-radius: 14px; padding: 12px; }}
.astrel-chat {{ background: {surface}; color: {ink}; border: 1px solid {line}; border-radius: 14px; }}
.astrel-card .head {{ color: {p["warn"]}; font-weight: bold; }}
.astrel-card .cmd {{ font-family: monospace; font-size: 10px; color: {ink2}; }}
.astrel-card .question {{ color: {ink}; }}
.astrel-card .error {{ color: {p["danger"]}; }}
.astrel-chat .user {{ color: {p["accent_ink"]}; }}
.astrel-chat .who {{ color: {ink2}; font-size: 10px; }}
.astrel-chat entry {{ background: {fill}; color: {ink}; border-radius: 8px; border: 1px solid {p["field_border"]};
                     box-shadow: none; background-image: none; padding: 4px 8px; }}
.astrel-chat entry:focus {{ border-color: {accent_c}; }}
.astrel-card button, .astrel-chat button {{ background-image: none; background-color: {fill}; color: {ink};
    border: 1px solid transparent; border-radius: 999px; box-shadow: none; text-shadow: none;
    -gtk-icon-shadow: none; padding: 3px 14px; min-height: 0; }}
.astrel-card button:hover, .astrel-chat button:hover {{ background-color: {fill_hover}; color: {ink}; }}
.astrel-card button.yes {{ background-color: {yes_bg}; color: {yes_ink}; }}
.astrel-card button.yes:hover {{ background-color: {yes_bg}; color: {yes_ink}; }}
.astrel-card button.no {{ color: {p["danger"]}; }}
.astrel-card button.always {{ color: {ink}; border-color: {line}; }}
.astrel-card button label, .astrel-chat button label {{ color: inherit; }}
.astrel-chat label {{ color: {ink}; }}
.astrel-chat scrolledwindow, .astrel-chat viewport {{ background-color: transparent; background-image: none; }}
.astrel-chat scrollbar slider {{ background-color: {_css_color(p["hi"], 0.25)}; border-radius: 999px; min-width: 4px; }}
.astrel-chat scrollbar, .astrel-chat scrollbar trough {{ background: transparent; border: none; }}
"""
    return css.encode("ascii")
