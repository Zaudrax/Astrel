"""Journal d'audit unifié (plan P3, lot E) : écriture en ajout seul (0600, rotation),
lecture fusionnée (journal Total, routage des agents), branchements du cœur
(confirmations, commandes refusées à Claude, plans validés) et de WhatsApp."""
import json
import os
import time

import pytest

from astrel import agents, audit, config, guard
from astrel.whatsapp import WhatsApp
from test_reports import ch, astrel  # noqa: F401  (fixtures)
from test_whatsapp import Host, wa_env  # noqa: F401  (fixtures)
from conftest import mode_prive


def entries():
    return [json.loads(line) for line in audit.AUDIT_FILE.read_text(encoding="utf-8").splitlines()]


def test_ajout_seul_droits_et_filtre():
    audit.record("confirmation", "claude", "Bash : un push | git push  origin\nmain", "voix", ok=True)
    audit.record("whatsapp", "astrel", "message : la clé sk-ant-abcdefghijklmnopqrstuvwxyz0123", "", ok=True)
    audit.record("refus", "claude", "x" * 1000, "raison")
    got = entries()
    assert [e["kind"] for e in got] == ["confirmation", "whatsapp", "refus"]
    assert got[0]["what"] == "Bash : un push | git push origin main" and got[0]["ok"] is True
    assert "sk-ant" not in got[1]["what"] and "[secret masqué]" in got[1]["what"]
    assert len(got[2]["what"]) == audit.WHAT_MAX and "ok" not in got[2]
    assert mode_prive(audit.AUDIT_FILE, 0o600)


def test_rotation(monkeypatch):
    monkeypatch.setattr(audit, "ROTATE_BYTES", 300)
    for i in range(12):
        audit.record("refus", "claude", f"commande {i} " + "y" * 60)
    names = sorted(p.name for p in audit.AUDIT_FILE.parent.glob("audit.jsonl*"))
    assert names == ["audit.jsonl", "audit.jsonl.1", "audit.jsonl.2"]
    got = audit.read(limit=200)
    assert got[0]["what"].startswith("commande 11")  # la plus récente d'abord, fichiers tournés relus


def test_jamais_d_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "AUDIT_FILE", tmp_path / "absent" / "x" / "audit.jsonl")
    (tmp_path / "absent").write_text("un fichier, pas un dossier")
    audit.record("refus", "claude", "x")  # rien ne lève


def test_lecture_fusionnee(monkeypatch):
    now = time.time()
    config.TOTAL_LOG.write_text(
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 300)) + "\tMODE\tactivé\tHUD\n"
        + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 200)) + "\tBash\trm -r build\tune suppression\n"
        + "ligne illisible\n", encoding="utf-8")
    agents.ROUTE_LOG.write_text(json.dumps({
        "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - 100)), "type": "codeur", "desc": "corrige",
        "asked": "haiku", "final": "opus", "decision": "allow", "why": "codeur : opus"}) + "\n", encoding="utf-8")
    audit.record("plan", "utilisateur", "plan P3 version 1 validé", "hud", ok=True)
    got = audit.read()
    assert [e["kind"] for e in got] == ["plan", "agent", "total", "total"]
    assert got[1]["what"] == "agent codeur : corrige (opus)" and got[1]["auth"] == "allow codeur : opus"
    assert got[2]["what"] == "Bash : rm -r build" and got[2]["auth"].startswith("sans question (Total)")
    assert got[3] == {"ts": got[3]["ts"], "kind": "total", "who": "HUD", "what": "Sans confirmation activé",
                      "auth": "mode"}
    assert [e["kind"] for e in audit.read(kinds=["total"])] == ["total", "total"]
    assert [e["kind"] for e in audit.read(before=got[1]["ts"])] == ["total", "total"]
    assert len(audit.read(limit=1)) == 1 and len(audit.read(limit=10_000)) == 4
    assert audit.read(kinds=["inconnu"]) == []
    assert audit.parse_kinds("confirmation, refus") == ["confirmation", "refus"] and audit.parse_kinds("") is None


# --- branchements du cœur ---

def test_confirmation_notee_avec_qui_a_tranche(monkeypatch, tmp_path):
    from test_lot_hud import make_astrel

    monkeypatch.setattr(guard, "GRANTS_FILE", tmp_path / "grants.json")  # jamais celui de la machine
    j = make_astrel(monkeypatch, tmp_path)
    j._total = True
    assert j._ask_confirmation("Bash", "un push", "git push") is True
    j._total = False
    guard.grant("bash:push")
    assert j._ask_confirmation("Bash", "un push", "git push", key="bash:push") is True
    guard.revoke()
    assert j._ask_confirmation("Bash", "un push", "git push", alive=lambda: False) is False

    def by_voice(question, box, answered):
        box.update(allow=True, by="voix", always=True)
        answered.set()

    j._confirm_by_voice = by_voice
    j._wait_until_silent = lambda *a, **k: None
    j._voice_muted = lambda: False
    j._away = lambda: False
    assert j._ask_confirmation("Bash", "un push", "git push", key="bash:push") is True
    guard.revoke()
    got = [e for e in entries() if e["kind"] == "confirmation"]
    assert [(e["auth"], e["ok"]) for e in got] == [("total", True), ("30 min", True), ("abandon", False),
                                                    ("voix (oui pour 30 min)", True)]
    assert got[0]["what"] == "Bash : un push | git push"


def test_commande_de_claude_refusee_notee(astrel):
    j, _said, _chimes = astrel
    r = j.on_command({"cmd": "plan_validate", "plan_id": "P1", "version": 1, "_from_claude": True})
    assert r["ok"] is False
    j.on_command({"cmd": "plan_validate", "plan_id": "P1", "version": 1})  # l'utilisateur : pas un refus de Claude
    got = [e for e in entries() if e["kind"] == "refus"]
    assert len(got) == 1 and got[0]["what"] == "plan_validate" and "refusés à Claude" in got[0]["auth"]


def test_plan_valide_note(astrel):
    from astrel.plans import plans

    j, _said, _chimes = astrel
    r = plans.submit("Titre", "1. Faire\n2. Vérifier\n", "demande")
    j._plan_turn = lambda r: None
    out = j.on_command({"cmd": "plan_validate", "plan_id": r["id"], "version": r["version"], "_via": "hud"})
    assert out["ok"] is True
    got = [e for e in entries() if e["kind"] == "plan"]
    assert got[0]["who"] == "utilisateur" and got[0]["auth"] == "hud" and f"plan {r['id']} version 1" in got[0]["what"]


def test_envois_whatsapp_notes(wa_env):
    w = WhatsApp(Host(), gap_s=0)
    w.send_text("Bonjour, voici le point du jour.", "compta")
    got = [e for e in entries() if e["kind"] == "whatsapp"]
    assert got[0]["what"] == "message : Bonjour, voici le point du jour." and got[0]["auth"] == "compta"
    assert w.send_file("/etc/passwd")["ok"] is False  # refusé : rien de noté
    assert len([e for e in entries() if e["kind"] == "whatsapp"]) == 1


# --- commande du HUD (lot F) ---

def test_audit_get_utilisateur_seul(astrel, monkeypatch):
    j, _said, _chimes = astrel
    clock = iter(range(1000, 2000))
    monkeypatch.setattr(audit.time, "time", lambda: float(next(clock)))
    for i in range(5):
        audit.record("refus", "claude", f"commande {i}")
    audit.record("whatsapp", "astrel", "message : x")
    r = j.on_command({"cmd": "audit_get", "kinds": ["refus"], "limit": 3, "_via": "hud"})
    assert r["ok"] and [e["what"] for e in r["items"]] == ["commande 4", "commande 3", "commande 2"] and r["more"]
    nxt = j.on_command({"cmd": "audit_get", "kinds": "refus", "before": r["items"][-1]["ts"], "limit": 3})
    assert [e["what"] for e in nxt["items"]] == ["commande 1", "commande 0"] and not nxt["more"]
    assert j.on_command({"cmd": "audit_get", "limit": True, "before": "x"})["ok"]  # valeurs folles ignorées
    refused = j.on_command({"cmd": "audit_get", "_from_claude": True})
    assert refused == {"ok": False, "error": "journal d'audit réservé à l'utilisateur (HUD ou terminal), refusé à Claude"}
    assert entries()[-1]["kind"] == "refus" and entries()[-1]["what"] == "audit_get"  # le refus lui-même est noté


def test_page_ne_coupe_pas_un_meme_instant(monkeypatch):
    monkeypatch.setattr(audit.time, "time", lambda: 5000.0)  # trois entrées à la même seconde
    for i in range(3):
        audit.record("refus", "claude", f"meme {i}")
    monkeypatch.setattr(audit.time, "time", lambda: 4000.0)
    audit.record("refus", "claude", "avant")
    page = audit.read(kinds=["refus"], limit=2)
    assert [e["what"] for e in page] == ["meme 2", "meme 1", "meme 0"]  # groupe gardé entier
    assert [e["what"] for e in audit.read(kinds=["refus"], limit=2, before=page[-1]["ts"])] == ["avant"]
