#!/usr/bin/env python3
"""Maquettes de deux nouveaux dessins de la bulle flottante (23/10/2026, demande de l'utilisateur :
la bulle actuelle, disque de verre + anneau qui tourne, est peu belle ; voir deux propositions
« pro » avant de choisir). IMAGES SEULEMENT : la vraie bulle (`astrel/bubble.py`,
`astrel/bubble_premium.py`) n'est ni modifiée ni relancée.

  A) « Orbe de verre » : bille de verre en dégradés radiaux (ombre, reflet, bord clair, teinte
     froide discrète), cœur lumineux ; écoute = ondes concentriques dont l'écart suit le niveau ;
     réflexion = deux gouttes qui orbitent et se rejoignent ; parole = cœur qui pulse + ondes
     sortantes.
  B) « Constellation » : étoile à quatre branches au repos ; écoute = quatre petites étoiles qui
     s'allument selon le niveau ; réflexion = cercle d'étoiles qui tourne ; parole = 7 barres
     d'onde arrondies.

Dans les deux : confirmation (ambre qui pulse), micro coupé (gris barré), agents au travail
(pastille avec le nombre), anneau et mot « TOTAL » orange, mode silencieux / dictée sous le
disque, clair et sombre. Cairo seul (comme `bubble_premium.py`), Pillow pour le GIF.

    .venv/bin/python tools/maquette_bulle.py            # tout dans runtime/maquettes/bulle/
    .venv/bin/python tools/maquette_bulle.py --mesure   # + temps de rendu d'une image
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import cairo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from astrel import bubble_premium as bp  # noqa: E402  (lecture seule : Look, couleurs, dessin actuel)
from astrel import theme  # noqa: E402

TAU = math.tau
SIZE = 148
OUT = ROOT / "runtime" / "maquettes" / "bulle"
Color = tuple[float, float, float]
WHITE: Color = (1.0, 1.0, 1.0)


def mix(a: Color, b: Color, k: float) -> Color:
    return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k)


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def smooth(x: float) -> float:
    x = clamp(x)
    return x * x * (3 - 2 * x)


@dataclass
class Scene:
    state: str = "sleeping"
    level: float = 0.0
    t: float = 0.3
    total: bool = False
    usage: str = ""        # "", "silencieux", "dictee"
    agents: int = 0


@dataclass
class Pal:
    lk: bp.Look
    ink: Color
    dark: bool
    ice: Color             # teinte froide de la maquette (orbe), seule couleur hors jetons


def pal(mode: str) -> Pal:
    p = theme.palette("premium", mode, "bleu")
    dark = mode == "dark"
    return Pal(lk=bp.look(mode, "bleu"), ink=theme.rgb(p["ink"]), dark=dark,
               ice=(0.56, 0.78, 1.0) if dark else (0.40, 0.58, 0.92))


def state_color(p: Pal, st: str) -> Color:
    return p.lk.states.get(st) or p.lk.states["sleeping"]


# ---------------------------------------------------------------------------------- outils cairo

def _circle(cr, x, y, r):
    cr.new_path()
    cr.arc(x, y, r, 0, TAU)


def _glow(cr, x, y, rad, color: Color, alpha: float, white: float) -> None:
    """Tache lumineuse : cœur éclairci vers le blanc, bord qui s'efface (pas de flou réel)."""
    if rad <= 0.2 or alpha <= 0.0:
        return
    g = cairo.RadialGradient(x, y, 0, x, y, rad)
    g.add_color_stop_rgba(0.0, *mix(color, WHITE, white), alpha)
    g.add_color_stop_rgba(0.30, *mix(color, WHITE, white * 0.4), alpha * 0.85)
    g.add_color_stop_rgba(0.62, *color, alpha * 0.35)
    g.add_color_stop_rgba(1.0, *color, 0.0)
    cr.set_source(g)
    _circle(cr, x, y, rad)
    cr.fill()


def _stroke_circle(cr, x, y, r, color: Color, alpha: float, width: float) -> None:
    if alpha <= 0.0 or r <= 0.0:
        return
    cr.set_line_width(width)
    cr.set_source_rgba(*color, clamp(alpha))
    _circle(cr, x, y, r)
    cr.stroke()


def _slash(cr, cx, cy, d, color: Color, width: float, alpha: float = 1.0) -> None:
    cr.new_path()
    cr.set_line_width(width)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_source_rgba(*color, alpha)
    cr.move_to(cx - d, cy + d)
    cr.line_to(cx + d, cy - d)
    cr.stroke()


def _text(cr, s: str, x: float, y: float, size: float, color: Color, *, bold: bool = False,
          align: str = "center", alpha: float = 1.0) -> None:
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL,
                        cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size)
    ext = cr.text_extents(s)
    if align == "center":
        x0 = x - ext.width / 2 - ext.x_bearing
    elif align == "right":
        x0 = x - ext.width - ext.x_bearing
    else:
        x0 = x
    cr.set_source_rgba(*color, alpha)
    cr.move_to(x0, y)
    cr.show_text(s)
    cr.new_path()


# ------------------------------------------------------------------------- A) orbe de verre

def orb_body(cr, cx, cy, r, p: Pal, cool: float) -> None:
    lk = p.lk
    oy = cy + r * 0.10
    sh = cairo.RadialGradient(cx, oy, r * 0.78, cx, oy, r * 1.26)
    sh.add_color_stop_rgba(0.0, 0, 0, 0, lk.shade_alpha * (1.1 if p.dark else 1.6))
    sh.add_color_stop_rgba(1.0, 0, 0, 0, 0.0)
    cr.set_source(sh)
    _circle(cr, cx, oy, r * 1.26)
    cr.fill()
    # corps éclairé d'en haut à gauche
    if p.dark:
        stops = ((0.0, (0.27, 0.28, 0.32)), (0.55, (0.16, 0.165, 0.185)), (1.0, (0.10, 0.105, 0.12)))
        a = 0.95
    else:
        stops = ((0.0, (1.0, 1.0, 1.0)), (0.55, (0.94, 0.95, 0.965)), (1.0, (0.84, 0.86, 0.90)))
        a = 0.98
    body = cairo.RadialGradient(cx - r * 0.28, cy - r * 0.38, 0, cx, cy, r)
    for o, c in stops:
        body.add_color_stop_rgba(o, *c, a)
    cr.set_source(body)
    _circle(cr, cx, cy, r)
    cr.fill()
    cr.save()
    _circle(cr, cx, cy, r)
    cr.clip()
    # lumière froide qui traverse le verre, en bas
    if cool > 0:
        ty = cy + r * 0.78
        tint = cairo.RadialGradient(cx, ty, 0, cx, ty, r * 0.85)
        tint.add_color_stop_rgba(0.0, *p.ice, (0.20 if p.dark else 0.16) * cool)
        tint.add_color_stop_rgba(1.0, *p.ice, 0.0)
        cr.set_source(tint)
        cr.paint()
    # bord clair (effet de Fresnel)
    rim = cairo.RadialGradient(cx, cy, r * 0.74, cx, cy, r)
    edge = WHITE if p.dark else mix(p.ice, (0.55, 0.60, 0.70), 0.5)
    rim.add_color_stop_rgba(0.0, *edge, 0.0)
    rim.add_color_stop_rgba(1.0, *edge, 0.16 if p.dark else 0.30)
    cr.set_source(rim)
    cr.paint()
    cr.restore()


def orb_gloss(cr, cx, cy, r, p: Pal, cool: float) -> None:
    cr.save()
    _circle(cr, cx, cy, r)
    cr.clip()
    cr.translate(cx - r * 0.18, cy - r * 0.56)
    cr.scale(1.0, 0.46)
    g = cairo.RadialGradient(0, 0, 0, 0, 0, r * 0.64)
    g.add_color_stop_rgba(0.0, *WHITE, 0.30 if p.dark else 0.95)
    g.add_color_stop_rgba(0.5, *WHITE, 0.10 if p.dark else 0.45)
    g.add_color_stop_rgba(1.0, *WHITE, 0.0)
    cr.set_source(g)
    _circle(cr, 0, 0, r * 0.64)
    cr.fill()
    cr.restore()
    # filet du contour + reflet froid en bas (caustique)
    _stroke_circle(cr, cx, cy, r - 0.5, p.lk.line, 0.20 if p.dark else 0.12, 1.0)
    if cool > 0:
        cr.new_path()
        cr.set_line_width(max(1.0, r * 0.022))
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_source_rgba(*p.ice, (0.26 if p.dark else 0.22) * cool)
        cr.arc(cx, cy, r - r * 0.05, TAU * 0.10, TAU * 0.40)
        cr.stroke()


def orb_motif(cr, st: str, cx, cy, r, sc: Scene, p: Pal) -> None:
    """Ce qui vit DANS la bille, découpé au disque."""
    t, lvl = sc.t, sc.level
    color = state_color(p, st)
    white = 0.70 if p.dark else 0.30
    lw = max(1.0, r * 0.022)
    cr.save()
    _circle(cr, cx, cy, r)
    cr.clip()
    if st == "off":
        _glow(cr, cx, cy, r * 0.26, color, 0.35, 0.0)
        _slash(cr, cx, cy, r * 0.36, color, max(2.0, r * 0.05), 0.95)
    elif st == "listening":
        core = r * (0.30 + 0.08 * lvl)
        _glow(cr, cx, cy, core * 1.15, color, 0.80 + 0.20 * lvl, white)
        gap = r * (0.08 + 0.13 * lvl)
        for i in range(3):
            rr = core * 0.80 + gap * (i + 1) + r * 0.012 * math.sin(t * 7.0 + i * 1.9)
            _stroke_circle(cr, cx, cy, rr, mix(color, p.ice, 0.35), (0.60, 0.38, 0.20)[i] * (0.45 + 0.55 * lvl), lw)
    elif st in ("thinking", "working", "booting"):
        speed = 2.4 if st == "thinking" else 3.2
        a = t * TAU / speed
        sep = 0.5 + 0.5 * math.cos(t * TAU / 1.9)          # 1 = écartées, 0 = fondues
        d = r * (0.03 + 0.25 * sep)
        rd = r * 0.19
        c2 = mix(color, p.ice, 0.55) if st != "booting" else color
        x1, y1 = cx + d * math.cos(a), cy + d * math.sin(a)
        x2, y2 = cx - d * math.cos(a), cy - d * math.sin(a)
        _glow(cr, cx, cy, r * 0.55, color, 0.16, 0.0)
        # pont entre les gouttes (métaballe approchée) : capsule d'autant plus épaisse qu'elles sont proches
        close = smooth((2.4 * rd - 2 * d) / (2.4 * rd - 0.6 * rd))
        if close > 0:
            lin = cairo.LinearGradient(x1, y1, x2, y2)
            lin.add_color_stop_rgba(0.0, *color, 0.85 * close)
            lin.add_color_stop_rgba(1.0, *c2, 0.85 * close)
            cr.new_path()
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_width(rd * (0.55 + 0.5 * close))
            cr.set_source(lin)
            cr.move_to(x1, y1)
            cr.line_to(x2, y2)
            cr.stroke()
        for (x, y, c) in ((x1, y1, color), (x2, y2, c2)):
            g = cairo.RadialGradient(x - rd * 0.25, y - rd * 0.25, 0, x, y, rd * 1.25)
            g.add_color_stop_rgba(0.0, *mix(c, WHITE, white), 1.0)
            g.add_color_stop_rgba(0.45, *c, 0.95)
            g.add_color_stop_rgba(0.78, *c, 0.55)
            g.add_color_stop_rgba(1.0, *c, 0.0)
            cr.set_source(g)
            _circle(cr, x, y, rd * 1.25)
            cr.fill()
    elif st == "speaking":
        beat = 0.5 + 0.5 * math.sin(t * TAU * 2.2)
        core = r * (0.24 + 0.16 * lvl * (0.55 + 0.45 * beat))
        for i in range(3):
            ph = (t / 0.9 + i / 3.0) % 1.0
            rr = core + ph * (r - core)
            _stroke_circle(cr, cx, cy, rr, mix(color, p.ice, 0.3), (1 - ph) ** 1.6 * (0.25 + 0.65 * lvl), lw * (1.8 - ph))
        _glow(cr, cx, cy, core * 1.35, color, 0.95, white)
    elif st == "asking":
        pulse = 0.5 + 0.5 * math.sin(t * TAU / 2.0)
        _glow(cr, cx, cy, r * (0.34 + 0.06 * pulse), color, 0.70 + 0.30 * pulse, white * 0.6)
        _stroke_circle(cr, cx, cy, r * 0.90, color, 0.35 + 0.55 * pulse, max(1.5, r * 0.04))
    else:  # repos : cœur à peine lumineux qui respire (4 s)
        breath = 0.5 + 0.5 * math.sin(t * TAU / 4.0)
        _glow(cr, cx, cy, r * (0.30 + 0.03 * breath), color, 0.30 + 0.10 * breath, white * 0.8)
    cr.restore()


# ------------------------------------------------------------------------- B) constellation

def star(cr, x, y, a, color: Color, alpha: float = 1.0, pinch: float = 0.20, rot: float = 0.0) -> None:
    """Étoile à quatre branches : côtés concaves (courbes de Bézier vers le centre)."""
    if a <= 0.2 or alpha <= 0:
        return
    k = a * pinch
    cr.save()
    cr.translate(x, y)
    cr.rotate(rot)
    cr.new_path()
    cr.move_to(0, -a)
    cr.curve_to(0, -k, k, 0, a, 0)
    cr.curve_to(k, 0, 0, k, 0, a)
    cr.curve_to(0, k, -k, 0, -a, 0)
    cr.curve_to(-k, 0, 0, -k, 0, -a)
    cr.close_path()
    cr.set_source_rgba(*color, clamp(alpha))
    cr.fill()
    cr.restore()


def star_glow(cr, x, y, rad, color: Color, alpha: float) -> None:
    g = cairo.RadialGradient(x, y, 0, x, y, rad)
    g.add_color_stop_rgba(0.0, *color, alpha)
    g.add_color_stop_rgba(1.0, *color, 0.0)
    cr.set_source(g)
    _circle(cr, x, y, rad)
    cr.fill()


def const_motif(cr, st: str, cx, cy, r, sc: Scene, p: Pal) -> None:
    t, lvl = sc.t, sc.level
    color = state_color(p, st)
    ink = p.ink
    halo = 0.14 if p.dark else 0.0
    if st == "off":
        star(cr, cx, cy, r * 0.36, color, 0.85)
        # barre détourée (comme un symbole « barré ») : un trait couleur du disque l'isole de l'étoile
        _slash(cr, cx, cy, r * 0.40, p.lk.fill, max(2.0, r * 0.05) + max(2.0, r * 0.06))
        _slash(cr, cx, cy, r * 0.40, color, max(2.0, r * 0.05))
    elif st == "listening":
        star(cr, cx, cy, r * 0.30, ink)
        # quatre petites étoiles, posées en diagonale, allumées une à une par le niveau
        spots = ((-0.48, -0.40, 0.16), (0.47, -0.30, 0.12), (0.40, 0.44, 0.17), (-0.42, 0.40, 0.11))
        for i, (dx, dy, s) in enumerate(spots):
            lit = clamp(lvl * 3.2 - i * 0.55)
            tw = 0.85 + 0.15 * math.sin(t * 9.0 + i * 2.1)
            x, y = cx + dx * r, cy + dy * r
            if halo:
                star_glow(cr, x, y, r * s * 1.8, color, 0.30 * lit)
            star(cr, x, y, r * s * (0.65 + 0.35 * lit) * tw, color, 0.22 + 0.78 * lit)
    elif st in ("thinking", "working", "booting"):
        n = 8
        speed = 3.0 if st == "thinking" else 4.0
        a0 = t * TAU / speed
        ring = r * 0.52
        for i in range(n):
            ang = a0 - i * TAU / n
            trail = 1.0 - i / n                      # la tête brille, la traîne s'éteint
            x, y = cx + ring * math.cos(ang), cy + ring * math.sin(ang)
            s = r * (0.075 + 0.075 * trail)
            if halo and i == 0:
                star_glow(cr, x, y, s * 2.2, color, 0.35)
            star(cr, x, y, s, color, 0.18 + 0.82 * trail ** 1.4)
        star(cr, cx, cy, r * 0.13, ink, 0.55)
    elif st == "speaking":
        env = (0.42, 0.66, 0.86, 1.0, 0.86, 0.66, 0.42)
        gap, w = r * 0.135, r * 0.078
        top = mix(color, WHITE, 0.35) if p.dark else color
        for i, e in enumerate(env):
            x = cx + (i - 3) * gap
            wob = 0.55 + 0.45 * math.sin(t * TAU * (1.25 + 0.31 * i) + i * 1.7)
            h = r * (0.10 + 0.62 * lvl * e * wob)
            lin = cairo.LinearGradient(0, cy - h / 2, 0, cy + h / 2)
            lin.add_color_stop_rgb(0.0, *top)
            lin.add_color_stop_rgb(0.5, *color)
            lin.add_color_stop_rgb(1.0, *top)
            cr.new_path()
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_width(w)
            cr.set_source(lin)
            cr.move_to(x, cy - h / 2)
            cr.line_to(x, cy + h / 2)
            cr.stroke()
    elif st == "asking":
        pulse = 0.5 + 0.5 * math.sin(t * TAU / 2.0)
        if halo:
            star_glow(cr, cx, cy, r * 0.62, color, 0.10 + 0.12 * pulse)
        star(cr, cx, cy, r * (0.34 + 0.03 * pulse), color)
        _stroke_circle(cr, cx, cy, r * 0.66, color, 0.30 + 0.55 * pulse, max(1.5, r * 0.035))
    else:  # repos
        breath = 0.5 + 0.5 * math.sin(t * TAU / 4.0)
        if halo:
            star_glow(cr, cx, cy, r * 0.62, ink, 0.07 + 0.03 * breath)
        star(cr, cx, cy, r * 0.40 * (1 + 0.02 * breath), ink)


# ------------------------------------------------------------------------- commun

def agents_badge(cr, cx, cy, R, n: int, p: Pal) -> None:
    """Pastille discrète en haut à droite : nombre d'agents au travail."""
    x, y = cx + R * 0.60, cy - R * 0.60
    rad = max(7.0, R * 0.15)
    cr.set_source_rgba(*p.lk.fill, 0.98)
    _circle(cr, x, y, rad)
    cr.fill()
    _stroke_circle(cr, x, y, rad - 0.5, p.lk.states["working"], 0.85, 1.0)
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(rad * 1.15)
    s = str(n)
    ext = cr.text_extents(s)
    cr.set_source_rgba(*p.ink, 1.0)
    cr.move_to(x - ext.width / 2 - ext.x_bearing, y - ext.height / 2 - ext.y_bearing)
    cr.show_text(s)
    cr.new_path()


def extras(cr, cx, cy, R, sc: Scene, p: Pal) -> None:
    if sc.total:
        pulse = 0.55 + 0.35 * math.sin(sc.t * 4.0)
        bp._ring(cr, cx, cy, R * bp.DISC + max(2.0, R * 0.05), 0, TAU, bp.TOTAL_RGB, max(2.0, R * 0.04), pulse)
        bp._label(cr, "TOTAL", cx, cy - R * 0.84, max(8.0, R * 0.14), bp.TOTAL_RGB)
    if sc.usage in ("silencieux", "dictee"):
        lab = "silencieux" if sc.usage == "silencieux" else "dictée"
        bp._label(cr, lab, cx, cy + R * 0.95, max(8.0, R * 0.14),
                  p.lk.warn if sc.usage == "silencieux" else p.lk.ok)
    if sc.agents > 0:
        agents_badge(cr, cx, cy, R, sc.agents, p)


def draw(concept: str, cr, w: float, h: float, layers: list[tuple[str, float]], sc: Scene, p: Pal) -> None:
    """Dessine une bulle. `layers` : [(état, opacité)] pour les fondus (300 ms dans la vraie)."""
    cx, cy = w / 2, h / 2
    R = min(w, h) / 2
    r = R * bp.DISC
    if concept == "actuelle":
        # la bulle actuelle ne montre plus ni Total, ni mode, ni pause (23/10, l'utilisateur : bulle SIMPLE)
        bp.draw(cr, w, h, layers=layers, t=sc.t, level=sc.level, lk=p.lk)
        return
    cool = sum(a for st, a in layers if st != "off")
    if concept == "orbe":
        orb_body(cr, cx, cy, r, p, cool)
        motif = orb_motif
    else:
        bp._disc(cr, cx, cy, r, p.lk)
        motif = const_motif
    for st, a in layers:
        if a <= 0.0:
            continue
        if a >= 1.0:
            motif(cr, st, cx, cy, r, sc, p)
        else:
            cr.push_group()
            motif(cr, st, cx, cy, r, sc, p)
            cr.pop_group_to_source()
            cr.paint_with_alpha(a)
    if concept == "orbe":
        orb_gloss(cr, cx, cy, r, p, cool)
    extras(cr, cx, cy, R, sc, p)


def bubble(concept: str, mode: str, sc: Scene, scale: int = 2, layers=None) -> cairo.ImageSurface:
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE * scale, SIZE * scale)
    cr = cairo.Context(surf)
    cr.scale(scale, scale)
    draw(concept, cr, SIZE, SIZE, layers or [(sc.state, 1.0)], sc, pal(mode))
    surf.flush()
    return surf


# ------------------------------------------------------------------------- planches

NAMES = {"orbe": "A · Orbe de verre", "constellation": "B · Constellation", "actuelle": "Bulle actuelle"}

CELLS = [  # (étiquette, scène) ; t choisi pour une image parlante
    ("Repos", Scene("sleeping", 0.0, 1.0)),
    ("Écoute", Scene("listening", 0.65, 0.35)),
    ("Réflexion", Scene("thinking", 0.0, 0.50)),
    ("Parole", Scene("speaking", 0.75, 0.30)),
    ("Agents au travail (3)", Scene("working", 0.0, 2.40, agents=3)),
    ("Confirmation", Scene("asking", 0.0, 0.5)),
    ("Micro coupé", Scene("off", 0.0, 0.3)),
    ("Autonome TOTAL", Scene("sleeping", 0.0, 0.40, total=True)),
    ("Mode silencieux", Scene("sleeping", 0.0, 1.0, usage="silencieux")),
    ("Mode dictée", Scene("listening", 0.5, 0.35, usage="dictee")),
]

BG = {"dark": (0.075, 0.075, 0.085), "light": (0.898, 0.898, 0.918)}
CELL_BG = {"dark": (0.125, 0.125, 0.135), "light": (0.955, 0.955, 0.968)}
INK = {"dark": (0.96, 0.96, 0.97), "light": (0.11, 0.11, 0.12)}
INK2 = {"dark": (0.68, 0.68, 0.70), "light": (0.39, 0.39, 0.40)}
MODE_FR = {"dark": "Sombre", "light": "Clair"}


def _rrect(cr, x, y, w, h, rad):
    cr.new_path()
    cr.arc(x + w - rad, y + rad, rad, -TAU / 4, 0)
    cr.arc(x + w - rad, y + h - rad, rad, 0, TAU / 4)
    cr.arc(x + rad, y + h - rad, rad, TAU / 4, TAU / 2)
    cr.arc(x + rad, y + rad, rad, TAU / 2, TAU * 3 / 4)
    cr.close_path()


def _blit(cr, surf, x, y) -> None:
    cr.set_source_surface(surf, x, y)
    cr.paint()


def planche_concept(concept: str, path: Path) -> None:
    cols, cell_w, cell_h, m = 5, 330, 372, 40
    small_w = 158
    W = m * 2 + cols * cell_w
    sec_h = 56 + 2 * cell_h + 44 + SIZE + 46 + 30
    H = 120 + 2 * sec_h
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
    cr = cairo.Context(surf)
    cr.set_source_rgb(*BG["dark"])
    cr.paint()
    _text(cr, f"Maquette de la bulle Astrel : {NAMES[concept]}", m, 56, 34, INK["dark"], bold=True, align="left")
    _text(cr, "Agrandi = rendu 2x (netteté d'un écran HiDPI), puis taille réelle 148 px. "
              "Dessin par le code (Cairo), aucun flou réel.", m, 92, 18, INK2["dark"], align="left")
    y0 = 120
    for mode in ("dark", "light"):
        cr.set_source_rgb(*BG[mode])
        cr.rectangle(0, y0, W, sec_h)
        cr.fill()
        _text(cr, f"{MODE_FR[mode]} · agrandi (2x)", m, y0 + 40, 24, INK[mode], bold=True, align="left")
        for i, (lab, sc) in enumerate(CELLS):
            col, row = i % cols, i // cols
            x, y = m + col * cell_w, y0 + 56 + row * cell_h
            cr.set_source_rgb(*CELL_BG[mode])
            _rrect(cr, x + 6, y + 6, cell_w - 12, cell_h - 12, 18)
            cr.fill()
            _blit(cr, bubble(concept, mode, sc, 2), x + (cell_w - 2 * SIZE) / 2, y + 14)
            _text(cr, lab, x + cell_w / 2, y + cell_h - 26, 20, INK[mode], bold=True)
        ys = y0 + 56 + 2 * cell_h + 34
        _text(cr, f"{MODE_FR[mode]} · taille réelle (148 px)", m, ys, 20, INK[mode], bold=True, align="left")
        for i, (lab, sc) in enumerate(CELLS):
            x = m + i * small_w + (W - 2 * m - 10 * small_w) / 2
            _blit(cr, bubble(concept, mode, sc, 1), x + (small_w - SIZE) / 2, ys + 12)
            _text(cr, lab if len(lab) < 18 else lab.split(" (")[0], x + small_w / 2, ys + 12 + SIZE + 22, 13, INK2[mode])
        y0 += sec_h
    surf.write_to_png(str(path))


COMP_CELLS = [CELLS[0], CELLS[1], CELLS[2], CELLS[3], CELLS[5], CELLS[6]]


def planche_comparaison(path: Path) -> None:
    concepts = ("actuelle", "orbe", "constellation")
    lab_w, m, cell = 230, 36, 316
    W = m * 2 + lab_w + len(COMP_CELLS) * cell
    small = 158
    row = SIZE + 24
    H = 166 + 3 * (cell + 12) + 2 * (24 + 44 + 3 * row + 12)
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
    cr = cairo.Context(surf)
    cr.set_source_rgb(*BG["dark"])
    cr.paint()
    _text(cr, "Bulle Astrel : comparaison des trois dessins", m, 56, 34, INK["dark"], bold=True, align="left")
    _text(cr, "Mêmes états, même taille (148 px), mêmes jetons Premium bleus. Haut : sombre agrandi 2x ; "
              "bas : taille réelle, sombre puis clair.", m, 92, 18, INK2["dark"], align="left")
    y = 150
    for j, (lab, _) in enumerate(COMP_CELLS):
        _text(cr, lab, m + lab_w + j * cell + cell / 2, y, 20, INK2["dark"], bold=True)
    y += 16
    for c in concepts:
        _text(cr, NAMES[c], m, y + cell / 2 + 6, 21, INK["dark"], bold=True, align="left")
        for j, (_, sc) in enumerate(COMP_CELLS):
            x = m + lab_w + j * cell
            cr.set_source_rgb(*CELL_BG["dark"])
            _rrect(cr, x + 6, y + 4, cell - 12, cell - 8, 18)
            cr.fill()
            _blit(cr, bubble(c, "dark", sc, 2), x + (cell - 2 * SIZE) / 2, y + (cell - 2 * SIZE) / 2)
        y += cell + 12
    for mode in ("dark", "light"):
        y += 24
        cr.set_source_rgb(*BG[mode])
        cr.rectangle(0, y, W, 3 * row + 44 + 12 if mode == "dark" else H - y)
        cr.fill()
        _text(cr, f"Taille réelle 148 px · {MODE_FR[mode].lower()}", m, y + 30, 20, INK[mode], bold=True, align="left")
        y += 44
        for c in concepts:
            _text(cr, NAMES[c], m, y + SIZE / 2 + 6, 19, INK[mode], bold=True, align="left")
            for j, (_, sc) in enumerate(COMP_CELLS):
                x = m + lab_w + j * cell + (cell - SIZE) / 2
                _blit(cr, bubble(c, mode, sc, 1), x, y)
            y += row
        y += 12
    surf.write_to_png(str(path))


# ------------------------------------------------------------------------- animation

SEQ = (("sleeping", "Repos"), ("listening", "Écoute"), ("thinking", "Réflexion"), ("speaking", "Parole"))
SEG, FADE, LOOP, FPS = 1.0, 0.3, 4.0, 25


def level_at(t: float) -> float:
    """Niveau de voix simulé (0..1), rythmé comme une phrase."""
    return clamp(0.52 + 0.24 * math.sin(TAU * 1.25 * t) + 0.14 * math.sin(TAU * 3.1 * t + 1.0), 0.12, 1.0)


def timeline(t: float) -> tuple[list[tuple[str, float]], str]:
    t %= LOOP
    i = min(int(t // SEG), len(SEQ) - 1)
    u = t - i * SEG
    cur, lab = SEQ[i]
    if u > SEG - FADE:
        k = smooth((u - (SEG - FADE)) / FADE)
        return [(cur, 1 - k), (SEQ[(i + 1) % len(SEQ)][0], k)], lab
    return [(cur, 1.0)], lab


def frame(concept: str, t: float) -> cairo.ImageSurface:
    pw, ph = 2 * SIZE + 40, 2 * SIZE + 64
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, pw * 2, ph)
    cr = cairo.Context(surf)
    layers, lab = timeline(t)
    sc = Scene(layers[-1][0], level_at(t), t)
    for k, mode in enumerate(("dark", "light")):
        cr.set_source_rgb(*BG[mode])
        cr.rectangle(k * pw, 0, pw, ph)
        cr.fill()
        _blit(cr, bubble(concept, mode, sc, 2, layers), k * pw + 20, 8)
        _text(cr, f"{NAMES[concept]} · {lab}", k * pw + pw / 2, ph - 22, 18, INK[mode], bold=True)
    surf.flush()
    return surf


def to_pil(surf: cairo.ImageSurface):
    from PIL import Image
    w, h = surf.get_width(), surf.get_height()
    return Image.frombuffer("RGBA", (w, h), bytes(surf.get_data()), "raw", "BGRA", surf.get_stride(), 1).convert("RGB")


def animation(concept: str) -> list[str]:
    made = []
    try:
        from PIL import Image
        frames = [to_pil(frame(concept, i / FPS)) for i in range(int(LOOP * FPS))]
        pals = [f.quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)
                for f in frames]
        gif = OUT / f"animation-{concept}.gif"
        pals[0].save(gif, save_all=True, append_images=pals[1:], duration=int(1000 / FPS), loop=0,
                     disposal=1, optimize=False)
        made.append(gif.name)
        # APNG en plus : sans palette, donc sans les cercles de dégradé du GIF (256 couleurs)
        png = OUT / f"animation-{concept}.png"
        frames[0].save(png, save_all=True, append_images=frames[1:], duration=int(1000 / FPS), loop=0,
                       default_image=False)
        made.append(png.name)
    except ImportError:
        pass
    # bande de 6 images (toujours : utile si le GIF ne s'anime pas dans la visionneuse)
    times = (0.40, 1.20, 1.55, 2.15, 2.50, 3.20)
    pw, ph = 2 * SIZE + 40, 2 * SIZE + 64
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, pw * 6, ph + 56)
    cr = cairo.Context(surf)
    cr.set_source_rgb(*BG["dark"])
    cr.paint()
    _text(cr, f"{NAMES[concept]} : six instants de l'animation (sombre, 2x)", 20, 36, 22, INK["dark"], bold=True, align="left")
    for k, t in enumerate(times):
        layers, lab = timeline(t)
        sc = Scene(layers[-1][0], level_at(t), t)
        _blit(cr, bubble(concept, "dark", sc, 2, layers), k * pw + 20, 64)
        _text(cr, f"{lab} · {t:.2f} s", k * pw + pw / 2, ph + 56 - 22, 17, INK2["dark"], bold=True)
    strip = OUT / f"bande-{concept}.png"
    surf.write_to_png(str(strip))
    made.append(strip.name)
    return made


# ------------------------------------------------------------------------- mesure

def mesure(n: int = 60) -> dict[str, dict[str, float]]:
    """Temps moyen (ms) pour dessiner UNE image de bulle, à 1x (148 px) et 2x (296 px)."""
    res: dict[str, dict[str, float]] = {}
    states = [("sleeping", 0.0), ("listening", 0.6), ("thinking", 0.0), ("speaking", 0.7), ("asking", 0.0)]
    for concept in ("actuelle", "orbe", "constellation"):
        row = {}
        for scale in (1, 2):
            per = []
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE * scale, SIZE * scale)
            p = pal("dark")
            for st, lvl in states:
                t0 = time.perf_counter()
                for i in range(n):
                    cr = cairo.Context(surf)
                    cr.set_operator(cairo.OPERATOR_CLEAR)
                    cr.paint()
                    cr.set_operator(cairo.OPERATOR_OVER)
                    cr.scale(scale, scale)
                    draw(concept, cr, SIZE, SIZE, [(st, 1.0)], Scene(st, lvl, i / 30), p)
                surf.flush()
                per.append((time.perf_counter() - t0) * 1000 / n)
            row[f"{scale}x_moy"] = sum(per) / len(per)
            row[f"{scale}x_max"] = max(per)
        res[concept] = row
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mesure", action="store_true", help="mesurer le temps de rendu d'une image")
    ap.add_argument("--sans-animation", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    planche_concept("orbe", OUT / "planche-orbe.png")
    planche_concept("constellation", OUT / "planche-constellation.png")
    planche_comparaison(OUT / "planche-comparaison.png")
    made = ["planche-orbe.png", "planche-constellation.png", "planche-comparaison.png"]
    if not args.sans_animation:
        for c in ("orbe", "constellation"):
            made += animation(c)
    for f in made:
        print(OUT / f)
    if args.mesure:
        for c, row in mesure().items():
            print(f"{c:14s} 1x {row['1x_moy']:.2f} ms (max {row['1x_max']:.2f})   "
                  f"2x {row['2x_moy']:.2f} ms (max {row['2x_max']:.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
