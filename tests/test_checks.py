"""Surveillances par script des réflexes (déclencheur `check`) : aucun modèle."""
import os
import sys
from datetime import datetime, timedelta

import pytest

from astrel.senses import checks, reflex


def test_fichiers(tmp_path):
    f = tmp_path / "build.log"
    assert checks.run({"kind": "file_exists", "path": str(f)}) == (False, {"path": str(f)})
    assert checks.run({"kind": "file_missing", "path": str(f)})[0]
    f.write_text("…\nBUILD SUCCESSFUL in 3m\n")
    assert checks.run({"kind": "file_exists", "path": str(f)})[0]
    hit, values = checks.run({"kind": "file_contains", "path": str(f), "pattern": r"BUILD (SUCCESSFUL|FAILED)"})
    assert hit and values["output"] == "BUILD SUCCESSFUL"
    assert checks.run({"kind": "file_exists", "path": str(f), "not": True})[0] is False


def test_fichier_change_une_fois_par_changement(tmp_path):
    f = tmp_path / "etat.json"
    f.write_text("a")
    spec = {"kind": "file_changed", "path": str(f)}
    assert not checks.run(spec)[0]        # première vue : la référence
    assert not checks.run(spec)[0]
    f.write_text("bb")
    assert checks.run(spec)[0]
    assert not checks.run(spec)[0]


def test_commande_en_lecture_seule():
    hit, values = checks.run({"kind": "command", "argv": [sys.executable, "-c", "print('status: FINISHED')"],
                              "contains": "FINISHED"})
    assert hit and values["status"] == 0 and "FINISHED" in values["output"]
    assert not checks.run({"kind": "command", "argv": [sys.executable, "-c", "import sys; sys.exit(3)"]})[0]
    assert checks.run({"kind": "command", "argv": [sys.executable, "-c", "import sys; sys.exit(3)"], "exit": 3})[0]


@pytest.mark.parametrize("argv", [
    ["git", "push", "origin", "main"],
    ["sh", "-c", "rm -rf /"],
    ["sudo", "ls"],
    ["git", "commit", "-am", "x"],
])
def test_commande_qui_ecrit_refusee(argv):
    with pytest.raises(ValueError, match="lecture seule"):
        checks.validate({"kind": "command", "argv": argv})


@pytest.mark.parametrize("bad", [
    {"kind": "lune"},
    {"kind": "file_exists"},
    {"kind": "url", "url": "file:///etc/passwd"},
    {"kind": "command"},
    {"kind": "quota_above"},
    {"kind": "file_contains", "path": "/tmp/x", "pattern": "("},
])
def test_verifications_mal_formees(bad):
    with pytest.raises(Exception):
        checks.validate(bad)


def test_quota():
    quota = lambda: {"windows": {"five_hour": {"utilization": 0.93}, "seven_day": {"utilization": 0.4}}}
    assert checks.run({"kind": "quota_above", "pct": 90}, quota=quota) == (True, {"pct": 93})
    assert not checks.run({"kind": "quota_above", "pct": 90, "window": "seven_day"}, quota=quota)[0]


def test_reflexe_check_reveille_le_cerveau_seulement_quand_c_est_pret(tmp_path, monkeypatch):
    done = tmp_path / "app.apk"
    rules = [{"id": "build", "name": "Build fini", "enabled": True, "once": True,
              "when": {"type": "check", "every_s": 60, "check": {"kind": "file_exists", "path": str(done)}},
              "do": {"type": "ask", "prompt": "Le build est prêt ({path}). Préviens l'utilisateur en une phrase."}}]
    monkeypatch.setattr(reflex, "RULES_FILE", tmp_path / "r.json")
    reflex.save_rules(rules)
    sent = []
    engine = reflex.Reflex(command=sent.append, presence=lambda: (False, False, 0.0))
    t = datetime(2026, 10, 14, 10, 0)
    assert engine.tick(t) == [] and sent == []           # rien : zéro token
    done.write_text("apk")
    assert engine.tick(t + timedelta(seconds=20)) == []  # pas revérifié avant every_s
    assert engine.tick(t + timedelta(seconds=61)) == ["build"]
    assert sent == [{"cmd": "say", "text": f"Le build est prêt ({done}). Préviens l'utilisateur en une phrase.",
                     "source": "réflexe", "reflex": "build"}]
    assert reflex.load_rules()[0]["enabled"] is False    # `once` : désactivé après coup
    assert engine.tick(t + timedelta(seconds=200)) == []


def test_creation_d_un_reflexe_check(tmp_path, monkeypatch):
    monkeypatch.setattr(reflex, "RULES_FILE", tmp_path / "r.json")
    reflex.save_rules([])
    reflex.create_rule({"id": "site", "name": "Site", "when": {"type": "check", "check": {
        "kind": "url", "url": "https://exemple.fr", "status": 200, "not": True}},
        "do": {"type": "announce", "text": "Le site ne répond plus ({status})."}})
    with pytest.raises(ValueError):
        reflex.create_rule({"id": "mal", "name": "Mal", "when": {"type": "check", "check": {
            "kind": "command", "argv": ["git", "push"]}}, "do": {"type": "announce", "text": "x"}})
    assert [r["id"] for r in reflex.load_rules()] == ["site"]


def test_reflexes_livres_valides():
    """Les exemples de config/reflexes.json passent la validation."""
    for rule in reflex.load_rules():
        if rule["when"].get("type") == "check":
            checks.validate(rule["when"]["check"])
