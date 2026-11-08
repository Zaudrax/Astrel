"""Dessin de la bulle dans le thème Premium : la « Constellation » (23/10, maquette B choisie
par l'utilisateur, `tools/maquette_bulle.py`, `runtime/maquettes/bulle/planche-constellation.png`),
qui remplace le disque de verre + anneaux du 21/10 (plan P17).

Cairo SEUL (pas de Gtk) : testable sans écran (`tests/test_bulle_theme.py` rend chaque état en
PNG). `astrel/bubble.py` l'appelle depuis `_on_draw` : depuis le 25/10 (plan P35, demande de
l'utilisateur), c'est le SEUL dessin de la bulle (le Classique est retiré).

SANS FOND depuis le 23/10 (demande de l'utilisateur, capture du 23/10 10 h 46 : « fond blanc ») : plus
de disque de verre sous les étoiles, seuls l'état et ses étoiles se voient sur le bureau. Pour rester
lisibles sur n'importe quel fond d'écran, les formes reçoivent un CONTOUR fin de la teinte
contraire (noir léger en sombre, blanc en clair ; `_contour`, masque décalé, pas de flou). La fenêtre
reste un carré entier pour les clics (aucune forme d'entrée posée par `bubble.py`).

Repos = étoile à quatre branches, couleur de l'encre
(blanche en sombre, noire en clair), qui respire à peine (4 s, ±2 %) ; écoute = quatre petites
étoiles d'accent allumées une à une par le niveau ; réflexion (et chargement, travail) = cercle
de huit étoiles qui tourne, la tête brille, la traîne s'éteint (3 s le tour, 4 s au travail) ;
parole = sept barres d'onde arrondies qui suivent le niveau ; confirmation = étoile et anneau
ambre qui pulsent (2 s) ; micro coupé = étoile grise barrée. En sombre seulement, une lueur
douce (dégradé, pas de flou) sous les étoiles allumées. Pas de pastille « nombre d'agents ».

07/11 (demande de l'utilisateur) : au REPOS seulement, une petite étoile par sous-agent de fond en cours
(`agents`, compté par `bulle_commun.agents_en_cours`, lissé par `agents_vers`) tourne lentement
autour de l'étoile centrale (30 s le tour), réparties régulièrement, 6 au plus ; teinte discrète
(`_agents_color` : encre secondaire teintée d'un peu d'accent), apparition et départ en fondu.

23/10 (demande : « la bulle doit rester SIMPLE ») : AUCUN texte ni marque de mode dans la bulle.
Ni anneau Autonome TOTAL, ni « TOTAL », ni mode d'usage (« silencieux », « dictée »), ni pastille
de pause, ni mission, ni levée du garde-fou : tout cela reste dans le HUD. `draw` ne reçoit donc
plus ces informations. Seuls les états d'Astrel se dessinent (« demande » compris : ambre, la
couleur `warn` du thème, pas celle de Total). `TOTAL_RGB`, `_ring`, `_label`, `_disc` servent
encore à `tools/maquette_bulle.py` (planches des anciennes maquettes), pas à la bulle.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import cairo

TAU = math.tau
DISC = 0.78          # rayon du disque / demi-côté : la marge garde l'ombre (bulle de 148 px)
TOTAL_RGB = (1.0, 140 / 255, 0.0)  # Autonome TOTAL (orange) : maquettes seulement, plus dans la bulle
WHITE = (1.0, 1.0, 1.0)

Color = tuple[float, float, float]


@dataclass
class Look:
    """Couleurs d'un mode (flottants 0..1) : verre du disque, filet, ombre, états, encres."""
    fill: Color
    fill_alpha: float
    line: Color
    line_alpha: float
    shade_alpha: float
    states: dict[str, Color] = field(default_factory=dict)
    warn: Color = (1.0, 0.84, 0.04)
    ok: Color = (0.19, 0.82, 0.35)
    ink_2: Color = (0.68, 0.68, 0.70)
    ink: Color = (0.96, 0.96, 0.97)   # étoile du repos : blanche en sombre, noire en clair
    dark: bool = True                 # lueur sous les étoiles en sombre seulement
    edge: Color = (0.0, 0.0, 0.0)     # contour de lisibilité (sans disque) : teinte contraire de l'encre
    edge_alpha: float = 0.40


def look(mode: str = "dark", accent: str = "bleu") -> Look:
    """Couleurs du thème Premium dans un mode, lues dans config/themes.json (`astrel.theme`)."""
    from . import theme

    pal = theme.palette("premium", mode, accent)
    light = mode == "light"
    states = {s: c[0] for s, c in theme.state_colors("premium", mode, accent).items()}
    return Look(fill=theme.rgb(pal["glass"]), fill_alpha=0.95 if light else 0.92,
                line=theme.rgb(pal["line"]), line_alpha=0.08 if light else 0.12,
                shade_alpha=0.12 if light else 0.35, states=states,
                warn=theme.rgb(pal["warn"]), ok=theme.rgb(pal["ok"]), ink_2=theme.rgb(pal["ink_2"]),
                ink=theme.rgb(pal["ink"]), dark=not light,
                edge=WHITE if light else (0.0, 0.0, 0.0), edge_alpha=0.55 if light else 0.40)


def _clamp(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _doux(x: float) -> float:
    """Courbe en S (0 → 0, 1 → 1, pente nulle aux deux bouts)."""
    x = _clamp(x)
    return x * x * (3.0 - 2.0 * x)


def _mix(a: Color, b: Color, k: float) -> Color:
    return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k)


def _disc(cr, cx: float, cy: float, r: float, lk: Look) -> None:
    # ombre douce : dégradé radial sous le disque, décalé vers le bas
    shade = cairo.RadialGradient(cx, cy + r * 0.06, r * 0.86, cx, cy + r * 0.06, r * 1.22)
    shade.add_color_stop_rgba(0.0, 0, 0, 0, lk.shade_alpha)
    shade.add_color_stop_rgba(1.0, 0, 0, 0, 0.0)
    cr.set_source(shade)
    cr.arc(cx, cy + r * 0.06, r * 1.22, 0, TAU)
    cr.fill()
    cr.set_source_rgba(*lk.fill, lk.fill_alpha)
    cr.arc(cx, cy, r, 0, TAU)
    cr.fill()
    cr.set_line_width(1.0)
    cr.set_source_rgba(*lk.line, lk.line_alpha)
    cr.arc(cx, cy, r - 0.5, 0, TAU)
    cr.stroke()


def _ring(cr, cx, cy, r, a0, a1, color: Color, width: float, alpha: float = 1.0) -> None:
    cr.new_path()
    cr.set_line_width(width)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_source_rgba(*color, alpha)
    cr.arc(cx, cy, r, a0, a1)
    cr.stroke()


def _star(cr, x: float, y: float, a: float, color: Color, alpha: float = 1.0, pinch: float = 0.20) -> None:
    """Étoile à quatre branches de demi-envergure `a` : côtés concaves (Bézier vers le centre)."""
    if a <= 0.2 or alpha <= 0.0:
        return
    k = a * pinch
    cr.new_path()
    cr.move_to(x, y - a)
    cr.curve_to(x, y - k, x + k, y, x + a, y)
    cr.curve_to(x + k, y, x, y + k, x, y + a)
    cr.curve_to(x, y + k, x - k, y, x - a, y)
    cr.curve_to(x - k, y, x, y - k, x, y - a)
    cr.close_path()
    cr.set_source_rgba(*color, _clamp(alpha))
    cr.fill()


def _glow(cr, x: float, y: float, rad: float, color: Color, alpha: float) -> None:
    """Lueur sous une étoile (sombre seulement) : dégradé radial qui s'efface, pas de flou."""
    if rad <= 0.2 or alpha <= 0.0:
        return
    g = cairo.RadialGradient(x, y, 0, x, y, rad)
    g.add_color_stop_rgba(0.0, *color, alpha)
    g.add_color_stop_rgba(1.0, *color, 0.0)
    cr.new_path()
    cr.set_source(g)
    cr.arc(x, y, rad, 0, TAU)
    cr.fill()


def _slash(cr, cx: float, cy: float, d: float, color: Color, width: float) -> None:
    cr.new_path()
    cr.set_line_width(width)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_source_rgba(*color, 1.0)
    cr.move_to(cx - d, cy + d)
    cr.line_to(cx + d, cy - d)
    cr.stroke()


# écoute : quatre petites étoiles en diagonale (dx, dy, taille), en fraction du rayon du disque
_SPOTS = ((-0.48, -0.40, 0.16), (0.47, -0.30, 0.12), (0.40, 0.44, 0.17), (-0.42, 0.40, 0.11))
# parole : enveloppe des sept barres (la plus haute au centre)
_ENVELOPE = (0.42, 0.66, 0.86, 1.0, 0.86, 0.66, 0.42)

# 07/11 : agents de fond au repos. Au-delà de AGENTS_MAX, on en dessine AGENTS_MAX.
AGENTS_MAX = 6
AGENTS_PAS = 0.03        # par image (60/s) : une étoile apparaît ou part en ≈ 0,55 s
AGENTS_TOUR_S = 30.0     # un tour complet en 30 s : mouvement lent, à peine 0,5 px par image à 15 im/s
AGENTS_ORBITE = 0.64     # rayon de l'orbite (fraction du rayon du disque) : hors des branches de l'étoile (0,40)
AGENTS_TAILLE = 0.085    # demi-envergure d'une petite étoile (l'écoute : 0,11 à 0,17)


def agents_vers(actuel: float, nombre: int) -> float:
    """Nombre d'étoiles d'agents dessiné, lissé : avance de `AGENTS_PAS` par image vers `nombre`
    (borné à 0..AGENTS_MAX). Valeur fractionnaire = la dernière étoile en fondu (`_agents`)."""
    cible = float(max(0, min(int(nombre), AGENTS_MAX)))
    if abs(cible - actuel) <= AGENTS_PAS + 1e-9:   # marge : 0,03 x 100 n'est pas tout à fait 3 en flottant
        return cible
    return actuel + (AGENTS_PAS if cible > actuel else -AGENTS_PAS)


def _agents_color(lk: Look, accent: Color) -> Color:
    """Teinte des étoiles d'agents : encre secondaire (`ink_2`, gris des textes discrets) teintée de 30 %
    d'accent. Discrète, proche de l'encre, jamais le bleu vif de l'écoute ; suit l'accent et le mode."""
    return _mix(lk.ink_2, accent, 0.30)


def _agents(cr, cx: float, cy: float, r: float, t: float, n: float, color: Color, halo: bool) -> None:
    """`n` petites étoiles (fractionnaire pendant un fondu) en orbite lente autour du centre.

    Écart TAU/n : quand `n` passe de k à k+1, la nouvelle étoile sort de la première en s'allumant
    pendant que les autres s'écartent ; à n entier elles sont régulièrement réparties. Rien ne saute."""
    n = max(0.0, min(float(n), float(AGENTS_MAX)))
    if n <= 0.0:
        return
    k = math.floor(n)
    n = k + _doux(n - k)                            # départ et arrivée en douceur (place et opacité)
    pas = TAU / max(n, 1.0)
    a0 = -TAU / 4 + t * TAU / AGENTS_TOUR_S       # la première part du haut
    for i in range(math.ceil(n)):
        alpha = _clamp(n - i)                       # 1 pour les étoiles pleines, la fraction pour la dernière
        if alpha <= 0.0:
            continue
        ang = a0 + i * pas
        orbite = r * (AGENTS_ORBITE + 0.025 * math.sin(t * TAU / 7.0 + i * 1.7))   # léger va-et-vient
        x, y = cx + orbite * math.cos(ang), cy + orbite * math.sin(ang)
        tw = 0.90 + 0.10 * math.sin(t * TAU / 3.0 + i * 2.3)                       # scintille doucement
        s = r * AGENTS_TAILLE * tw * (0.6 + 0.4 * alpha)
        if halo:
            _glow(cr, x, y, s * 2.0, color, 0.22 * alpha)
        _star(cr, x, y, s, color, 0.85 * alpha * (0.85 + 0.15 * tw))


def draw_state(cr, st: str, cx: float, cy: float, R: float, t: float, lvl: float, color: Color,
               lk: Look | None = None, agents: float = 0.0) -> None:
    """Motif d'un état (rayon `R` = demi-côté), dans SA couleur ; maquette B « Constellation »."""
    lk = lk or Look(fill=(0.17, 0.17, 0.18), fill_alpha=0.92, line=WHITE, line_alpha=0.12, shade_alpha=0.35)
    r = R * DISC
    ink = lk.ink
    halo = lk.dark
    lvl = _clamp(lvl) if lvl == lvl else 0.0     # niveau borné à 0..1 (NaN = silence)
    if st == "off":
        _star(cr, cx, cy, r * 0.36, color, 0.85)
        # barre détourée (symbole « barré ») : un trait EFFACÉ l'isole de l'étoile (plus de disque
        # dont reprendre la couleur ; `draw` peint les motifs dans un groupe, rien d'autre n'est effacé)
        w = max(2.0, r * 0.05)
        cr.save()
        cr.set_operator(cairo.OPERATOR_CLEAR)
        _slash(cr, cx, cy, r * 0.40, lk.fill, w + max(2.0, r * 0.06))
        cr.restore()
        _slash(cr, cx, cy, r * 0.40, color, w)
    elif st == "listening":
        _star(cr, cx, cy, r * 0.30, ink)
        for i, (dx, dy, s) in enumerate(_SPOTS):
            lit = _clamp(lvl * 3.2 - i * 0.55)       # allumées une à une quand la voix monte
            tw = 0.85 + 0.15 * math.sin(t * 9.0 + i * 2.1)
            x, y = cx + dx * r, cy + dy * r
            if halo:
                _glow(cr, x, y, r * s * 1.8, color, 0.30 * lit)
            _star(cr, x, y, r * s * (0.65 + 0.35 * lit) * tw, color, 0.22 + 0.78 * lit)
    elif st in ("thinking", "working", "booting"):
        n = 8
        a0 = t * TAU / (3.0 if st == "thinking" else 4.0)
        ring = r * 0.52
        for i in range(n):
            ang = a0 - i * TAU / n
            trail = 1.0 - i / n                      # la tête brille, la traîne s'éteint
            x, y = cx + ring * math.cos(ang), cy + ring * math.sin(ang)
            s = r * (0.075 + 0.075 * trail)
            if halo and i == 0:
                _glow(cr, x, y, s * 2.2, color, 0.35)
            _star(cr, x, y, s, color, 0.18 + 0.82 * trail ** 1.4)
        _star(cr, cx, cy, r * 0.13, ink, 0.55)
    elif st == "speaking":
        gap, bw = r * 0.135, r * 0.078
        top = _mix(color, WHITE, 0.35) if halo else color
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_width(bw)
        for i, e in enumerate(_ENVELOPE):
            x = cx + (i - 3) * gap
            wob = 0.55 + 0.45 * math.sin(t * TAU * (1.25 + 0.31 * i) + i * 1.7)
            h = r * (0.10 + 0.62 * lvl * e * wob)
            lin = cairo.LinearGradient(0, cy - h / 2, 0, cy + h / 2)
            lin.add_color_stop_rgb(0.0, *top)
            lin.add_color_stop_rgb(0.5, *color)
            lin.add_color_stop_rgb(1.0, *top)
            cr.new_path()
            cr.set_source(lin)
            cr.move_to(x, cy - h / 2)
            cr.line_to(x, cy + h / 2)
            cr.stroke()
    elif st == "asking":
        pulse = 0.5 + 0.5 * math.sin(t * TAU / 2.0)
        if halo:
            _glow(cr, cx, cy, r * 0.62, color, 0.10 + 0.12 * pulse)
        _star(cr, cx, cy, r * (0.34 + 0.03 * pulse), color)
        _ring(cr, cx, cy, r * 0.66, 0, TAU, color, max(1.5, r * 0.035), 0.30 + 0.55 * pulse)
    else:  # repos (veille)
        breath = 0.5 + 0.5 * math.sin(t * TAU / 4.0)
        if halo:
            _glow(cr, cx, cy, r * 0.62, ink, 0.07 + 0.03 * breath)
        _star(cr, cx, cy, r * 0.40 * (1 + 0.02 * breath), ink)
        # 07/11 : sous-agents de fond en cours (`color` = accent de la veille)
        _agents(cr, cx, cy, r, t, agents, _agents_color(lk, color), halo)


def _label(cr, text: str, cx: float, y: float, size: float, color: Color) -> None:
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(size)
    ext = cr.text_extents(text)
    cr.set_source_rgba(*color, 0.98)
    cr.move_to(cx - ext.width / 2 - ext.x_bearing, y)
    cr.show_text(text)
    cr.new_path()  # show_text laisse un point courant : le prochain arc tracerait un trait jusqu'à lui


_AROUND = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))


def _contour(cr, motif, lk: Look, R: float) -> None:
    """Contour de lisibilité sous le motif (sans disque, le fond d'écran peut avoir la couleur de
    l'encre) : le masque du motif, décalé dans 8 directions sur deux rayons, en teinte contraire.
    Union peinte d'un coup (pas d'accumulation), anneau extérieur plus léger : un liseré, pas un fond."""
    if lk.edge_alpha <= 0.0:
        return
    # décalages ENTIERS et filtre rapide : un décalage fractionnaire passe par le filtrage
    # bilinéaire de pixman (5 ms mesurés pour le contour seul, contre le budget de 5 ms par image)
    d = max(1, round(R * 0.014))                     # 1 px pour la bulle de 148 px
    motif.set_filter(cairo.FILTER_FAST)
    for k, alpha in ((1, lk.edge_alpha), (2, lk.edge_alpha * 0.45)):
        cr.push_group()
        cr.set_source_rgb(*lk.edge)
        for dx, dy in _AROUND:
            cr.save()
            cr.translate(dx * d * k, dy * d * k)
            cr.mask(motif)
            cr.restore()
        cr.pop_group_to_source()
        cr.paint_with_alpha(alpha)


def draw(cr, w: float, h: float, *, layers: list[tuple[str, float]], t: float, level: float,
         lk: Look, agents: float = 0.0) -> None:
    """Dessine la bulle Premium, SANS fond ni texte ni marque de mode (23/10). `layers` :
    [(état, opacité)] (fondu entre états, 300 ms) ; `t` : secondes (figé si les animations du
    bureau sont coupées) ; `level` : 0..1 ; `agents` : sous-agents de fond en cours, lissé par
    `agents_vers` (07/11), dessinés dans la couche du repos seulement."""
    cx, cy = w / 2, h / 2
    R = min(w, h) / 2

    def paint(lk_: Look):
        cr.push_group()                              # motifs, contourés ensemble ensuite
        for st, a in layers:
            if a <= 0.0:
                continue
            color = lk_.states.get(st) or lk_.states.get("sleeping") or (0.04, 0.52, 1.0)
            if a >= 1.0:
                draw_state(cr, st, cx, cy, R, t, level, color, lk_, agents)
            else:
                cr.push_group()
                draw_state(cr, st, cx, cy, R, t, level, color, lk_, agents)
                cr.pop_group_to_source()
                cr.paint_with_alpha(max(0.0, min(1.0, a)))
        return cr.pop_group()

    # le contour suit les FORMES sans les lueurs (sinon une lueur devient une tache sombre sur un
    # fond clair) : masque tiré d'un rendu sans lueur, puis le vrai motif par-dessus
    motif = paint(lk)
    shapes = paint(replace(lk, dark=False)) if lk.dark else motif   # lueurs en sombre seulement
    _contour(cr, shapes, lk, R)
    cr.set_source(motif)
    cr.paint()
