"""rtk (20/10, plan P12, lot 2) : hook `astrel/rtk_hook.py` qui fait passer quelques commandes
Bash SIMPLES par rtk, sans jamais émettre de décision ; le garde-fou juge l'original."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from astrel import brain, config, guard, rtk_hook

RTK = Path("/opt/rtk")
DATA = Path("/data/run")


@pytest.mark.parametrize("cmd", [
    "git status", "  git status  ", "git log", "git log -5", "git log -n 20",
    "ls -l", "ls -la astrel", "ls -lah 'mon dossier'", "ls -al *.py",
])
def test_commandes_reecrites(cmd):
    new = rtk_hook.rewrite(cmd, RTK, DATA)
    assert new == ("env XDG_DATA_HOME=/data/run RTK_TELEMETRY_DISABLED=1 RTK_SUPPRESS_HOOK_WARNING=1 "
                   f"/opt/rtk {cmd.strip()}")


@pytest.mark.parametrize("cmd", [
    "", "git push", "git push origin main", "git status -s", "git status --porcelain", "git status x",
    "git diff", "git log -p", "git log --format=%H", "git log --stat -3", "git -C x status",
    "ls", "ls astrel", "ls -R", "ls -la --color", "ls -l -a", "grep -r x .",
    "git status | head", "git status && git push", "git status; rm -rf /tmp/x", "git log > /tmp/l",
    "ls -l $(pwd)", "ls -l `pwd`", "git status\ngit push", "FOO=1 git status", "cd x && git status",
    "git status &", "ls -l 'non fermé", "ls -l (x)", "rtk git status", "sudo ls -l",
])
def test_commandes_laissees_telles_quelles(cmd):
    assert rtk_hook.rewrite(cmd, RTK, DATA) is None


def _hook(payload, tmp_path, monkeypatch, switch=None, binary=True):
    rtk = tmp_path / "rtk"
    if binary:
        rtk.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(config, "RTK_BIN", rtk)
    monkeypatch.setattr(config, "RTK_SWITCH_FILE", tmp_path / "rtk.txt")
    monkeypatch.setattr(config, "RUNTIME", tmp_path)
    if switch is not None:
        (tmp_path / "rtk.txt").write_text(switch, encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps(payload) if not isinstance(payload, str) else payload))
    return rtk


def test_sortie_du_hook_sans_decision(tmp_path, monkeypatch, capsys):
    rtk = _hook({"tool_name": "Bash", "tool_input": {"command": "git status", "description": "État"}},
                tmp_path, monkeypatch)
    assert rtk_hook.main() == 0
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert "permissionDecision" not in out  # jamais d'allow : le garde-fou seul décide
    assert out["hookEventName"] == "PreToolUse"
    assert out["updatedInput"]["description"] == "État"
    assert out["updatedInput"]["command"].endswith(f"{rtk} git status")


@pytest.mark.parametrize("case", ["off", "absent", "autre_outil", "illisible", "non_reecrite"])
def test_le_hook_se_tait(case, tmp_path, monkeypatch, capsys):
    payload = {"tool_name": "Bash", "tool_input": {"command": "git status"}}
    if case == "autre_outil":
        payload = {"tool_name": "Read", "tool_input": {"file_path": "/x"}}
    if case == "non_reecrite":
        payload["tool_input"]["command"] = "git push"
    _hook("pas du json" if case == "illisible" else payload, tmp_path, monkeypatch,
          switch="off\n" if case == "off" else None, binary=case != "absent")
    assert rtk_hook.main() == 0 and capsys.readouterr().out == ""


def test_script_lance_seul_se_tait_sur_entree_illisible():
    proc = subprocess.run([sys.executable, str(config.RTK_HOOK_SCRIPT)], input="pas du json",
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0 and proc.stdout.strip() == ""


def test_reglages_declarent_rtk_seulement_si_installe(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "s.json")
    monkeypatch.setattr(config, "RTK_BIN", tmp_path / "absent")
    hooks = json.loads(brain.write_settings().read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    # P20 : navigateur de test ; P34 : ordre de quota glissé aux sous-agents (« * », `quota_hook.py`) ;
    # 01/11 : instantanés avant écriture (`hooks/suivi_outils.py`).
    s = config.SUIVI_MATCHER
    assert [h["matcher"] for h in hooks] == ["*", "Agent|Task", "mcp__playwright.*__browser_navigate", s, "*"]
    (tmp_path / "rtk").write_text("", encoding="utf-8")
    monkeypatch.setattr(config, "RTK_BIN", tmp_path / "rtk")
    hooks = json.loads(brain.write_settings().read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert [h["matcher"] for h in hooks] == ["*", "Agent|Task", "mcp__playwright.*__browser_navigate", s, "Bash", "*"]
    assert hooks[0]["hooks"][0]["command"] == config.hook_command(config.HOOK_SCRIPT)  # le garde-fou reste le premier
    assert str(config.RTK_HOOK_SCRIPT) in hooks[4]["hooks"][0]["command"]


def test_le_garde_fou_juge_toujours_l_original(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "MODE_FILE", tmp_path / "autonomy.json")
    monkeypatch.setattr(guard, "GRANTS_FILE", tmp_path / "grants.json")
    assert rtk_hook.rewrite("git push", RTK, DATA) is None
    assert guard.assess("Bash", {"command": "git push"}, mode="autonome", cwd=str(config.ROOT)).needs_confirm


REAL = config.ROOT / ".rtk" / "rtk"


@pytest.mark.skipif(not REAL.is_file() or not shutil.which("git"), reason="rtk non installé")
def test_vrai_rtk_sur_un_depot(tmp_path):
    repo = tmp_path / "depot"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "f.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    new = rtk_hook.rewrite("git status", REAL, tmp_path / "donnees")
    r = subprocess.run(["bash", "-c", new], cwd=repo, capture_output=True, text=True, timeout=30,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert r.returncode == 0 and "f.txt" in r.stdout and r.stderr == ""
    assert (tmp_path / "donnees" / "rtk" / "history.db").is_file()  # `rtk gain` lit ici, pas dans ~
    assert not (tmp_path / ".local" / "share" / "rtk").exists()
