"""Chat, lot 2 (04/11, demandes de l'utilisateur) : recherche dans tous les canaux (Ctrl+K, commande `history_query`,
HUD seul), chemins de fichiers copiés d'un clic, Échap = ■ hors carte.

Le rendu des chemins et les fonctions pures de la recherche sont testés en JavaScript
(hud/tests/markdown.test.mjs, hud/tests/recherche.test.mjs, lancés ici par Node) ; le parcours dans le HUD est
vérifié en Chrome headless (tools/hud_check_rendu.py, `check_recherche_chemins_echap`)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from astrel import config, core, history
from astrel.events import Bus

ROOT = Path(__file__).resolve().parent.parent
HUD = ROOT / "hud"
SRC = HUD / "src"
CLE = "sk-ant-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4"


def ev(kind, text, mid, channel="general", **extra):
    base = {"type": kind, "text": text, "mid": mid, "channel": channel, "ts": extra.pop("ts", 1_780_000_000.0)}
    if kind == "transcript":
        base.update(who="user", source=extra.pop("source", "clavier"))
    return {**base, **extra}


def line(kind, text, ts, session="s1"):
    content = text if kind == "user" else [{"type": "text", "text": text}]
    return json.dumps({"type": kind, "sessionId": session, "timestamp": ts,
                       "message": {"role": kind, "content": content}}, ensure_ascii=False) + "\n"


@pytest.fixture
def lieux(tmp_path):
    folder = tmp_path / "projets"
    folder.mkdir()
    return folder, tmp_path / "history.db"


def count_fil(db):
    import sqlite3

    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT count(*) FROM fil").fetchone()[0]
    except sqlite3.OperationalError:  # 04/11 (CI Windows) : fichier créé, table pas encore : on réessaie
        return 0
    finally:
        con.close()


# --------------------------------------------------------------------------- index du fil

def test_fil_garde_les_messages_de_utilisateur_et_les_reponses(lieux):
    folder, db = lieux
    n = history.fil_ecrire([
        ev("transcript", "Où en est le bracelet Studio ?", "m1", "studio"),
        ev("reply", "Le **bracelet** est en test.", "r1", "studio", reply_to="m1"),
        ev("report", "Compte rendu : bracelet livré.", "c1", "studio"),
        ev("transcript", "bracelet par WhatsApp", "m2", source="whatsapp"),
    ], db=db)
    assert n == 4
    out = history.query("bracelet", db=db, folder=folder)
    assert {(r["who"], r["channel"], r["mid"]) for r in out["results"]} == {
        ("user", "studio", "m1"), ("astrel", "studio", "r1"), ("astrel", "studio", "c1"), ("user", "general", "m2")}
    rep = next(r for r in out["results"] if r["mid"] == "r1")
    assert rep["reply_to"] == "m1" and rep["ts"] == 1_780_000_000.0
    assert "**" not in rep["extrait"]  # l'extrait se lit en texte simple
    assert f"{history.MARQUE_DEBUT}bracelet{history.MARQUE_FIN}" in rep["extrait"].lower()
    assert out["archives"] == [] and out["more"] is False


def test_fil_exclut_local_dictee_automatique_et_canaux_local(lieux):
    folder, db = lieux
    n = history.fil_ecrire([
        ev("transcript", "secret local", "l1", engine="local"),
        ev("reply", "réponse locale", "l2", engine="local"),
        ev("transcript", "texte dicté", "d1", source="dictée"),
        ev("transcript", "consigne de réflexe", "a1", source="réflexe"),
        ev("transcript", "message dans le canal local", "c1", "local"),
        ev("transcript", "venu d'Astrel", "x1", who="astrel"),
        ev("reply", "", "v1"),
        ev("reply", "sans identifiant", ""),
        ev("tool", "outil", "t1"),
    ], db=db, exclus=frozenset({"local"}))
    assert n == 0
    for mot in ("local", "dicté", "consigne", "canal", "Astrel", "identifiant", "outil"):
        assert history.query(mot, db=db, folder=folder)["results"] == [], mot


def test_meme_mid_pas_double_et_deplacement_de_canal(lieux):
    folder, db = lieux
    history.fil_ecrire([ev("transcript", "le projet Orion", "m1"), ev("reply", "Orion avance", "r1", reply_to="m1")],
                       db=db)
    # Message proposé à nouveau au démarrage (même mid) : pas de doublon.
    assert history.fil_ecrire([ev("transcript", "le projet Orion", "m1", unsent=True)], db=db) == 0
    assert count_fil(db) == 2
    # Déplacé à la main dans un autre canal : il emmène sa réponse.
    history.fil_ecrire([{"type": "channel_move", "mid": "m1", "channel": "orion"}], db=db)
    assert {r["channel"] for r in history.query("orion", db=db, folder=folder)["results"]} == {"orion"}


def test_secrets_masques_et_controles_retires(lieux):
    folder, db = lieux
    history.fil_ecrire([ev("reply", f"ta clé {CLE} pour\x02 le serveur\x07 Zephyr", "r1")], db=db)
    import sqlite3

    con = sqlite3.connect(db)
    try:
        texte = con.execute("SELECT text FROM fil").fetchone()[0]
    finally:
        con.close()
    assert CLE not in texte and "[secret masqué]" in texte
    assert "\x02" not in texte and "\x07" not in texte
    out = history.query("zephyr", db=db, folder=folder)["results"][0]["extrait"]
    assert CLE not in out and out.count(history.MARQUE_DEBUT) == 1


def test_accents_ignores_et_requete_bornee(lieux):
    folder, db = lieux
    history.fil_ecrire([ev("transcript", "La décision est prise pour l'été", "m1")], db=db)
    assert history.query("decision ete", db=db, folder=folder)["results"]
    assert history.query("DÉCISION", db=db, folder=folder)["results"]
    # Syntaxe FTS5 dans la question : cherchée comme des mots, jamais interprétée.
    assert history.query('decision OR "x" NEAR(*)', db=db, folder=folder)["results"] == []
    assert history.query("décis", db=db, folder=folder)["results"]  # préfixe à partir de 4 lettres
    with pytest.raises(ValueError):
        history.query("   ", db=db, folder=folder)
    with pytest.raises(ValueError):
        history.query("a ? !", db=db, folder=folder)
    assert len(history.nettoyer_requete("x" * 5000)) == history.REQUETE_MAX
    assert history.nettoyer_requete({"q": 1}) == ""
    assert history._requete_fts(" ".join(f"mot{i}" for i in range(20))).count('"') == 2 * history.TERMES_MAX


def test_reponse_bornee(lieux):
    folder, db = lieux
    long = "Orion " + "mot " * 400
    history.fil_ecrire([ev("reply", long, f"r{i}") for i in range(history.FIL_MAX + 5)], db=db)
    out = history.query("orion", db=db, folder=folder)
    assert len(out["results"]) == history.FIL_MAX and out["more"] is True
    assert all(len(r["extrait"]) <= history.EXTRAIT_MAX + 4 for r in out["results"])
    assert len(json.dumps(out, ensure_ascii=False)) < 20_000


def test_archives_avant_le_fil_sans_notes_du_coeur(lieux):
    folder, db = lieux
    (folder / "s1.jsonl").write_text(
        line("user", "[Mode silencieux : l'utilisateur lit ta réponse à l'écran, elle n'est pas dite.] "
                     "Parle-moi du télescope Vega", "2026-10-01T09:00:00Z")
        + line("assistant", "Le **télescope** Vega est commandé.", "2026-10-01T09:01:00Z")
        + line("user", "[Réflexe programmé, l'utilisateur n'a rien demandé à l'instant.] télescope", "2026-10-01T09:02:00Z")
        + line("user", "Et le télescope après ?", "2099-01-01T00:00:00Z"),  # après le début du fil : déjà dans le fil
        encoding="utf-8")
    history.fil_ecrire([ev("transcript", "Le télescope arrive", "m1", ts=time.time())], db=db)
    out = history.query("telescope", db=db, folder=folder)
    assert [r["mid"] for r in out["results"]] == ["m1"]
    textes = [a["extrait"] for a in out["archives"]]
    assert len(textes) == 2, textes  # ni le réflexe (automatique), ni ce qui suit le début du fil
    assert all("Mode silencieux" not in x and "**" not in x for x in textes)
    assert {a["who"] for a in out["archives"]} == {"user", "astrel"}
    assert out["fil_debut"] > 0 and all(a["ts"] < out["fil_debut"] for a in out["archives"])
    assert history._epoch("2026-10-01T09:00:00") == history._epoch("2026-10-01T09:00:00Z")  # sans fuseau = UTC


def test_effacer_et_oublier_vident_aussi_le_fil(lieux, tmp_path, monkeypatch):
    folder, db = lieux
    vieux = time.time() - 40 * 86400
    history.fil_ecrire([ev("transcript", "ancien Kepler", "m1", ts=vieux), ev("reply", "récent Kepler", "r1",
                                                                              ts=time.time())], db=db)
    assert history.purge(30, db) == 1
    assert [r["mid"] for r in history.query("kepler", db=db, folder=folder)["results"]] == ["r1"]
    (folder / "s1.jsonl").write_text(line("user", "Kepler encore", "2026-09-01T09:00:00Z"), encoding="utf-8")
    assert history.query("kepler", db=db, folder=folder)["archives"]  # lue en archives (avant le fil)
    assert history.clear(db) == 2
    assert count_fil(db) == 0
    # après un effacement, les transcriptions déjà lues ne reviennent pas en archives (position gardée)
    out = history.query("kepler", db=db, folder=folder)
    assert out["results"] == [] and out["archives"] == []


# --------------------------------------------------------------------------- abonné du bus

def test_filindex_suit_le_bus_sans_le_ralentir(lieux, monkeypatch):
    folder, db = lieux
    monkeypatch.setattr(config, "HISTORY_DB", db)
    bus, index = Bus(), history.FilIndex()
    stop = index.brancher(bus, exclus=lambda: frozenset({"local"}))
    try:
        bus.publish("transcript", who="user", text="la fusée Ariane", source="voix", channel="general", mid="m1")
        bus.publish("transcript", who="user", text="Ariane en local", source="voix", channel="local", mid="m2")
        bus.publish("reply", text="Ariane rejouée", channel="general", mid="r0", replay=True)
        bus.publish("reply", text="Ariane décolle", channel="general", mid="r1", reply_to="m1")
        for _ in range(100):
            if db.exists() and count_fil(db) >= 2:
                break
            time.sleep(0.05)
        time.sleep(0.1)
        assert sorted(r["mid"] for r in history.query("ariane", db=db, folder=folder)["results"]) == ["m1", "r1"]
    finally:
        stop()


def test_file_pleine_un_message_perdu_jamais_le_coeur_bloque(monkeypatch):
    index = history.FilIndex()
    index._q = __import__("queue").Queue(maxsize=1)
    index.noter({"type": "reply", "text": "a", "mid": "1"})
    t0 = time.monotonic()
    index.noter({"type": "reply", "text": "b", "mid": "2"})
    assert time.monotonic() - t0 < 0.5 and index._plein


# --------------------------------------------------------------------------- commande du HUD

@pytest.fixture
def coeur(lieux, monkeypatch):
    folder, db = lieux
    monkeypatch.setattr(config, "HISTORY_DB", db)
    monkeypatch.setattr(config, "TRANSCRIPTS_DIR", folder)
    history.fil_ecrire([ev("transcript", "le dossier Pégase", "m1", "pegase")], db=db)
    j = object.__new__(core.Astrel)
    j._off = False
    return j


def test_history_query_du_hud(coeur):
    r = coeur._on_command({"cmd": "history_query", "query": "  pegase\x00 ", "_from_claude": False, "_via": "hud"})
    assert r["ok"] is True and r["query"] == "pegase"
    assert [(x["channel"], x["mid"]) for x in r["results"]] == [("pegase", "m1")]
    vide = coeur._on_command({"cmd": "history_query", "query": "", "_from_claude": False, "_via": "hud"})
    assert vide == {"ok": False, "error": "question vide : donne un ou plusieurs mots à chercher"}


@pytest.mark.parametrize("cmd", [
    {"_from_claude": True, "_via": "hud"},       # Claude, même par le HUD
    {"_from_claude": True, "_via": "socket"},
    {"_from_claude": False, "_via": "socket"},   # canal de contrôle, --send
    {},
])
def test_history_query_refusee_hors_du_hud_de_utilisateur(coeur, cmd):
    r = coeur._on_command({"cmd": "history_query", "query": "pegase", **cmd})
    assert r == {"ok": False, "error": "history_query est réservé au HUD de l'utilisateur"}


def test_history_query_pas_dans_le_serveur_mcp():
    """Claude a son outil `history_search` ; la commande du HUD n'est pas exposée au MCP."""
    assert "history_query" not in (ROOT / "astrel" / "mcp_server.py").read_text(encoding="utf-8")


def test_coeur_branche_l_index_du_fil_au_demarrage():
    code = (ROOT / "astrel" / "core.py").read_text(encoding="utf-8")
    assert "history.fil.brancher(bus, exclus=channels.local_ids)" in code
    assert core.Astrel._COMMANDES["history_query"] == "_cmd_history_query"


def test_textes_python_traduits():
    en = json.loads((ROOT / "astrel" / "locales" / "en.json").read_text(encoding="utf-8"))
    for fr in ("{name} est réservé au HUD de l'utilisateur", "recherche impossible : {exc}",
               "question vide : donne un ou plusieurs mots à chercher"):
        assert fr in en, fr


# --------------------------------------------------------------------------- sources du HUD

def _js(name):
    return (SRC / name).read_text(encoding="utf-8")


@pytest.mark.skipif(not shutil.which("node"), reason="Node absent")
def test_fonctions_pures_de_la_recherche_node():
    r = subprocess.run(["node", "--test", str(HUD / "tests" / "recherche.test.mjs")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert re.search(r"^# fail 0$", r.stdout, re.M)


def test_echap_passe_par_la_meme_commande_que_stop():
    main = _js("main.js")
    assert 'const stopNow = () => command("stop");' in main
    assert '$("#stop-btn").onclick = stopNow;' in main
    bloc = main[main.index("const cardShown"):main.index("initSettings(toast)")]
    for garde in ("cardShown()", "!answering()", "e.repeat", "e.isComposing", "composing", "confirmId",
                  ".modal-back", "m.choice", "stopNow()"):
        assert garde in main[main.index("const answering"):main.index("initSettings(toast)")], garde
    assert "e.defaultPrevented" in bloc
    html = (HUD / "index.html").read_text(encoding="utf-8")
    assert re.search(r'id="stop-btn"[^>]*aria-keyshortcuts="Escape"', html)
    # le panneau Activité fermé par Échap n'arrête pas aussi la réponse
    act = main[main.index('if ($("#activity").classList.contains("hidden")) return;'):][:200]
    assert "e.preventDefault()" in act


def test_ctrl_k_ouvre_la_recherche_et_ctrl_f_reste_a_chrome():
    main = _js("main.js")
    assert 'e.key?.toLowerCase() !== "k"' in main and "openSearch()" in main
    assert not re.search(r'key(\?\.toLowerCase\(\))?\s*[!=]==?\s*"f"', main)
    html = (HUD / "index.html").read_text(encoding="utf-8")
    assert re.search(r'id="search-btn"[^>]*aria-keyshortcuts="Control\+K"', html)
    css = (HUD / "styles.css").read_text(encoding="utf-8")
    assert re.search(r':root\[data-affichage="simple"\] :is\([^)]*#search-btn', css)  # caché en Simple


def test_recherche_sans_innerhtml_et_commande_history_query():
    code = _js("recherche.js")
    assert 'ctx.command("history_query"' in code
    assert not re.search(r"\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML|document\.write", code)
    assert "textContent" in code


def test_chemins_seulement_dans_le_fil_et_copies_sans_rien_ouvrir():
    main = _js("main.js")
    assert "renderMarkdown(m.raw, document, { paths: true })" in main
    bloc = main[main.index("function copyPath"):main.index("function goToMessage")]
    assert "copyText(path" in bloc
    assert not re.search(r"open_link|media_open|window\.open|location", bloc)
    for f in SRC.glob("*.js"):
        if f.name not in ("main.js", "markdown.js"):
            assert "paths: true" not in f.read_text(encoding="utf-8"), f.name


def test_bouton_envoyer_meme_chemin_qu_entree():
    """04/11 (« comme ChatGPT ») : le bouton Envoyer appelle la MÊME fonction qu'Entrée, rien pendant une
    composition IME, grisé sans rien à envoyer ; libellés FR et EN."""
    main = _js("main.js")
    entree = main[main.index('$("#ask").addEventListener("keydown", (e) => {\n  if (e.key !== "Enter"'):][:400]
    assert "sendAsk();" in entree
    bouton = main[main.index('$("#send-btn").addEventListener("click"'):][:300]
    assert "composing" in bouton and "sendAsk();" in bouton
    assert re.search(r"b\.disabled = .*!a\.value\.trim\(\)", main)
    html = (HUD / "index.html").read_text(encoding="utf-8")
    assert re.search(r'id="send-btn"[^>]*disabled[^>]*data-i18n-title="send\.title"[^>]*><i data-icon="arrow-up">', html)
    lang = _js("langues.js")
    assert '"send.title": "Envoyer (Entrée)"' in lang and '"send.title": "Send (Enter)"' in lang
    assert '"arrow-up":' in _js("icons.js")
