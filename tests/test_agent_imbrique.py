"""Agent IA imbriqué (05/11, trou confirmé, autorisation explicite de l'utilisateur ; docs/DURCISSEMENT-GARDE-FOU.md,
section « Agents imbriqués »).

`claude -p "…"` lancé par le Bash de Claude passait sans question ; ce CLI ne charge pas le hook d'Astrel (posé par
le seul `--settings` du cerveau) : il agissait sans garde-fou. Lancer un autre agent IA = plafond (ni Total, ni
« oui pour 30 min », ni levée), case `agent_imbrique`. Restent libres : aide, version, gestion du CLI claude."""
import pytest

from astrel import config, guard, limites, totalgate
from conftest import CheminBash

R = CheminBash(config.ROOT)


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "MODE_FILE", tmp_path / "autonomy.json")
    monkeypatch.setattr(guard, "GRANTS_FILE", tmp_path / "grants.json")


def _assess(cmd: str, cwd="/tmp"):
    return guard.assess("Bash", {"command": cmd}, mode="autonome", cwd=str(cwd))


def _agent(v) -> None:
    assert v.needs_confirm and v.guard is True, v.desc
    assert guard.L_AGENT_IMBRIQUE in v.desc, v.desc
    assert limites.AGENT_IMBRIQUE in v.cats and limites.PLAFOND in v.cats, v.cats
    assert not v.keys and not v.lift  # ni « oui pour 30 min », ni levée
    assert not totalgate.total_grants(True, v.guard, False, v.cats, dict.fromkeys(limites.PAR_ID, True))


@pytest.mark.parametrize("cmd", [
    # le nom, le chemin, le binaire natif
    "claude -p bonjour",
    "claude",
    "claude --dangerously-skip-permissions 'fais x'",
    "~/.local/bin/claude -p x",
    "/usr/local/bin/claude -p x",
    "~/.local/share/claude/versions/2.1.285 -p x",
    "claude.exe -p x",
    "claude mcp serve",  # les outils du CLI (Bash, Edit…) servis sans garde-fou
    # derrière un lanceur
    "command claude -p x",
    "env FOO=1 claude -p x",
    "exec claude -p x",
    "nohup claude -p x",
    "setsid claude -p x",
    "timeout 30 claude -p x",
    "echo x | xargs claude -p",
    "bash -c 'claude -p x'",
    "bash <<'E'\nclaude -p x\nE",
    "python3 -c \"import subprocess; subprocess.run(['claude', '-p', 'x'])\"",
    "python3 - <<'E'\nimport os\nos.system('claude -p x')\nE",
    "python3 -c 'from claude_agent_sdk import query'",
    # paquet npm, node
    "npx @anthropic-ai/claude-code -p x",
    "npx -y @anthropic-ai/claude-code@latest -p x",
    "pnpm dlx @anthropic-ai/claude-code -p x",
    "bunx @anthropic-ai/claude-code -p x",
    "node ~/.npm-global/lib/node_modules/@anthropic-ai/claude-code/cli.js -p x",
    # autres CLI d'agents qui exécutent des outils
    "codex exec 'fais x'",
    "gemini -p x",
    "aider --message x",
    "opencode run x",
    "goose run -t x",
    "cursor-agent -p x",
    "amp -x x",
    "grok -p x",
    "qwen -p x",
    "crush run x",
    "openhands",
    "uvx aider-chat",
    "pipx run aider-chat",
    "python -m aider",
    # variables d'un CLI de Claude posées pour lancer
    "CLAUDE_CONFIG_DIR=/tmp/c python3 agent.py",
    "export CLAUDE_CODE_USE_BEDROCK=1",
    "env -u CLAUDECODE python3 agent.py",
    # formes cachées (demandées par la consigne)
    "C=claude; $C -p x",
    '"cl""aude" -p x',
    "$(which claude) -p x",
    '"/proc/$PPID/exe" -p x',  # le binaire du cerveau lui-même
    # multiplexeurs et terminaux (avant le 05/11 : rien n'y était lu)
    "tmux new -d 'claude -p x'",
    "screen -dmS w claude -p x",
    "gnome-terminal -- claude -p x",
])
def test_agent_imbrique_au_plafond(cmd):
    _agent(_assess(cmd))


@pytest.mark.parametrize("cmd", [
    "echo Y2xhdWRlIC1wIHg= | base64 -d | sh",  # contenu illisible : doute (critique, jamais « 30 min »)
    "$C -p x",                                  # programme calculé inconnu : doute
])
def test_formes_illisibles_demandees(cmd):
    v = _assess(cmd)
    assert v.needs_confirm and not v.keys, v.desc


def test_multiplexeur_lit_aussi_les_autres_dangers():
    v = _assess("tmux new -d 'sudo reboot'")
    assert v.needs_confirm and guard.L_ADMIN in v.desc, v.desc


@pytest.mark.parametrize("cmd", [
    "claude --version",
    "claude -v",
    "claude --help 2>&1 | grep -i tools",
    "claude auth login --help",
    "claude doctor",
    "claude auth status --json",
    "claude mcp list",
    "claude mcp get playwright",
    "claude plugin list",
    "npx @anthropic-ai/claude-code --version",
    "codex --version",
    "gemini --help",
    "which claude",
    "command -v claude",
    "grep -rn -- --dangerously-skip-permissions astrel/",
    "grep -rn 'claude -p' astrel/",
    "git commit -m 'claude -p'",
    "cat docs/README.md",
    "tmux ls",
    "screen -ls",
    ".venv/bin/python -m pytest -q tests/test_guard.py",
    "python3 -c \"print('claude')\"",
    'while read f; do cp "$f" "/tmp/x/$f"; done < liste',  # mot calculé : plus de doute d'analyse (rejeu)
    '"$DIR/python" outil.py',
    # rejeu : lire le binaire n'est pas le lancer ; « gemini » est aussi un site d'images (imagegen)
    "python3 -c \"d=open('/home/u/.local/share/claude/versions/2.1.285','rb').read(); print(len(d))\"",
    "node - <<'E'\nconst r = await lancer(J(d, 'gemini', 'ok', { service: 'gemini' }));\n"
    "const x = rows.filter((x) => x.site === 'gemini');\nE",
    "python3 - <<'E'\nimport subprocess\ndef run(d, args=('codex', 'outils')):\n    return d\nfor f in ('codex', 'grok'):\n"
    "    print(run(f))\nE",
])
def test_reste_libre(cmd):
    v = _assess(cmd, R)
    assert guard.L_AGENT_IMBRIQUE not in v.desc, (cmd, v.desc)
    assert not v.guard, (cmd, v.desc)


def test_case_cochee_par_defaut_et_traduite():
    assert limites.CASES_PAR_ID[limites.AGENT_IMBRIQUE].coche is True
    assert limites.AGENT_IMBRIQUE in limites.PLAFONDS and limites.AGENT_IMBRIQUE not in limites.PAR_ID
    v = _assess("claude -p x")
    assert not limites.sans_carte(v.cats, limites.coches_defaut(), False, False)


def test_cli_des_fournisseurs_et_du_quota_lances_par_le_coeur_non_concernes():
    """Le cœur lance `claude -p "/usage"` (quota), codex et grok (fournisseurs) sans passer par ce garde-fou ;
    il ne juge que les outils de Claude. Rien de ces modules n'importe `guard`."""
    import ast

    for f in ("quota_releve.py", "fournisseurs_cli/base.py", "fournisseurs_cli/codex.py", "fournisseurs_cli/grok.py"):
        tree = ast.parse((config.ROOT / "astrel" / f).read_text(encoding="utf-8"))
        noms = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        assert "guard" not in noms, f
