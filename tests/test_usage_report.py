"""tools/usage_report.py : consommation lue dans les transcriptions."""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("usage_report", Path(__file__).parent.parent / "tools" / "usage_report.py")
usage_report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(usage_report)


def entry(mid, model, ts, **usage):
    return json.dumps({"type": "assistant", "timestamp": ts, "entrypoint": "sdk-cli",
                       "message": {"id": mid, "model": model, "usage": usage}}) + "\n"


def test_rapport(tmp_path):
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    old = (now - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    main = tmp_path / "abc.jsonl"
    main.write_text(
        json.dumps({"type": "user", "entrypoint": "sdk-cli", "timestamp": recent}) + "\n"
        # même message répété par bloc de contenu : une seule requête, sortie au plus haut
        + entry("m1", "claude-sonnet-5-5", recent, input_tokens=5, cache_read_input_tokens=100_000, output_tokens=10)
        + entry("m1", "claude-sonnet-5-5", recent, input_tokens=5, cache_read_input_tokens=100_000, output_tokens=300)
        + entry("m0", "claude-sonnet-5-5", old, cache_read_input_tokens=9_999_999)   # hors période
        + entry("m2", "<synthetic>", recent, output_tokens=1))
    sub = tmp_path / "abc" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a1.jsonl").write_text(
        entry("s1", "claude-opus-5-5", recent, cache_creation_input_tokens=20_000, output_tokens=2_000)
        + entry("s2", "claude-opus-5-5", recent, cache_read_input_tokens=22_000, output_tokens=500))
    (sub / "agent-a1.meta.json").write_text(json.dumps({"agentType": "codeur", "description": "Corriger le micro"}))

    report = usage_report.collect(tmp_path, days=7)
    rows = {(r["kind"], r["model"]): r for r in report["rows"]}
    main_row = rows[("principal", "claude-sonnet-5-5")]
    assert main_row["requests"] == 1 and main_row["output_tokens"] == 300
    assert main_row["cache_read_input_tokens"] == 100_000 and main_row["origin"] == "Astrel"
    sub_row = rows[("sous-agent", "claude-opus-5-5")]
    assert sub_row["requests"] == 2 and sub_row["cache_creation_input_tokens"] == 20_000
    assert report["agents"][0]["type"] == "codeur" and report["agents"][0]["total"] == 44_500
    text = usage_report.render(report, top=5)
    assert "Corriger le micro" in text and "TOTAL" in text and "sous-agent" in text
    assert usage_report.main(["--dir", str(tmp_path), "--days", "1", "--json"]) == 0


def _bash_lines(ts, calls):
    out = ""
    for i, (cmd, result) in enumerate(calls):
        out += json.dumps({"type": "assistant", "timestamp": ts, "message": {"id": f"b{i}", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": cmd}}]}}) + "\n"
        out += json.dumps({"type": "user", "timestamp": ts, "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": result}]}}) + "\n"
    return out


def test_programme_d_une_commande_bash():
    p = usage_report.bash_program
    assert p("git status") == "git status"
    assert p("cd /x && git log -3 | head") == "git log"
    assert p("FOO=1 env BAR=2 timeout 30 grep -r x .") == "grep"
    assert p("/data/x/.rtk/rtk git status") == "rtk git"
    assert p("ls -la") == "ls" and p("") == "?"


def test_sorties_bash_par_programme_et_periode(tmp_path):
    from datetime import datetime, timedelta, timezone
    day = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    (tmp_path / "s.jsonl").write_text(
        _bash_lines("2026-10-10T12:00:00Z", [("git status", "x" * 100), ("cd a && git status", [{"type": "text", "text": "y" * 50}]),
                                             ("ls -l", "z" * 10)])
        + _bash_lines("2026-10-15T12:00:00Z", [("grep -r a .", "w" * 999)]))
    rep = usage_report.collect(tmp_path, since=day - timedelta(hours=1), until=day + timedelta(hours=1))
    assert rep["bash"] == {"git status": {"calls": 2, "chars": 150}, "ls": {"calls": 1, "chars": 10}}
    assert "Sorties Bash" in usage_report.render(rep, top=5)
    assert usage_report.main(["--dir", str(tmp_path), "--from", "2026-10-15", "--to", "2026-10-15", "--json"]) == 0
    assert usage_report.main(["--dir", str(tmp_path), "--from", "2026-10-15", "--to", "2026-10-10"]) == 2
    assert usage_report.main(["--dir", str(tmp_path), "--to", "2026-10-15"]) == 2


def test_colonne_travail_et_plusieurs_dossiers(tmp_path):
    """04/11 (T50) : travail = entrée + cache écrit + sortie (le cache relu à part), par ligne, par agent et
    par jour ; `--dir` répété additionne les dossiers."""
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    a, b = tmp_path / "a", tmp_path / "b"
    for folder, mid in ((a, "m1"), (b, "m2")):
        folder.mkdir()
        (folder / f"{mid}.jsonl").write_text(entry(mid, "claude-sonnet-5-5", recent, input_tokens=10,
                                                   cache_read_input_tokens=90_000, cache_creation_input_tokens=1_000,
                                                   output_tokens=200))
    sub = a / "m1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-x.jsonl").write_text(entry("s1", "claude-opus-5-5", recent, cache_creation_input_tokens=5_000,
                                             cache_read_input_tokens=40_000, output_tokens=700))
    rep = usage_report.collect_all([a, b], days=1)
    row = next(r for r in rep["rows"] if r["kind"] == "principal")
    assert row["requests"] == 2 and usage_report.travail(row) == 2 * 1_210
    assert rep["agents"][0]["travail"] == 5_700 and rep["agents"][0]["end_ts"]
    assert sum(rep["by_day_work"].values()) == 2 * 1_210 + 5_700
    text = usage_report.render(rep, top=3)
    assert "travail" in text and "Par jour (travail)" in text
    assert usage_report.main(["--dir", str(a), "--dir", str(b), "--days", "1", "--json"]) == 0
