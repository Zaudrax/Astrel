#!/usr/bin/env python3
"""Logo « anneau » d'Astrel et icône du lanceur, dessinés en VECTEUR (22/10, plan P25, point 14
de la note du 21/10 ; demande de l'utilisateur : anneau lumineux + point central, comme l'orbe de la
bulle, icône dans la barre des tâches / le lanceur).

    .venv/bin/python tools/logo.py                 # SVG + PNG des 4 variantes dans hud/assets/logo/
    .venv/bin/python tools/logo.py --choix e       # + variante retenue : choix.txt et astrel-icon.png
    .venv/bin/python tools/logo.py --planche /tmp/p25/planche.png   # aperçu 16/32/48 px

Une seule description (`formes()`) sert aux deux sorties : SVG écrit à la main (`svg()`) et PNG
rendus par cairo à chaque taille (`png()`), jamais une image réduite (flou à 16 px). Les couleurs
viennent de `config/themes.json` (`astrel.theme.palette`) : fond = `bg`, haut du dégradé =
`glass`, filet = `line`, anneau = `accent` ou `ink`. Icône UNIQUE pour le dock (question 1 du
plan) : carré arrondi, transparent hors du carré (question 2), statique (ne suit pas le thème).

Variantes (lot A du plan) :
  a  Classique : anneau cyan lumineux, point central, fond #04060c.
  b  Premium sombre : anneau fin mat, sans lueur, point à l'accent.
  c  Premium clair : anneau bleu sur blanc cassé (carré clair : à éviter sur un dock clair).
  d  Anneau épais, grand point, sans lueur : le plus lisible en petit.
  e  ASTREL (24/10, plan P31, lot D) : étoile à 4 branches concave, même géométrie que le logo du
     HUD et que le repos de la bulle (`bubble_premium._star`, pincement 0,20), accent Premium sur
     fond Premium sombre. C'est la variante retenue (choix du 23/10 : logo a de P31).

Le choix de l'utilisateur (`--choix`) est retenu dans `hud/assets/logo/choix.txt` (lu par
`install.py`, qui pose l'icône `astrel-hud` dans hicolor) et remplace `hud/assets/astrel-icon.png`
EN PLACE (même nom, 1024², RGBA : favicon de `hud/index.html`).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from astrel import theme  # noqa: E402

LOGO_DIR = ROOT / "hud" / "assets" / "logo"
PNG_DIR = LOGO_DIR / "png"
CHOIX_FILE = LOGO_DIR / "choix.txt"
ICON_FILE = ROOT / "hud" / "assets" / "astrel-icon.png"
SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512, 1024)
SVG_UNIT = 1024  # viewBox du SVG

# Géométrie commune, en fraction du côté de l'icône.
INSET = 0.03         # marge du carré arrondi (coins transparents)
CORNER = 0.225       # rayon des coins, en fraction du côté du carré
MARGE = 0.15         # bord extérieur de l'anneau à 15 % du bord de l'icône
EDGE = 0.012         # filet du carré (lisible sur un dock de même teinte)
MIN_TRAIT_PX = 1.4   # trait de l'anneau jamais plus fin (PNG seulement : 16 px)
MIN_POINT_PX = 1.6   # rayon du point jamais plus petit (PNG seulement)
LUEUR_MIN_PX = 32    # sous cette taille, pas de lueur (elle salirait l'anneau à 16 px)
LUEUR_PAS = 6        # traits de lueur autour de l'anneau
LUEUR_LARGEUR = 0.012
LUEUR_ALPHA = 0.09

VARIANTES: dict[str, dict] = {
    "a": {"nom": "Classique", "theme": "classique", "mode": "dark", "anneau": 0.075, "point": 0.09,
          "couleur_anneau": "accent", "lueur": True, "coeur": "hi"},
    "b": {"nom": "Premium sombre", "theme": "premium", "mode": "dark", "anneau": 0.045, "point": 0.075,
          "couleur_anneau": "ink", "lueur": False, "coeur": None},
    "c": {"nom": "Premium clair", "theme": "premium", "mode": "light", "anneau": 0.055, "point": 0.08,
          "couleur_anneau": "accent", "lueur": False, "coeur": None},
    "d": {"nom": "Lisible en petit", "theme": "classique", "mode": "dark", "anneau": 0.11, "point": 0.13,
          "couleur_anneau": "accent", "lueur": False, "coeur": None},
    "e": {"nom": "étoile à 4 branches","theme": "premium", "mode": "dark", "etoile": 0.5 - MARGE, "pincement": 0.20,
          "couleur_etoile": "accent"},
}

Rgba = tuple[float, float, float, float]


def _rgba(color: str, alpha: float = 1.0) -> Rgba:
    r, g, b = theme.rgb(color)
    return (r, g, b, alpha)


def couleurs(lettre: str) -> dict[str, str]:
    """Palette de `config/themes.json` pour une variante (accent par défaut du fichier)."""
    v = VARIANTES[lettre]
    return theme.palette(v["theme"], v["mode"], theme.data()["default"]["accent"])


def formes(lettre: str) -> list[tuple]:
    """Description de l'icône en coordonnées 0..1, partagée par le SVG et les PNG :
    ("carre", x, y, cote, rayon, haut, bas), ("filet", x, y, cote, rayon, largeur, couleur),
    ("anneau", cx, cy, r, largeur, couleur, principal), ("point", cx, cy, r, centre, bord),
    ("etoile", cx, cy, a, pincement, couleur) : a = demi-envergure (pointes à la marge de 15 %)."""
    v = VARIANTES[lettre]
    pal = couleurs(lettre)
    cote = 1 - 2 * INSET
    out: list[tuple] = [
        ("carre", INSET, INSET, cote, CORNER * cote, _rgba(pal["glass"]), _rgba(pal["bg"])),
        ("filet", INSET, INSET, cote, CORNER * cote, EDGE, _rgba(pal["line"], 0.14)),
    ]
    if "etoile" in v:
        out.append(("etoile", 0.5, 0.5, v["etoile"], v["pincement"], _rgba(pal[v["couleur_etoile"]])))
        return out
    anneau = _rgba(pal[v["couleur_anneau"]])
    r = 0.5 - MARGE - v["anneau"] / 2
    if v["lueur"]:
        for k in range(LUEUR_PAS, 0, -1):
            out.append(("lueur", 0.5, 0.5, r, v["anneau"] + 2 * k * LUEUR_LARGEUR,
                        anneau[:3] + (LUEUR_ALPHA * (LUEUR_PAS + 1 - k) / LUEUR_PAS,)))
    out.append(("anneau", 0.5, 0.5, r, v["anneau"], anneau, True))
    accent = _rgba(pal["accent"])
    centre = _rgba(pal[v["coeur"]]) if v["coeur"] else accent
    out.append(("point", 0.5, 0.5, v["point"], centre, accent))
    return out


def _etoile_courbes(x: float, y: float, a: float, pincement: float) -> list[tuple]:
    """Contour de l'étoile, comme `bubble_premium._star` : un départ (None, None, x, y) puis quatre
    Bézier (contrôle 1, contrôle 2, x, y), côtés concaves vers le centre."""
    k = a * pincement
    return [
        (None, None, x, y - a),
        ((x, y - k), (x + k, y), x + a, y),
        ((x + k, y), (x, y + k), x, y + a),
        ((x, y + k), (x - k, y), x - a, y),
        ((x - k, y), (x, y - k), x, y - a),
    ]


# --- PNG (cairo) ---

def _rounded(ctx, x: float, y: float, w: float, r: float) -> None:
    import math
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    ctx.arc(x + w - r, y + w - r, r, 0, math.pi / 2)
    ctx.arc(x + r, y + w - r, r, math.pi / 2, math.pi)
    ctx.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    ctx.close_path()


def surface(lettre: str, size: int):
    """Surface cairo ARGB32 de l'icône à `size` px."""
    import math

    import cairo
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surf)
    s = float(size)
    for f in formes(lettre):
        kind = f[0]
        if kind == "carre":
            _, x, y, w, r, haut, bas = f
            grad = cairo.LinearGradient(0, y * s, 0, (y + w) * s)
            grad.add_color_stop_rgba(0, *haut)
            grad.add_color_stop_rgba(1, *bas)
            _rounded(ctx, x * s, y * s, w * s, r * s)
            ctx.set_source(grad)
            ctx.fill()
        elif kind == "filet":
            _, x, y, w, r, largeur, coul = f
            lw = max(largeur * s, 1.0)
            _rounded(ctx, x * s + lw / 2, y * s + lw / 2, w * s - lw, max(r * s - lw / 2, 0))
            ctx.set_line_width(lw)
            ctx.set_source_rgba(*coul)
            ctx.stroke()
        elif kind == "lueur":
            if size < LUEUR_MIN_PX:
                continue
            _, cx, cy, r, largeur, coul = f
            ctx.arc(cx * s, cy * s, r * s, 0, 2 * math.pi)
            ctx.set_line_width(largeur * s)
            ctx.set_source_rgba(*coul)
            ctx.stroke()
        elif kind == "anneau":
            _, cx, cy, r, largeur, coul, _principal = f
            lw = max(largeur * s, MIN_TRAIT_PX)
            outer = (r + largeur / 2) * s  # le bord extérieur reste à la marge de 15 %
            ctx.arc(cx * s, cy * s, outer - lw / 2, 0, 2 * math.pi)
            ctx.set_line_width(lw)
            ctx.set_source_rgba(*coul)
            ctx.stroke()
        elif kind == "point":
            _, cx, cy, r, centre, bord = f
            rr = max(r * s, MIN_POINT_PX)
            grad = cairo.RadialGradient(cx * s, cy * s, 0, cx * s, cy * s, rr)
            grad.add_color_stop_rgba(0, *centre)
            grad.add_color_stop_rgba(1 if centre == bord else 0.75, *bord)
            ctx.arc(cx * s, cy * s, rr, 0, 2 * math.pi)
            ctx.set_source(grad)
            ctx.fill()
        elif kind == "etoile":
            _, cx, cy, a, pincement, coul = f
            for kx, ky, rx, ry in _etoile_courbes(cx * s, cy * s, a * s, pincement):
                if kx is None:
                    ctx.move_to(rx, ry)
                else:
                    ctx.curve_to(*kx, *ky, rx, ry)
            ctx.close_path()
            ctx.set_source_rgba(*coul)
            ctx.fill()
    surf.flush()
    return surf


def png(lettre: str, size: int, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    surface(lettre, size).write_to_png(str(tmp))
    tmp.replace(dest)
    return dest


def image(lettre: str, size: int):
    """Image PIL RGBA (aperçus, tests)."""
    import io

    from PIL import Image
    buf = io.BytesIO()
    surface(lettre, size).write_to_png(buf)
    buf.seek(0)
    return Image.open(buf).convert("RGBA")


# --- SVG (écrit à la main, mêmes formes) ---

def _n(v: float) -> str:
    return f"{v * SVG_UNIT:.2f}".rstrip("0").rstrip(".")


def svg(lettre: str) -> str:
    v = VARIANTES[lettre]
    defs: list[str] = []
    body: list[str] = []

    def paint(c: Rgba, attr: str) -> str:
        r, g, b, a = c
        hexa = "#%02x%02x%02x" % tuple(round(x * 255) for x in (r, g, b))
        return f'{attr}="{hexa}"' + (f' {attr}-opacity="{a:.3f}"' if a < 1 else "")

    def stop(off: float, c: Rgba) -> str:
        r, g, b, a = c
        hexa = "#%02x%02x%02x" % tuple(round(x * 255) for x in (r, g, b))
        return f'<stop offset="{off:g}" stop-color="{hexa}"' + (f' stop-opacity="{a:.3f}"' if a < 1 else "") + "/>"

    for f in formes(lettre):
        kind = f[0]
        if kind == "carre":
            _, x, y, w, r, haut, bas = f
            defs.append(f'<linearGradient id="fond" x1="0" y1="0" x2="0" y2="1">{stop(0, haut)}{stop(1, bas)}'
                        "</linearGradient>")
            body.append(f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(w)}" rx="{_n(r)}" '
                        'fill="url(#fond)"/>')
        elif kind == "filet":
            _, x, y, w, r, largeur, coul = f
            body.append(f'<rect x="{_n(x + largeur / 2)}" y="{_n(y + largeur / 2)}" width="{_n(w - largeur)}" '
                        f'height="{_n(w - largeur)}" rx="{_n(max(r - largeur / 2, 0))}" fill="none" '
                        f'{paint(coul, "stroke")} stroke-width="{_n(largeur)}"/>')
        elif kind in ("lueur", "anneau"):
            cx, cy, r, largeur, coul = f[1:6]
            body.append(f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{_n(r)}" fill="none" {paint(coul, "stroke")} '
                        f'stroke-width="{_n(largeur)}"/>')
        elif kind == "point":
            _, cx, cy, r, centre, bord = f
            if centre == bord:
                body.append(f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{_n(r)}" {paint(bord, "fill")}/>')
            else:
                defs.append(f'<radialGradient id="point">{stop(0, centre)}{stop(0.75, bord)}</radialGradient>')
                body.append(f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{_n(r)}" fill="url(#point)"/>')
        elif kind == "etoile":
            _, cx, cy, a, pincement, coul = f
            d = []
            for kx, ky, rx, ry in _etoile_courbes(cx, cy, a, pincement):
                if kx is None:
                    d.append(f"M{_n(rx)} {_n(ry)}")
                else:
                    d.append(f"C{_n(kx[0])} {_n(kx[1])} {_n(ky[0])} {_n(ky[1])} {_n(rx)} {_n(ry)}")
            body.append(f'<path d="{" ".join(d)} Z" {paint(coul, "fill")}/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_UNIT}" height="{SVG_UNIT}" '
            f'viewBox="0 0 {SVG_UNIT} {SVG_UNIT}">\n'
            f'<title>Astrel ({v["nom"]})</title>\n'
            f'<defs>{"".join(defs)}</defs>\n' + "\n".join(body) + "\n</svg>\n")


# --- fichiers ---

def svg_path(lettre: str) -> Path:
    return LOGO_DIR / f"astrel-logo-{lettre}.svg"


def png_path(lettre: str, size: int) -> Path:
    return PNG_DIR / f"astrel-logo-{lettre}-{size}.png"


def generer(lettres=tuple(VARIANTES)) -> list[Path]:
    out = []
    for lettre in lettres:
        p = svg_path(lettre)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(svg(lettre), encoding="utf-8")
        out.append(p)
        out += [png(lettre, size, png_path(lettre, size)) for size in SIZES]
    return out


def lire_choix(path: Path | None = None) -> str | None:
    """Variante retenue (une lettre de VARIANTES) ou None (pas encore choisie, fichier abîmé)."""
    try:
        value = (path or CHOIX_FILE).read_text(encoding="utf-8").strip().lower()
    except OSError:
        return None
    return value if value in VARIANTES else None


def appliquer_choix(lettre: str) -> list[Path]:
    """Retient le choix et remplace `hud/assets/astrel-icon.png` en place (1024², RGBA)."""
    lettre = lettre.strip().lower()
    if lettre not in VARIANTES:
        raise SystemExit(f"variante inconnue : {lettre!r} ({', '.join(VARIANTES)})")
    CHOIX_FILE.write_text(lettre + "\n", encoding="utf-8")
    return [CHOIX_FILE, png(lettre, 1024, ICON_FILE)]


def planche(dest: Path, tailles=(16, 32, 48), zoom: int = 6) -> Path:
    """Aperçu : chaque variante à 16/32/48 px, à taille réelle puis agrandie sans lissage,
    sur un fond sombre et un fond clair (comme un dock GNOME)."""
    from PIL import Image, ImageDraw
    pad = 12
    col_w = sum(t * zoom + pad for t in tailles) + sum(t + pad for t in tailles) + pad
    row_h = max(tailles) * zoom + 2 * pad
    fonds = ((0x24, 0x24, 0x24), (0xf0, 0xf0, 0xf0))
    sheet = Image.new("RGB", (col_w * len(fonds), row_h * len(VARIANTES)), (128, 128, 128))
    draw = ImageDraw.Draw(sheet)
    for j, fond in enumerate(fonds):
        draw.rectangle([j * col_w, 0, (j + 1) * col_w - 1, sheet.height], fill=fond)
        for i, lettre in enumerate(VARIANTES):
            x, y = j * col_w + pad, i * row_h + pad
            for t in tailles:
                im = image(lettre, t)
                sheet.paste(im, (x, y), im)
                x += t + pad
            for t in tailles:
                im = image(lettre, t).resize((t * zoom, t * zoom), Image.NEAREST)
                sheet.paste(im, (x, y), im)
                x += t * zoom + pad
            draw.text((j * col_w + 2, i * row_h + 1), lettre.upper(), fill=(200, 60, 60))
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest)
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Logo anneau d'Astrel : SVG et PNG 16 à 1024 px (plan P25).")
    ap.add_argument("--choix", choices=sorted(VARIANTES), help="variante retenue par l'utilisateur")
    ap.add_argument("--planche", metavar="PNG", help="écrire un aperçu 16/32/48 px des variantes")
    args = ap.parse_args(argv)
    written = generer()
    if args.choix:
        written += appliquer_choix(args.choix)
    if args.planche:
        written.append(planche(Path(args.planche)))
    for p in written:
        print(p.relative_to(ROOT) if p.is_relative_to(ROOT) else p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
