"""Lot de sécurité A (04/11/2026, accord explicite de l'utilisateur ; audits du 03/11 dans
Sauvegardes-Astrel/audit-2026-11-03/) : chaque test échouait avant le lot.

1. Modules qui décident : fermeture des imports des hooks qui voient Bash et Write (calculée ici, en
   sous-process isolé ET par lecture des imports, même tardifs), scripts cités par les réglages du CLI et
   leurs imports directs, contrôles (doctor, preuve, levée) : protégés et dans l'empreinte du doctor.
2. Outils MCP : `browser_run_code_unsafe` refusé en dur ; un outil absent d'un serveur connu n'est plus libre ;
   chaque outil d'Astrel a une classe écrite.
3. Fichiers : `runtime/mcp.json` (secret, plafond), profil, micro ; sources audio : critiques.
4. Marqueur `ASTREL_BRAIN` retiré : garde-fou ; détachement hors cgroup du service : question.
5. Chemins Windows (`\\\\?\\`, flux NTFS, point final, 8.3, casse) : simulés ici, partout.
6. Vérification des réflexes jugée dans son dossier. 7. En-têtes d'agents en liste blanche.
"""
from __future__ import annotations

import chemins_machine  # chemins de la machine qui lance la suite
import ast
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path, PureWindowsPath

import pytest

from astrel import agents, brain, config, doctor, fournisseurs, guard, i18n, limites
from astrel.platform import proc
from astrel.senses import checks

R = config.ROOT.resolve()


@pytest.fixture(autouse=True)
def _isole(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "MODE_FILE", tmp_path / "autonomy.json")
    monkeypatch.setattr(guard, "GRANTS_FILE", tmp_path / "grants.json")


def ask(tool, inp, mode="autonome", cwd=None):
    return guard.assess(tool, inp, mode=mode, cwd=cwd or str(R))


def bash(cmd, mode="autonome", cwd=None):
    return ask("Bash", {"command": cmd}, mode, cwd)


def protege(p: Path) -> bool:
    p = Path(p).resolve()
    return guard._under(p, guard.PROTECTED_CODE) or guard._under(p, guard.PROTECTED_PATHS)


def empreinte() -> set[Path]:
    return {Path(p).resolve() for p in doctor.GUARD_FILES}


# ================================================================== 1. modules qui décident

def _module(name: str) -> Path | None:
    p = R.joinpath(*name.split("."))
    if (p / "__init__.py").is_file():
        return p / "__init__.py"
    return p.with_suffix(".py") if p.with_suffix(".py").is_file() else None


def _imports(f: Path) -> set[Path]:
    """Fichiers du paquet `astrel` importés par `f`, à tout niveau du code (imports tardifs compris)."""
    tree = ast.parse(f.read_text(encoding="utf-8"))
    rel = f.resolve().relative_to(R).with_suffix("").parts
    pkg = list(rel[:-1]) if rel[-1] != "__init__" else list(rel[:-1])
    names: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names |= {a.name for a in n.names if a.name.split(".")[0] == "astrel"}
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                base = pkg[:len(pkg) - (n.level - 1)]
                mod = ".".join(base + ([n.module] if n.module else []))
            elif (n.module or "").split(".")[0] == "astrel":
                mod = n.module
            else:
                continue
            names.add(mod)
            names |= {f"{mod}.{a.name}" for a in n.names}
    out = set()
    for name in names:
        parts = name.split(".")
        for k in range(1, len(parts) + 1):  # « astrel.platform.proc » charge aussi astrel et astrel.platform
            m = _module(".".join(parts[:k]))
            if m:
                out.add(m.resolve())
    return out


def _fermeture(starts: list[Path]) -> set[Path]:
    seen: set[Path] = set()
    todo = [s.resolve() for s in starts]
    while todo:
        f = todo.pop()
        if f not in seen:
            seen.add(f)
            todo += [x for x in _imports(f) if x not in seen]
    return seen


_SOUS_PROCESS = r"""
import importlib.util, json, sys
root = sys.argv[1]
sys.path.insert(0, root)
for name in ("permission_hook", "suivi_outils"):  # chargés comme le CLI les charge, sans lancer `main`
    spec = importlib.util.spec_from_file_location("_hook_" + name, f"{root}/hooks/{name}.py")
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
from astrel import guard, instantanes, state  # imports tardifs de suivi_outils et d'instantanes
from astrel.platform import proc
for tool, inp in (("Bash", {"command": "ls && git status"}), ("Write", {"file_path": "/tmp/x", "content": ""}),
                  ("mcp__x__y", {})):
    guard.assess(tool, inp, mode="autonome")
print(json.dumps(sorted({m.__file__ for n, m in sys.modules.items()
                         if (n == "astrel" or n.startswith("astrel.")) and getattr(m, "__file__", None)})))
"""


def _modules_charges() -> set[Path]:
    """Modules du paquet chargés par les hooks PreToolUse Bash/Write, dans un interpréteur ISOLÉ comme le leur."""
    out = subprocess.run([str(config.VENV_PYTHON), *config.hook_python_args(), "-B", "-c", _SOUS_PROCESS, str(R)],
                         capture_output=True, text=True, timeout=60, cwd=str(R), check=True).stdout
    return {Path(f).resolve() for f in json.loads(out.strip().splitlines()[-1])}


HOOKS_BASH_WRITE = [config.HOOK_SCRIPT, config.SUIVI_HOOK_SCRIPT]


def test_fermeture_des_imports_des_hooks_bash_write_protegee():
    charges = _modules_charges()
    lus = _fermeture(HOOKS_BASH_WRITE)
    assert {R / "astrel" / "guard.py", R / "astrel" / "config.py", R / "astrel" / "__init__.py",
            R / "astrel" / "platform" / "__init__.py", R / "astrel" / "instantanes.py"} <= charges | lus
    libres = sorted(str(p.relative_to(R)) for p in charges | lus if not protege(p))
    assert libres == [], f"modules des hooks non protégés : {libres}"
    absents = sorted(str(p.relative_to(R)) for p in charges | lus if p not in empreinte())
    assert absents == [], f"modules des hooks hors de l'empreinte du doctor : {absents}"


def _scripts_des_reglages(tmp_path, monkeypatch) -> set[Path]:
    """Fichiers du projet cités par les commandes des réglages du CLI (hooks, `apiKeyHelper`)."""
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "claude-settings.json")
    data = json.loads(brain.write_settings().read_text(encoding="utf-8"))
    lignes = [h["command"] for groupes in data["hooks"].values() for g in groupes for h in g["hooks"]]
    lignes += [fournisseurs.helper("cle_claude"), fournisseurs.helper("passerelle")]
    out = set()
    for ligne in lignes:
        for mot in shlex.split(ligne.replace(" || exit 2", "")):
            p = Path(mot)
            if p.is_absolute() and p.is_file() and R in p.resolve().parents and p.resolve() != config.VENV_PYTHON.resolve():
                out.add(p.resolve())
    return out


def test_scripts_cites_par_les_reglages_du_cli_et_leurs_imports_directs(tmp_path, monkeypatch):
    scripts = _scripts_des_reglages(tmp_path, monkeypatch)
    scripts |= {Path(p).resolve() for p in (config.ROUTE_HOOK_SCRIPT, config.RTK_HOOK_SCRIPT, config.QUOTA_HOOK_SCRIPT,
                                            config.MEMORY_HOOK_SCRIPT, config.STOP_HOOK_SCRIPT, config.CLE_API_SCRIPT)}
    assert R / "astrel" / "route_hook.py" in scripts and R / "astrel" / "cle_api.py" in scripts
    directs = {d for s in scripts for d in _imports(s)}
    assert R / "astrel" / "rappel.py" in directs and R / "astrel" / "quota_arret.py" in directs
    libres = sorted(str(p.relative_to(R)) for p in scripts | directs if not protege(p))
    assert libres == [], f"scripts des hooks ou imports directs non protégés : {libres}"
    absents = sorted(str(p.relative_to(R)) for p in scripts | directs if p not in empreinte())
    assert absents == [], f"hors de l'empreinte du doctor : {absents}"


def test_programme_ajoute_par_rtk_protege():
    for p in (config.RTK_BIN, R / ".rtk" / "rtk", R / ".rtk"):
        v = ask("Write", {"file_path": str(p), "content": "x"})
        assert v.needs_confirm and v.guard is True, p
    assert bash("cp /tmp/x .rtk/rtk").guard is True
    assert (R / ".rtk" / "rtk") in empreinte()


CONTROLES = ["astrel/doctor.py", "astrel/preuve.py", "astrel/levee.py", "astrel/rappel.py", "astrel/agents.py",
             "astrel/quota_arret.py", "astrel/__init__.py", "astrel/config.py", "astrel/platform/__init__.py"]


@pytest.mark.parametrize("rel", CONTROLES)
def test_controles_proteges_meme_en_total(rel):
    for tool, inp in (("Write", {"file_path": str(R / rel), "content": "x"}),
                      ("Edit", {"file_path": str(R / rel), "old_string": "a", "new_string": "b"})):
        v = ask(tool, inp)
        assert v.needs_confirm and v.guard is True and limites.PLAFOND in v.cats, (tool, rel)
        assert limites.CODE_IDENTITE in v.cats
    assert bash(f"sed -i s/a/b/ {rel}").guard is True
    assert (R / rel) in empreinte()


def test_doctor_dans_sa_propre_empreinte():
    assert (R / "astrel" / "doctor.py") in empreinte()
    assert len(doctor.GUARD_FILES) == len(set(empreinte()))  # pas de doublon


@pytest.mark.parametrize("rel", ["astrel/core.py", "astrel/coeur/cerveau.py", "astrel/coeur/mission.py",
                                 "astrel/brain.py", "tests/test_x.py"])
def test_coeur_reste_libre(rel):
    """Choix de l'utilisateur : `core.py` et `astrel/coeur/` restent libres en écriture."""
    assert not ask("Write", {"file_path": str(R / rel), "content": "x"}).needs_confirm


@pytest.mark.parametrize("cmd", [
    "grep -n WORKDIR astrel/config.py", "sed -n 1,40p astrel/config.py", "cat astrel/__init__.py",
    ".venv/bin/python -m pytest -q tests/test_securite_lot_a.py", ".venv/bin/python -m py_compile astrel/config.py",
    "git diff astrel/config.py astrel/doctor.py", "git status", "ls -l .rtk",
    'chmod +x "$D/config.py"',  # dossier inconnu, nom courant de tout projet : comme avant le 04/11
    f"cd {chemins_machine.PROJETS} && touch Boutique/app/__init__.py",
])
def test_lectures_et_usage_courant_restent_libres(cmd):
    assert not bash(cmd).needs_confirm, cmd


def test_ecriture_par_bash_des_modules_proteges():
    for cmd in ("echo x >> astrel/config.py", "cp /tmp/x astrel/platform/__init__.py", "chmod 000 astrel/events.py",
                "cd astrel && sed -i s/a/b/ levee.py", "truncate -s0 astrel/rappel.py"):
        v = bash(cmd)
        assert v.needs_confirm and v.guard is True, cmd


# ================================================================== 2. outils MCP

def _outils(module: str) -> list[str]:
    tree = ast.parse((R / "astrel" / module).read_text(encoding="utf-8"))
    return [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any("tool" in ast.unparse(d) for d in n.decorator_list)]


@pytest.mark.parametrize("server, module", [("astrel", "mcp_server.py"), ("connecteurs", "mcp_connectors.py")])
def test_chaque_outil_des_serveurs_d_astrel_a_une_classe_ecrite(server, module):
    tools = _outils(module)
    assert len(tools) >= (50 if server == "astrel" else 8)
    table = guard.MCP_POLICY[server]
    assert [t for t in tools if t not in table] == [], "outil sans classe dans guard.MCP_POLICY"
    assert [t for t in table if t not in tools] == [], "classe d'un outil qui n'existe plus"
    assert set(table.values()) <= {"", guard.SENSITIVE, guard.CRITICAL}


def test_aucun_outil_ne_devient_plus_libre_qu_avant():
    """Avant le 04/11 : un outil du serveur « connecteurs » (inconnu) était sensible par son nom ; ceux du
    serveur « astrel » cités l'étaient déjà. Rien n'est desserré."""
    for tool in _outils("mcp_connectors.py"):
        assert guard.MCP_POLICY["connecteurs"][tool] in (guard.SENSITIVE, guard.CRITICAL) or \
            guard._mcp_level(tool) == ""
    for tool, level in (("power_action", guard.CRITICAL), ("wifi_toggle", guard.CRITICAL),
                        ("reminder_delete", guard.SENSITIVE), ("reflex_create", guard.SENSITIVE),
                        ("reflex_delete", guard.SENSITIVE)):
        assert guard.MCP_POLICY["astrel"][tool] == level


@pytest.mark.parametrize("server", ["playwright", "playwright2", "playwright3", "playwright4",
                                    "plugin_playwright_playwright"])
def test_run_code_unsafe_refuse_en_dur(server, tmp_path, monkeypatch):
    tool = f"mcp__{server}__browser_run_code_unsafe"
    vide = tmp_path / "tool-policy.json"
    vide.write_text(json.dumps({"mcp": {}, "refuser": []}), encoding="utf-8")
    monkeypatch.setattr(guard, "TOOL_POLICY_FILE", vide)  # une politique remplacée ne le rouvre pas
    assert guard.refused(tool)
    monkeypatch.setattr(guard, "TOOL_POLICY_FILE", tmp_path / "absente.json")
    assert guard.refused(tool)
    for mode in ("autonome", "prudent"):
        v = ask(tool, {"code": "async (page) => 1"}, mode)
        assert v.needs_confirm and v.level == guard.CRITICAL and v.guard is True


def test_run_code_unsafe_dans_la_politique_d_outils():
    data = json.loads((R / "hooks" / "tool-policy.json").read_text(encoding="utf-8"))
    for n in ("", "2", "3", "4"):
        assert f"mcp__playwright{n}__browser_run_code_unsafe" in data["refuser"]


_PW_TOOLS = ["browser_navigate", "browser_click", "browser_type", "browser_press_key", "browser_tabs",
             "browser_network_request", "browser_webmcp_call", "browser_snapshot", "browser_evaluate",
             "browser_file_upload", "browser_fill_form", "browser_close", "browser_install", "browser_drag"]
_RANG = {"": 0, guard.SENSITIVE: 1, guard.CRITICAL: 2}


@pytest.mark.parametrize("tool", _PW_TOOLS)
def test_navigateur_principal_au_moins_aussi_strict_que_les_jetables(tool):
    for n in ("2", "3", "4"):
        assert _RANG[guard.classify(f"mcp__playwright__{tool}", {}).level or ""] >= \
            _RANG[guard.classify(f"mcp__playwright{n}__{tool}", {}).level or ""]
    assert ask(f"mcp__playwright__{tool}", {}, "prudent").needs_confirm  # avant : libre même en prudent


@pytest.mark.parametrize("tool", ["open_url", "open_path", "open_app", "whatsapp_send_file", "generate_video",
                                  "generate_image", "set_sensor", "clipboard_read", "clipboard_write",
                                  "reflex_enable", "screen_capture", "camera_capture"])
def test_effet_exterieur_demande_en_prudent(tool):
    assert guard.MCP_POLICY["astrel"][tool] in (guard.SENSITIVE, guard.CRITICAL)
    assert ask(f"mcp__astrel__{tool}", {"path": "/tmp/rapport.pdf"}, "prudent").needs_confirm


@pytest.mark.parametrize("tool", ["mcp__astrel__outil_ajoute_demain", "mcp__playwright__browser_nouveau",
                                  "mcp__connecteurs__agenda_nouveau"])
def test_outil_absent_d_un_serveur_connu_n_est_pas_libre(tool):
    assert ask(tool, {}, "prudent").needs_confirm
    assert guard.classify(tool, {}).level in (guard.SENSITIVE, guard.CRITICAL)


def test_usage_autonome_inchange(monkeypatch):
    """Le défaut (autonome) : ouvrir une page, générer une image, envoyer un fichier à soi-même restent libres ;
    mémoire, compte rendu, courrier libres même en prudent ; extinction toujours demandée."""
    monkeypatch.setattr(guard, "TOOL_POLICY_FILE", R / "hooks" / "tool-policy.json")  # la vraie politique
    for tool in ("open_url", "open_app", "generate_image", "generate_video", "whatsapp_send_file", "set_sensor",
                 "clipboard_write", "reflex_enable", "screen_capture"):
        assert not ask(f"mcp__astrel__{tool}", {}).needs_confirm, tool
    assert not ask("mcp__astrel__open_path", {"path": "/tmp/rapport.pdf"}).needs_confirm
    for tool in ("memory_search", "memory_write", "post_report", "show_media", "ask_choice", "todo_add",
                 "passation", "history_search"):
        assert not ask(f"mcp__astrel__{tool}", {}, "prudent").needs_confirm, tool
    assert ask("mcp__astrel__power_action", {}).needs_confirm
    assert not ask("mcp__context7__resolve-library-id", {}, "prudent").needs_confirm  # tool-policy.json : libre


@pytest.mark.parametrize("nom", ["lanceur.desktop", "outil.sh", "x.py", "setup.exe", "installe.bat", "a.AppImage"])
def test_ouvrir_un_fichier_qui_lance_du_code_est_critique(nom):
    v = ask("mcp__astrel__open_path", {"path": f"/tmp/{nom}"})
    assert v.needs_confirm and v.level == guard.CRITICAL


def test_ouvrir_un_executable_est_critique(tmp_path):
    f = tmp_path / "sans-extension"
    f.write_text("#!/bin/sh\n", encoding="utf-8")
    f.chmod(0o700)
    assert ask("mcp__astrel__open_path", {"path": str(f)}).level == guard.CRITICAL
    assert ask("mcp__astrel__open_path", {}).level == guard.CRITICAL  # entrée illisible : dans le doute


# ================================================================== 3. fichiers

def test_mcp_json_secret_et_plafond():
    f = str(config.MCP_CONFIG_FILE)
    for tool, inp in (("Write", {"file_path": f, "content": "{}"}),
                      ("Edit", {"file_path": f, "old_string": "a", "new_string": "b"})):
        v = ask(tool, inp)
        assert v.needs_confirm and v.guard is True and limites.SECRETS in v.cats, tool
    assert bash(f"echo x > {f}").guard is True
    assert bash("cd runtime && echo '{}' > mcp.json").guard is True
    for v in (ask("Read", {"file_path": f}), bash("cat runtime/mcp.json"), bash("cd runtime && jq . mcp.json"),
              bash("cat runtime/*.json | head")):  # un joker qui l'atteint (avant : libre)
        assert v.needs_confirm and v.guard is True and v.desc == guard.L_SECRET
    assert config.MCP_CONFIG_FILE.resolve() in guard._SECRET_FILES
    assert not bash("cat .mcp.json mcp-servers/package.json").needs_confirm  # un autre « mcp.json » : libre


@pytest.mark.parametrize("cible", [config.PROFILE_FILE, config.MIC_FILE, config.CONFIG_DIR / "mic_profiles.json",
                                   config.RUNTIME / "mic-muted"], ids=lambda p: p.name)
def test_profil_et_micro_proteges(cible):
    v = ask("Write", {"file_path": str(cible), "content": "x"})
    assert v.needs_confirm and v.guard is True
    # 04/11 (CI Windows) : « / » partout (Git Bash le lit, et `config\profile.json` NON cité y devient
    # « configprofile.json », un autre fichier) ; sous Windows, aussi la forme « \ » entre guillemets.
    rel = cible.relative_to(config.ROOT).as_posix()
    formes = [rel] + ([f"'{cible.relative_to(config.ROOT)}'"] if os.name == "nt" else [])
    for forme in formes:
        for cmd in (f"echo x > {forme}", f"rm -f {forme}", f"mv {forme} /tmp/", f"touch {forme}"):
            assert bash(cmd).guard is True, cmd
    assert not ask("Read", {"file_path": str(cible)}).needs_confirm
    assert not bash(f"cat {rel}").needs_confirm


@pytest.mark.parametrize("cmd", [
    "pactl load-module module-null-sink sink_name=x", "pactl load-module module-virtual-source source_name=v",
    "pactl set-default-source v", "pactl move-source-output 12 v", "pacmd load-module module-loopback",
    "pw-loopback --capture-props='media.class=Audio/Source'", "pw-cli create-node adapter '{ factory.name=x }'",
    "pw-cli load-module libpipewire-module-loopback", "pw-link lecteur:sortie astrel:entree",
    "pw-metadata 0 default.configured.audio.source '{\"name\":\"v\"}'", "wpctl set-default 52",
])
def test_sources_audio_critiques(cmd):
    v = bash(cmd)
    assert v.needs_confirm and v.level == guard.CRITICAL and guard.L_AUDIO in v.desc, cmd


@pytest.mark.parametrize("cmd", ["pactl list sources short", "pactl info", "pactl get-default-source", "pw-cli ls",
                                 "pw-link -l", "pw-metadata 0", "wpctl status", "pactl --help"])
def test_lire_l_audio_reste_libre(cmd):
    assert not bash(cmd).needs_confirm, cmd


# ================================================================== 4. marqueur du cerveau, détachement

def test_meme_marqueur_que_le_cerveau():
    assert guard.BRAIN_VAR == proc.BRAIN_VAR


@pytest.mark.parametrize("cmd", [
    "env -u ASTREL_BRAIN python3 x.py", "env --unset=ASTREL_BRAIN python3 x.py", "env -uASTREL_BRAIN x",
    "env --unset ASTREL_BRAIN x", "unset ASTREL_BRAIN; python3 x.py", "unset -v ASTREL_BRAIN", 'unset "$V"',
    "env -i python3 x.py", "env -i PATH=/usr/bin python3 x.py", "env --ignore-environment x", "env - python3 x.py",
    "exec -c python3 x.py", "export ASTREL_BRAIN=; python3 x.py", "export ASTREL_BRAIN=autre", "export -n ASTREL_BRAIN",
    "ASTREL_BRAIN=x python3 x.py", "ASTREL_BRAIN= ./outil", "ASTREL_BRAIN=; python3 x.py", "env ASTREL_BRAIN= x",
    "f() { local ASTREL_BRAIN; python3 x.py; }; f", "declare +x ASTREL_BRAIN", 'env -u "$V" x',
    "python3 -c \"import os; os.environ.pop('ASTREL_BRAIN'); os.system('x')\"",
    "bash -c 'unset ASTREL_BRAIN; python3 x.py'",
])
def test_retirer_le_marqueur_est_une_action_sur_le_garde_fou(cmd, systeme):
    """Même règle sur chaque système : là où le marqueur DÉCIDE (aucun confinement : macOS, Linux hors service,
    Windows hors du Job Object), plafond ; là où un confinement reconnaît Claude sans lui (Linux en service :
    cgroup ; Windows : Job Object du cerveau), critique autorisable (« oui pour 30 min », Total)."""
    for plateforme, confine in MARQUEUR_PAR_SYSTEME:
        systeme(plateforme, confine)
        v = bash(cmd)
        cas = (cmd, plateforme, confine, v.desc)
        assert v.needs_confirm and v.level == guard.CRITICAL and guard.L_BRAIN_MARK in v.desc, cas
        if guard._marqueur_compte():
            assert v.guard is True and limites.CODE_IDENTITE in v.cats and not v.keys, cas
        else:
            assert v.guard is False and v.keys, cas


# (système du hook, confiné) : Linux en service ou non, Windows dans le Job Object ou non, macOS (jamais confiné).
MARQUEUR_PAR_SYSTEME = [("linux", False), ("linux", True), ("win32", False), ("win32", True), ("darwin", False),
                        ("darwin", True)]


@pytest.fixture
def systeme(monkeypatch):
    """Simule le système du hook (`sys.platform`) et son confinement : cgroup du service (Linux), Job Object du
    cerveau (Windows) ; macOS n'en a pas, ce qui y est posé ne compte pas."""
    def poser(plateforme: str, confine: bool) -> None:
        monkeypatch.setattr(guard.sys, "platform", plateforme)
        monkeypatch.setattr(guard, "_CGROUP", [confine])
        monkeypatch.setattr(guard, "_JOB", [confine])
    return poser


def test_marqueur_decide_selon_le_systeme(systeme):
    attendu = {("linux", False): True, ("linux", True): False, ("win32", False): True, ("win32", True): False,
               ("darwin", False): True, ("darwin", True): True}
    for (plateforme, confine), decide in attendu.items():
        systeme(plateforme, confine)
        assert guard._marqueur_compte() is decide, (plateforme, confine)
    assert sorted(attendu) == sorted(MARQUEUR_PAR_SYSTEME)


def test_job_object_vu_par_le_hook(monkeypatch):
    """04/11 (CI Windows) : le hook se sait dans le Job Object du cerveau en l'ouvrant par son nom, tiré du marqueur
    hérité du CLI. Ici (pas Windows) : jamais trouvé, donc le marqueur décide (vrai Windows : test_guard_windows)."""
    assert guard.JOB_PREFIXE == proc.JOB_PREFIXE
    assert proc.nom_du_job() == guard.JOB_PREFIXE + proc.BRAIN_TOKEN and proc.nom_du_job("ab") == guard.JOB_PREFIXE + "ab"
    assert guard.re.fullmatch(r"[0-9a-f]{32}", proc.BRAIN_TOKEN)  # la forme que `guard._dans_le_job` exige
    for jeton in ("", "x", "../" + proc.BRAIN_TOKEN, proc.BRAIN_TOKEN.upper() + "\\x"):
        assert guard._dans_le_job(jeton) is False, jeton
    monkeypatch.setattr(guard.sys, "platform", "win32")
    monkeypatch.setattr(guard, "_JOB", [])
    monkeypatch.setenv(guard.BRAIN_VAR, proc.BRAIN_TOKEN)
    if sys.platform != "win32":  # aucun Job Object de ce nom ici : le marqueur décide, au plafond
        assert guard._job_fiable() is False and guard._marqueur_compte() is True
        assert bash("env -u ASTREL_BRAIN python3 x.py").guard is True
    monkeypatch.setattr(guard, "_JOB", [])
    monkeypatch.setattr(guard.sys, "platform", "darwin")
    assert guard._job_fiable() is False  # hors Windows : jamais lu


def test_env_tiret_ne_cache_plus_le_programme():
    """`env - prog` : avant le 04/11, « - » était pris pour le programme et `sudo reboot` passait inaperçu."""
    v = bash("env - sudo reboot")
    assert guard.L_ADMIN in v.desc and guard.L_POWER in v.desc


@pytest.mark.parametrize("cmd", ["grep -rn ASTREL_BRAIN astrel/", 'echo "$ASTREL_BRAIN"', "printenv ASTREL_BRAIN",
                                 "env | grep ASTREL", "env -u FOO python3 x.py", "env LANG=C sort f",
                                 "unset FOO; ls", "export PATH_X=1"])
def test_marqueur_lu_ou_autre_variable_libre(cmd):
    assert not bash(cmd).needs_confirm, cmd


@pytest.fixture
def cgroup(monkeypatch):
    def poser(fiable: bool):
        monkeypatch.setattr(guard, "_CGROUP", [fiable])
    return poser


@pytest.mark.parametrize("cmd", ["setsid python3 x.py", "nohup python3 serveur.py &", "sleep 60 & disown",
                                 "timeout 5 setsid -f x", "( nohup x & )"])
def test_detachement_demande_hors_cgroup_du_service(cmd, cgroup):
    cgroup(False)
    v = bash(cmd)
    assert v.needs_confirm and v.level == guard.CRITICAL and guard.L_DETACH in v.desc, cmd
    cgroup(True)
    assert guard.L_DETACH not in bash(cmd).desc
    assert not bash(cmd).needs_confirm, cmd  # en service, le cgroup le rattrape : libre


def test_detection_du_cgroup_du_service(monkeypatch):
    for cg, attendu in (("/user.slice/user-1000.slice/user@1000.service/app.slice/astrel.service", True),
                        ("/user.slice/user-1000.slice/user@1000.service/app.slice/app-code.scope", False),
                        ("", False)):
        monkeypatch.setattr(guard, "_CGROUP", [])
        monkeypatch.setattr(guard, "_own_cgroup", lambda cg=cg: cg)
        monkeypatch.setattr(guard.sys, "platform", "linux")
        assert guard._cgroup_fiable() is attendu, cg
    monkeypatch.setattr(guard, "_CGROUP", [])
    monkeypatch.setattr(guard.sys, "platform", "darwin")  # macOS : le marqueur survit au détachement
    assert guard._cgroup_fiable() is True


# ================================================================== 5. chemins Windows (simulés)

@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(guard, "_WINDOWS", True)


@pytest.mark.parametrize("forme, normale", [
    ("\\\\?\\C:\\Astrel\\astrel\\guard.py", "C:\\Astrel\\astrel\\guard.py"),
    ("\\\\.\\C:\\Astrel\\astrel\\guard.py", "C:\\Astrel\\astrel\\guard.py"),
    ("//?/C:/Astrel/astrel/guard.py", "C:/Astrel/astrel/guard.py"),
    ("\\\\?\\UNC\\serveur\\partage\\a.txt", "\\\\serveur\\partage\\a.txt"),
    ("\\\\?\\UNC\\localhost\\C$\\Astrel\\hooks", "C:\\Astrel\\hooks"),
    ("\\\\127.0.0.1\\c$\\Astrel\\astrel\\state.py", "C:\\Astrel\\astrel\\state.py"),
    ("C:\\Astrel\\astrel\\guard.py::$DATA", "C:\\Astrel\\astrel\\guard.py"),
    ("C:\\Astrel\\astrel\\guard.py:flux:$DATA", "C:\\Astrel\\astrel\\guard.py"),
    ("C:\\Astrel\\astrel\\guard.py.", "C:\\Astrel\\astrel\\guard.py"),
    ("C:\\Astrel\\astrel\\guard.py . .", "C:\\Astrel\\astrel\\guard.py"),
    ("C:\\Astrel.\\astrel \\guard.py", "C:\\Astrel\\astrel\\guard.py"),
    ("guard.py::$DATA", "guard.py"),
    ("/c/Astrel/astrel/guard.py.", "/c/Astrel/astrel/guard.py"),
    ("C:\\Astrel\\..\\Astrel\\.git", "C:\\Astrel\\..\\Astrel\\.git"),
    ("HEAD:astrel/guard.py", "HEAD:astrel/guard.py"),  # « rév:chemin » de git : pas un flux
    ("https://exemple.fr/a:b.", "https://exemple.fr/a:b."),
])
def test_forme_normale_windows(windows, forme, normale):
    assert guard._win_forme(forme) == normale


def test_casse_ignoree_apres_normalisation(windows):
    assert PureWindowsPath(guard._win_forme("\\\\?\\c:\\ASTREL\\Astrel\\GUARD.PY.")) == \
        PureWindowsPath("C:\\Astrel\\astrel\\guard.py")
    assert guard._cites_code_name("ASTREL\\STATE.PY") and guard._cites_code_name("x/Config.Py")


def test_noms_courts_8_3(windows, monkeypatch):
    longs = {"C:\\PROGRA~1\\ASTREL~1\\astrel\\STATE~1.PY": "C:\\Program Files\\Astrel\\astrel\\state.py"}
    monkeypatch.setattr(guard, "_nom_long", lambda s: longs.get(s, s))
    assert guard._win_forme("\\\\?\\C:\\PROGRA~1\\ASTREL~1\\astrel\\STATE~1.PY") == \
        "C:\\Program Files\\Astrel\\astrel\\state.py"
    assert guard._msys("C:\\PROGRA~1\\ASTREL~1\\astrel\\STATE~1.PY::$DATA") == \
        "C:\\Program Files\\Astrel\\astrel\\state.py"


def test_msys_applique_la_forme_normale(windows):
    assert guard._msys("\\\\?\\C:\\Astrel\\hooks\\permission_hook.py") == "C:\\Astrel\\hooks\\permission_hook.py"
    assert guard._msys("/c/Astrel/hooks").startswith("C:/Astrel/hooks")  # Git Bash, inchangé


@pytest.mark.parametrize("forme", ["\\\\?\\Volume{0f1e2d3c-0000-0000-0000-000000000000}\\Astrel\\astrel\\guard.py",
                                   "\\\\?\\GLOBALROOT\\Device\\HarddiskVolume3\\Astrel\\astrel\\guard.py",
                                   "\\\\.\\PhysicalDrive0", "\\\\.\\pipe\\x", "\\\\BUREAU-1\\C$\\Astrel\\hooks\\x"])
def test_forme_incertaine_demandee(windows, forme, tmp_path):
    assert guard._win_douteux(forme)
    v = ask("Write", {"file_path": forme, "content": "x"})
    assert v.needs_confirm and v.guard is True
    v = ask("Read", {"file_path": forme})
    assert v.needs_confirm and v.desc == guard.L_SECRET
    assert bash(f"echo x > '{forme}'").needs_confirm
    # 04/11 (CI Windows) : par Bash, même verdict que Write (doute au plafond), y compris `rm -f` (avant : libre)
    # et quand le « ? » du préfixe passait pour un joker ; dossier courant sans fichier protégé, nom quelconque.
    autre = forme.replace("guard.py", "ipc.py")
    for cmd in (f"echo x > '{autre}'", f"rm -f '{autre}'", f"cp README.md '{autre}'", f"touch '{autre}'"):
        v = bash(cmd, cwd=str(tmp_path))
        assert v.needs_confirm and v.guard is True, cmd


@pytest.mark.parametrize("forme", ["C:\\Projets\\a.txt", "\\\\serveur\\partage\\a.txt", "\\\\?\\C:\\Projets\\a.txt",
                                   "relatif\\a.txt", "/c/Projets/a.txt"])
def test_forme_connue_pas_incertaine(windows, forme):
    assert not guard._win_douteux(forme)


def test_hors_windows_rien_ne_change(monkeypatch):
    monkeypatch.setattr(guard, "_WINDOWS", False)  # 04/11 (CI Windows) : simulé partout, vrai Windows compris
    assert guard._msys("\\\\?\\C:\\x") == "\\\\?\\C:\\x" and not guard._win_douteux("\\\\?\\Volume{x}\\y")
    assert guard._win_sans_prefixe("\\\\?\\C:\\x") == "\\\\?\\C:\\x"


@pytest.mark.parametrize("prefixe", ["\\\\?\\", "//?/", "\\\\.\\"])
def test_prefixe_win32_n_est_pas_un_joker(windows, monkeypatch, tmp_path, prefixe):
    """04/11 (CI Windows) : `echo x > '\\\\?\\D:\\…\\ipc.py'` était libre : le « ? » du préfixe passait pour un joker
    et, sans « / » à couper, le motif visait le dossier courant. Simulé : la forme normale de ce chemin Windows est
    le vrai `astrel/ipc.py` (sous Windows, `_win_forme` le fait seul)."""
    forme = prefixe + "C:\\Astrel\\astrel\\ipc.py"
    vrai = str(R / "astrel" / "ipc.py")
    reel = guard._win_forme
    monkeypatch.setattr(guard, "_win_forme", lambda s: vrai if s == forme else reel(s))
    assert guard._win_sans_prefixe(forme) == vrai
    ctx = guard._Ctx(cwds=[tmp_path])
    assert guard._scopes(forme, ctx) == [(Path(vrai).resolve(), None)]  # un chemin, pas un motif
    for cmd in (f"echo x > '{forme}'", f"rm -f '{forme}'", f"cp README.md '{forme}'", f"echo x | tee '{forme}'"):
        v = bash(cmd, cwd=str(tmp_path))  # dossier courant sans fichier protégé : seule la cible compte
        assert v.needs_confirm and v.guard is True, cmd


# ================================================================== 6. vérification des réflexes

def test_verification_jugee_dans_son_dossier(monkeypatch):
    seen = []
    real = guard.classify

    def espion(tool, inp, cwd=None):
        seen.append(cwd)
        return real(tool, inp, cwd=cwd)

    monkeypatch.setattr(guard, "classify", espion)
    checks.validate({"kind": "command", "argv": ["ls"], "cwd": "/etc"})
    checks.validate({"kind": "command", "argv": ["ls"]})
    assert seen == ["/etc", str(config.WORKDIR)]


def test_verification_refusee_si_le_dossier_en_fait_un_secret():
    # « cat mcp.json » : rien de secret dans /tmp, la configuration MCP dans runtime/.
    checks.validate({"kind": "command", "argv": ["cat", "mcp.json"], "cwd": "/tmp"})
    with pytest.raises(ValueError, match="lecture seule"):
        checks.validate({"kind": "command", "argv": ["cat", "mcp.json"], "cwd": str(config.RUNTIME)})


@pytest.mark.parametrize("cwd", ["runtime", "./x", "~/x", 3, ["/tmp"]])
def test_dossier_absolu_exige(cwd):
    with pytest.raises(ValueError, match="absolu"):
        checks.validate({"kind": "command", "argv": ["ls"], "cwd": cwd})


def test_commande_lancee_dans_le_dossier_juge(tmp_path):
    hit, values = checks.run({"kind": "command", "argv": ["pwd"], "cwd": str(tmp_path),
                              "contains": tmp_path.name})
    assert hit, values


# ================================================================== 7. en-têtes d'agents

def test_en_tete_d_agent_en_liste_blanche(tmp_path):
    (tmp_path / "piege.md").write_text(
        "---\nname: piege\ndescription: un agent\nmodel: sonnet\neffort: high\ntools: Read, Grep\n"
        "disallowedTools: Write\nmaxTurns: 5\nomitClaudeMd: true\nhooks: {\"PreToolUse\": []}\n"
        "mcpServers: evil\npermissionMode: bypassPermissions\nskills: x\nbackground: true\ncolor: red\n---\n"
        "Consigne.\n", encoding="utf-8")
    a = agents.load_agents(tmp_path, efforts=False)["piege"]
    assert set(a) == {"description", "model", "effort", "tools", "disallowedTools", "maxTurns", "omitClaudeMd",
                      "prompt"}
    assert a["tools"] == ["Read", "Grep"] and a["maxTurns"] == 5 and a["omitClaudeMd"] is True
    assert agents.HEADER_KEYS == {"name", "description", "model", "effort", "tools", "disallowedTools", "maxTurns",
                                  "omitClaudeMd"}


def test_agents_du_projet_gardent_leurs_cles():
    for name, a in agents.load_agents(efforts=False).items():
        assert set(a) - {"prompt"} <= agents.HEADER_KEYS, name


# ================================================================== étiquettes nouvelles

@pytest.mark.parametrize("label", [guard.L_AUDIO, guard.L_BRAIN_MARK, guard.L_DETACH])
def test_etiquettes_traduites_et_classees(label):
    assert label in guard.CATS
    assert i18n.tr(label, "en") != label
    assert all(c == limites.PLAFOND or c in limites.PAR_ID for c in guard.CATS[label])
