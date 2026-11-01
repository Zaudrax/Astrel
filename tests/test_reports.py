"""Comptes rendus à la demande (30/09) : la fin d'une tâche de fond n'est plus
dite d'office. Le compte rendu s'affiche en silence dans le canal du projet,
avec un bouton 🔊 ; au clic, le cœur dit le texte affiché, sans repasser par
Claude. Le réglage « direct » rend la lecture immédiate d'avant."""
import chemins_machine  # chemins de la machine qui lance la suite
import json
import threading

import pytest

from astrel import audio, config, core
from astrel.channels import Channels
from astrel.events import bus
from astrel.reports import reports
from test_channels import CONF
from test_pump import brain_events, make_astrel
from conftest import remplacer_coeur


@pytest.fixture
def ch(tmp_path, monkeypatch):
    f = tmp_path / "channels.json"
    f.write_text(json.dumps(CONF), encoding="utf-8")
    monkeypatch.setattr(config, "CHANNELS_FILE", f)
    monkeypatch.setattr(config, "CHANNEL_FILE", tmp_path / "channel.txt")
    c = Channels()
    remplacer_coeur(monkeypatch, "channels", c)
    return c


@pytest.fixture
def published():
    out = []
    unsub = bus.subscribe(out.append)
    yield out
    unsub()


@pytest.fixture
def astrel(monkeypatch, ch):
    chimes = []
    j, said = make_astrel(monkeypatch)
    monkeypatch.setattr(audio, "chime", chimes.append)
    monkeypatch.setattr(core.sensors, "is_on", lambda name: False)
    j.brain.send = lambda *a, **k: None
    j._send_lock = threading.Lock()
    return j, said, chimes


def of(events, kind):
    return [ev for ev in events if ev["type"] == kind]


def fond_turn(j, text="La recherche est finie. Trois pistes sérieuses, je te les détaille quand tu veux. "):
    """Un tour relancé seul par le CLI à la fin d'une tâche de fond."""
    j._on_stream(core.BrainEvent("message_start"))
    j._on_stream(core.BrainEvent("text", text=text))
    j._on_stream(core.BrainEvent("turn_end"))


# ------------------------------------------------------------------ le réglage

def test_reglage_par_defaut_retenu_et_bascule(monkeypatch):
    assert reports.mode == "demande" and reports.on_demand
    assert reports.set_mode("direct") == {"ok": True, "mode": "direct"}
    assert config.REPORTS_FILE.read_text(encoding="utf-8").strip() == "direct"
    monkeypatch.setattr(reports, "_mode", None)  # relance d'Astrel : relu sur le disque
    assert reports.mode == "direct"
    assert reports.set_mode("toggle")["mode"] == "demande"
    assert reports.set_mode("lus aussitôt")["ok"] is False
    assert reports.set_mode("à la demande")["mode"] == "demande"
    monkeypatch.setenv("ASTREL_REPORTS", "direct")
    monkeypatch.setattr(reports, "_mode", None)
    assert reports.mode == "direct"  # la variable d'environnement prime


def test_commande_reports(astrel):
    j, _, _ = astrel
    assert j.on_command({"cmd": "reports"})["mode"] == "demande"
    assert j.on_command({"cmd": "reports direct"})["mode"] == "direct"  # --send "reports direct"
    assert j.on_command({"cmd": "reports", "mode": "demande"})["mode"] == "demande"  # HUD
    assert not j.on_command({"cmd": "reports n'importe"})["ok"]


def test_reglage_dans_la_page_reglages(astrel, monkeypatch):
    """28/10 (hud-boutons-t10) : l'interrupteur « Lire aussitôt » quitte la barre du HUD ; le réglage
    passe dans Réglages › Voix, audio et langue. Même fichier (runtime/reports.txt), jamais
    settings.json ; `ASTREL_REPORTS` prime et grise la ligne ; refusé à Claude comme tout réglage."""
    from astrel.settings import ITEMS, SettingError, reglages

    j, _, _ = astrel
    assert ITEMS["reports"].group == "voix" and ITEMS["reports"].live
    assert [v for v, _l in ITEMS["reports"].choices] == ["demande", "direct"]
    assert reglages.value("reports") == "demande"
    r = j.on_command({"cmd": "settings_set", "key": "reports", "value": "direct"})  # HUD de l'utilisateur
    assert r["ok"] and reports.mode == "direct"
    assert config.REPORTS_FILE.read_text(encoding="utf-8").strip() == "direct"
    assert "reports" not in json.loads(config.ASTREL_SETTINGS_FILE.read_text(encoding="utf-8")
                                       if config.ASTREL_SETTINGS_FILE.exists() else "{}")
    r = j.on_command({"cmd": "settings_set", "key": "reports", "value": "demande", "_from_claude": True})
    assert r["ok"] is False and reports.mode == "direct"  # Claude ne le change pas
    with pytest.raises(SettingError):
        reglages.set("reports", "bavard")
    j.on_command({"cmd": "reports", "mode": "demande"})  # terminal : la page suit (même source)
    assert reglages.value("reports") == "demande"
    monkeypatch.setenv("ASTREL_REPORTS", "direct")
    monkeypatch.setattr(reports, "_mode", None)
    assert ITEMS["reports"].lock_reason() and reglages.value("reports") == "direct"
    with pytest.raises(SettingError):
        reglages.set("reports", "demande")


def test_mission_et_extinction_dans_les_reglages():
    """28/10 (hud-boutons-t10) : « Lancer une mission » (Garde-fou) et « Éteindre » (Système) sont des
    boutons exécutés par le HUD lui-même (`hud`), qui envoie sa commande habituelle : rien à exécuter
    côté cœur par `settings_action`."""
    from astrel.settings import ITEMS, reglages

    for key, group, hud in (("mission", "garde_fou", "mission_start"), ("systeme.eteindre", "systeme", "power")):
        assert ITEMS[key].group == group and ITEMS[key].kind == "action"
        actions = reglages.view(key)["actions"]
        assert [a["hud"] for a in actions] == [hud]
        assert reglages.action(key, actions[0]["id"])["ok"] is False
    assert reglages.view("systeme.eteindre")["actions"][0]["danger"] is True
    assert ITEMS["systeme.eteindre"].label == "Éteindre Astrel"  # jamais l'ancien nom à l'écran


# ------------------------------------------------------------------ un tour de fond

def test_compte_rendu_affiche_en_silence(astrel, published):
    j, said, chimes = astrel
    fond_turn(j)
    assert said == [] and chimes == []  # ni voix ni carillon
    reply = of(published, "reply")[-1]
    assert reply["report"] and reply["pending_read"]
    assert reports.text(reply["mid"]).startswith("La recherche est finie.")
    assert not j._followup  # pas d'écoute ouverte pour une réponse que personne n'a entendue


def test_clic_sur_lire_dit_le_texte_affiche(astrel, published):
    j, said, _ = astrel
    fond_turn(j)
    mid = of(published, "reply")[-1]["mid"]
    assert j.on_command({"cmd": "read_report", "mid": mid}) == {"ok": True, "paused": False}
    assert said == ["La recherche est finie.", "Trois pistes sérieuses, je te les détaille quand tu veux."]
    assert of(published, "report_read")[-1]["mid"] == mid
    replayed = [ev for ev in bus.snapshot()["history"] if ev.get("mid") == mid]
    assert replayed and not replayed[-1]["pending_read"]  # un HUD qui se reconnecte le voit « lu »
    j.on_command({"cmd": "read_report", "mid": mid})  # « Relire »
    assert len(said) == 4 and len(of(published, "report_read")) == 1


def test_lire_apres_une_relance(astrel):
    """Le cœur a perdu ses comptes rendus : le HUD resté ouvert envoie le texte affiché."""
    j, said, _ = astrel
    assert j.on_command({"cmd": "read_report", "mid": "inconnu", "fallback": "Tout est prêt. "})["ok"]
    assert said == ["Tout est prêt."]
    assert not j.on_command({"cmd": "read_report", "mid": "inconnu"})["ok"]


def test_mode_direct_dit_le_compte_rendu(astrel, published, monkeypatch):
    j, said, chimes = astrel
    monkeypatch.setattr(reports, "_mode", "direct")
    fond_turn(j)
    assert said and chimes == ["confirm"]
    reply = of(published, "reply")[-1]
    assert reply["report"] and not reply["pending_read"]  # bouton « Relire » seulement


def test_focus_garde_le_compte_rendu_meme_en_direct(astrel, published, monkeypatch):
    j, said, _ = astrel
    monkeypatch.setattr(reports, "_mode", "direct")
    monkeypatch.setattr(core.sensors, "is_on", lambda name: name == "FOCUS")
    fond_turn(j)
    assert said == [] and of(published, "reply")[-1]["pending_read"]


def test_reponse_a_utilisateur_pas_marquee(astrel, published):
    j, said, _ = astrel
    j.handle("Quelle heure est-il ?", "clavier", "general")
    j._on_stream(core.BrainEvent("text", text="Il est midi pile. "))
    j._on_stream(core.BrainEvent("turn_end"))
    assert said == ["Il est midi pile."]
    assert "report" not in of(published, "reply")[-1]


def test_utilisateur_parle_pendant_le_compte_rendu(astrel, published):
    """Il se mêle d'un tour de fond gardé en silence : le compte rendu reste « à
    lire » (même sa phrase inachevée), la réponse à l'utilisateur est dite."""
    j, said, _ = astrel
    j._on_stream(core.BrainEvent("message_start"))
    j._on_stream(core.BrainEvent("text", text="La compilation est finie. Il reste un avertissement"))
    j.handle("Studio, lance les tests", "voix", "general")
    j._on_stream(core.BrainEvent("text", text="\nJe lance les tests. "))
    j._on_stream(core.BrainEvent("turn_end"))
    first, second = of(published, "reply")[-2:]
    assert first["partial"] and first["report"] and first["pending_read"]
    assert first["text"] == "La compilation est finie. Il reste un avertissement"
    assert "report" not in second and second["channel"] == "studio"
    # Ni « La compilation… », ni la phrase inachevée qui attendait dans le découpeur.
    assert said == ["Je lance les tests."]


def test_tour_de_fond_dans_le_canal_de_sa_tache(astrel, published, ch):
    """Enregistrement réel : le compte rendu va dans le canal de la tâche, en attente."""
    j, said, _ = astrel
    j.handle("Lance la commande en fond", "clavier", "compta")
    for ev in brain_events():
        j._on_stream(ev)
    last = of(published, "reply")[-1]
    assert last["channel"] == "compta" and last["pending_read"] and "finie" in last["text"]
    assert not any("finie" in s for s in said)  # le premier tour (réponse à l'utilisateur) est dit, pas le second


# ------------------------------------------------------------------ l'outil post_report

def test_post_report_en_silence(astrel, published, ch):
    j, said, _ = astrel
    j.handle("Où en est Studio ?", "clavier", "studio")
    r = j.on_command({"cmd": "report", "text": "L'agent a fini l'audit. Rien de bloquant."})
    assert r["ok"] and r["dit"] is False and r["canal"] == "studio"  # canal de l'échange en cours
    ev = of(published, "report")[-1]
    assert ev["pending_read"] and ev["mid"] == r["mid"] and ev["channel"] == "studio"
    assert said == []
    assert j.on_command({"cmd": "report", "text": "Autre chose.", "channel": "Compta"})["canal"] == "compta"
    assert not j.on_command({"cmd": "report", "text": "x", "channel": "inconnu"})["ok"]
    assert not j.on_command({"cmd": "report", "text": "  "})["ok"]
    assert j.on_command({"cmd": "read_report", "mid": r["mid"]})["ok"]
    assert said == ["L'agent a fini l'audit.", "Rien de bloquant."]


def test_post_report_en_direct(astrel, published, monkeypatch):
    j, said, _ = astrel
    monkeypatch.setattr(reports, "_mode", "direct")
    r = j.on_command({"cmd": "report", "text": "L'agent a fini l'audit, rien de bloquant."})
    assert r["dit"] is True and said == ["L'agent a fini l'audit, rien de bloquant."]
    assert not of(published, "report")[-1]["pending_read"]


def test_outil_mcp_declare(monkeypatch):
    from astrel import mcp_server

    sent = []
    monkeypatch.setattr(mcp_server, "_core", lambda cmd: sent.append(cmd) or {"ok": True})
    mcp_server.post_report("Compte rendu.", "Studio")
    assert sent == [{"cmd": "report", "text": "Compte rendu.", "channel": "Studio", "kind": "info", "files": []}]
    mcp_server.post_report("À trancher.", kind="decision", files=[f"/tmp/f{i}" for i in range(12)])
    assert sent[-1]["kind"] == "decision" and len(sent[-1]["files"]) == 10


# ------------------------------------------------------------------ comptes rendus typés (15/10)

@pytest.mark.parametrize("word, kind", [("decision", "decision"), ("Décision", "decision"), ("résultat", "resultat"),
                                        ("blocage", "blocage"), ("info", "info"), ("", "info"), (None, "info"),
                                        ("urgent", "info"), (3, "info")])
def test_type_de_compte_rendu(word, kind):
    from astrel.reports import parse_kind

    assert parse_kind(word) == kind


def test_fichiers_nettoyes():
    from astrel.reports import clean_files

    assert clean_files(None) == [] and clean_files(42) == []
    assert clean_files("/tmp/a.txt") == ["/tmp/a.txt"]
    assert clean_files([" /tmp/a ", "/tmp/a", "", 5, "/tmp/\x1b[31mb"]) == ["/tmp/a", "/tmp/[31mb"]
    assert len(clean_files([f"/tmp/{i}" for i in range(30)])) == 10
    assert len(clean_files(["/" + "x" * 1000])[0]) == 300


def test_post_report_type_et_fichiers(astrel, published):
    j, said, _ = astrel
    r = j.on_command({"cmd": "report", "text": "Il faut choisir le nom du produit.", "kind": "decision",
                      "files": [f"{chemins_machine.PROJETS}/a.md", f"{chemins_machine.PROJETS}/b.md"]})
    ev = of(published, "report")[-1]
    assert ev["report_kind"] == "decision" and ev["report_files"] == [f"{chemins_machine.PROJETS}/a.md", f"{chemins_machine.PROJETS}/b.md"]
    assert reports.get(r["mid"])["kind"] == "decision" and reports.get(r["mid"])["files"] == ev["report_files"]
    replayed = [e for e in bus.snapshot()["history"] if e.get("mid") == r["mid"]]
    assert replayed[-1]["report_kind"] == "decision" and replayed[-1]["report_files"]  # rejeu
    j.on_command({"cmd": "read_report", "mid": r["mid"]})
    assert said == ["Il faut choisir le nom du produit."]  # les chemins ne sont jamais lus
    replayed = [e for e in bus.snapshot()["history"] if e.get("mid") == r["mid"]]
    assert replayed[-1]["report_kind"] == "decision" and not replayed[-1]["pending_read"]
    j.on_command({"cmd": "report", "text": "Rien de neuf.", "kind": "n'importe"})
    ev = of(published, "report")[-1]
    assert ev["report_kind"] == "info" and ev["report_files"] == []


def test_compte_rendu_de_fond_type_info(astrel, published):
    j, _, _ = astrel
    fond_turn(j)
    reply = of(published, "reply")[-1]
    assert reply["report_kind"] == "info" and reply["report_files"] == []
