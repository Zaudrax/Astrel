"""`./run.sh --doctor` (15/10, idées HERMES 1, 2, 46, 47, 70) : un contrôle de santé
en lecture seule, une ligne par contrôle, code 1 s'il reste une alerte."""
import json
import os

import pytest

from astrel import cli, config, doctor, state


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """Réglages du CLI tels que `brain.write_settings` les écrit (hook pour « * »)."""
    f = tmp_path / "claude-settings.json"
    f.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
        {"type": "command", "command": config.hook_command(config.HOOK_SCRIPT)}]}]}}), encoding="utf-8")
    monkeypatch.setattr(config, "SETTINGS_FILE", f)
    return f


# ------------------------------------------------------------------ hook

def test_le_vrai_hook_refuse_quand_il_plante(settings):
    level, reason = doctor.check_hook()
    assert level == doctor.OK, reason


def test_hook_qui_laisse_passer_en_plantant_est_signale(settings, tmp_path, monkeypatch):
    """L'ancien hook : une exception du garde-fou sortait en code 1, que Claude Code
    traite comme une erreur non bloquante (l'action passe)."""
    script = tmp_path / "hook.py"
    script.write_text("import json, sys\n"
                      "payload = json.load(sys.stdin)\n"
                      "from astrel import guard\n"
                      "guard.assess('Bash', {})\n"
                      "print(json.dumps({'hookSpecificOutput': {'permissionDecision': 'allow'}}))\n",
                      encoding="utf-8")
    monkeypatch.setattr(config, "HOOK_SCRIPT", script)
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
        {"command": config.hook_command(script)}]}]}}), encoding="utf-8")
    level, reason = doctor.check_hook()
    assert level == doctor.ALERT
    assert "entrée illisible" in reason and "laisserait passer" in reason


def test_hook_declare_sans_mode_isole(settings):
    """23/10 (audit P29) : l'ancienne ligne (sans -I/-S, sans « || exit 2 ») est signalée tant que
    Astrel n'a pas été relancé (le cerveau réécrit ses réglages au démarrage)."""
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
        {"command": config.python_command(config.HOOK_SCRIPT)}]}]}}), encoding="utf-8")
    level, reason = doctor.check_hook()
    assert level == doctor.ALERT and "mode isolé" in reason


def test_hook_absent_des_reglages(settings):
    settings.write_text(json.dumps({"hooks": {"PreToolUse": []}}), encoding="utf-8")
    assert doctor.check_hook() == (doctor.ALERT, "hook absent des réglages du CLI (PreToolUse « * »)")
    settings.unlink()
    assert doctor.check_hook()[0] == doctor.ALERT


# ------------------------------------------------------------------ empreinte

@pytest.fixture
def guard_files(tmp_path, monkeypatch):
    files = []
    for name in ("a.py", "b.py"):
        f = tmp_path / "astrel" / name
        f.parent.mkdir(exist_ok=True)
        f.write_text(f"# {name}\n", encoding="utf-8")
        files.append(f)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(doctor, "GUARD_FILES", files)
    monkeypatch.setattr(doctor, "FINGERPRINT_FILE", tmp_path / "empreinte.json")
    return files


def test_empreinte_absente_puis_retenue_puis_changee(guard_files, monkeypatch):
    assert doctor.check_fingerprint()[0] == doctor.ALERT
    monkeypatch.setattr(doctor, "launched_by_claude", lambda: False)
    assert doctor.accept()[0]
    assert doctor.check_fingerprint() == (doctor.OK, "inchangée (2 fichiers)")
    guard_files[1].write_text("# modifié\n", encoding="utf-8")
    level, reason = doctor.check_fingerprint()
    assert level == doctor.ALERT and reason.endswith(": b.py")


def test_claude_ne_peut_pas_valider_l_empreinte(guard_files, monkeypatch):
    monkeypatch.setattr(doctor, "launched_by_claude", lambda: True)
    ok, message = doctor.accept()
    assert not ok and message.startswith("Refusé")
    assert not doctor.FINGERPRINT_FILE.exists()


def test_process_de_claude_reconnu(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    assert doctor.launched_by_claude()
    monkeypatch.delenv("CLAUDECODE")
    monkeypatch.setattr(state, "cgroup_of", lambda pid: "/user.slice/app.slice/astrel.service")
    assert doctor.launched_by_claude()
    monkeypatch.setattr(state, "cgroup_of", lambda pid: "/user.slice/app.slice/vte-spawn.scope")
    assert not doctor.launched_by_claude(1)


def test_empreinte_couvre_le_hook_et_le_garde_fou():
    names = {p.name for p in doctor.GUARD_FILES}
    assert {"permission_hook.py", "confirm.py", "totalgate.py"} <= names
    assert doctor.FINGERPRINT_FILE.parent == config.ROOT / "hooks"  # protégé par le garde-fou


# ------------------------------------------------------------------ MCP

def test_serveur_mcp_connecteurs_joignable():
    assert doctor.probe_mcp("astrel.mcp_connectors") > 0


def test_serveur_mcp_injoignable_ou_non_declare(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "MCP_SERVERS", {"astrel": "astrel.module_qui_n_existe_pas"})
    monkeypatch.setattr(config, "MCP_CONFIG_FILE", tmp_path / "absent.json")
    level, reason = doctor.check_mcp("astrel")
    assert level == doctor.ALERT and reason.startswith("injoignable")
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {"playwright": {}}}), encoding="utf-8")
    monkeypatch.setattr(config, "MCP_CONFIG_FILE", tmp_path / "mcp.json")
    assert doctor.check_mcp("astrel") == (doctor.ALERT, "non déclaré au CLI")


# ------------------------------------------------------------------ droits

def test_droits_sans_jamais_le_chemin(tmp_path, monkeypatch):
    token = tmp_path / "secret-token"
    token.write_text("ne-jamais-afficher", encoding="utf-8")
    os.chmod(token, 0o600)
    folder = tmp_path / "profil"
    folder.mkdir(mode=0o700)
    monkeypatch.setattr(doctor, "SENSITIVE", [("jeton du HUD", token, False), ("profil", folder, True),
                                              ("absent", tmp_path / "rien", False)])
    assert doctor.check_permissions() == (doctor.OK, "2 fichiers sensibles fermés aux autres")
    os.chmod(token, 0o644)
    code, lines = doctor.run([("droits", doctor.check_permissions)])
    assert code == 1
    assert lines == ["ALERTE  droits : jeton du HUD accessible aux autres (0644)"]
    assert str(tmp_path) not in lines[0] and "ne-jamais-afficher" not in lines[0]


# ------------------------------------------------------------------ socket, coffre, réflexes

def test_socket_de_controle(monkeypatch):
    monkeypatch.setattr(state, "send_command", lambda cmd, timeout=3.0: None)
    assert doctor.check_control()[0] == doctor.ALERT
    monkeypatch.setattr(state, "send_command", lambda cmd, timeout=3.0: json.dumps({"ok": True, "state": "sleeping"}))
    assert doctor.check_control() == (doctor.OK, "répond (état : sleeping)")


def test_coffre(tmp_path, monkeypatch):
    from astrel.tools import memory

    monkeypatch.setattr(memory, "VAULT", tmp_path / "vault")
    assert doctor.check_vault()[0] == doctor.ALERT
    (tmp_path / "vault").mkdir()
    (tmp_path / "vault" / "Accueil.md").write_text("# Accueil\n", encoding="utf-8")
    assert doctor.check_vault() == (doctor.OK, "lisible, 1 notes")


def test_reflexes(tmp_path):
    from astrel.senses import reflex

    reflex.RULES_FILE.write_text("{pas du json", encoding="utf-8")
    assert doctor.check_reflexes()[0] == doctor.ALERT
    reflex.RULES_FILE.write_text(json.dumps([{"id": "a", "enabled": True, "when": {}, "do": {}}, {"id": "b"}]),
                                 encoding="utf-8")
    assert doctor.check_reflexes() == (doctor.ALERT, "règle(s) incomplète(s) : b")
    reflex.RULES_FILE.write_text(json.dumps([{"id": "a", "enabled": True, "when": {}, "do": {}}]), encoding="utf-8")
    assert doctor.check_reflexes() == (doctor.OK, "1 règles valides (1 actives)")


# ------------------------------------------------------------------ ensemble

def test_un_controle_qui_plante_devient_une_alerte_sans_chemin():
    def boom():
        raise PermissionError(13, "Permission non accordée", "/chemin/secret")

    code, lines = doctor.run([("a", lambda: (doctor.OK, "bien")), ("b", boom)])
    assert code == 1
    assert lines == ["OK      a : bien", "ALERTE  b : contrôle en échec (PermissionError)"]
    assert doctor.run([("a", lambda: (doctor.OK, "bien"))])[0] == 0


def test_commande_doctor(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "run", lambda: (1, ["ALERTE  x : y"]))
    assert cli.main(["doctor"]) == 1
    assert cli.main(["--doctor"]) == 1
    assert capsys.readouterr().out.count("ALERTE  x : y") == 2
