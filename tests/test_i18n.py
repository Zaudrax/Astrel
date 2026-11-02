"""Langue de l'interface : français (défaut) ou anglais (20/10, demande de l'utilisateur : « avoir une
traduction complète de l'application en anglais et avoir le choix de passer de anglais à français »).

- HUD : dictionnaires FR/EN (`hud/src/langues.js`), testés en Node (hud/tests/i18n.test.mjs) ;
- textes composés par le Python : catalogue `astrel/locales/en.json`, lu par `astrel/i18n.py` ET par
  `hud/src/i18n.js` (mêmes résultats, vérifié ici) ; inventaire `tools/i18n_inventaire.py` ;
- réglage `ui_language` (défaut français), mot de confirmation traduit, guide anglais ;
- le HUD rendu en anglais dans Chrome headless : `tools/hud_check_langue.py` (si Chrome est là).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from astrel import config, i18n

ROOT = Path(__file__).resolve().parent.parent
HUD = ROOT / "hud"
PH = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
FR_HINT = re.compile(
    r"[àâäçéèêëîïôöùûüœÀÂÇÉÈÊÎÔÙÛŒ]|\b(le|la|les|des|du|une|et|pour|avec|dans|sur|est|pas|tu|ton|ta|tes|aucun|"
    r"aucune|rien|ici|sans|quand|qui|que|je|il|ne|plus|tout)\b", re.I)


def looks_french(text: str) -> bool:
    return bool(FR_HINT.search(re.sub(r"«[^»]*»", " ", text)))


@pytest.mark.skipif(not shutil.which("node"), reason="Node absent")
def test_dictionnaires_du_hud_node():
    """(a) chaque clé fr a sa clé en ; pluriels, repli sur le français, clés utilisées présentes."""
    r = subprocess.run(["node", "--test", str(HUD / "tests" / "i18n.test.mjs")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert re.search(r"^# fail 0$", r.stdout, re.M)


def test_catalogue_valeurs_et_parties_variables():
    raw = json.loads(i18n.CATALOGUE.read_text(encoding="utf-8"))
    assert len(raw) > 800
    for fr, en in raw.items():
        if fr.startswith("_"):
            continue
        assert isinstance(en, str) and en.strip(), fr
        assert sorted(PH.findall(fr)) == sorted(PH.findall(en)), fr


def test_inventaire_python_entierement_traduit():
    """Chaque texte d'écran composé par le Python (notices, Réglages, erreurs, bulle) a son anglais."""
    sys.path.insert(0, str(ROOT / "tools"))
    from i18n_inventaire import inventaire

    inv = inventaire()
    assert len(inv) > 700
    missing = [f"{f}:{ln}: {t}" for f, ln, t in inv if not i18n.covered(t)]
    assert not missing, "\n".join(missing[:30])


def test_francais_par_defaut_et_bascule_a_chaud():
    from astrel.settings import reglages

    assert i18n.lang() == "fr" and not config.ASTREL_SETTINGS_FILE.exists()
    assert i18n.tr("Garde-fou levé.") == "Garde-fou levé."  # français : rien ne change
    assert reglages.value("ui_language") == "fr"
    assert reglages.set("ui_language", "en") == "en"
    assert i18n.lang() == "en"  # relu dans le fichier, sans relance
    assert i18n.tr("Garde-fou levé.") == "Guardrail lifted."
    assert i18n.tr("Texte inconnu du catalogue.") == "Texte inconnu du catalogue."  # repli
    reglages.set("ui_language", "fr")
    assert i18n.lang() == "fr" and "ui_language" not in json.loads(config.ASTREL_SETTINGS_FILE.read_text())["values"]


def test_variable_d_environnement_prioritaire(monkeypatch):
    monkeypatch.setenv("ASTREL_UI_LANGUAGE", "en")
    from astrel.settings import reglages

    assert i18n.lang() == "en" and reglages.locked("ui_language")


def test_modeles_parties_variables_et_morceaux():
    tr = lambda s: i18n.tr(s, "en")  # noqa: E731
    assert tr("Voix Edge revenue.") == "Edge voice is back."
    assert tr("Attention, une commande administrateur, une opération git et l'envoi d'un e-mail. Je continue ?") \
        == "Heads up: an admin command, a git operation and sending an email. Should I continue?"
    # morceaux connus « a · b » ; « a, b » seulement si TOUS sont connus
    assert tr("Contrôle · en cours…") == "Check · running…"
    assert tr("Bonjour, une commande administrateur") == "Bonjour, une commande administrateur"
    # pas de modèle trop vague : « avant de » ne devient pas « avant of »
    assert tr("Qwen 3.5, moyen, réfléchit avant de répondre") == "Qwen 3.5, medium, thinks before answering"
    assert tr("NVIDIA RTX, 8,0 Go de mémoire graphique") == "NVIDIA RTX, 8.0 GB of graphics memory"


SAMPLES = [
    "Garde-fou levé.", "Voix Edge revenue.", "Contrôle · en cours…", "Réglages appliqués : cerveau redémarré, "
    "conversation reprise.", "Attention, une publication git (push). Je continue ?", "Texte inconnu.",
    "Qwen 3.5, moyen, réfléchit avant de répondre", "NVIDIA RTX, 8,0 Go de mémoire graphique",
    "Ta phrase est partie avec 2 pièce(s) ; refusée(s), restée(s) dans la barre : trop gros",
    "Limite atteinte, reprise prévue à 17 h 40. Les réflexes qui réveillent le modèle attendent jusque-là ; rien",
    "  Garde-fou levé.  ", "une installation de paquets et l'envoi d'un e-mail",
]


@pytest.mark.skipif(not shutil.which("node"), reason="Node absent")
def test_python_et_hud_traduisent_pareil():
    """Le HUD (tx) et la bulle (tr) lisent le même catalogue avec la même règle."""
    script = ("import { tx, useLang } from " + json.dumps((HUD / "src" / "i18n.js").as_uri()) + ";"
              "useLang('en'); const s = JSON.parse(process.argv[1]);"
              "console.log(JSON.stringify(s.map(tx)));")
    r = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(SAMPLES)],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[-2000:]
    assert json.loads(r.stdout.strip().splitlines()[-1]) == [i18n.tr(s, "en") for s in SAMPLES]


def _visible_strings(d: dict):
    for g in d["groups"]:
        yield g["id"], g["title"]
        for it in g["items"]:
            for k in ("label", "help", "lock_reason", "unit"):
                yield it["key"], it.get(k) or ""
            for c in it.get("choices") or []:
                yield it["key"], c.get("label") or ""
            v = it.get("value")
            if isinstance(v, dict):
                for line in v.get("lines") or []:
                    yield it["key"], line.get("label") or ""
                    yield it["key"], line.get("text") or ""
                if isinstance(v.get("link"), dict):
                    yield it["key"], v["link"].get("label") or ""
            for a in it.get("actions") or []:
                for k in ("label", "reason", "confirm"):
                    yield it["key"], a.get(k) or ""
                if isinstance(a.get("input"), dict):
                    yield it["key"], a["input"].get("label") or ""


def test_page_reglages_decrite_en_anglais():
    """(b, côté cœur) toute la page Réglages telle que le HUD l'affiche en anglais : aucun texte
    français, hors noms propres et citations « … »."""
    from astrel.settings import reglages

    fixture = {"indisponible pendant les tests."}  # raison posée par tests/conftest.py, pas par Astrel
    left = sorted({(key, s, i18n.tr(s, "en")) for key, s in _visible_strings(reglages.describe())
                   if s and s not in fixture and looks_french(i18n.tr(s, "en"))})
    assert not left, "\n".join(f"{k} | {s} → {e}" for k, s, e in left[:20])


def test_mot_de_confirmation_traduit():
    """« lever » s'affiche « lift » en anglais : le cœur accepte les deux, rien d'autre."""
    from astrel.settings import reglages

    done = []
    reglages.bind("garde_fou_levee", view=lambda: {"value": {"lines": []}, "actions": [
        {"id": "lever", "label": "Lever le garde-fou", "danger": True, "word": "lever"}]},
        run=lambda action, cmd: done.append(action) or {"ok": True})
    assert reglages.action("garde_fou_levee", "lever", {"confirm": "autre"})["ok"] is False
    assert reglages.action("garde_fou_levee", "lever", {"confirm": "LIFT"})["ok"] is True
    assert reglages.action("garde_fou_levee", "lever", {"confirm": "lever"})["ok"] is True
    assert done == ["lever", "lever"]


def test_guide_anglais_memes_sujets(monkeypatch, tmp_path):
    from astrel import guide as guide_mod

    fr = guide_mod.parse((ROOT / "docs" / "GUIDE-DEMARRAGE.md").read_text(encoding="utf-8"))
    en = guide_mod.parse((ROOT / "docs" / "GUIDE-DEMARRAGE.en.md").read_text(encoding="utf-8"), "en")
    strip = lambda d: [(t["id"], t["etape"], t["test"], t["reglage"]) for t in d["topics"]]  # noqa: E731
    assert strip(en) == strip(fr) and len(en["topics"]) >= 20
    assert " " not in json.dumps(en, ensure_ascii=False)  # pas d'espace insécable « à la française »
    monkeypatch.setattr(config, "GUIDE_STATE_FILE", tmp_path / "guide.json")
    monkeypatch.setattr(config, "FIRST_RUN_FILE", tmp_path / "first-run-done")
    gd = guide_mod.Guide()
    monkeypatch.setattr(guide_mod, "guide", gd)
    assert guide_mod.command("guide_get", {"lang": "en"})["title"] == en["title"]
    assert guide_mod.command("guide_get", {})["title"] == fr["title"]  # défaut : français


def test_reglage_langue_a_son_icone_et_son_aide():
    from astrel.settings import GUIDE_TOPICS, ITEMS

    item = ITEMS["ui_language"]
    assert item.group == "voix" and item.applies == "hot" and item.default() == "fr"
    assert [v for v, _ in item.choices] == ["fr", "en"] and item.env == "ASTREL_UI_LANGUAGE"
    assert re.search(r"ui_language: \"languages\"", (HUD / "src" / "settings.js").read_text(encoding="utf-8"))
    assert "stt_language" in GUIDE_TOPICS


@pytest.mark.skipif(not (shutil.which("google-chrome") or shutil.which("chromium")) or os.environ.get(
    "ASTREL_SKIP_CHROME") == "1", reason="Chrome absent (ou ASTREL_SKIP_CHROME=1)")
def test_hud_rendu_en_anglais_chrome_headless():
    """(b) le HUD de la démo en anglais, page par page, dans Chrome HEADLESS : aucun texte
    français hors contenu ; bascule à chaud et retour en français."""
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "hud_check_langue.py")], cwd=ROOT,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert "contrôles passés" in r.stdout and "ÉCHEC" not in r.stdout


BULLE = r"""
import json
from astrel.bubble import ChatWindow, ConfirmCard, Gtk
from astrel.minichat import MiniChat
chat = MiniChat()
card = ConfirmCard(chat)
card.show_for({"question": "Attention, une publication git (push). Je continue ?", "cmd": "git push",
               "grantable": True, "error": "Question déjà close : réponse non prise en compte."}, (900, 500, 148))
win = ChatWindow(chat)
win.toggle((900, 500, 148))
for _ in range(30):
    Gtk.main_iteration_do(False)
print(json.dumps([card.question.get_text(), card.no.get_label(), card.yes.get_label(), card.always.get_label(),
                  card.error.get_text(), win.entry.get_placeholder_text()], ensure_ascii=False))
"""


@pytest.mark.skipif(not shutil.which("xvfb-run"), reason="Xvfb absent")
@pytest.mark.parametrize("lang, want", [
    ("fr", ["Attention, une publication git (push). Je continue ?", "Non", "Oui", "Oui 30 min",
            "Question déjà close : réponse non prise en compte.", "Écris à Astrel… (Entrée ; Ctrl+V colle une image)"]),
    ("en", ["Heads up: a git publish (push). Should I continue?", "No", "Yes", "Yes 30 min",
            "Question already closed: reply ignored.", "Message Astrel… (Enter; Ctrl+V pastes an image)"]),
])
def test_bulle_dans_la_langue_choisie(lang, want, tmp_path):
    """Carte de confirmation et mini-chat de la bulle, rendus sous Xvfb (écran virtuel, rien sur le bureau)."""
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    env.update(ASTREL_UI_LANGUAGE=lang, PYTHONPATH=str(ROOT))
    r = subprocess.run(["xvfb-run", "-a", sys.executable, "-c", BULLE], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    assert json.loads(r.stdout.strip().splitlines()[-1]) == want


# 23/10 (plan P31, demande de l'utilisateur) : nom affiché « Astrel » posé par `tr()` dans les parties FIXES
# des textes connus du catalogue, jamais dans une partie variable, le mot d'éveil ni un identifiant.
def test_nom_affiche_dans_les_textes_du_coeur(monkeypatch, tmp_path):
    monkeypatch.delenv("ASTREL_ASSISTANT_NAME", raising=False)
    monkeypatch.setattr(config, "ASTREL_SETTINGS_FILE", tmp_path / "settings.json")
    from astrel.i18n import clean_name, nom
    assert i18n.tr("réponse refusée par Astrel", "fr") == "réponse refusée par Astrel"
    assert i18n.tr("réponse refusée par Astrel", "en") == "reply refused by Astrel"
    # modèle : le nom dans le texte fixe, la partie variable (ici un nom de fichier) intacte
    assert i18n.tr("une modification de Astrel.txt, fichier protégé d'Astrel", "fr") == \
        "une modification de Astrel.txt, fichier protégé d'Astrel"
    assert i18n.tr("Astrel : texte inconnu du catalogue", "fr") == "Astrel : texte inconnu du catalogue"
    assert nom("systemctl --user restart Astrel.service") == "systemctl --user restart Astrel.service"
    # 25/10 (plan P36) : le mot d'éveil suit le nom ; l'alias s'écrit par son identifiant
    assert nom("dis « OK Astrel » ou « Hey Astrel »") == "dis « OK Astrel » ou « Hey Astrel »"
    assert nom("modèle audio hey_jarvis") == "modèle audio hey_jarvis"
    assert nom("Parle à Astrel.", language="fr") == "Parle à Astrel."
    assert nom("une bibliothèque Astrel", language="fr") == "une bibliothèque Astrel"   # pas « bibliothèqu'Astrel »
    assert nom("le code d'Astrel", language="fr") == "le code d'Astrel"
    assert nom("le code d'Astrel", "Nova", "fr") == "le code de Nova"
    assert clean_name("<b>Nova</b>") == "Nova" and clean_name("No\nva") == "No va" and clean_name("<>") == "Astrel"
    assert clean_name("x" * 80) == "x" * 32


# 28/10 (plan P42, renommage) : le code écrit « Astrel » ; un nom choisi le remplace toujours, élision
# défaite devant une consonne, jamais dans un identifiant ni une partie variable.
def test_nom_choisi_remplace_astrel_ecrit_par_le_code(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ASTREL_SETTINGS_FILE", tmp_path / "settings.json")
    from astrel.i18n import nom
    assert nom("le code d'Astrel", "Astrel", "fr") == "le code d'Astrel"
    assert nom("le code d'Astrel", "Nova", "fr") == "le code de Nova"
    assert nom("ce qu'Astrel sait faire", "Nova", "fr") == "ce que Nova sait faire"
    assert nom("le code d'Astrel", "Orion", "fr") == "le code d'Orion"
    assert nom("Parle à Astrel.", "Nova", "fr") == "Parle à Nova."
    assert nom("Astrel.service et Astrel/runtime", "Nova", "fr") == "Astrel.service et Astrel/runtime"
    monkeypatch.setenv("ASTREL_ASSISTANT_NAME", "Nova")
    assert i18n.tr("Ce qu'il faut pour qu'Astrel marche", "fr") == "Ce qu'il faut pour que Nova marche"
    monkeypatch.setenv("ASTREL_ASSISTANT_NAME", "Astrel")
    assert i18n.tr("Ce qu'il faut pour qu'Astrel marche", "fr") == "Ce qu'il faut pour qu'Astrel marche"


# 28/10 (mission renommage-astrel-t32, constat du testeur) : un nom choisi qui contient « Astrel » n'est
# jamais doublé, que le texte porte l'ancien nom ou « Astrel » (une seule passe de remplacement).
def test_nom_choisi_contenant_astrel_pas_double(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ASTREL_SETTINGS_FILE", tmp_path / "settings.json")
    from astrel.i18n import nom
    assert nom("Astrel ne répond pas.", "Mon Astrel", "fr") == "Mon Astrel ne répond pas."
    assert nom("Astrel ne répond pas.", "Mon Astrel", "fr") == "Mon Astrel ne répond pas."
    assert nom("Astrel et Astrel", "Astrel Bis", "fr") == "Astrel Bis et Astrel Bis"
    assert nom("le code d'Astrel", "Mon Astrel", "fr") == "le code de Mon Astrel"
    assert nom("le code d'Astrel", "Mon Astrel", "fr") == "le code de Mon Astrel"
    # cas déjà valides, conservés
    assert nom("le code d'Astrel", "Nova", "fr") == "le code de Nova"
    assert nom("le code d'Astrel", "Éole", "fr") == "le code d'Éole"
    assert nom("le code d'Astrel", "Éole", "fr") == "le code d'Éole"
    monkeypatch.setenv("ASTREL_ASSISTANT_NAME", "Mon Astrel")
    texte = "Astrel ne répond pas : réponse non transmise, réessaie ou réponds dans le HUD"
    assert i18n.tr(texte, "fr").startswith("Mon Astrel ne répond pas : ")
    assert i18n.tr(texte, "en").startswith("Mon Astrel isn't responding: ")
