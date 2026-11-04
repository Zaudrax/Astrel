"""Liens du fil du HUD (04/11, demande : « les liens ne sont pas ouvrables comme c'est le cas pour ChatGPT avec un
logo indiquant que c'est un lien, on est obligé de copier-coller ») et petits détails du fil et de la barre.

Le rendu lui-même est testé en JavaScript (hud/tests/markdown.test.mjs, lancé par test_hud_markdown.py) et en
Chrome headless (tools/hud_check_rendu.py, `check_liens_et_barre`) ; ici, la commande du cœur `open_link` (lien
ouvert dans le navigateur du SYSTÈME, réservée au HUD) et ce qui doit rester vrai dans les sources du HUD."""
import json
import re
from pathlib import Path

import pytest

from astrel import config
from astrel.coeur.medias import lien_ouvrable

HUD = Path(__file__).resolve().parent.parent / "hud"
SRC = HUD / "src"


@pytest.fixture
def astrel(monkeypatch):
    from test_pump import make_astrel

    j, _said = make_astrel(monkeypatch)
    return j


@pytest.fixture
def ouverts(monkeypatch):
    from astrel.tools import desktop

    out: list[str] = []
    monkeypatch.setattr(desktop, "open_url", lambda url: out.append(url) or f"Ouvert : {url}")
    return out


def test_lien_ouvrable_https_seulement():
    assert lien_ouvrable("https://github.com/Zaudrax/Astrel") == "https://github.com/Zaudrax/Astrel"
    assert lien_ouvrable("  https://x.fr/a?b=1#c  ") == "https://x.fr/a?b=1#c"
    for refuse in ("http://x.fr", "javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "https://",
                   "https:///chemin", "https://x.fr/a b", "https://x.fr/\x07", "https://x.fr/a\nb", "mailto:a@b.fr",
                   "https://banque.fr@pirate.example/", "https://u:p@x.fr/", "https://[mauvais/",
                   "https://x.fr/" + "a" * 2000, "", None, 5, ["https://x.fr"]):
        assert lien_ouvrable(refuse) is None, refuse


def test_open_link_ouvre_dans_le_navigateur_du_systeme(astrel, ouverts):
    r = astrel.on_command({"cmd": "open_link", "url": "https://github.com/Zaudrax/Astrel", "_via": "hud",
                           "_from_claude": False})
    assert r == {"ok": True}
    assert ouverts == ["https://github.com/Zaudrax/Astrel"]


def test_open_link_reserve_au_hud_de_utilisateur(astrel, ouverts):
    """Venant de Claude (son Bash, `--send`, une page qu'il aurait ouverte) ou du canal de contrôle : refusé.
    Claude a son propre outil `open_url`, jugé par le garde-fou."""
    for cmd in ({"_via": "hud", "_from_claude": True}, {"_via": "socket", "_from_claude": False},
                {"_from_claude": False}, {"_via": "api"}):
        r = astrel.on_command({"cmd": "open_link", "url": "https://x.fr/", **cmd})
        assert r["ok"] is False and "réservé au HUD" in r["error"], cmd
    assert ouverts == []


def test_open_link_refuse_ce_qui_n_est_pas_https(astrel, ouverts):
    for url in ("http://x.fr", "javascript:alert(1)", "https://a@b.fr", ""):
        r = astrel.on_command({"cmd": "open_link", "url": url, "_via": "hud", "_from_claude": False})
        assert r == {"ok": False, "error": "lien refusé : seules les adresses https:// s'ouvrent"}, url
    assert ouverts == []


def test_open_link_panne_du_navigateur_dite_sans_planter(astrel, monkeypatch):
    from astrel.tools import desktop

    def panne(url):
        raise RuntimeError("xdg-open absent")

    monkeypatch.setattr(desktop, "open_url", panne)
    r = astrel.on_command({"cmd": "open_link", "url": "https://x.fr/", "_via": "hud", "_from_claude": False})
    assert r == {"ok": False, "error": "xdg-open absent"}


def test_erreurs_de_open_link_traduites():
    cat = json.loads((config.ROOT / "astrel" / "locales" / "en.json").read_text(encoding="utf-8"))
    assert cat["lien refusé : seules les adresses https:// s'ouvrent"]
    assert cat["{name} est réservé au HUD de l'utilisateur"]


def test_le_hud_ouvre_les_liens_par_le_coeur_avec_repli_sur():
    """Un clic sur un lien passe par `open_link` (navigateur de l'utilisateur) ; repli : nouvel onglet SANS opener
    ni referrer ; l'adresse est revalidée (https) avant tout envoi."""
    code = (SRC / "main.js").read_text(encoding="utf-8")
    assert 'command("open_link", { url: href }' in code
    assert 'window.open(href, "_blank", "noopener,noreferrer")' in code
    assert 'const href = safeHref(a.getAttribute("href"));' in code
    assert 'document.addEventListener("click", linkClick)' in code and 'document.addEventListener("auxclick", linkClick)' in code


def test_message_de_utilisateur_rendu_sans_markdown_avec_liens():
    """Son message reste du texte brut (rien n'y est interprété), seules les adresses deviennent des liens ;
    Copier et ↑ partent de m.raw, jamais du rendu."""
    code = (SRC / "main.js").read_text(encoding="utf-8")
    assert 'if (m.who !== "astrel") { m.body.replaceChildren(linkifyText(m.raw)); return; }' in code
    assert 'if (who === "astrel" || who === "user")' in code
    assert "const copyRaw = (m) => copyText(m.raw" in code
    assert not re.search(r"copyText\([^)]*\.(textContent|innerText)", code)
    md = (SRC / "markdown.js").read_text(encoding="utf-8")
    assert 'icon("external-link", "md-link-ico", doc)' in md
    assert '"external-link":' in (SRC / "icons.js").read_text(encoding="utf-8")


def test_textes_des_petits_details_en_francais_et_en_anglais():
    langues = (SRC / "langues.js").read_text(encoding="utf-8")
    for key in ("md.link_opens", "link.opened", "msg.copy_user_title", "chat.jump_new"):
        assert langues.count(f'"{key}":') == 2, key  # FR et EN
    html = (HUD / "index.html").read_text(encoding="utf-8")
    assert 'id="ask" rows="1" aria-keyshortcuts="/"' in html


def test_clic_droit_sur_un_lien_laisse_le_menu_du_navigateur():
    """« Copier l'adresse du lien » : le menu « Déplacer vers un canal » ne prend pas le clic droit d'un lien."""
    code = (SRC / "main.js").read_text(encoding="utf-8")
    body = code[code.index("function moveMenu(m, e) {"):]
    body = body[:body.index("e.preventDefault();")]
    assert 'if (e.target?.closest?.("a.md-link")) return;' in body
