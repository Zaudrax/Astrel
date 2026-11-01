"""Premier lancement et diagnostic (17/10, plan P4, lot 4) : liste de contrôle tirée de
`tools/check_install.py --json` (sous-processus, fil à part), marqueur
`runtime/first-run-done`, rapport à coller anonymisé."""
import json
import sys
import time

import pytest

from astrel import config, demarrage as dem
from astrel.settings import reglages
from test_install import chk
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)

RESULT = {"checks": [{"name": "Claude Code", "status": "OK", "detail": "2.1.290", "fix": ""},
                     {"name": "connexion Claude", "status": "MANQUE", "detail": "non connecté",
                      "fix": "lance « claude » une fois"},
                     {"name": "micro", "status": "ALERTE", "detail": "aucun micro vu", "fix": "branche un micro"},
                     {"name": "carte graphique", "status": "INFO", "detail": "pas de carte NVIDIA", "fix": ""}],
          "bloquant": 1, "rapport": "===== Rapport Astrel (à coller) =====\n[OK] Claude Code : 2.1.290\n====="}


@pytest.fixture
def fake(tmp_path, monkeypatch):
    script = tmp_path / "check.py"
    script.write_text(f"import json, time\ntime.sleep(0.2)\nprint(json.dumps({RESULT!r}))\n")
    d = dem.Demarrage()
    monkeypatch.setattr(d, "_command", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(dem, "demarrage", d)
    return d


def wait(cond, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


def lines(view):
    return {line["label"]: line for line in view["value"]["lines"]}


def test_liste_de_controle_hors_du_fil(fake):
    first = fake.view()
    assert lines(first)["Contrôle"]["text"] == "en cours…"
    assert first["actions"][0] == {"id": "check", "label": "Revérifier", "disabled": True, "reason": "vérification en cours"}
    assert wait(lambda: fake._result is not None)
    got = lines(fake.view())
    assert got["Bilan"] == {"label": "Bilan", "text": "1 point(s) bloquant(s) à corriger", "level": "error"}
    assert got["Claude Code"]["level"] == "ok" and got["Claude Code"]["text"] == "2.1.290"
    assert got["connexion Claude"]["level"] == "error" and "→ lance « claude »" in got["connexion Claude"]["text"]
    assert got["micro"]["level"] == "warn" and got["carte graphique"]["level"] == "info"


def test_premier_lancement(fake, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FIRST_RUN_FILE", tmp_path / "runtime" / "first-run-done")
    monkeypatch.setattr(config, "SESSION_FILE", tmp_path / "session.json")
    assert dem.first_run()
    d = reglages.describe()
    groups = {g["id"]: g for g in d["groups"]}
    assert d["first_run"] is True and groups["demarrage"]["open"] is True and groups["compte"]["open"] is True
    assert "done" in [a["id"] for a in fake.view()["actions"]]
    res = reglages.action("demarrage", "done", {})
    assert res["ok"] and config.FIRST_RUN_FILE.exists()
    d = reglages.describe()
    assert d["first_run"] is False and "open" not in {g["id"]: g for g in d["groups"]}["demarrage"]
    assert "done" not in [a["id"] for a in fake.view()["actions"]]


def test_seul_le_marqueur_compte(tmp_path, monkeypatch):
    """18/10 (plan P5) : une conversation passée ne suffit plus, un ami qui a essayé
    `./run.sh --text` avant d'ouvrir le HUD doit voir le guide."""
    monkeypatch.setattr(config, "FIRST_RUN_FILE", tmp_path / "neuf" / "first-run-done")
    session = tmp_path / "session.json"
    session.write_text("{}")
    monkeypatch.setattr(config, "SESSION_FILE", session)
    assert dem.first_run()
    assert dem.mark_first_run_done()["ok"] and not dem.first_run()


def test_diagnostic_a_coller(fake):
    fake.refresh(wait=True)
    v = fake.diagnostic_view()
    assert "block" not in v["value"] and v["actions"][0]["label"] == "Préparer le rapport"  # rien avant le clic
    res = reglages.action("diagnostic", "report", {})
    assert res["ok"] and "Copier" in res["text"]
    assert wait(lambda: "block" in fake.diagnostic_view()["value"])
    v = fake.diagnostic_view()
    assert v["value"]["block"].startswith("===== Rapport Astrel") and v["actions"][0]["label"] == "Refaire le rapport"


def test_controle_illisible(fake, monkeypatch, tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("print('pas du json')\n")
    monkeypatch.setattr(fake, "_command", lambda: [sys.executable, str(bad)])
    fake.refresh(wait=True)
    assert lines(fake.view())["Contrôle"] == {"label": "Contrôle", "text": "résultat du contrôle illisible", "level": "warn"}


def test_check_install_json_porte_le_rapport_anonyme(monkeypatch, capsys):
    from pathlib import Path

    monkeypatch.setattr(chk, "run_checks", lambda ci=False: [
        chk.Check("haut-parleur", chk.OK, "2 sortie(s)"),
        chk.Check("x", chk.INFO, f"{Path.home()}/a, prenom.nom@exemple.fr")])
    assert chk.main(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["rapport"].startswith("===== Rapport Astrel (à coller) =====")
    assert "[OK] haut-parleur : 2 sortie(s)" in data["rapport"]
    assert str(Path.home()) not in data["rapport"] and "@exemple.fr" not in json.dumps(data)


def test_haut_parleur_controle(monkeypatch):
    monkeypatch.setattr(chk.shutil, "which", lambda t: f"/usr/bin/{t}")
    outs = {("pactl", "info"): (0, "Server Name: PulseAudio (on PipeWire 1.4)"),
            ("pactl", "list", "short", "sources"): (0, "1\tmicro\n"),
            ("pactl", "list", "short", "sinks"): (0, "")}
    monkeypatch.setattr(chk, "_out", lambda cmd, timeout=15.0: outs[tuple(cmd)])
    got = {c.name: c for c in chk.check_audio("linux", ci=False)}
    assert got["haut-parleur"].status == chk.ALERTE and got["micro"].status == chk.OK
