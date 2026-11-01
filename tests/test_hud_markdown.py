"""HUD (15/10) : réponses rendues en markdown SÛR, boîtes de dialogue maison.

Le rendu lui-même est testé en JavaScript (hud/tests/markdown.test.mjs, lancé
ici par Node) ; ici, ce qui doit rester vrai dans les sources du HUD."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from astrel import config
from astrel.channels import Channel, Channels

HUD = Path(__file__).resolve().parent.parent / "hud"
SRC = HUD / "src"


@pytest.mark.skipif(not shutil.which("node"), reason="Node absent")
def test_rendu_markdown_node():
    r = subprocess.run(["node", "--test", str(HUD / "tests" / "markdown.test.mjs")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert re.search(r"^# fail 0$", r.stdout, re.M)


def test_aucune_insertion_de_html_dans_le_hud():
    """Le texte affiché peut venir d'une page web ou d'un mail : jamais d'innerHTML."""
    for f in SRC.glob("*.js"):
        code = f.read_text(encoding="utf-8")
        assert not re.search(r"\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML|document\.write", code), f.name


def test_plus_aucune_boite_native_du_navigateur():
    """confirm()/prompt()/alert() remplacés par la boîte du HUD (titre « 127.0.0.1 indique »)."""
    for f in SRC.glob("*.js"):
        code = re.sub(r"//.*", "", f.read_text(encoding="utf-8"))
        assert not re.search(r"(?<![\w.])(window\.)?(confirm|prompt|alert)\s*\(", code), f.name


def test_le_texte_dit_reste_le_texte_brut():
    """« 🔊 Lire » envoie le texte brut (m.raw), pas le texte rendu (sans marques ni retours)."""
    code = (SRC / "main.js").read_text(encoding="utf-8")
    assert 'fallback: m.raw' in code
    assert "fallback: m.body.textContent" not in code


def test_canal_public_expose_ses_mots_cles(tmp_path, monkeypatch):
    """La fenêtre « Modifier » du HUD préremplit les mots-clés du canal, tels
    qu'écrits (sans les variantes du nom ajoutées pour reconnaître le canal)."""
    monkeypatch.setattr(config, "CHANNELS_FILE", tmp_path / "channels.json")
    monkeypatch.setattr(config, "CHANNEL_FILE", tmp_path / "channel.txt")
    ch = Channels()
    ch.create("Essai Rendu", keywords="bourse, Démo")
    pub = {c["id"]: c for c in ch.describe()["channels"]}["essai-rendu"]
    assert pub["keywords"] == ["bourse", "Démo"]
    assert "keywords" not in Channel("g", "Général", "#fff").public()
