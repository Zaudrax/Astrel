"""Thème Premium (21/10, plan P17 ; demande de l'utilisateur : « changer le thème de l'app Astrel ainsi
que sa bulle entièrement via les réglages, un thème premium simple et sobre »). 25/10 (plan P35,
demande de l'utilisateur) : Classique abandonné, Premium SEUL ; palette Classique gardée dans
themes.json (non sélectionnable : icônes) et dans le :root du haut de styles.css (repli).

- config/themes.json = liste unique : mêmes modes et accents que astrel/theme.py,
  astrel/settings.py et hud/src/apparence.js ; mêmes couleurs que hud/styles.css (Classique :
  le :root d'origine ; Premium : blocs data-theme/data-mode/data-accent) et que hud/src/theme.js ;
- jetons complets (chaque couleur du :root d'origine redéfinie en Premium clair ET sombre) ;
- contraste WCAG : texte ≥ 4,5:1, anneaux et champs ≥ 3:1 ;
- mouvement : jamais `transition: all`, bloc prefers-reduced-motion, animations Premium gardées ;
- Total (orange) et levée du garde-fou (rouge) ne dépendent pas du thème ;
- astrel/theme.py : relu à chaud, clé `theme` et ASTREL_THEME sans effet (P35), CSS GTK ASCII.
"""
import json
import re
import shutil
import subprocess

import pytest

from astrel import config, settings, theme
from astrel.settings import reglages

ROOT = config.ROOT
CSS = (ROOT / "hud" / "styles.css").read_text(encoding="utf-8")
DATA = json.loads((ROOT / "config" / "themes.json").read_text(encoding="utf-8"))
PREMIUM = CSS[CSS.index("/* === P17 thèmes : début === */"):CSS.index("/* === P17 thèmes : fin === */")]
TOKENS = CSS[CSS.index("/* === P17 jetons : début === */"):CSS.index("/* === P17 jetons : fin === */")]


def block(selector: str, text: str = CSS) -> dict[str, str]:
    """Déclarations `--x: valeur` des blocs (en début de ligne) dont le sélecteur est exactement
    `selector`, dans l'ordre du fichier (le dernier l'emporte, comme la cascade à spécificité égale).
    P35 : `:is(:root, .theme-preview)` devenu `:root`, le bloc des couleurs et celui des bulles
    (`--bubble-user`) ont le même sélecteur."""
    head = selector + " {"
    starts = [m.start() for m in re.finditer(re.escape(head), text)
              if m.start() == 0 or text[m.start() - 1] == "\n"]
    assert starts, selector
    out: dict[str, str] = {}
    for i in starts:
        body = text[i + len(head):text.index("}", i)]
        out.update({k: v.strip() for k, v in re.findall(r"(--[\w-]+):\s*([^;]+);", body)})
    return out


def root_block() -> dict[str, str]:
    return {k: v.strip() for k, v in re.findall(r"(--[\w-]+):\s*([^;]+);", CSS[:CSS.index("}")])}


def mode_block(mode: str) -> dict[str, str]:
    return block(f':root[data-theme="premium"][data-mode="{mode}"]', PREMIUM)


def hexrgb(color: str) -> tuple[int, int, int]:
    c = color.strip().lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def triple(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in text.split(","))


def lum(rgb) -> float:
    def f(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a, b) -> float:
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def over(fg, alpha, bg):
    return tuple(round(f * alpha + b * (1 - alpha)) for f, b in zip(fg, bg))


# ------------------------------------------------------------------ liste unique

def test_liste_unique_des_themes():
    # 25/10 (plan P35) : Premium seul sélectionnable ; la palette Classique reste (icônes, tools/logo.py)
    selectable = tuple(k for k, v in DATA["themes"].items() if v.get("selectable", True))
    assert selectable == theme.THEMES == ("premium",)
    assert DATA["themes"]["classique"]["selectable"] is False and "palettes" in DATA["themes"]["classique"]
    assert "theme" not in settings.ITEMS and not hasattr(settings, "THEMES")
    assert tuple(DATA["modes"]) == theme.MODES == tuple(v for v, _ in settings.THEME_MODES)
    assert tuple(DATA["themes"]["premium"]["accents"]) == theme.ACCENTS == tuple(v for v, _ in settings.THEME_ACCENTS)
    assert DATA["default"] == theme.DEFAULT == {"theme": "premium", "mode": "auto", "accent": "bleu"}
    js = (ROOT / "hud" / "src" / "apparence.js").read_text(encoding="utf-8")
    assert "const THEMES" not in js   # P35 : plus de liste de thèmes dans le HUD (lot 1)
    assert 'const MODES = ["auto", "light", "dark"];' in js
    assert 'const ACCENTS = ["bleu", "graphite", "vert", "orange"];' in js
    boot = (ROOT / "hud" / "theme-boot.js").read_text(encoding="utf-8")
    assert 'var accents = ["bleu", "graphite", "vert", "orange"];' in boot
    # le HUD lit la MÊME liste au build (pas de copie)
    assert 'from "../../config/themes.json"' in (ROOT / "hud" / "src" / "theme.js").read_text(encoding="utf-8")


def test_reglages_apparence():
    d = reglages.describe()
    g = next(x for x in d["groups"] if x["id"] == "apparence")
    found = {i["key"]: i for i in g["items"]}
    # affichage : 22/10, plan P23 ; assistant_name (nom affiché) : 23/10, plan P31
    # 25/10 (plan P35) : plus de choix du thème (Premium seul)
    assert list(found) == ["theme_mode", "theme_accent", "affichage", "assistant_name"]
    assert all(i["applies"] == "hot" for i in found.values())
    assert all(i["kind"] == "choice" for k, i in found.items() if k != "assistant_name")
    assert found["assistant_name"]["kind"] == "text" and found["assistant_name"]["value"] == "Astrel"
    assert found["affichage"]["value"] == "avance"  # défaut : Avancé (l'écran d'avant) ; Simple à l'installation
    assert [c["value"] for c in found["affichage"]["choices"]] == ["simple", "avance"]
    assert found["theme_accent"]["display"] == "swatches"
    assert found["theme_mode"]["value"] == "auto" and found["theme_accent"]["value"] == "bleu"
    # l'aide ne parle plus d'un autre thème (fiches P35-2 et 3)
    assert not any("Classique" in i["help"] or "Premium seulement" in i["help"] for i in found.values())


# ------------------------------------------------------------------ CSS = themes.json

def test_classique_egale_le_root_d_origine():
    """Le :root du haut (repli, plus affiché depuis P35) garde la palette Classique de themes.json."""
    root, tok = root_block(), block(":root", TOKENS)
    pal = DATA["themes"]["classique"]["palettes"]["dark"]
    assert root["--bg"] == pal["bg"] and root["--ink"] == pal["ink"] and root["--muted"] == pal["ink_2"]
    assert root["--cyan"] == pal["accent"] and root["--green"] == pal["ok"] and root["--amber"] == pal["warn"]
    assert root["--red"] == pal["danger"] and root["--violet"] == pal["violet"]
    assert tok["--rgb-glass"] == pal["glass"] and tok["--rgb-line"] == pal["line"] and tok["--rgb-hi"] == pal["hi"]
    assert triple(tok["--rgb-accent"]) == hexrgb(pal["accent"]) and triple(tok["--rgb-ok"]) == hexrgb(pal["ok"])
    assert triple(tok["--rgb-warn"]) == hexrgb(pal["warn"]) and triple(tok["--rgb-danger"]) == hexrgb(pal["danger"])


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_premium_css_egale_themes_json(mode):
    b, pal = mode_block(mode), DATA["themes"]["premium"]["palettes"][mode]
    pairs = {"--bg": "bg", "--ink": "ink", "--muted": "ink_2", "--ink-title": "ink_title", "--on-fill": "on_fill",
             "--field-border": "field_border", "--green": "ok", "--amber": "warn", "--red": "danger", "--violet": "violet",
             "--rgb-glass": "glass", "--rgb-line": "line", "--rgb-hi": "hi"}
    for var, key in pairs.items():
        assert b[var].lower() == pal[key].lower(), (mode, var)
    for var, key in (("--rgb-ok", "ok"), ("--rgb-warn", "warn"), ("--rgb-danger", "danger"), ("--rgb-violet", "violet")):
        assert triple(b[var]) == hexrgb(pal[key]), (mode, var)


@pytest.mark.parametrize("mode", ["dark", "light"])
@pytest.mark.parametrize("accent", ["bleu", "graphite", "vert", "orange"])
def test_accents_css_egale_themes_json(mode, accent):
    a = DATA["themes"]["premium"]["accents"][accent]
    sel = f':root[data-theme="premium"][data-mode="{mode}"]'
    if accent != "bleu":
        sel += f'[data-accent="{accent}"]'
    b = block(sel, PREMIUM[PREMIUM.index("/* accents"):])
    assert b["--accent"] == a[mode] and b["--accent-ink"] == a[f"{mode}_ink"] and b["--cyan"] == a[mode]
    assert triple(b["--rgb-accent"]) == hexrgb(a[mode])


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_jetons_complets(mode):
    """Chaque COULEUR du Classique (jeton du lot 1 ou :root d'origine) a sa valeur Premium."""
    colour = re.compile(r"^(#[0-9a-fA-F]{3,8}|rgba?\(|\d+, \d+, \d+$)")
    classic = {**{k: v for k, v in root_block().items() if colour.match(v)},
               **{k: v for k, v in block(":root", TOKENS).items() if colour.match(v)}}
    premium = {**mode_block(mode), **block(f':root[data-theme="premium"][data-mode="{mode}"]',
                                           PREMIUM[PREMIUM.index("/* accents"):])}
    missing = sorted(set(classic) - set(premium))
    assert missing == [], f"{mode} : jetons sans valeur Premium {missing}"
    assert {"--shadow-1", "--panel", "--panel-border", "--accent"} <= set(premium)  # dérivés, recalculés


def test_theme_js_egale_themes_json():
    node = shutil.which("node")
    if not node:
        pytest.skip("node absent")
    code = ("import('./hud/src/theme.js').then(m => console.log(JSON.stringify({state: m.STATE, "
            "dark: m.palette('premium', 'dark', 'vert'), light: m.premiumStateColors('light', 'orange')})))")
    out = subprocess.run([node, "-e", code], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    for name, (color, accent) in DATA["themes"]["classique"]["states"].items():
        assert got["state"][name]["color"] == color and got["state"][name]["accent"] == accent
    assert got["dark"] == theme.palette("premium", "dark", "vert")
    assert got["light"]["asking"] == theme.palette("premium", "light", "orange")["warn"]
    assert got["light"]["sleeping"] == "#c93400"


# ------------------------------------------------------------------ contraste (WCAG)

@pytest.mark.parametrize("mode", ["dark", "light"])
def test_contraste_premium(mode):
    pal = DATA["themes"]["premium"]["palettes"][mode]
    bg = hexrgb(pal["bg"])
    surface = over(triple(pal["glass"]), 0.72, bg)       # panneau de verre posé sur le fond
    for key in ("ink", "ink_2", "ink_title", "ok", "warn", "danger", "violet"):
        for under in (bg, surface):
            assert ratio(hexrgb(pal[key]), under) >= 4.5, (mode, key)
    # texte secondaire sur un remplissage de bouton (voile de 8 % en sombre, 5 % en clair)
    fill = over(triple(pal["hi"]), 0.08 if mode == "dark" else 0.05, surface)
    assert ratio(hexrgb(pal["ink_2"]), fill) >= 4.5, mode
    assert ratio(hexrgb(pal["field_border"]), surface) >= 3, mode   # bord des champs
    for state_fill in ("ok", "warn", "danger"):                      # texte posé sur un aplat d'état
        assert ratio(hexrgb(pal["on_fill"]), hexrgb(pal[state_fill])) >= 4.5, (mode, state_fill)
    for name, a in DATA["themes"]["premium"]["accents"].items():
        assert ratio(hexrgb(a[f"{mode}_ink"]), surface) >= 4.5, (mode, name)   # liens, texte d'accent
        assert ratio(hexrgb(a[mode]), surface) >= 3, (mode, name)              # anneaux, focus
        if mode == "light":                                                    # bouton plein, texte blanc
            assert ratio((255, 255, 255), hexrgb(a[mode])) >= 4.5, name
        else:                                                                  # bouton teinté, texte clair
            assert ratio(hexrgb(pal["ink"]), over(hexrgb(a["dark"]), 0.24, surface)) >= 4.5, name


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_contraste_piece_jointe_sur_la_bulle_de_utilisateur(mode):
    """23/10 (demande : « la couleur du texte de la pièce jointe est invisible avec la bulle bleue, à mettre en
    clair comme l'heure du message ») : la pastille `.att-chip` d'un message de l'utilisateur prend la couleur du texte
    de la bulle ; nom et trombone ≥ 4,5:1, contour (70 %) ≥ 3:1, pour les 4 accents. Classique : non touché."""
    rule = ':root[data-theme="premium"] .msg.user .att-chip { color: inherit; ' \
           'border-color: color-mix(in srgb, currentColor 70%, transparent); }'
    assert rule in PREMIUM
    assert ".msg.user .att-chip" not in CSS[:CSS.index("/* === P17 thèmes : début === */")]  # Classique inchangé
    pal = DATA["themes"]["premium"]["palettes"][mode]
    surface = over(triple(pal["glass"]), 0.72, hexrgb(pal["bg"]))
    bubble = block(f':root[data-theme="premium"][data-mode="{mode}"]', PREMIUM)
    ink = hexrgb(mode_block(mode)["--ink-hi"] if bubble["--bubble-user-ink"] == "var(--ink-hi)" else bubble["--bubble-user-ink"])
    for name, a in DATA["themes"]["premium"]["accents"].items():
        if bubble["--bubble-user"] == "var(--accent)":
            under = hexrgb(a[mode])
        else:
            assert bubble["--bubble-user"] == "rgba(var(--rgb-accent), 0.24)", mode
            under = over(hexrgb(a[mode]), 0.24, surface)
        assert ratio(ink, under) >= 4.5, (mode, name, "texte")
        assert ratio(over(ink, 0.70, under), under) >= 3, (mode, name, "contour")


def test_total_et_levee_ne_dependent_pas_du_theme():
    assert DATA["semantics"]["total"] == "#ff8c00" == theme.palette("premium", "light")["total"]
    assert "rgb(255, 140, 0)" in CSS[:CSS.index("/* === P17 thèmes")]  # Total reste orange (l'utilisateur, 21/10)
    for mode in ("dark", "light"):
        pal = theme.palette("premium", mode)
        assert pal["levee"] == pal["danger"]               # levée : rouge du thème, la plus alarmante
        r, g, b = hexrgb(pal["danger"])
        assert r > 200 and g < 120 and b < 120, mode
    # aucune règle Premium ne repeint les bandeaux Total / levée ni le cadre
    for sel in ("#total-banner", "#lift-banner", "#total-frame", "#lift-frame", ".chip.toggle.total", ".chip.lift"):
        assert sel not in PREMIUM, sel


# ------------------------------------------------------------------ mouvement

def test_mouvement_sobre_et_reduit():
    assert not re.search(r"transition(-property)?\s*:\s*all\b", CSS)
    assert "@media (prefers-reduced-motion: reduce)" in PREMIUM
    # dans le bloc Premium, toute animation (déplacement, échelle) est sous une requête de mouvement
    depth, media, outside = 0, [], []
    for line in PREMIUM.splitlines():
        if line.lstrip().startswith("@media (prefers-reduced-motion"):
            media.append(depth)
        if re.search(r"\banimation:|\bscale:", line) and not media:
            outside.append(line.strip())
        depth += line.count("{") - line.count("}")
        while media and depth <= media[-1]:
            media.pop()
    assert outside == [], outside
    assert "cubic-bezier(0.2, 0, 0, 1)" in TOKENS and "--press: 0.96" in TOKENS


def test_police_embarquee_sans_toucher_au_classique():
    font = ROOT / "hud" / "assets" / "fonts" / "InterVariable.woff2"
    assert font.is_file() and font.stat().st_size < 400_000
    assert "SIL Open Font License" in (font.parent / "OFL.txt").read_text(encoding="utf-8")
    # nom de famille à part : le Classique (« Inter », absente ici) ne la prend jamais
    assert 'font-family: "Inter Astrel"' in PREMIUM and '"Inter Astrel"' not in CSS[:CSS.index("/* === P17 thèmes")]


# ------------------------------------------------------------------ astrel/theme.py

def test_current_relu_a_chaud(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    monkeypatch.setattr(config, "ASTREL_SETTINGS_FILE", f)
    monkeypatch.delenv("ASTREL_THEME", raising=False)
    assert theme.current() == {"theme": "premium", "mode": "auto", "accent": "bleu"}
    f.write_text(json.dumps({"values": {"theme": "classique", "theme_mode": "light", "theme_accent": "vert"}}))
    assert theme.current() == {"theme": "premium", "mode": "light", "accent": "vert"}   # P35 : `theme` ignoré
    f.write_text(json.dumps({"values": {"theme_mode": "nuit", "theme_accent": "rose"}}))
    assert theme.current() == theme.DEFAULT                     # valeurs inconnues : défauts
    f.write_text("{pas du json")
    assert theme.current() == theme.DEFAULT
    monkeypatch.setenv("ASTREL_THEME", "classique")
    assert theme.current()["theme"] == "premium"                 # P35 : la variable n'a plus d'effet


def test_reglage_ecrit_puis_lu_par_la_bulle(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ASTREL_SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.delenv("ASTREL_THEME", raising=False)
    reglages.reset()
    try:
        assert reglages.set("theme_mode", "dark") == "dark"
        assert reglages.set("theme_accent", "vert") == "vert"
        assert theme.current() == {"theme": "premium", "mode": "dark", "accent": "vert"}
        with pytest.raises(settings.SettingError):
            reglages.set("theme", "premium")                     # P35 : plus de réglage `theme`
        reglages.set("theme_mode", "auto")
        values = json.loads(config.ASTREL_SETTINGS_FILE.read_text())["values"]
        assert "theme_mode" not in values                        # défaut : non écrit
        assert values["theme"] == "premium"                      # compatibilité bulle d'avant P35
    finally:
        reglages.reset()


@pytest.mark.parametrize("env", ["premium", "classique", " CLASSIQUE ", "neon", ""])
def test_astrel_theme_sans_effet(env, tmp_path, monkeypatch):
    """25/10 (plan P35) : ASTREL_THEME ne grise plus rien et ne change plus le thème : Premium
    partout (bulle `theme.current`, HUD : plus de réglage `theme` dans l'évènement settings)."""
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"values": {"theme": "classique", "theme_mode": "light"}}))
    monkeypatch.setattr(config, "ASTREL_SETTINGS_FILE", f)
    monkeypatch.setenv("ASTREL_THEME", env)
    reglages.reset()
    try:
        assert theme.current() == {"theme": "premium", "mode": "light", "accent": "bleu"}
        keys = {i["key"] for g in reglages.describe()["groups"] for i in g["items"]}
        assert "theme" not in keys and "theme_mode" in keys
        assert not reglages.locked("theme_mode") and reglages.value("theme_mode") == "light"
    finally:
        reglages.reset()


def test_ancienne_cle_theme_ignoree_sans_avertissement(tmp_path, monkeypatch, caplog):
    """25/10 (plan P35) : `theme` (classique ou premium) reste dans le settings.json de l'utilisateur :
    ignorée à la lecture SANS avertissement (`settings.LEGACY_KEYS`), réécrite à « premium » à la
    prochaine écriture (`settings.LEGACY_WRITE`, bulle d'avant P35 encore lancée)."""
    f = tmp_path / "settings.json"
    monkeypatch.setattr(config, "ASTREL_SETTINGS_FILE", f)
    monkeypatch.delenv("ASTREL_THEME", raising=False)
    for old in ("classique", "premium", "NEON"):
        f.write_text(json.dumps({"version": 1, "values": {"theme": old, "theme_mode": "dark"}}))
        reglages.reset()
        caplog.clear()
        try:
            with caplog.at_level("WARNING", logger="astrel.settings"):
                assert reglages.value("theme_mode") == "dark"
            assert not [r for r in caplog.records if "theme" in r.getMessage()], caplog.text
            reglages.set("theme_accent", "orange")
            assert json.loads(f.read_text())["values"] == {"theme_mode": "dark", "theme_accent": "orange",
                                                            "theme": "premium"}
        finally:
            reglages.reset()


def test_majuscules_dans_settings_json_lues_pareil(tmp_path, monkeypatch):
    """« LIGHT » écrit à la main : la bulle le lisait en minuscules, la page Réglages le rejetait."""
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"values": {"theme_mode": " Light", "theme_accent": "VERT"}}))
    monkeypatch.setattr(config, "ASTREL_SETTINGS_FILE", f)
    monkeypatch.delenv("ASTREL_THEME", raising=False)
    reglages.reset()
    try:
        assert theme.current() == {"theme": "premium", "mode": "light", "accent": "vert"}
        assert {k: reglages.value(k) for k in ("theme_mode", "theme_accent")} == \
            {"theme_mode": "light", "theme_accent": "vert"}
        assert reglages.set("theme_mode", "DARK") == "dark"
        with pytest.raises(settings.SettingError):
            reglages.set("theme_accent", "ROSE")
        # les autres listes restent sensibles à la casse (voix, modèles, micros…)
        assert "theme_mode" in settings.LOWER_CHOICES and "audio.voice" not in settings.LOWER_CHOICES
        assert "theme" not in settings.LOWER_CHOICES
    finally:
        reglages.reset()


def test_settings_json_edite_a_la_main_relu_par_le_coeur(tmp_path, monkeypatch):
    """Relecture P17 : le cœur gardait le choix en mémoire ; settings.json édité à la main, la bulle
    passait en clair et le HUD (settings_get à la connexion) recevait encore l'ancien mode."""
    f = tmp_path / "settings.json"
    monkeypatch.setattr(config, "ASTREL_SETTINGS_FILE", f)
    monkeypatch.delenv("ASTREL_THEME", raising=False)
    reglages.reset()
    try:
        assert reglages.set("theme_mode", "dark") == "dark"
        f.write_text(json.dumps({"version": 1, "values": {"theme": "classique", "theme_mode": "light", "extra": 1}}))
        assert reglages.value("theme_mode") == "light" == theme.current()["mode"]
        assert theme.current()["theme"] == "premium"
        # une écriture du cœur ensuite garde ce qui a été édité à la main (clé inconnue oubliée,
        # ancienne clé `theme` remise à « premium »)
        reglages.set("theme_accent", "vert")
        assert json.loads(f.read_text())["values"] == {"theme_mode": "light", "theme_accent": "vert", "theme": "premium"}
    finally:
        reglages.reset()


def test_mode_resolu():
    assert theme.resolved_mode({"mode": "light"}) == "light"
    assert theme.resolved_mode({"theme": "classique", "mode": "light"}) == "light"  # P35 : plus de Classique
    assert theme.resolved_mode({"theme": "premium", "mode": "dark"}) == "dark"
    assert theme.resolved_mode({"theme": "premium", "mode": "auto"}, system_dark=False) == "light"
    assert theme.resolved_mode({"theme": "premium", "mode": "auto"}, system_dark=True) == "dark"
    assert theme.resolved_mode({"theme": "premium", "mode": "auto"}, system_dark=None) == "dark"


def test_css_gtk():
    assert isinstance(theme.css_gtk(), bytes)                   # P35 : plus de `None` (Classique)
    for mode in ("dark", "light"):
        css = theme.css_gtk(mode, "bleu")
        css.decode("ascii")  # le fournisseur CSS de GTK reçoit des octets ASCII
        for cls in (b".astrel-card", b".astrel-chat", b"button.yes", b"button.no", b"entry"):
            assert cls in css
        assert b"box-shadow: 0 0" not in css  # pas de lueur


def test_palette_classique_gardee_pour_les_icones():
    """P35 : la palette Classique n'est plus choisie mais reste lisible (tools/logo.py) ; un thème
    inconnu donne Premium."""
    assert theme.palette("classique", "dark")["accent"] == DATA["themes"]["classique"]["palettes"]["dark"]["accent"]
    assert theme.palette("neon", "dark") == theme.palette("premium", "dark")
    assert theme.palette() == theme.palette("premium", "dark", "bleu")


def test_couleurs_des_etats_toujours_premium():
    """P35 : `state_colors` (bulle, `bubble_premium.py`) n'a plus de branche Classique : un autre
    thème demandé donne les couleurs Premium, une seule teinte par état, chaque état couvert."""
    for mode in ("dark", "light"):
        for accent in ("bleu", "orange"):
            prem = theme.state_colors("premium", mode, accent)
            assert theme.state_colors("classique", mode, accent) == prem
            assert theme.state_colors("neon", mode, accent) == prem
            assert set(prem) == set(DATA["themes"]["premium"]["states"])
            pal = theme.palette("premium", mode, accent)
            for etat, ref in DATA["themes"]["premium"]["states"].items():
                assert prem[etat][0] == prem[etat][1] == theme.rgb(pal[ref])


def test_hud_apparence_en_node():
    """apparence.js (attributs de <html>, cache, mode automatique) et orbe Premium, en Node."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node absent")
    r = subprocess.run([node, "--test", str(ROOT / "hud" / "tests" / "apparence.test.mjs")], cwd=ROOT,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
