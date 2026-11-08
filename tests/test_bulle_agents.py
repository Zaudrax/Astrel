"""Bulle (07/11/2026, demande de l'utilisateur) : au REPOS, une petite étoile par sous-agent de fond en
cours tourne autour de l'étoile centrale (1 agent = 1 étoile, 6 au plus), en fondu à l'apparition
et au départ ; aucune hors du repos.

- Compte : `bulle_commun.agents_en_cours`, lu dans l'évènement sticky `tasks` (rejoué au `hello`),
  le même pour la bulle GTK (`bubble.py`) et la bulle Qt (`bulle_qt.py`).
- Dessin : `bubble_premium.draw(..., agents=)`, lissé par `bubble_premium.agents_vers`.
- Coût : au repos stable, 15 images/s avec ou sans agents ; 60 seulement pendant un fondu.

Rendu cairo sans écran ; la vraie `Bubble` GTK tourne sous Xvfb (rien à l'écran, la bulle de
l'utilisateur n'est ni lancée ni relancée). `ASTREL_BULLE_PNG=dossier` garde les images du repos."""
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

from astrel import bulle_commun

ROOT = Path(__file__).resolve().parent.parent
SIZE = 148


def _taches(*kinds: str, done: list | None = None) -> dict:
    return {"type": "tasks", "items": [{"id": f"t{i}", "kind": k, "desc": "x"} for i, k in enumerate(kinds)],
            "done": done or []}


# ------------------------------------------------------------------ compte (sans interface, partout)

def test_compte_des_sous_agents_seulement():
    assert bulle_commun.agents_en_cours({"type": "state", "state": "sleeping"}) is None   # autre évènement
    assert bulle_commun.agents_en_cours(_taches()) == 0
    assert bulle_commun.agents_en_cours(_taches("agent")) == 1
    # commandes Bash longues ou de fond (`kind` du CLI : local_bash…) : pas des agents
    assert bulle_commun.agents_en_cours(_taches("agent", "local_bash", "agent", "", "agent")) == 3
    assert bulle_commun.agents_en_cours(_taches(*["agent"] * 9)) == 9        # le plafond est celui du DESSIN
    # tâches finies : dans `done`, jamais comptées
    assert bulle_commun.agents_en_cours(_taches("agent", done=[{"id": "x", "kind": "agent"}])) == 1
    # flux abîmé : rien ne casse
    assert bulle_commun.agents_en_cours({"type": "tasks"}) == 0
    assert bulle_commun.agents_en_cours({"type": "tasks", "items": "?"}) == 0
    assert bulle_commun.agents_en_cours({"type": "tasks", "items": [None, 3, {"kind": "agent"}]}) == 1


class _SansFil:
    def __init__(self, *a, **k) -> None:
        pass

    def start(self) -> None:
        pass


@pytest.fixture
def flux(monkeypatch):
    """`bulle_commun.Feed` sans son fil de connexion au HUD."""
    monkeypatch.setattr(bulle_commun.threading, "Thread", _SansFil)
    return bulle_commun.Feed()


def test_flux_commun_suit_les_taches_et_le_hello(flux):
    assert flux.agents == 0
    # connexion : `hello` rejoue les sujets sticky (dont `tasks`), puis l'historique (sans `tasks`)
    flux.recevoir({"type": "hello", "sticky": [{"type": "state", "state": "sleeping"},
                                                _taches("agent", "agent", "local_bash", "agent")],
                   "history": [{"type": "notice", "text": "x"}]})
    assert flux.agents == 3 and flux.state == "sleeping"
    flux.recevoir(_taches("agent", "agent", done=[{"id": "t2", "kind": "agent"}]))   # une finie
    assert flux.agents == 2
    flux.recevoir({"type": "level", "value": 0.4, "source": "voice"})                # autre sujet : inchangé
    flux.recevoir({"type": "state", "state": "listening"})
    assert flux.agents == 2
    flux.recevoir(_taches())                                                          # session neuve, Stop tout
    assert flux.agents == 0


def test_flux_perdu_remet_le_compte_a_zero(flux, monkeypatch):
    """Cœur arrêté : plus de flux, plus rien ne dit que des agents tournent (relus au `hello` suivant)."""
    class Fin(Exception):
        pass

    def run(coro):
        coro.close()
        raise OSError("HUD injoignable")

    def stop(_s):
        raise Fin

    monkeypatch.setattr(bulle_commun.asyncio, "run", run)
    monkeypatch.setattr(bulle_commun.time, "sleep", stop)
    monkeypatch.setattr(flux, "_from_file", lambda: None)
    flux.connected, flux.agents = True, 3
    with pytest.raises(Fin):
        flux._run()
    assert flux.agents == 0 and not flux.connected


# ------------------------------------------------------------------ dessin (cairo, sans écran)

cairo = pytest.importorskip("cairo")  # bulle : pycairo vient du système (absent de la CI linux)

from astrel import bubble_premium as bp  # noqa: E402


def render(st: str = "sleeping", mode: str = "dark", agents: float | None = None, t: float = 0.3,
           size: int = SIZE, accent: str = "bleu") -> cairo.ImageSurface:
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    kw = {} if agents is None else {"agents": agents}
    bp.draw(cairo.Context(surf), size, size, layers=[(st, 1.0)], t=t, level=0.0, lk=bp.look(mode, accent), **kw)
    surf.flush()
    return surf


def _alpha(surf, x: float, y: float) -> int:
    xi, yi = int(round(x)), int(round(y))
    if not (0 <= xi < surf.get_width() and 0 <= yi < surf.get_height()):
        return 0
    return surf.get_data()[yi * surf.get_stride() + xi * 4 + 3]


def etoiles_en_orbite(surf, size: int = SIZE, seuil: int = 150) -> list[int]:
    """Petites étoiles vues sur l'orbite : pour chaque degré, l'opacité la plus forte entre 0,55 et
    0,75 du rayon du disque (l'étoile centrale s'arrête à 0,41) ; une suite de degrés allumés = une
    étoile. Renvoie l'opacité de pointe de chacune."""
    c, r = size / 2, size / 2 * bp.DISC
    prof = []
    for deg in range(360):
        a = math.radians(deg)
        prof.append(max(_alpha(surf, c + rr * r * math.cos(a), c + rr * r * math.sin(a))
                        for rr in (0.55 + 0.01 * k for k in range(21))))
    debut = next((i for i, v in enumerate(prof) if v < seuil), None)
    if debut is None:
        return [max(prof)]
    pics, cur = [], 0
    for i in range(360):
        v = prof[(debut + i) % 360]
        if v >= seuil:
            cur = max(cur, v)
        elif cur:
            pics.append(cur)
            cur = 0
    if cur:
        pics.append(cur)
    return pics


def test_repos_sans_agent_identique_a_avant():
    """N = 0 : l'image du repos ne change pas d'un octet (rien d'ajouté, rien de déplacé)."""
    for mode in ("dark", "light"):
        assert bytes(render(mode=mode).get_data()) == bytes(render(mode=mode, agents=0).get_data())
        assert etoiles_en_orbite(render(mode=mode)) == []


@pytest.mark.parametrize("mode", ["dark", "light"])
@pytest.mark.parametrize("n, vues", [(1, 1), (2, 2), (3, 3), (6, 6), (9, 6)])
def test_une_etoile_par_agent_au_repos(mode, n, vues):
    """1 agent = 1 étoile, 3 = 3 ; au-delà de 6, on en garde 6 (plafond du dessin, pas du compte)."""
    for t in (0.0, 7.3, 21.9):                  # à tout moment de la rotation
        assert len(etoiles_en_orbite(render(mode=mode, agents=n, t=t))) == vues, (mode, n, t)


@pytest.mark.parametrize("st", ["listening", "thinking", "working", "booting", "speaking", "asking", "off"])
def test_aucune_etoile_d_agent_hors_du_repos(st):
    for mode in ("dark", "light"):
        assert bytes(render(st, mode).get_data()) == bytes(render(st, mode, agents=3).get_data()), (st, mode)


def test_fondu_entre_deux_etats_garde_les_etoiles_avec_le_repos():
    """Repos qui s'efface vers l'écoute : ses étoiles d'agents s'effacent avec lui (même couche)."""
    lk = bp.look("dark", "bleu")

    def img(a_repos: float, agents: float) -> bytes:
        s = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE, SIZE)
        layers = [(st, a) for st, a in (("sleeping", a_repos), ("listening", 1.0 - a_repos)) if a > 0]
        bp.draw(cairo.Context(s), SIZE, SIZE, layers=layers, t=0.3, level=0.0, lk=lk, agents=agents)
        s.flush()
        return bytes(s.get_data())

    assert img(0.5, 3) != img(0.5, 0)          # encore visibles à demi
    assert img(0.0, 3) == img(0.0, 0)          # repos parti : plus rien


def test_reparties_regulierement_et_en_mouvement():
    c, r = SIZE / 2, SIZE / 2 * bp.DISC

    def angles(t: float) -> list[float]:
        s = render(agents=3, t=t)
        vus = []
        for deg in range(360):
            a = math.radians(deg)
            if max(_alpha(s, c + rr * r * math.cos(a), c + rr * r * math.sin(a))
                   for rr in (0.55 + 0.01 * k for k in range(21))) >= 150:
                vus.append(deg)
        groupes: list[list[int]] = []
        for d in vus:
            if groupes and d - groupes[-1][-1] <= 1:
                groupes[-1].append(d)
            else:
                groupes.append([d])
        if len(groupes) > 1 and groupes[0][0] == 0 and groupes[-1][-1] == 359:
            groupes[0] = [g - 360 for g in groupes.pop()] + groupes[0]
        return sorted((sum(g) / len(g)) % 360 for g in groupes)

    a = angles(0.0)
    assert len(a) == 3
    ecarts = [(a[(i + 1) % 3] - a[i]) % 360 for i in range(3)]
    assert all(abs(e - 120) <= 4 for e in ecarts), ecarts            # régulièrement réparties
    b = angles(5.0)                                                  # 5 s plus tard : 60° de tourné (30 s le tour)
    assert abs(((b[0] - a[0]) % 120) - 60) <= 4, (a, b)


def test_lissage_et_plafond():
    v, pas = 0.0, []
    for _ in range(200):
        v = bp.agents_vers(v, 3)
        pas.append(v)
    assert pas[-1] == 3.0 and max(pas) == 3.0
    assert max(b - a for a, b in zip([0.0] + pas, pas)) <= bp.AGENTS_PAS + 1e-9   # jamais de saut
    n_images = pas.index(3.0) + 1
    assert 60 <= n_images <= 120, n_images                           # ≈ 1,7 s pour trois étoiles à 60 images/s
    assert bp.agents_vers(6.0, 9) == 6.0 and bp.agents_vers(0.0, 9) == bp.AGENTS_PAS
    assert bp.agents_vers(2.0, 0) == 2.0 - bp.AGENTS_PAS and bp.agents_vers(0.02, 0) == 0.0
    assert bp.agents_vers(3.0, -1) == 3.0 - bp.AGENTS_PAS


def test_apparition_et_depart_en_fondu():
    """Le passage de 2 à 3 agents se fait par petites touches : une image du fondu ressemble à la
    précédente, la nouvelle étoile s'allume peu à peu (opacité de pointe croissante)."""
    def ecart(a: float, b: float) -> int:
        x, y = bytes(render(agents=a).get_data()), bytes(render(agents=b).get_data())
        return sum(abs(p - q) for p, q in zip(x, y))

    saut = ecart(2.0, 3.0)                     # ce que serait une étoile posée d'un coup
    pas = [ecart(v, min(3.0, v + bp.AGENTS_PAS)) for v in (2.0 + bp.AGENTS_PAS * k for k in range(34)) if v < 3.0]
    assert max(pas) < saut / 2.5, (max(pas), saut)                      # aucune image ne fait le saut
    assert pas[0] < saut / 10 and pas[-1] < saut / 10, (pas[0], pas[-1], saut)   # départ et arrivée en douceur

    def pic_troisieme(n: float) -> int:
        """Opacité de pointe autour de la place de la 3e étoile (t = 0 : la 1re en haut, écart 360/n)."""
        s = render(agents=n, t=0.0)
        c, r = SIZE / 2, SIZE / 2 * bp.DISC
        a = math.radians(-90 + 2 * 360 / n)
        x, y = c + bp.AGENTS_ORBITE * r * math.cos(a), c + bp.AGENTS_ORBITE * r * math.sin(a)
        return max(_alpha(s, x + dx, y + dy) for dx in range(-5, 6) for dy in range(-5, 6))

    pics = [pic_troisieme(2.0 + k / 4) for k in (1, 2, 3, 4)]
    assert pics == sorted(pics) and pics[0] < pics[-1] * 0.6, pics      # elle s'allume peu à peu


@pytest.mark.parametrize("mode", ["dark", "light"])
@pytest.mark.parametrize("accent", ["bleu", "graphite", "vert", "orange"])
def test_teinte_discrete_distincte_de_l_ecoute(mode, accent):
    """Teinte des étoiles d'agents : proche de l'encre secondaire, loin de l'accent vif de l'écoute
    (sauf l'accent graphite, déjà gris), lisible : écart de luminance avec le fond du mode."""
    lk = bp.look(mode, accent)
    acc = lk.states["listening"]
    col = bp._agents_color(lk, lk.states["sleeping"])
    dist = max(abs(col[i] - acc[i]) for i in range(3))
    if accent != "graphite":
        assert dist > 0.15, (mode, accent, col, acc)
    assert max(abs(col[i] - lk.ink_2[i]) for i in range(3)) < 0.35
    lum = 0.2126 * col[0] + 0.7152 * col[1] + 0.0722 * col[2]
    assert (lum > 0.40) if mode == "dark" else (lum < 0.50), (mode, accent, lum)


def test_bulle_compacte():
    assert len(etoiles_en_orbite(render(agents=3, size=52), size=52, seuil=100)) == 3


def test_rendu_rapide_avec_six_agents():
    """Une image de 148 px en moins de 5 ms, comme les autres états (`test_bulle_theme.test_rendu_rapide`)."""
    lk = bp.look("dark", "bleu")
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE, SIZE)
    times = []
    for i in range(40):
        cr = cairo.Context(surf)
        t0 = time.perf_counter()
        bp.draw(cr, SIZE, SIZE, layers=[("sleeping", 1.0)], t=i * 0.066, level=0.0, lk=lk, agents=6)
        surf.flush()
        times.append(time.perf_counter() - t0)
    assert statistics.median(times) < 0.005, f"{statistics.median(times) * 1000:.2f} ms"


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_images_du_repos(mode, tmp_path):
    """Repos avec 0, 1, 3 et 6 agents, en PNG (`ASTREL_BULLE_PNG=dossier` pour les garder)."""
    out = Path(os.environ.get("ASTREL_BULLE_PNG", tmp_path))
    out.mkdir(parents=True, exist_ok=True)
    vus = set()
    for n in (0, 1, 3, 6):
        s = render(mode=mode, agents=n)
        s.write_to_png(str(out / f"bulle-premium-{mode}-repos-{n}-agents.png"))
        vus.add(bytes(s.get_data()))
    assert len(vus) == 4


# ------------------------------------------------------------------ vraie bulle GTK (Xvfb)

CODE_GTK = r"""
import json, tempfile, time
from pathlib import Path
from astrel import config
tmp = Path(tempfile.mkdtemp(prefix="bulle-agents-"))
config.ASTREL_SETTINGS_FILE = tmp / "settings.json"
config.HUD_TOKEN_FILE = tmp / "hud-token"          # jamais le vrai jeton, jamais le vrai HUD
config.HUD_TOKEN_FILE.write_text("x" * 40)
config.HUD_PORT = 1
config.ASTREL_SETTINGS_FILE.write_text(json.dumps({"values": {"theme_mode": "dark"}}))
import cairo
from astrel import bubble as b
b.Feed._from_file = lambda self: None              # jamais le vrai runtime/state.json
bub = b.Bubble()
f = bub.feed
r = {}
# flux : `hello` (sticky), tâches qui changent ; même compte que la bulle Qt
f._apply({"type": "tasks", "items": [{"kind": "agent"}, {"kind": "local_bash"}, {"kind": "agent"}], "done": []})
r["deux"] = f.agents
f._apply({"type": "tasks", "items": [{"kind": "agent"}], "done": [{"kind": "agent"}]})
r["une"] = f.agents
f._apply({"type": "tasks", "items": [], "done": []})
r["zero"] = f.agents
# cadence : repos stable, puis 3 agents (fondu), puis 3 agents stables
f.state = "sleeping"
dessins = [0]
bub._area.queue_draw = lambda: dessins.__setitem__(0, dessins[0] + 1)
def images(n):
    dessins[0] = 0
    for _ in range(n): bub._tick()
    return dessins[0]
for _ in range(120): bub._tick()                   # thème lu, fondu off -> repos fini
r["repos_0"] = images(60)
f.agents = 3
r["fondu"] = images(120)                           # 3 étoiles x 0,03 par image : 100 images de fondu
r["etoiles"] = bub._agents
r["repos_3"] = images(60)
f.agents = 0
r["depart"] = images(120)
r["fin"] = bub._agents
r["repos_fin"] = images(60)
# dessin : 3 agents au repos, image différente ; à l'écoute, identique avec ou sans agents
class Area:
    def get_allocated_width(self): return b.SIZE
    def get_allocated_height(self): return b.SIZE
time.monotonic = lambda: bub._t0
def img(n):
    bub._agents = n
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, b.SIZE, b.SIZE)
    bub._on_draw(Area(), cairo.Context(s)); s.flush()
    return bytes(s.get_data())
r["repos_change"] = img(0.0) != img(3.0)
bub._layers = {"listening": 1.0}
r["ecoute_pareille"] = img(0.0) == img(3.0)
print(json.dumps(r))
"""


def test_bulle_gtk_compte_et_cadence():
    """Vraie `Bubble` GTK sous Xvfb : compte des agents depuis `tasks` ; au repos stable 15 images/s
    (1 sur 4) avec ou sans agents, 60 pendant le fondu d'une étoile ; dessin au repos seulement."""
    pytest.importorskip("gi")
    if not sys.platform.startswith("linux") or not shutil.which("xvfb-run"):
        pytest.skip("bulle GTK : Linux avec xvfb-run")
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "GDK_SCALE", "ASTREL_THEME")}
    env["XDG_CURRENT_DESKTOP"] = ""
    env["PYTHONPATH"] = str(ROOT)
    out = subprocess.run(["xvfb-run", "-a", sys.executable, "-c", CODE_GTK], cwd=str(ROOT), env=env,
                         capture_output=True, text=True, timeout=90)
    assert out.returncode == 0, out.stderr[-2000:]
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert (r["deux"], r["une"], r["zero"]) == (2, 1, 0)
    assert r["repos_0"] == 15                      # inchangé : 15 images/s au repos sans agent
    assert r["fondu"] >= 100 and r["etoiles"] == 3.0  # fondu des trois étoiles à 60 images/s, puis fini
    assert r["repos_3"] == 15                      # orbite lente : 15 images/s suffisent
    assert r["depart"] >= 100 and r["fin"] == 0.0 and r["repos_fin"] == 15
    assert r["repos_change"] and r["ecoute_pareille"]


# ------------------------------------------------------------------ bulle Qt (là où PySide6 est installé)

def test_bulle_qt_passe_les_agents_au_dessin(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from astrel import bulle_qt
    lk = bp.look("dark", "bleu")
    a = bulle_qt.peindre(SIZE, SIZE, 1.0, [("sleeping", 1.0)], 0.3, 0.0, lk)
    b = bulle_qt.peindre(SIZE, SIZE, 1.0, [("sleeping", 1.0)], 0.3, 0.0, lk, agents=3)
    assert a != b
    src = (ROOT / "astrel" / "bulle_qt.py").read_text(encoding="utf-8")
    assert "bubble_premium.agents_vers(self._agents, self.flux.agents)" in src and "agents=self._agents" in src
