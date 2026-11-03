"""Bulle Premium (21/10, plan P17 ; dessin « Constellation » depuis le 23/10, maquette B choisie
par l'utilisateur) : dessin cairo SANS écran (`astrel/bubble_premium.py`, 8 états x clair/sombre
rendus en PNG, temps de rendu) et branchement dans `astrel/bubble.py` : thème relu à chaud dans
`runtime/settings.json`, fondu, CSS des cartes rechargé. 25/10 (plan P35, demande de l'utilisateur) :
dessin Classique retiré, Premium seul (repli Premium sombre si le thème est illisible). La partie
GTK tourne dans un serveur X virtuel (Xvfb) : rien ne s'affiche, la vraie bulle (service
astrel-bubble) n'est ni lancée ni relancée."""
from __future__ import annotations

import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest

cairo = pytest.importorskip("cairo")  # bulle : pycairo vient du système (absent de la CI)

from astrel import bubble_premium as bp
from astrel import theme

ROOT = Path(__file__).resolve().parent.parent
STATES = ("booting", "sleeping", "listening", "thinking", "working", "speaking", "asking", "off")
SIZE = 148


def render(st: str, mode: str = "dark", level: float = 0.0, t: float = 0.3, **kw) -> cairo.ImageSurface:
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE, SIZE)
    cr = cairo.Context(surf)
    bp.draw(cr, SIZE, SIZE, layers=[(st, 1.0)], t=t, level=level, lk=bp.look(mode, "bleu"), **kw)
    surf.flush()
    return surf


def px(surf: cairo.ImageSurface, x: float, y: float) -> tuple[int, int, int, int]:
    """(r, g, b, a) non prémultiplié du pixel (x, y)."""
    data, stride = surf.get_data(), surf.get_stride()
    i = int(y) * stride + int(x) * 4
    b, g, r, a = data[i], data[i + 1], data[i + 2], data[i + 3]
    if a == 0:
        return (0, 0, 0, 0)
    return (round(r * 255 / a), round(g * 255 / a), round(b * 255 / a), a)


def near(c, rgb, tol=40) -> bool:
    return all(abs(c[i] - round(rgb[i] * 255)) <= tol for i in range(3)) and c[3] > 120


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_huit_etats_en_png(mode, tmp_path):
    out = Path(os.environ.get("ASTREL_BULLE_PNG", tmp_path))
    out.mkdir(parents=True, exist_ok=True)
    seen = set()
    lk = bp.look(mode, "bleu")
    for st in STATES:
        surf = render(st, mode, level=0.6)
        surf.write_to_png(str(out / f"bulle-premium-{mode}-{st}.png"))
        seen.add(bytes(surf.get_data()))
        c = SIZE / 2
        # 23/10 : plus de disque de verre, l'ancien bord du disque et le coin sont transparents
        assert px(surf, c, c + SIZE / 2 * bp.DISC * 0.9)[3] == 0, (mode, st)
        assert px(surf, c - SIZE / 2 * bp.DISC * 0.9, c)[3] == 0, (mode, st)
        assert px(surf, 1, 1)[3] == 0, (mode, st)
        assert lk.fill  # couleur du verre gardée (pastille de pause, outil de maquette)
    assert len(seen) == len(STATES)  # chaque état a son dessin


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_motifs_par_etat(mode):
    """Constellation : étoile d'encre au repos, quatre étoiles d'écoute, cercle d'étoiles qui
    tourne, sept barres, étoile et anneau ambre, étoile grise barrée."""
    lk = bp.look(mode, "bleu")
    c = SIZE / 2
    r = SIZE / 2 * bp.DISC                                            # rayon du disque
    accent, warn, gray = lk.states["sleeping"], lk.states["asking"], lk.states["off"]
    assert warn == lk.warn and gray == lk.ink_2 and lk.states["listening"] == accent
    assert lk.dark == (mode == "dark")
    ink = theme.rgb(theme.palette("premium", mode, "bleu")["ink"])
    assert lk.ink == ink

    # repos : étoile blanche en sombre, noire en clair, au centre ; branches fines (pas un disque)
    rest = render("sleeping", mode)
    assert near(px(rest, c, c), ink)
    assert near(px(rest, c, c - r * 0.20), ink)                       # branche
    assert not near(px(rest, c + r * 0.25, c - r * 0.25), ink)        # creux entre deux branches

    # écoute : étoile d'encre au centre, petites étoiles d'accent allumées une à une par le niveau
    spot0 = (c - 0.48 * r, c - 0.40 * r)
    spot3 = (c - 0.42 * r, c + 0.40 * r)
    quiet, mid, loud = (render("listening", mode, level=v) for v in (0.0, 0.3, 1.0))
    assert near(px(loud, c, c), ink)
    assert not near(px(quiet, *spot0), accent)                        # éteintes en silence
    assert near(px(mid, *spot0), accent) and not near(px(mid, *spot3), accent)
    assert near(px(loud, *spot0), accent) and near(px(loud, *spot3), accent)

    # réflexion : la tête du cercle d'étoiles (rayon 0,52 r) tourne, un tour en 3 s
    # (sans disque depuis le 23/10, la traîne est de l'accent PLUS TRANSPARENT : on compose sur du noir)
    head_a, head_b = (c + 0.52 * r, c), (c, c + 0.52 * r)
    t0, t1 = render("thinking", mode, t=0.0), render("thinking", mode, t=0.75)

    def on_black(p):
        return (round(p[0] * p[3] / 255), round(p[1] * p[3] / 255), round(p[2] * p[3] / 255), 255)

    assert near(on_black(px(t0, *head_a)), accent) and not near(on_black(px(t0, *head_b)), accent)
    assert near(on_black(px(t1, *head_b)), accent) and not near(on_black(px(t1, *head_a)), accent)
    assert near(px(render("working", mode, t=0.0), *head_a), accent)

    # parole : sept barres sur la ligne médiane ; la barre centrale grandit avec le niveau
    talk = render("speaking", mode, level=1.0, t=0.2)
    row = [px(talk, x, c) for x in range(SIZE)]
    blue = [cc[3] > 120 and cc[2] > cc[0] + 80 for cc in row]
    runs = sum(1 for i in range(1, SIZE) if blue[i] and not blue[i - 1])
    assert runs == 7, runs
    low = render("speaking", mode, level=0.1, t=0.2)
    tall = px(talk, c, c - r * 0.30)
    assert tall[2] > tall[0] + 80 and not px(low, c, c - r * 0.30)[2] > px(low, c, c - r * 0.30)[0] + 80

    # confirmation : étoile ambre au centre et anneau ambre (rayon 0,66 r) au plus fort de la pulsation
    ask = render("asking", mode, t=0.5)
    assert near(px(ask, c, c), warn) and near(px(ask, c + r * 0.66, c), warn)

    # micro coupé : étoile grise, barre grise en diagonale, plus d'encre ni d'accent
    off = render("off", mode)
    assert near(px(off, c + r * 0.30, c - r * 0.30), gray)
    assert not near(px(off, c, c - r * 0.20), ink, tol=20)            # étoile grise, pas d'encre


def test_rendu_rapide():
    """Une image de 148 px en moins de 5 ms (maquette : 0,49 ms ; la bulle peint à 60 i/s)."""
    lk = bp.look("dark", "bleu")
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE, SIZE)
    worst = 0.0
    for st in STATES:
        times = []
        for i in range(30):
            cr = cairo.Context(surf)
            t0 = time.perf_counter()
            bp.draw(cr, SIZE, SIZE, layers=[(st, 1.0)], t=i * 0.016, level=0.6, lk=lk)
            surf.flush()
            times.append(time.perf_counter() - t0)
        worst = max(worst, statistics.median(times))
    assert worst < 0.005, f"{worst * 1000:.2f} ms"


def test_fumee_sans_gtk():
    """Le dessin Premium n'importe pas Gtk : rendu de tous les états dans un process neuf."""
    code = ("import sys, cairo\nfrom astrel import bubble_premium as bp\n"
            "for m in ('dark', 'light'):\n"
            "    for st in %r:\n"
            "        s = cairo.ImageSurface(cairo.FORMAT_ARGB32, 148, 148)\n"
            "        bp.draw(cairo.Context(s), 148, 148, layers=[(st, 0.6), ('sleeping', 0.4)], t=1.3, level=0.5,\n"
            "                lk=bp.look(m))\n"
            "print('gi' in sys.modules)\n") % (STATES,)
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env={**os.environ, "PYTHONPATH": str(ROOT)},
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "False"


@pytest.mark.parametrize("kw", [{"total": True}, {"paused": True}, {"usage_label": "dictée"},
                                {"usage": "silencieux"}, {"compact": True}, {"mission": True}, {"lift": True}])
def test_aucun_mode_ne_peut_etre_dessine(kw):
    """23/10 (demande : bulle SIMPLE) : `draw` ne reçoit plus ni Total, ni pause, ni mode d'usage,
    ni mission, ni levée ; un ancien appelant échoue au lieu de dessiner un texte en douce."""
    with pytest.raises(TypeError):
        render("sleeping", "light", **kw)


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_demande_sans_couleur_de_mode(mode):
    """L'état « demande » (une question attend) reste, en ambre `warn` : ni l'orange de Total, ni le
    rouge de la levée, ni le violet de la mission."""
    lk = bp.look(mode, "bleu")
    pal = theme.palette("premium", mode, "bleu")
    ask = lk.states["asking"]
    for name in ("total", "levee", "violet"):
        other = theme.rgb(pal[name])
        assert max(abs(ask[i] - other[i]) for i in range(3)) > 0.15, (mode, name)
    assert ask == lk.warn
    compact = cairo.ImageSurface(cairo.FORMAT_ARGB32, 52, 52)
    bp.draw(cairo.Context(compact), 52, 52, layers=[("speaking", 1.0)], t=0.1, level=0.5, lk=lk)  # compacte


def test_fondu_entre_deux_etats():
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE, SIZE)
    bp.draw(cairo.Context(surf), SIZE, SIZE, layers=[("sleeping", 0.5), ("asking", 0.5)], t=0.5, level=0.0,
            lk=bp.look("dark"))
    a = px(surf, SIZE / 2 + SIZE / 2 * bp.DISC * 0.66, SIZE / 2)
    assert 0 < a[3] and not near(a, bp.look("dark").warn, tol=10)    # anneau ambre à demi-opacité


def _on(bg: tuple[float, float, float], st: str, mode: str) -> cairo.ImageSurface:
    """La bulle posée sur un fond d'écran uni (ce que voit l'utilisateur sur son bureau)."""
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, SIZE, SIZE)
    cr = cairo.Context(surf)
    cr.set_source_rgb(*bg)
    cr.paint()
    bp.draw(cr, SIZE, SIZE, layers=[(st, 1.0)], t=0.3, level=0.6, lk=bp.look(mode, "bleu"))
    surf.flush()
    return surf


def _lum(surf: cairo.ImageSurface, x: int, y: int) -> float:
    data, stride = surf.get_data(), surf.get_stride()
    i = y * stride + x * 4
    return 0.2126 * data[i + 2] + 0.7152 * data[i + 1] + 0.0722 * data[i]


@pytest.mark.parametrize("mode, bg", [("dark", (1.0, 1.0, 1.0)), ("light", (0.0, 0.0, 0.0))])
def test_sans_fond_mais_lisible(mode, bg):
    """23/10 : sans disque, l'encre (blanche en sombre, noire en clair) peut avoir la couleur du fond
    d'écran. Un contour fin de la teinte contraire borde l'étoile ; le reste du carré laisse voir
    le fond tel quel (ni disque, ni tache sous les lueurs)."""
    c, r = SIZE // 2, SIZE / 2 * bp.DISC
    surf = _on(bg, "sleeping", mode)
    back = round(255 * bg[0])
    # loin de l'étoile (ancien disque) : le fond, au niveau près
    for x, y in ((c + int(r * 0.7), c), (c, c + int(r * 0.7)), (c - int(r * 0.55), c - int(r * 0.55))):
        assert abs(_lum(surf, x, y) - back) <= 2, (x, y, _lum(surf, x, y))
    # autour de l'étoile : des pixels nettement contrastés avec le fond (le contour)
    ring = [_lum(surf, x, y) for x in range(SIZE) for y in range(SIZE)
            if r * 0.05 < math.hypot(x - c, y - c) < r * 0.5]
    contrast = sum(1 for v in ring if abs(v - back) > 60)
    assert contrast > 80, contrast
    # en sombre, la lueur des étoiles d'écoute ne devient pas une tache sombre sur fond blanc
    if mode == "dark":
        listen = _on(bg, "listening", "dark")
        x, y = c - int(0.48 * r), c - int(0.40 * r) - int(r * 0.16 * 1.6)   # dans la lueur, hors étoile
        assert _lum(listen, x, y) > 200, _lum(listen, x, y)


CODE = r"""
import json, sys, tempfile
from pathlib import Path
from astrel import config
tmp = Path(tempfile.mkdtemp(prefix="bulle-theme-"))
config.ASTREL_SETTINGS_FILE = tmp / "settings.json"
config.HUD_TOKEN_FILE = tmp / "hud-token"          # jamais le vrai jeton, jamais le vrai HUD
config.HUD_TOKEN_FILE.write_text("x" * 40)
config.HUD_PORT = 1                               # injoignable : le flux retombe sur state.json
import cairo
from astrel import bubble as b

class Area:
    def get_allocated_width(self): return b.SIZE
    def get_allocated_height(self): return b.SIZE

def frame(bub):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, b.SIZE, b.SIZE)
    bub._on_draw(Area(), cairo.Context(surf)); surf.flush()
    return bytes(surf.get_data())

# mode sombre explicite : « auto » suivrait le réglage GNOME de la machine qui lance le test
config.ASTREL_SETTINGS_FILE.write_text(json.dumps({"values": {"theme_mode": "dark"}}))
bub = b.Bubble()
css = lambda: bub._provider.to_string().replace(" ", "")   # GTK réécrit les couleurs en rgb(r,g,b)
r = {"debut": bub._theme_key, "css_sombre": "rgb(255,214,10)" in css() and "rgb(255,181,71)" not in css()}
sombre = frame(bub)
# P35 : une ancienne clé `theme: classique` ne change rien
config.ASTREL_SETTINGS_FILE.write_text(json.dumps({"values": {"theme": "classique", "theme_mode": "dark"}}))
for _ in range(60): bub._tick()
r["classique_ignore"] = bub._theme_key
config.ASTREL_SETTINGS_FILE.write_text(json.dumps({"values": {"theme_mode": "light", "theme_accent": "vert"}}))
for _ in range(60): bub._tick()                   # ~1 s : relu à la 60e image
r["apres"] = bub._theme_key
r["fondu"] = bub._theme_mix
r["css_clair"] = "rgb(31,122,53)" in css() and "rgb(255,181,71)" not in css()
milieu = frame(bub)
for _ in range(30): bub._tick()
r["fondu_fini"] = bub._theme_mix
clair = frame(bub)
config.ASTREL_SETTINGS_FILE.write_text(json.dumps({"values": {"theme_mode": "dark"}}))
for _ in range(120): bub._tick()
r["retour"] = bub._theme_key
r["css_retour"] = "rgb(255,214,10)" in css()
r["dessins"] = len({sombre, milieu, clair})
print(json.dumps(r))
"""


def test_bulle_relit_le_theme_a_chaud():
    if not shutil.which("xvfb-run"):
        pytest.skip("xvfb-run absent")
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "GDK_SCALE", "ASTREL_THEME")}
    env["XDG_CURRENT_DESKTOP"] = ""
    env["PYTHONPATH"] = str(ROOT)
    out = subprocess.run(["xvfb-run", "-a", sys.executable, "-c", CODE], cwd=str(ROOT), env=env,
                         capture_output=True, text=True, timeout=90)
    assert out.returncode == 0, out.stderr[-2000:]
    r = json.loads(out.stdout.strip().splitlines()[-1])
    # P35 : Premium dès le départ, CSS Premium (ambre #ffd60a, plus l'ambre Classique #ffb547)
    assert r["debut"] == ["premium", "dark", "bleu"] and r["css_sombre"]
    assert r["classique_ignore"] == ["premium", "dark", "bleu"]
    assert r["apres"] == ["premium", "light", "vert"] and r["fondu"] < 1.0 and r["css_clair"]
    assert r["fondu_fini"] == 1.0
    assert r["retour"] == ["premium", "dark", "bleu"] and r["css_retour"]
    assert r["dessins"] == 3   # sombre, fondu, clair : trois images différentes


CODE_PANNE = r"""
import json, logging, tempfile
from pathlib import Path
from astrel import config
tmp = Path(tempfile.mkdtemp(prefix="bulle-theme-panne-"))
config.ASTREL_SETTINGS_FILE = tmp / "settings.json"
config.HUD_TOKEN_FILE = tmp / "hud-token"          # jamais le vrai jeton, jamais le vrai HUD
config.HUD_TOKEN_FILE.write_text("x" * 40)
config.HUD_PORT = 1
config.ASTREL_SETTINGS_FILE.write_text(json.dumps({"values": {"theme": "premium"}}))
from astrel import theme
bad = tmp / "themes.json"; bad.write_text("{abîmé")
theme.THEMES_FILE, theme._data = bad, None        # config/themes.json illisible
seen = []
class H(logging.Handler):
    def emit(self, rec): seen.append(rec.getMessage())
logging.getLogger("astrel.bubble").addHandler(H())
import cairo
from astrel import bubble as b
from astrel import bubble_premium as bp
bub = b.Bubble()
for _ in range(300): bub._tick()                   # ~5 s : 5 nouvelles lectures du thème
class Area:
    def get_allocated_width(self): return b.SIZE
    def get_allocated_height(self): return b.SIZE
surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, b.SIZE, b.SIZE)
bub._on_draw(Area(), cairo.Context(surf)); surf.flush()
r = {"logs": list(seen), "key": bub._theme_key, "visible": sum(1 for a in bytes(surf.get_data())[3::4] if a > 100)}
repli = bub._theme["look"]
theme.THEMES_FILE = config.ROOT / "config" / "themes.json"   # réparé : Premium pris, sans nouveau message
r["repli_egal_premium_sombre"] = repli == bp.look("dark", "bleu")
for _ in range(60): bub._tick()
r["apres"], r["logs_apres"] = bub._theme_key, len(seen)
print(json.dumps(r))
"""


def test_themes_json_illisible_journalise_une_fois():
    """Relecture P17 : `_check_theme` avalait l'erreur sans rien dire, toutes les secondes."""
    if not shutil.which("xvfb-run"):
        pytest.skip("xvfb-run absent")
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "GDK_SCALE", "ASTREL_THEME")}
    env["XDG_CURRENT_DESKTOP"] = ""
    env["PYTHONPATH"] = str(ROOT)
    out = subprocess.run(["xvfb-run", "-a", sys.executable, "-c", CODE_PANNE], cwd=str(ROOT), env=env,
                         capture_output=True, text=True, timeout=90)
    assert out.returncode == 0, out.stderr[-2000:]
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert len(r["logs"]) == 1 and "thème illisible" in r["logs"][0] and "JSONDecodeError" in r["logs"][0], r
    assert r["key"] is None                       # la bulle garde son apparence : le repli Premium (P35)
    assert r["visible"] > 50, r["visible"]        # repli dessiné : la bulle ne disparaît pas
    assert r["repli_egal_premium_sombre"]         # repli = Premium sombre bleu de themes.json, au chiffre près
    assert r["apres"][0] == "premium" and r["logs_apres"] == 1


def test_dessin_premium_seul():
    """25/10 (plan P35, demande de l'utilisateur) : le dessin Classique (halo, sphère, plasma, motifs) et sa
    palette `THEME` sont retirés ; `_paint` ne dessine plus que le Premium."""
    src = (ROOT / "astrel" / "bubble.py").read_text(encoding="utf-8")
    for gone in ("def _draw_classic(", "def _draw_pattern(", "THEME = {", "def _colors(", "_from_colors",
                 "# Halo extérieur", '"classique"'):
        assert gone not in src, gone
    paint = src[src.index("    def _paint(self, widget, cr, th: dict) -> bool:"):src.index("    def _draw_premium(")]
    assert "return self._draw_premium(widget, cr, " in paint
    assert "show_text" not in src and "_draw_pause_mark" not in src
    assert "theme_mod.css_gtk(mode, " in src and "self._ticks % 60 == 0" in src


CODE_TOTAL = r"""
import json, sys, tempfile, time
from pathlib import Path
from astrel import config
tmp = Path(tempfile.mkdtemp(prefix="bulle-total-"))
config.ASTREL_SETTINGS_FILE = tmp / "settings.json"
config.HUD_TOKEN_FILE = tmp / "hud-token"          # jamais le vrai jeton, jamais le vrai HUD
config.HUD_TOKEN_FILE.write_text("x" * 40)
config.HUD_PORT = 1
import cairo
from astrel import bubble as b

class Area:
    def get_allocated_width(self): return b.SIZE
    def get_allocated_height(self): return b.SIZE

bub = b.Bubble()
time.monotonic = lambda: bub._t0                  # t = 0 : images comparables
out = Path(sys.argv[1])

def frame(total=False, usage="conversation", paused=False, name=""):
    bub.feed.chat.total, bub.feed.chat.usage, bub.feed.paused = total, usage, paused
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, b.SIZE, b.SIZE)
    bub._on_draw(Area(), cairo.Context(surf)); surf.flush()
    if name:
        surf.write_to_png(str(out / name))
    return bytes(surf.get_data())

r = {}
for th, values in (("sombre", {"theme_mode": "dark"}), ("clair", {"theme_mode": "light"})):
    config.ASTREL_SETTINGS_FILE.write_text(json.dumps({"values": values}))
    for _ in range(120): bub._tick()             # thème relu (60 images) puis fondu fini
    base = frame(name=f"bulle-{th}-conversation.png")
    r[th] = {"theme": bub._theme_key, "changent": [
        nom for nom, kw in (("total", {"total": True}), ("silencieux", {"usage": "silencieux"}),
                            ("dictee", {"usage": "dictee"}), ("pause", {"paused": True}),
                            ("tout", {"total": True, "usage": "dictee", "paused": True}))
        if frame(name=f"bulle-{th}-{nom}.png", **kw) != base]}
print(json.dumps(r))
"""


def test_bulle_sans_aucun_mode(tmp_path):
    """23/10 (demande de l'utilisateur : bulle SIMPLE) : ni anneau orange, ni « TOTAL », ni mode d'usage
    (« silencieux », « dictée »), ni pastille de pause, en sombre comme en clair (le HUD les montre) :
    l'image ne change pas d'un octet. Vraie `Bubble` sous Xvfb."""
    if not shutil.which("xvfb-run"):
        pytest.skip("xvfb-run absent")
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "GDK_SCALE", "ASTREL_THEME")}
    env["XDG_CURRENT_DESKTOP"] = ""
    env["PYTHONPATH"] = str(ROOT)
    png = Path(os.environ.get("ASTREL_BULLE_PNG", tmp_path))
    png.mkdir(parents=True, exist_ok=True)
    out = subprocess.run(["xvfb-run", "-a", sys.executable, "-c", CODE_TOTAL, str(png)], cwd=str(ROOT), env=env,
                         capture_output=True, text=True, timeout=90)
    assert out.returncode == 0, out.stderr[-2000:]
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["sombre"]["theme"][:2] == ["premium", "dark"] and r["clair"]["theme"][:2] == ["premium", "light"]
    for th in ("sombre", "clair"):
        assert r[th]["changent"] == [], (th, r[th])


def test_bulle_sans_mode_dans_le_code():
    """Le dessin de la bulle ne lit ni Total, ni le mode d'usage, ni la pause, ni la mission, ni la levée."""
    src = (ROOT / "astrel" / "bubble.py").read_text(encoding="utf-8")
    premium = src[src.index("    def _draw_premium(self, widget, cr, look) -> bool:"):src.index("\ndef main(")]
    for part in (premium,):
        part = "\n".join(line.split("#", 1)[0] for line in part.splitlines())   # le code, sans commentaires
        for mot in ("chat.total", "chat.usage", "feed.paused", "mission", "lift", "levee", "show_text"):
            assert mot not in part, mot
    bp_src = (ROOT / "astrel" / "bubble_premium.py").read_text(encoding="utf-8")
    draw = bp_src[bp_src.index("def draw("):]
    assert "_label(" not in draw and "TOTAL_RGB" not in draw and "_pause_mark" not in bp_src
