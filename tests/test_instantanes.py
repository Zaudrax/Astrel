"""Instantané avant écriture et « annule ce que tu viens de faire » (01/11/2026, `astrel/instantanes.py`,
`hooks/suivi_outils.py`) : copie avant l'outil, empreinte après, rétablissement du dernier lot, et
jamais un secret copié ni un fichier protégé réécrit. Stockage isolé par conftest (`_suivi_isole`)."""

import importlib.util
import io
import json
import os
import sys
from pathlib import Path

import pytest

from astrel import brain, config, guard, instantanes

# Droits Unix (bits de mode, dossier 0700 qui cache ses fichiers) : sans objet sous Windows, où le profil protège.
unix = pytest.mark.skipif(os.name == "nt", reason="droits Unix")
BASE = os.path.abspath("/data/p")  # absolu sur tous les systèmes (Windows : avec son lecteur)

R = config.ROOT
_spec = importlib.util.spec_from_file_location("suivi_outils", R / "hooks" / "suivi_outils.py")
suivi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(suivi)


def _pre(path, tool="Edit", tid="t1", prompt="p1", **inp):
    payload = {"session_id": "s", "prompt_id": prompt, "hook_event_name": "PreToolUse", "tool_name": tool,
               "tool_use_id": tid, "cwd": str(Path(path).parent) if path else "/tmp",
               "tool_input": {"file_path": str(path), **inp} if tool != "Bash" else inp}
    return instantanes.avant(payload)


def _post(tid="t1", event="PostToolUse"):
    return instantanes.apres({"session_id": "s", "hook_event_name": event, "tool_use_id": tid, "tool_name": "Edit"})


@pytest.fixture
def proj(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    return d


# --- cibles des commandes shell ------------------------------------------------------------

@pytest.mark.parametrize("cmd, cibles", [
    ("echo x > a.txt", ["a.txt"]),
    ("echo x >> a.txt 2>&1", ["a.txt"]),
    ("cat > b.py <<'EOF'\nprint('> c.py')\nEOF", ["b.py"]),
    ("printf x | tee -a c.log d.log", ["c.log", "d.log"]),
    ("sed -i 's/a/b/' e.py f.py", ["e.py", "f.py"]),
    ("sed -i.bak -e 's/a/b/' g.py", ["g.py"]),
    ("sed -ni -e p h.py", ["h.py"]),
    ("cd sous && echo x > i.txt", ["sous/i.txt"]),
    ("python3 x.py 2>/dev/null > j.out", ["j.out"]),
    ("echo x &> k.log", ["k.log"]),
])
def test_cibles_bash(cmd, cibles):
    assert instantanes.cibles_bash(cmd, BASE) == [os.path.normpath(os.path.join(BASE, c)) for c in cibles]


@pytest.mark.parametrize("cmd", [
    "echo x > $OUT", "echo x > ~/$X", "echo x > *.txt", "ls -la", "grep '>' f", "echo 'a > b'",
    "cd $D && echo x > a", "(cd sous && echo x > a)", "cat f | sed 's/a/b/' > /dev/null",
    "sed 's/a/b/' f.py", "x=$(echo a > f)", "echo x >&2",
])
def test_cibles_bash_rien_de_douteux(cmd):
    assert instantanes.cibles_bash(cmd, "/data/p") == []


# --- copie, empreinte, rétablissement -----------------------------------------------------

def test_edit_puis_annule(proj):
    f = proj / "a.py"
    f.write_text("avant\n")
    assert _pre(f) == 1
    f.write_text("après\n")
    assert _post() == 1
    r = instantanes.restaurer(par="test")
    assert r["restaures"] == [str(f)] and f.read_text() == "avant\n"
    # Le lot est annulé : un second appel n'a plus rien (pas de lot plus ancien).
    assert instantanes.restaurer()["lot"] is None


def test_creation_retiree(proj):
    f = proj / "neuf.py"
    _pre(f, tool="Write", content="x")
    f.write_text("x")
    _post()
    r = instantanes.restaurer()
    assert r["supprimes"] == [str(f)] and not f.exists()


def test_fichier_inchange_sort_du_lot(proj):
    f = proj / "a.py"
    f.write_text("même\n")
    _pre(f)
    _post(event="PostToolUseFailure")  # Edit échoué : rien n'a changé
    assert instantanes.lots()[0]["fichiers"] == []
    assert instantanes.restaurer()["lot"] is None


def test_modifie_depuis_n_est_pas_ecrase(proj):
    f = proj / "a.py"
    f.write_text("avant\n")
    _pre(f)
    f.write_text("par Claude\n")
    _post()
    f.write_text("par l'utilisateur ensuite\n")
    r = instantanes.restaurer()
    assert r["laisses"] == [{"chemin": str(f), "pourquoi": "modifié depuis"}]
    assert f.read_text() == "par l'utilisateur ensuite\n"


def test_apercu_ne_change_rien(proj):
    f = proj / "a.py"
    f.write_text("avant\n")
    _pre(f)
    f.write_text("après\n")
    _post()
    r = instantanes.restaurer(apercu=True)
    assert r["restaures"] == [str(f)] and f.read_text() == "après\n"
    assert instantanes.restaurer()["restaures"] == [str(f)] and f.read_text() == "avant\n"


def test_un_lot_par_message_et_on_remonte(proj):
    f = proj / "a.py"
    f.write_text("v0")
    _pre(f, tid="t1", prompt="p1")
    _pre(f, tid="t2", prompt="p1")  # même message : la PREMIÈRE version d'avant fait foi
    f.write_text("v1")
    _post("t1")
    _post("t2")
    _pre(f, tid="t3", prompt="p2")
    f.write_text("v2")
    _post("t3")
    assert len(instantanes.lots()) == 2
    instantanes.restaurer()
    assert f.read_text() == "v1"
    instantanes.restaurer()
    assert f.read_text() == "v0"


def test_redirection_shell(proj):
    f = proj / "out.txt"
    f.write_text("ancien")
    assert _pre(None, tool="Bash", command=f"echo neuf > {f}") == 1
    f.write_text("neuf\n")
    _post()
    instantanes.restaurer()
    assert f.read_text() == "ancien"


@unix
def test_mode_rendu(proj):
    f = proj / "outil.sh"
    f.write_text("#!/bin/sh\n")
    f.chmod(0o755)
    _pre(f)
    f.write_text("cassé")
    _post()
    instantanes.restaurer()
    assert f.stat().st_mode & 0o777 == 0o755 and f.read_text() == "#!/bin/sh\n"


# --- jamais un secret copié, jamais un fichier protégé écrit -------------------------------

@pytest.mark.parametrize("name", [".env", ".env.local", "id_rsa", "server.key", "client_secret.json",
                                  "api-token.txt", "credentials.json"])
def test_noms_de_secrets_jamais_copies(proj, name):
    f = proj / name
    f.write_text("SECRET")
    assert _pre(f) == 0
    assert not any((instantanes.DIR).rglob("*.bin"))


def test_fichier_prive_et_lien_dur_jamais_copies(proj):
    priv = proj / "prive.txt"
    priv.write_text("SECRET")
    priv.chmod(0o600)
    assert _pre(priv) == 0
    src = proj / "src.txt"
    src.write_text("x")
    os.link(src, proj / "dur.txt")
    assert _pre(proj / "dur.txt") == 0
    lien = proj / "lien.txt"
    lien.symlink_to(src)
    assert _pre(lien) == 0
    assert not any(instantanes.DIR.rglob("*.bin"))


@pytest.mark.parametrize("path", [
    R / "astrel" / "guard.py", R / "astrel" / "instantanes.py", R / "hooks" / "permission_hook.py",
    R / "runtime" / "hud-token", R / "runtime" / "grants.json", R / "config" / "secrets.json",
    Path.home() / ".bashrc", Path.home() / ".claude" / ".credentials.json", R / ".venv" / "bin" / "python",
    Path.home() / "Documents" / "hors-projets.txt",
])
def test_cibles_protegees_refusees(path):
    assert instantanes.refus(str(path)), path


def test_protege_jamais_copie_ni_retabli(proj, monkeypatch):
    """Un fichier protégé n'est pas copié ; un index FORGÉ qui le vise n'est pas rétabli."""
    prot = proj / "protege.py"
    prot.write_text("vrai")
    monkeypatch.setattr(guard, "PROTECTED_CODE", guard.PROTECTED_CODE + [prot.resolve()])
    assert _pre(prot) == 0
    assert instantanes.lots()[0]["ignores"] == {"protégé": 1}
    # Index forgé : copie « avant » malveillante, empreinte « après » = contenu actuel.
    import hashlib
    lot = {"id": "1-0000abcd", "cle": "", "debut": 1, "fin": 1, "restaure": None, "ignores": {},
           "fichiers": [{"chemin": str(prot), "avant": hashlib.sha256(b"forge").hexdigest(), "copie": "0.bin",
                         "apres": hashlib.sha256(b"vrai").hexdigest(), "attente": [], "mode": 0o644, "taille": 5}]}
    (instantanes.DIR / "1-0000abcd").mkdir()
    (instantanes.DIR / "1-0000abcd" / "0.bin").write_bytes(b"forge")
    instantanes.INDEX.write_text(json.dumps({"lots": [lot]}))
    r = instantanes.restaurer()
    assert r["laisses"] == [{"chemin": str(prot), "pourquoi": "protégé"}] and prot.read_text() == "vrai"


def test_dossier_remplace_par_un_lien_avant_annulation(proj, tmp_path, monkeypatch):
    """Le dossier du fichier devient un lien vers un dossier protégé : rien n'y est écrit."""
    d = proj / "sous"
    d.mkdir()
    f = d / "a.py"
    f.write_text("avant")
    _pre(f)
    f.write_text("après")
    _post()
    garde = tmp_path / "garde"
    garde.mkdir()
    (garde / "a.py").write_text("après")  # même contenu : l'empreinte « après » correspondrait
    monkeypatch.setattr(guard, "PROTECTED_CODE", guard.PROTECTED_CODE + [garde.resolve()])
    os.rename(d, proj / "ancien")
    d.symlink_to(garde)
    r = instantanes.restaurer()
    assert r["restaures"] == [] and (garde / "a.py").read_text() == "après"


def test_copie_abimee_non_retablie(proj):
    f = proj / "a.py"
    f.write_text("avant")
    _pre(f)
    f.write_text("après")
    _post()
    for b in instantanes.DIR.rglob("*.bin"):
        b.write_bytes(b"autre chose")
    r = instantanes.restaurer()
    assert r["laisses"][0]["pourquoi"] == "copie abîmée" and f.read_text() == "après"


def test_bornes(proj, monkeypatch):
    monkeypatch.setattr(instantanes, "MAX_FICHIER", 10)
    gros = proj / "gros.py"
    gros.write_text("x" * 50)
    assert _pre(gros) == 0
    monkeypatch.setattr(instantanes, "MAX_LOTS", 3)
    for i in range(6):
        f = proj / f"f{i}.py"
        f.write_text("v")
        _pre(f, tid=f"t{i}", prompt=f"p{i}")
    assert len(instantanes.lots()) == 3
    assert len([d for d in instantanes.DIR.iterdir() if d.is_dir()]) <= 3


@unix
def test_droits_du_stockage(proj):
    f = proj / "a.py"
    f.write_text("avant")
    _pre(f)
    assert instantanes.DIR.stat().st_mode & 0o777 == 0o700
    assert instantanes.INDEX.stat().st_mode & 0o777 == 0o600
    assert all(b.stat().st_mode & 0o777 == 0o600 for b in instantanes.DIR.rglob("*.bin"))


def test_garde_fou_indisponible_rien_n_est_copie(proj, monkeypatch):
    f = proj / "a.py"
    f.write_text("avant")
    monkeypatch.setattr(guard, "classify", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("panne")))
    assert _pre(f) == 0
    assert not any(instantanes.DIR.rglob("*.bin"))


# --- garde-fou : le stockage et le code sont protégés --------------------------------------

@pytest.mark.parametrize("tool, inp", [
    ("Write", {"file_path": str(R / "runtime" / "instantanes" / "index.json")}),
    ("Edit", {"file_path": str(R / "runtime" / "garde-refus" / "series.json")}),
    ("Edit", {"file_path": str(R / "astrel" / "instantanes.py")}),
    ("Bash", {"command": "echo '{}' > runtime/instantanes/index.json"}),
    ("Bash", {"command": "rm -rf runtime/garde-refus"}),
    ("Bash", {"command": "sed -i 's/a/b/' astrel/instantanes.py"}),
    ("Bash", {"command": "ln -s /tmp/x runtime/instantanes/1-0000abcd"}),
    # 01/11 : messages de l'utilisateur en attente (`attente.py`) : ni écrits, ni effacés par Claude.
    ("Write", {"file_path": str(R / "runtime" / "messages-en-attente.json")}),
    ("Bash", {"command": "echo '{}' > runtime/messages-en-attente.json"}),
    ("Bash", {"command": "rm -f runtime/messages-en-attente.json"}),
    ("Bash", {"command": "cd runtime && truncate -s 0 messages-en-attente.json"}),
    ("Bash", {"command": "python3 -c \"open('runtime/messages-en-attente.json','w')\""}),
])
def test_stockage_protege(tool, inp):
    v = guard.assess(tool, inp, mode="autonome", cwd=str(R))
    assert v.needs_confirm and v.guard and not v.keys, (tool, inp)


@pytest.mark.parametrize("cmd", ["cat runtime/instantanes/index.json", "ls -la runtime/garde-refus",
                                 "grep -n restaurer astrel/instantanes.py", "cat runtime/messages-en-attente.json",
                                 "jq . runtime/messages-en-attente.json"])
def test_lecture_libre(cmd):
    assert not guard.assess("Bash", {"command": cmd}, mode="autonome", cwd=str(R)).needs_confirm


# --- le hook ----------------------------------------------------------------------------

def _hook(monkeypatch, capsys, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert suivi.main() == 0
    return capsys.readouterr().out


def test_hook_pre_ne_decide_rien(proj, monkeypatch, capsys):
    f = proj / "a.py"
    f.write_text("avant")
    out = _hook(monkeypatch, capsys, {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_use_id": "t9",
                                      "session_id": "s", "prompt_id": "p", "cwd": str(proj),
                                      "tool_input": {"file_path": "a.py"}})
    assert out == ""  # aucune décision : le garde-fou reste seul juge
    f.write_text("après")
    assert _hook(monkeypatch, capsys, {"hook_event_name": "PostToolUse", "tool_name": "Edit",
                                       "tool_use_id": "t9", "tool_response": {}}) == ""
    assert instantanes.restaurer()["restaures"] == [str(f.resolve())]


def test_hook_coupe_par_variable(proj, monkeypatch, capsys):
    monkeypatch.setenv("ASTREL_INSTANTANES", "0")
    f = proj / "a.py"
    f.write_text("avant")
    _hook(monkeypatch, capsys, {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_use_id": "t9",
                                "cwd": str(proj), "tool_input": {"file_path": "a.py"}})
    assert not instantanes.INDEX.exists()


def test_hook_entree_illisible(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("pas du json"))
    assert suivi.main() == 0 and capsys.readouterr().out == ""


def test_reglages_declarent_le_suivi(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "claude-settings.json")
    hooks = json.loads(brain.write_settings().read_text(encoding="utf-8"))["hooks"]
    assert hooks["PreToolUse"][0]["matcher"] == "*"  # le garde-fou reste le premier
    cmd = config.hook_command(config.SUIVI_HOOK_SCRIPT, fail_closed=False)
    for event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        assert any(h.get("matcher") == config.SUIVI_MATCHER and h["hooks"][0]["command"] == cmd
                   for h in hooks[event]), event
    assert "-I" in cmd and "|| exit 2" not in cmd  # isolé, mais jamais bloquant
    assert hooks["SubagentStop"][0]["hooks"][0]["command"] == config.python_command(config.STOP_HOOK_SCRIPT)


# --- outil MCP et terminal ---------------------------------------------------------------

def test_outil_mcp_refuse_sous_estop(proj):
    pytest.importorskip("mcp")
    from mcp.server.mcpserver.exceptions import ToolError

    from astrel import mcp_server
    f = proj / "a.py"
    f.write_text("avant")
    _pre(f)
    f.write_text("après")
    _post()
    config.ESTOP_FILE.write_text("")
    with pytest.raises(ToolError):
        mcp_server.annuler_ecritures()
    assert f.read_text() == "après"
    config.ESTOP_FILE.unlink()
    assert mcp_server.annuler_ecritures()["restaures"] == [str(f)] and f.read_text() == "avant"


def test_terminal(proj, capsys):
    from astrel import cli
    f = proj / "a.py"
    f.write_text("avant")
    _pre(f)
    f.write_text("après")
    _post()
    assert cli.main(["annule", "--liste"]) == 0 and str(f) in capsys.readouterr().out
    assert cli.main(["annule"]) == 0
    assert "rétabli" in capsys.readouterr().out and f.read_text() == "avant"


# --- relecture du 01/11 -------------------------------------------------------------------

@pytest.fixture
def faux_runtime(tmp_path, monkeypatch):
    """Un `runtime/` simulé avec un secret 0644 dans un dossier 0700 (comme whatsapp-auth)."""
    rt = tmp_path / "fakeruntime"
    (rt / "whatsapp-auth").mkdir(parents=True)
    os.chmod(rt / "whatsapp-auth", 0o700)
    secret = rt / "whatsapp-auth" / "creds.json"
    secret.write_text('{"noiseKey":"SECRET"}')
    os.chmod(secret, 0o644)
    monkeypatch.setattr(config, "RUNTIME", rt)
    return secret


def _bascule(lien, vers):
    tmp = lien.with_name(".bascule")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(vers)
    os.replace(tmp, lien)


def _aucune_copie_du_secret():
    return not any(b.read_bytes() == b'{"noiseKey":"SECRET"}' for b in instantanes.DIR.rglob("*.bin"))


def test_course_lien_qui_bascule_apres_le_jugement(proj, faux_runtime, monkeypatch):
    """Point 1 : le lien bascule vers le secret JUSTE APRÈS le jugement : rien n'est copié."""
    (proj / "real").mkdir()
    (proj / "real" / "creds.json").write_text("inoffensif")
    w = proj / "w"
    _bascule(w, proj / "real")
    vrai = instantanes.refus

    def puis_bascule(*a, **k):
        r = vrai(*a, **k)
        _bascule(w, faux_runtime.parent)
        return r
    monkeypatch.setattr(instantanes, "refus", puis_bascule)
    _pre(w / "creds.json")
    monkeypatch.setattr(instantanes, "refus", vrai)
    assert _aucune_copie_du_secret()


def test_course_dossier_remplace_entre_resolution_et_ouverture(proj, faux_runtime, monkeypatch):
    """Point 1 : un dossier du chemin RÉSOLU devient un lien vers le secret avant l'ouverture : le
    vrai chemin du fichier ouvert est rejugé, rien n'est copié."""
    (proj / "real").mkdir()
    (proj / "real" / "creds.json").write_text("inoffensif")
    vrai = instantanes.refus
    fait = []

    def puis_remplace(chemin, *a, **k):
        r = vrai(chemin, *a, **k)
        if not fait and str(chemin).endswith("real/creds.json"):
            fait.append(1)
            os.rename(proj / "real", proj / "real.old")
            (proj / "real").symlink_to(faux_runtime.parent)
        return r
    monkeypatch.setattr(instantanes, "refus", puis_remplace)
    assert _pre(proj / "real" / "creds.json") == 0
    monkeypatch.setattr(instantanes, "refus", vrai)
    assert fait and _aucune_copie_du_secret()


def test_course_en_boucle(proj, faux_runtime):
    import threading
    (proj / "real").mkdir()
    (proj / "real" / "creds.json").write_text("inoffensif")
    w = proj / "w"
    _bascule(w, proj / "real")
    stop = []

    def boucle():
        while not stop:
            _bascule(w, faux_runtime.parent)
            _bascule(w, proj / "real")
    th = threading.Thread(target=boucle, daemon=True)
    th.start()
    try:
        for i in range(150):
            _pre(w / "creds.json", tid=f"t{i}", prompt=f"p{i}")
    finally:
        stop.append(1)
        th.join()
    assert _aucune_copie_du_secret()


@unix
def test_fichier_0644_dans_dossier_0700(tmp_path, monkeypatch):
    """Point 1 : un fichier lisible rangé dans un dossier privé d'un projet n'est pas copié (sous
    /tmp, non jugé : les dossiers de travail y sont en 0700)."""
    projets = tmp_path / "projets"
    (projets / "app" / "prive").mkdir(parents=True)
    os.chmod(projets / "app" / "prive", 0o700)
    f = projets / "app" / "prive" / "reglages.py"
    f.write_text("x")
    os.chmod(f, 0o644)
    monkeypatch.setattr(guard, "ALLOWED_WRITE_ROOTS", [projets, Path("/tmp")])
    assert instantanes.refus(str(f)) == "secret"
    os.chmod(projets / "app" / "prive", 0o755)
    assert instantanes.refus(str(f)) == ""


def test_modif_hors_outil_dans_le_meme_lot(proj):
    """Point 2 : l'utilisateur modifie le fichier entre deux écritures de Claude du même message : annuler
    ne l'écrase pas, et le dit."""
    f = proj / "notes.py"
    f.write_text("A\n")
    _pre(f, tid="t1")
    f.write_text("A\nclaude1\n")
    _post("t1")
    f.write_text("A\nclaude1\nUTILISATEUR\n")
    _pre(f, tid="t2")
    f.write_text("A\nclaude1\nUTILISATEUR\nclaude2\n")
    _post("t2")
    r = instantanes.restaurer()
    assert r["laisses"] == [{"chemin": str(f), "pourquoi": "modifié hors outil pendant le lot"}]
    assert f.read_text() == "A\nclaude1\nUTILISATEUR\nclaude2\n"


def test_estop_refuse_a_claude_quel_que_soit_par(proj, monkeypatch):
    """Point 7 : sous ESTOP, `restaurer` lui-même refuse à un process de Claude (même `par="terminal"`,
    même par `python -c`) ; le terminal de l'utilisateur rétablit."""
    from astrel import cli
    f = proj / "a.py"
    f.write_text("avant")
    _pre(f)
    f.write_text("après")
    _post()
    config.ESTOP_FILE.write_text("")
    monkeypatch.setattr(instantanes, "vient_de_claude", lambda pid=None: True)
    for par in ("claude", "terminal", "utilisateur"):
        with pytest.raises(instantanes.ArretUrgence):
            instantanes.restaurer(par=par)
    assert cli.main(["annule"]) == 1 and f.read_text() == "après"
    assert instantanes.restaurer(apercu=True)["restaures"] == [str(f)]  # voir reste permis
    monkeypatch.setattr(instantanes, "vient_de_claude", lambda pid=None: False)
    assert cli.main(["annule"]) == 0 and f.read_text() == "avant"


def test_vient_de_claude_dans_le_doute(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    assert instantanes.vient_de_claude()


def test_doctor_surveille_le_suivi():
    """Point 6 : l'empreinte du doctor couvre le module et le hook."""
    from astrel import doctor
    assert R / "astrel" / "instantanes.py" in doctor.GUARD_FILES
    assert config.SUIVI_HOOK_SCRIPT in doctor.GUARD_FILES


# Point 8 (hors lot) : lire un secret À TRAVERS un lien du projet.
@pytest.fixture
def lien_secret(tmp_path):
    (tmp_path / "w").symlink_to(R / "runtime" / "whatsapp-auth")
    (tmp_path / "h").symlink_to(R / "runtime")
    return tmp_path


@pytest.mark.parametrize("cmd", [
    "cat w/creds.json", "base64 w/creds.json", "cp w/creds.json /tmp/x", "head -c 10 < w/creds.json",
    "cat w/*", "cat h/hud-token", "python3 x.py h/hud-token", "cat ./w/../w/creds.json",
])
def test_lecture_d_un_secret_par_un_lien(lien_secret, cmd):
    v = guard.assess("Bash", {"command": cmd}, mode="autonome", cwd=str(lien_secret))
    assert v.needs_confirm and v.guard, cmd


@pytest.mark.parametrize("cmd", ["cat h/channels.json", "ls h/../", "cat w2/x", "grep -n x h/notes.txt"])
def test_lien_vers_ailleurs_reste_libre(lien_secret, cmd):
    assert not guard.assess("Bash", {"command": cmd}, mode="autonome", cwd=str(lien_secret)).needs_confirm, cmd


def test_modif_hors_outil_apres_un_edit_refuse(proj):
    """Contre-relecture N1 : t1 écrit, t2 refusé (jamais de PostToolUse), l'utilisateur modifie, t3 écrit :
    annuler ne revient pas à « A » par-dessus la modification de l'utilisateur."""
    f = proj / "notes.py"
    f.write_text("A\n")
    _pre(f, tid="t1")
    f.write_text("A\nclaude1\n")
    _post("t1")
    _pre(f, tid="t2")  # refusé par le garde-fou : rien après
    f.write_text("A\nclaude1\nUTILISATEUR\n")
    _pre(f, tid="t3")
    f.write_text("A\nclaude1\nUTILISATEUR\nclaude3\n")
    _post("t3")
    r = instantanes.restaurer()
    assert r["laisses"] == [{"chemin": str(f), "pourquoi": "modifié hors outil pendant le lot"}]
    assert f.read_text() == "A\nclaude1\nUTILISATEUR\nclaude3\n"


@unix
def test_dossier_a_creer_n_est_pas_prive(tmp_path, monkeypatch):
    """Contre-relecture N2 : un Write qui crée nouveau/a.py est suivi (le dossier absent n'est pas
    « privé ») ; un dossier privé EXISTANT au-dessus reste refusé."""
    projets = tmp_path / "projets"
    (projets / "app").mkdir(parents=True)
    os.chmod(projets / "app", 0o755)
    monkeypatch.setattr(guard, "ALLOWED_WRITE_ROOTS", [projets, Path("/tmp")])
    f = projets / "app" / "nouveau" / "sous" / "a.py"
    assert instantanes.refus(str(f)) == ""
    assert _pre(f, tool="Write") == 1
    os.chmod(projets / "app", 0o700)
    assert instantanes.refus(str(f)) == "secret"
