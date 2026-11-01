"""Politique d'outils lue par le garde-fou (20/10, plan P12, lot 6 ; idée d'openclaw) :
`hooks/tool-policy.json` règle la gravité d'un outil MCP sans abaisser une gravité CRITIQUE
du code, et refuse d'office les outils de « refuser », sans carte."""
import io
import json
import os
import sys

import pytest

from astrel import config, doctor, guard


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "MODE_FILE", tmp_path / "autonomy.json")
    monkeypatch.setattr(guard, "GRANTS_FILE", tmp_path / "grants.json")


def poser(tmp_path, data, raw=None):
    f = guard.TOOL_POLICY_FILE
    f.write_text(raw if raw is not None else json.dumps(data), encoding="utf-8")
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))  # deux écritures dans la même ms
    return f


def asks(tool, mode="prudent"):
    return guard.assess(tool, {}, mode=mode).needs_confirm


def test_sans_fichier_les_valeurs_du_code():
    assert not guard.TOOL_POLICY_FILE.exists()
    assert asks("mcp__context7__query-docs") and not asks("mcp__context7__query-docs", "autonome")
    assert guard.refused("mcp__context7__query-docs") == ""
    assert doctor.check_tool_policy() == (doctor.OK, "aucun fichier (valeurs du code)")


def test_libre_sensible_critique(tmp_path):
    poser(tmp_path, {"mcp": {"context7": {"*": "libre"},
                             "astrel": {"memory_write": "critique", "screen_capture": "sensible"},
                             "playwright2": {"*": "libre", "browser_evaluate": "sensible"}}})
    assert not asks("mcp__context7__query-docs") and not asks("mcp__context7__resolve-library-id")
    assert asks("mcp__astrel__memory_write", "autonome")          # relevée : demandée même en autonome
    assert asks("mcp__astrel__screen_capture") and not asks("mcp__astrel__memory_read")
    assert not asks("mcp__playwright2__browser_click") and asks("mcp__playwright2__browser_evaluate")
    assert doctor.check_tool_policy()[0] == doctor.OK


def test_une_gravite_critique_du_code_n_est_jamais_abaissee(tmp_path):
    poser(tmp_path, {"mcp": {"astrel": {"power_action": "libre"}, "claude_ai_Gmail": {"*": "libre"}}})
    assert asks("mcp__astrel__power_action", "autonome")
    assert asks("mcp__claude_ai_Gmail__send_message", "autonome")
    assert asks("mcp__claude_ai_Gmail__delete_label", "autonome")
    assert not asks("mcp__claude_ai_Gmail__search_threads")       # sensible du code : abaissable


@pytest.mark.parametrize("raw", [
    "pas du json", '["liste"]', '{"mcp": []}', '{"refuser": "x"}', '{"mcp": {"s": {"t": "permis"}}}',
    '{"mcp": {"s": "libre"}}', '{"refuser": [""]}',
])
def test_fichier_illisible_ignore_et_signale(tmp_path, raw):
    poser(tmp_path, None, raw=raw)
    assert asks("mcp__context7__query-docs") and guard.refused("mcp__x__y") == ""
    level, reason = doctor.check_tool_policy()
    assert level == doctor.ALERT and "ignorée" in reason
    assert asks("mcp__astrel__power_action", "autonome")


def test_refuser_d_office(tmp_path):
    poser(tmp_path, {"refuser": ["mcp__claude_ai_Dropbox__*", "CronCreate"]})
    assert "refusé par la politique" in guard.refused("mcp__claude_ai_Dropbox__search_jobs")
    assert guard.refused("CronCreate") and not guard.refused("CronDelete")
    assert not guard.refused("mcp__claude_ai_Gmail__search_threads")


def test_relu_quand_le_fichier_change(tmp_path):
    poser(tmp_path, {"mcp": {"context7": {"*": "libre"}}})
    assert not asks("mcp__context7__query-docs")
    poser(tmp_path, {"mcp": {"context7": {"*": "sensible"}}})
    assert asks("mcp__context7__query-docs")
    guard.TOOL_POLICY_FILE.unlink()
    assert asks("mcp__context7__query-docs")


def _hook(monkeypatch, capsys, tool):
    import importlib.util

    spec = importlib.util.spec_from_file_location("ph_test", config.HOOK_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    asked = []
    monkeypatch.setattr(mod.confirm, "request", lambda *a, **k: asked.append(a) or True)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_name": tool, "tool_input": {}})))
    assert mod.main() == 0
    return json.loads(capsys.readouterr().out)["hookSpecificOutput"], asked


def test_le_hook_refuse_sans_carte(tmp_path, monkeypatch, capsys):
    poser(tmp_path, {"refuser": ["mcp__claude_ai_Dropbox__*"]})
    out, asked = _hook(monkeypatch, capsys, "mcp__claude_ai_Dropbox__search_jobs")
    assert out["permissionDecision"] == "deny" and "politique" in out["permissionDecisionReason"]
    assert asked == []  # aucune carte
    out, asked = _hook(monkeypatch, capsys, "Read")
    assert out["permissionDecision"] == "allow" and asked == []


def test_le_fichier_est_protege_et_dans_l_empreinte():
    real = config.ROOT / "hooks" / "tool-policy.json"
    v = guard.assess("Write", {"file_path": str(real)}, mode="autonome")
    assert v.needs_confirm and v.guard
    v = guard.assess("Bash", {"command": f"echo '{{}}' > {real}"}, mode="autonome")
    assert v.needs_confirm and v.level == guard.CRITICAL
    assert real in doctor.GUARD_FILES


def test_le_fichier_livre_est_valide(monkeypatch):
    real = config.ROOT / "hooks" / "tool-policy.json"
    if not real.exists():
        pytest.skip("pas encore posé")
    monkeypatch.setattr(guard, "TOOL_POLICY_FILE", real)
    assert guard.policy_error() == "" and guard._tool_policy()["mcp"]["context7"] == {"*": ""}


def test_valeur_en_liste_ne_plante_pas_et_garde_refuser(tmp_path):
    """Relecture 20/10 : une valeur non textuelle levait TypeError (tous les outils refusés) ;
    un fichier devenu illisible gardait aussi la liste « refuser » du dernier fichier valide."""
    poser(tmp_path, {"mcp": {}, "refuser": ["OutilInterdit"]})
    assert guard.refused("OutilInterdit")
    poser(tmp_path, {"mcp": {"s": {"t": ["libre"]}}, "refuser": ["OutilInterdit"]})
    assert guard.refused("Bash") == ""
    assert guard.refused("OutilInterdit")
    assert guard.policy_error()
