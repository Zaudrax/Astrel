"""Lot 5 du moteur local (05/11/2026, docs/chantiers/HYBRIDE-QWEN.md) : commandes AUTORISÉES par l'utilisateur
(liste blanche, serveur MCP `astrel_local`, hook) et PROPOSITIONS de modification appliquées par le cœur
(empreinte, périmètre, garde-fou, instantané, HUD seul), ouverture par le banc, routage."""
from __future__ import annotations

import importlib.util
import inspect
import io
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from conftest import mode_prive

from astrel import (commandes_locales, config, core, guard, instantanes, outils_local, propositions_local,
                    routeur)
from astrel.events import bus

R = config.ROOT.resolve()
PY = sys.executable


def _liste(tmp_path, cmds: dict) -> Path:
    f = commandes_locales.FICHIER
    f.write_text(json.dumps({"commandes": cmds}), encoding="utf-8")
    return f


def _cmd(tmp_path, argv, **kw) -> dict:
    return {"argv": argv, "dossier": str(tmp_path), **kw}


@pytest.fixture
def racine(tmp_path, monkeypatch):
    """Le dossier du test fait office de dossier des projets (`config.WORKDIR`)."""
    monkeypatch.setattr(config, "WORKDIR", tmp_path)
    return tmp_path


# ================================================================== 1. la liste blanche

def test_liste_absente_vide_et_exemple_valide(racine):
    assert commandes_locales.charger() == {}
    data = json.loads(commandes_locales.EXEMPLE.read_text(encoding="utf-8"))
    assert data["commandes"] == {}  # liste réelle vide par défaut : les exemples sont « commentés »
    exemples = {"commandes": {k: {**v, "dossier": str(racine)} for k, v in data["_exemples"].items()}}
    cmds = commandes_locales.valider(exemples)
    assert {"tests", "git-status", "journal-astrel"} <= set(cmds)
    assert all(isinstance(c.argv, tuple) for c in cmds.values())


@pytest.mark.parametrize("entree, morceau", [
    ({"argv": "pytest -q"}, "LISTE"),                                  # une ligne de shell : jamais
    ({"argv": []}, "LISTE"),
    ({"argv": ["ls", ""]}, "vide"),
    ({"argv": ["ls"], "shell": True}, "clé inconnue"),
    ({"argv": ["ls"], "dossier": "relatif/x"}, "absolu"),
    ({"argv": ["ls"], "dossier": os.path.abspath("/etc")}, "hors de tes projets"),  # 05/11 : absolu sous Windows aussi
    ({"argv": ["ls"], "delai_s": 0}, "delai_s"),
    ({"argv": ["ls"], "delai_s": 10_000}, "delai_s"),
    ({"argv": ["ls"], "sortie_max": 10}, "sortie_max"),
    ({"argv": ["ls"], "description": "x" * 500}, "description"),
])
def test_validation_stricte(racine, entree, morceau):
    e = {"dossier": str(racine), **entree}
    with pytest.raises(commandes_locales.ListeInvalide, match=morceau):
        commandes_locales.valider({"commandes": {"x": e}})


@pytest.mark.parametrize("cid", ["Majuscule", "a b", "../x", "", "x" * 41, "-x"])
def test_identifiant_refuse(racine, cid):
    with pytest.raises(commandes_locales.ListeInvalide, match="identifiant"):
        commandes_locales.valider({"commandes": {cid: {"argv": ["ls"], "dossier": str(racine)}}})


def test_ecrire_valide_avant_d_ecrire_et_0600(racine):
    f = commandes_locales.FICHIER
    with pytest.raises(commandes_locales.ListeInvalide):
        commandes_locales.ecrire('{"commandes": {"x": {"argv": "rm -rf /", "dossier": "/"}}}')
    assert not f.exists()
    cmds = commandes_locales.ecrire(json.dumps({"commandes": {"ls": {"argv": ["ls"], "dossier": str(racine)}}}))
    assert list(cmds) == ["ls"] and mode_prive(f)  # 05/11 : pas de bits de mode sous Windows
    assert json.loads(f.read_text(encoding="utf-8"))["commandes"]["ls"]["delai_s"] == 60.0


# ================================================================== 2. exécution bornée

def test_commande_lancee_telle_quelle(racine):
    _liste(racine, {"echo": _cmd(racine, [PY, "-c", "import sys, os; print('BONJOUR', os.getcwd(), sys.argv[1:])"])})
    r = commandes_locales.lancer("echo")
    assert r["code"] == 0 and "BONJOUR" in r["sortie"] and str(racine) in r["sortie"] and "[]" in r["sortie"]
    assert not r["tronquee"] and not r["expiree"]


def test_id_inconnu_refuse_par_le_serveur(racine):
    _liste(racine, {"echo": _cmd(racine, [PY, "-c", "print(1)"])})
    for cid in ("rm-tout", "echo; rm -rf /", None, ""):
        with pytest.raises(commandes_locales.ListeInvalide, match="inconnue"):
            commandes_locales.lancer(cid)


def test_sortie_bornee(racine):
    _liste(racine, {"bavard": _cmd(racine, [PY, "-c", "print('x' * 300000)"], sortie_max=1000)})
    r = commandes_locales.lancer("bavard")
    assert r["tronquee"] and len(r["sortie"]) <= 1000 and r["code"] == 0


def test_delai_tue_tout_le_groupe(racine):
    marque = racine / "survivant.txt"
    # Un enfant de la commande écrirait la marque après 3 s : il doit mourir avec elle au bout du délai.
    enfant = racine / "enfant.py"
    enfant.write_text(f"import time\ntime.sleep(3)\nopen({str(marque)!r}, 'w').close()\n", encoding="utf-8")
    parent = racine / "parent.py"
    parent.write_text(f"import subprocess, sys, time\nsubprocess.Popen([sys.executable, {str(enfant)!r}])\n"
                      "time.sleep(60)\n", encoding="utf-8")
    _liste(racine, {"lent": _cmd(racine, [PY, str(parent)], delai_s=1)})
    t0 = time.monotonic()
    r = commandes_locales.lancer("lent")
    assert r["expiree"] and r["code"] is None and time.monotonic() - t0 < 10
    time.sleep(3.5)
    assert not marque.exists()


def test_environnement_nettoye(racine, monkeypatch):
    for k, v in (("CLAUDECODE", "1"), ("ANTHROPIC_API_KEY", "sk-ant-x"), ("ASTREL_BRAIN", "1"),
                 ("CLAUDE_CONFIG_DIR", "/x"), ("GITHUB_TOKEN", "ghp_x"), ("CLE_API_X", "y")):
        monkeypatch.setenv(k, v)
    _liste(racine, {"env": _cmd(racine, [PY, "-c", "import os, json; print(json.dumps(sorted(os.environ)))"])})
    vu = set(json.loads(commandes_locales.lancer("env")["sortie"]))
    assert not vu & {"CLAUDECODE", "ANTHROPIC_API_KEY", "ASTREL_BRAIN", "CLAUDE_CONFIG_DIR", "GITHUB_TOKEN", "CLE_API_X"}
    assert "PATH" in vu


def test_dossier_rejuge_au_lancement(racine, tmp_path_factory):
    ailleurs = tmp_path_factory.mktemp("ailleurs")
    cmd = commandes_locales.Commande("x", (PY, "-c", "print(1)"), ailleurs, 5.0, 1000, "")
    with pytest.raises(commandes_locales.ListeInvalide, match="hors de tes projets"):
        commandes_locales.executer(cmd)


# ================================================================== 3. le serveur MCP

def test_outils_du_serveur_sans_argument_libre(racine, monkeypatch):
    from astrel import mcp_local
    assert list(inspect.signature(mcp_local.commande_autorisee).parameters) == ["id"]
    assert list(inspect.signature(mcp_local.liste_commandes).parameters) == []
    monkeypatch.setitem(mcp_local._ARGS, "projet", racine)
    _liste(racine, {"echo": _cmd(racine, [PY, "-c", "print('OK-7781')"], description="écho")})
    assert mcp_local.liste_commandes()["commandes"] == [{"id": "echo", "description": "écho"}]
    assert "OK-7781" in mcp_local.commande_autorisee("echo")["sortie"]
    from mcp.server.mcpserver.exceptions import ToolError
    with pytest.raises(ToolError, match="inconnue"):
        mcp_local.commande_autorisee("rm-tout")
    config.ESTOP_FILE.write_text("1")
    with pytest.raises(ToolError, match="arrêt d'urgence"):
        mcp_local.commande_autorisee("echo")


def test_chaque_outil_du_serveur_local_a_une_classe_ecrite():
    import ast
    tree = ast.parse((R / "astrel" / "mcp_local.py").read_text(encoding="utf-8"))
    outils = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)
              and any("tool" in ast.unparse(d) for d in n.decorator_list)}
    assert outils == set(guard.MCP_POLICY["astrel_local"]) == set(guard._ENTREES_LOCALES)


def test_serveur_jamais_declare_pour_claude():
    from astrel import mcp_config
    src = inspect.getsource(mcp_config)
    assert "mcp_local" not in src and "astrel_local" not in src


# ================================================================== 4. le garde-fou

@pytest.fixture
def cli_local(monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(guard.LOCAL_CLI_DIR))


def test_hook_refuse_hors_du_cli_local(racine, monkeypatch):
    _liste(racine, {"echo": _cmd(racine, ["echo"])})
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert "modèle local" in guard.refused("mcp__astrel_local__commande_autorisee", {"id": "echo"})


def test_hook_id_connu_libre_id_inconnu_refuse(racine, cli_local):
    _liste(racine, {"echo": _cmd(racine, ["echo"])})
    assert guard.refused("mcp__astrel_local__commande_autorisee", {"id": "echo"}) == ""
    assert "absente de la liste" in guard.refused("mcp__astrel_local__commande_autorisee", {"id": "rm-tout"})
    assert guard.refused("mcp__astrel_local__liste_commandes", {}) == ""
    v = guard.assess("mcp__astrel_local__commande_autorisee", {"id": "echo"}, mode="prudent")
    assert not v.needs_confirm  # classe écrite : libre (le refus d'office est passé avant)


@pytest.mark.parametrize("entree", [
    {"id": "echo", "args": "--force"}, {"id": "echo", "argv": ["rm"]}, {"id": "echo", "command": "rm -rf /"},
    {"id": ["echo"]}, {}, None, "echo",
])
def test_hook_argument_ajoute_refuse(racine, cli_local, entree):
    _liste(racine, {"echo": _cmd(racine, ["echo"])})
    assert guard.refused("mcp__astrel_local__commande_autorisee", entree)


def test_hook_sans_entree_refuse(racine, cli_local):
    _liste(racine, {"echo": _cmd(racine, ["echo"])})
    assert guard.refused("mcp__astrel_local__commande_autorisee")  # ancien appel, sans entrée : refusé
    assert guard.refused("mcp__astrel_local__liste_commandes", {"x": 1})
    assert guard.refused("mcp__astrel_local__inconnu", {})
    assert guard.refused("mcp__astrel_local__proposer_modification", {"contenu": "x"})
    assert guard.refused("mcp__astrel_local__proposer_modification", {"fichier": "a", "mode": "w"})
    assert guard.refused("mcp__astrel_local__proposer_modification", {"fichier": "a", "contenu": "x"}) == ""


def test_liste_illisible_tout_refuse(racine, cli_local):
    commandes_locales.FICHIER.write_text("{pas du json", encoding="utf-8")
    assert guard.refused("mcp__astrel_local__commande_autorisee", {"id": "echo"})


def _hook():
    spec = importlib.util.spec_from_file_location("permission_hook_lot5", R / "hooks" / "permission_hook.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _par_le_hook(monkeypatch, tool, inp):
    from astrel import confirm
    monkeypatch.setattr(confirm, "request", lambda *a, **k: pytest.fail("aucune carte attendue"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "s", "prompt_id": "p", "tool_name": tool,
                                                              "tool_input": inp, "cwd": "/tmp"})))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    assert _hook().main() == 0
    return json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"]


def test_le_vrai_hook_juge_l_entree(racine, cli_local, monkeypatch):
    _liste(racine, {"echo": _cmd(racine, ["echo"])})
    assert _par_le_hook(monkeypatch, "mcp__astrel_local__commande_autorisee", {"id": "echo"}) == "allow"
    assert _par_le_hook(monkeypatch, "mcp__astrel_local__commande_autorisee", {"id": "inconnu"}) == "deny"
    assert _par_le_hook(monkeypatch, "mcp__astrel_local__commande_autorisee", {"id": "echo", "x": "-f"}) == "deny"


REELLE = guard.PROTECTED_PATHS  # chemins RÉELS (le test ne remplace que celui qui est lu)


@pytest.mark.parametrize("cible", [config.CONFIG_DIR / "commandes-locales.json",
                                   config.RUNTIME / "propositions-local" / "L20261105-120000-abcdef.json"])
def test_liste_et_propositions_proteges_contre_claude(cible):
    for tool, inp in (("Write", {"file_path": str(cible), "content": "{}"}),
                      ("Edit", {"file_path": str(cible), "old_string": "a", "new_string": "b"})):
        v = guard.assess(tool, inp, mode="autonome")
        assert v.needs_confirm and v.guard is True, (tool, cible)
    rel = cible.relative_to(R)
    for cmd in (f"echo '{{}}' > {rel}", f"cp /tmp/x {rel}", f"rm {rel}", f"sed -i s/a/b/ {rel}"):
        v = guard.assess("Bash", {"command": cmd}, mode="autonome", cwd=str(R))
        assert v.needs_confirm and v.guard is True, cmd
    assert not guard.assess("Bash", {"command": f"cat {rel}"}, mode="autonome", cwd=str(R)).needs_confirm


# ================================================================== 5. propositions

@pytest.fixture
def projet(tmp_path):
    p = tmp_path / "projet"
    p.mkdir()
    (p / "notes.txt").write_text("ligne 1\ncode ZORGLUB-4217\nligne 3\n", encoding="utf-8")
    return p


def _deposer(projet, **kw):
    kw.setdefault("ancien", "ZORGLUB-4217")
    kw.setdefault("nouveau", "ZORGLUB-0000")
    return propositions_local.deposer(kw.pop("fichier", "notes.txt"), projet, raison="code changé", canal="general",
                                      **kw)


def test_deposer_n_ecrit_pas_le_fichier(projet):
    avant = (projet / "notes.txt").read_bytes()
    r = _deposer(projet)
    assert r["ok"] and r["lignes_ajoutees"] == 1 and r["lignes_retirees"] == 1
    assert (projet / "notes.txt").read_bytes() == avant
    f = propositions_local.DIR / f"{r['proposition']}.json"
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["statut"] == "en attente" and data["empreinte_origine"] == propositions_local._sha_bytes(avant)
    assert mode_prive(f)
    (item,) = propositions_local.lister()
    assert "-code ZORGLUB-4217" in item["diff"] and "+code ZORGLUB-0000" in item["diff"]
    assert item["etat_fichier"] == "identique"


@pytest.mark.parametrize("kw, morceau", [
    ({"ancien": "absent"}, "exactement une fois"),
    ({"ancien": "ligne"}, "exactement une fois"),
    ({"fichier": "/etc/hostname", "ancien": "", "nouveau": "", "contenu": "x"}, "hors du dossier"),
    ({"fichier": "../dehors.txt", "ancien": "", "nouveau": "", "contenu": "x"}, "hors du dossier"),
    ({"ancien": "", "nouveau": "", "contenu": ""}, "rien à proposer"),
    ({"contenu": "x"}, "pas les deux"),
    ({"fichier": "neuf/a.txt", "ancien": "", "nouveau": "", "contenu": "x"}, "introuvable"),
])
def test_depot_refuse(projet, kw, morceau):
    with pytest.raises(propositions_local.Refus, match=morceau):
        _deposer(projet, **kw)


def test_depot_refuse_lien_secret_et_fichier_prive(projet, tmp_path):
    dehors = tmp_path / "dehors.txt"
    dehors.write_text("secret\n", encoding="utf-8")
    (projet / "lien.txt").symlink_to(dehors)
    with pytest.raises(propositions_local.Refus, match="lien"):
        _deposer(projet, fichier="lien.txt", ancien="", nouveau="", contenu="x")
    (projet / ".env").write_text("A=1\n", encoding="utf-8")
    with pytest.raises(propositions_local.Refus, match="protégé ou secret"):
        _deposer(projet, fichier=".env", ancien="A=1", nouveau="A=2")
    if os.name == "nt":  # 05/11 : un fichier « privé » se reconnaît à ses bits de mode, absents sous Windows
        return
    prive = projet / "prive.txt"
    prive.write_text("x\n", encoding="utf-8")
    prive.chmod(0o600)
    with pytest.raises(propositions_local.Refus, match="protégé ou secret"):
        _deposer(projet, fichier="prive.txt", ancien="x", nouveau="y")


def _appliquer(pid, projet, empreinte=None, perimetres=None):
    item = next(p for p in propositions_local.lister(toutes=True) if p["proposition_id"] == pid)
    return propositions_local.appliquer(pid, empreinte or item["empreinte"],
                                        [projet] if perimetres is None else perimetres)


def test_appliquer_avec_instantane_puis_annuler(projet):
    avant = (projet / "notes.txt").read_text(encoding="utf-8")
    pid = _deposer(projet)["proposition"]
    r = _appliquer(pid, projet)
    assert r["ok"] and "ZORGLUB-0000" in (projet / "notes.txt").read_text(encoding="utf-8")
    assert stat.S_IMODE((projet / "notes.txt").stat().st_mode) & 0o044  # droits d'avant gardés
    lot = instantanes.lots()[0]
    assert str((projet / "notes.txt").resolve()) in lot["fichiers"]
    assert propositions_local.lister() == []  # plus en attente
    with pytest.raises(propositions_local.Refus, match="déjà traitée"):
        _appliquer(pid, projet)
    instantanes.restaurer(par="terminal")  # « ./run.sh annule » défait l'application
    assert (projet / "notes.txt").read_text(encoding="utf-8") == avant


def test_appliquer_refuse_si_le_fichier_a_change(projet):
    pid = _deposer(projet)["proposition"]
    item = propositions_local.lister()[0]
    (projet / "notes.txt").write_text("autre contenu\n", encoding="utf-8")
    assert propositions_local.lister()[0]["etat_fichier"] == "modifié"
    with pytest.raises(propositions_local.Refus, match="a changé depuis la proposition"):
        propositions_local.appliquer(pid, item["empreinte"], [projet])
    assert (projet / "notes.txt").read_text(encoding="utf-8") == "autre contenu\n"
    assert instantanes.lots() == []  # aucun instantané ni écriture


def test_appliquer_refuse_une_vue_perimee(projet):
    pid = _deposer(projet)["proposition"]
    with pytest.raises(propositions_local.Refus, match="relis-la"):
        propositions_local.appliquer(pid, "0" * 16, [projet])


def test_appliquer_refuse_hors_perimetre(projet, tmp_path_factory):
    pid = _deposer(projet)["proposition"]
    autre = tmp_path_factory.mktemp("autre-projet")
    with pytest.raises(propositions_local.Refus, match="périmètre"):
        _appliquer(pid, projet, perimetres=[autre])
    with pytest.raises(propositions_local.Refus, match="périmètre"):
        _appliquer(pid, projet, perimetres=[])
    assert "ZORGLUB-4217" in (projet / "notes.txt").read_text(encoding="utf-8")


def _forger(cible: Path, projet: Path) -> str:
    """Une proposition écrite à la main (comme si quelqu'un écrivait le dossier protégé) : le cœur rejuge tout."""
    data = cible.read_bytes()
    pid = "L20261105-120000-abcdef"
    propositions_local.DIR.mkdir(parents=True, exist_ok=True)
    (propositions_local.DIR / f"{pid}.json").write_text(json.dumps({
        "id": pid, "date": "", "statut": "en attente", "fichier": str(cible), "projet": str(projet), "canal": "",
        "existe": True, "empreinte_origine": propositions_local._sha_bytes(data), "contenu_propose": "PIRATE\n",
        "raison": "", "par": "?"}), encoding="utf-8")
    return pid


@pytest.mark.parametrize("rel", ["astrel/guard.py", "hooks/permission_hook.py", "astrel/instantanes.py"])
def test_jamais_un_fichier_protege(rel):
    cible = R / rel
    avant = cible.read_bytes()
    pid = _forger(cible, R)
    with pytest.raises(propositions_local.Refus, match="protégé"):
        _appliquer(pid, R, perimetres=[R])
    assert cible.read_bytes() == avant


def test_jamais_un_secret(projet):
    s = projet / "mes-secrets.txt"
    s.write_text("x\n", encoding="utf-8")
    pid = _forger(s, projet)
    with pytest.raises(propositions_local.Refus, match="protégé ou secret"):
        _appliquer(pid, projet)
    assert s.read_text(encoding="utf-8") == "x\n"


def test_jamais_une_zone_confidentielle(projet, monkeypatch):
    monkeypatch.setattr(guard, "zones_confidentielles", lambda: [projet.resolve()])
    pid = _forger(projet / "notes.txt", projet)
    with pytest.raises(propositions_local.Refus, match="protégé"):
        _appliquer(pid, projet)


def test_refuser_garde_la_raison(projet):
    pid = _deposer(projet)["proposition"]
    propositions_local.refuser(pid, "  pas   maintenant ")
    (item,) = propositions_local.lister(toutes=True)
    assert item["statut"] == "refusée" and item["traitee"]["raison"] == "pas maintenant"
    assert "ZORGLUB-4217" in (projet / "notes.txt").read_text(encoding="utf-8")


def test_nouveau_fichier(projet):
    pid = _deposer(projet, fichier="neuf.txt", ancien="", nouveau="", contenu="bonjour\n")["proposition"]
    assert propositions_local.lister()[0]["nouveau"] is True
    _appliquer(pid, projet)
    assert (projet / "neuf.txt").read_text(encoding="utf-8") == "bonjour\n"


# ================================================================== 6. commandes du cœur (HUD seul)

def _astrel(monkeypatch, projet):
    j = object.__new__(core.Astrel)
    j._off = False
    monkeypatch.setattr(j, "_perimetres_canal", lambda canal: [projet], raising=False)
    return j


def test_refus_a_claude_et_hors_hud(monkeypatch, projet):
    j = _astrel(monkeypatch, projet)
    pid = _deposer(projet)["proposition"]
    emp = propositions_local.lister()[0]["empreinte"]
    for name in ("propositions_local_get", "proposition_local_appliquer", "proposition_local_refuser"):
        r = j._on_command({"cmd": name, "proposition_id": pid, "empreinte": emp, "_from_claude": True, "_via": "hud"})
        assert r == {"ok": False, "error": "propositions du modèle local réservées à l'utilisateur (HUD), refusées à Claude"}
    for via in ("socket", "bubble", "whatsapp", None):
        r = j._on_command({"cmd": "proposition_local_appliquer", "proposition_id": pid, "empreinte": emp, "_via": via})
        assert r["ok"] is False and "HUD" in r["error"]
    assert "ZORGLUB-4217" in (projet / "notes.txt").read_text(encoding="utf-8")


def test_appliquer_depuis_le_hud_et_notice(monkeypatch, projet):
    vus = []
    monkeypatch.setattr(bus, "publish", lambda kind, **kw: vus.append((kind, kw)))
    j = _astrel(monkeypatch, projet)
    pid = _deposer(projet)["proposition"]
    j._publish_propositions_local(force=True)
    assert ("propositions_local", {"en_attente": 1}) in vus
    socket = j._on_command({"cmd": "propositions_local_get", "_via": "socket"})["propositions"]
    assert set(socket[0]) == {"proposition_id", "fichier", "statut", "empreinte"}  # canal de contrôle : réduit
    emp = j._on_command({"cmd": "propositions_local_get", "_via": "hud"})["propositions"][0]["empreinte"]
    r = j._on_command({"cmd": "proposition_local_appliquer", "proposition_id": pid, "empreinte": emp, "_via": "hud"})
    assert r["ok"] and "ZORGLUB-0000" in (projet / "notes.txt").read_text(encoding="utf-8")
    assert any(k == "notice" and "appliquée" in kw["text"] for k, kw in vus)
    assert ("propositions_local", {"en_attente": 0}) in vus


def test_badge_rejoue_a_la_connexion_du_hud():
    """05/11 : publié au démarrage du cœur, le nombre doit atteindre un HUD ouvert APRÈS (sujet collant)."""
    from astrel import events
    assert "propositions_local" in events.STICKY and "propositions_local" not in events.HISTORY
    b = events.Bus()
    b.publish("propositions_local", en_attente=2)
    assert [ev["en_attente"] for ev in b.snapshot()["sticky"] if ev["type"] == "propositions_local"] == [2]


def test_notice_dans_le_fil_pour_une_nouvelle_proposition(monkeypatch, projet):
    vus = []
    monkeypatch.setattr(bus, "publish", lambda kind, **kw: vus.append((kind, kw)))
    j = _astrel(monkeypatch, projet)
    j._publish_propositions_local(force=True)  # démarrage : rien
    _deposer(projet)
    j._qprop_vu = 0.0
    j._propositions_local_tick()
    (notice,) = [kw for k, kw in vus if k == "notice"]
    assert "Propositions de Qwen" in notice["text"] and notice["channel"] == "general"


# ================================================================== 7. ouverture et routage

def _banc(**kw):
    data = {"tag": "astrel-qwen3.5-9b", "ok": True, "cli": "0.0.0-test", **kw}
    outils_local.BANC_DIR.mkdir(parents=True, exist_ok=True)
    outils_local.fichier_banc("astrel-qwen3.5-9b").write_text(json.dumps(data), encoding="utf-8")


def test_ouverture_exige_les_epreuves_du_lot_5():
    _banc()
    assert outils_local.raison_indisponible("qwen3.5:9b", False, False) == ""  # lecture : lot 4 seul
    assert "lot 5" in outils_local.raison_actions_indisponible("qwen3.5:9b", False, False)
    _banc(actions_ok=True)
    assert outils_local.raison_actions_indisponible("qwen3.5:9b", False, False) == ""
    assert outils_local.raison_actions_indisponible("qwen3.5:9b", True, False)   # jamais en Total
    assert outils_local.raison_actions_indisponible("qwen3.5:9b", False, True)   # jamais sous la levée
    _banc(ok=False, actions_ok=True)
    assert outils_local.raison_actions_indisponible("qwen3.5:9b", False, False)


def test_ligne_du_cli_avec_le_seul_serveur_local(tmp_path):
    mcp = tmp_path / "mcp.json"
    cmd = outils_local.commande("astrel-qwen3.5-9b", tmp_path, "fr", tmp_path / "r.json", mcp)
    assert "--strict-mcp-config" in cmd and cmd[cmd.index("--mcp-config") + 1] == str(mcp)
    assert "--bare" not in cmd and cmd[cmd.index("--tools") + 1] == "Read,Grep,Glob"
    assert "commande_autorisee" in cmd[cmd.index("--system-prompt") + 1]
    conf = outils_local.mcp_config(tmp_path, "general")["mcpServers"]
    assert list(conf) == ["astrel_local"]
    args = conf["astrel_local"]["args"]
    assert args[args.index("-m") + 1] == "astrel.mcp_local" and args[args.index("--projet") + 1] == str(tmp_path)
    assert "--commandes" not in args and "--propositions" not in args  # options du banc seulement


def test_tour_local_ouvre_les_actions_seulement_si_le_banc_le_permet(monkeypatch, tmp_path):
    import threading
    j = object.__new__(core.Astrel)
    j._total, j._lift = False, False
    vus = []

    class Outils:
        base = "qwen3.5:9b"

        def repondre(self, demande, langue="fr", projet=None, stop=None, actions=False, canal=""):
            vus.append((actions, canal))
            yield "ok"

    j.__dict__["_outils_locaux_obj"] = Outils()
    monkeypatch.setattr(outils_local, "raison_indisponible", lambda *a: "")
    monkeypatch.setattr(outils_local, "raison_actions_indisponible", lambda *a: "fermé")
    list(j._local_flux("lance les tests", "general", True, threading.Event())[0])
    monkeypatch.setattr(outils_local, "raison_actions_indisponible", lambda *a: "")
    list(j._local_flux("lance les tests", "general", True, threading.Event())[0])
    assert vus == [(False, "general"), (True, "general")]


def _decider(texte, actions=True):
    etat = routeur.Etat(lecture_locale=True, actions_locales=actions, scan=lambda t, p: [])
    return routeur.decider(texte, "", (), {"moteur_mode": "auto"}, etat)


@pytest.mark.parametrize("texte", [
    "en local : lance les tests du projet",
    "en local : exécute la commande git status",
    "en local : propose une modification du README pour corriger la faute",
    "en local : corrige le fichier notes.txt",
    "locally: run the tests",
])
def test_routage_actions_locales(texte):
    d = _decider(texte)
    assert d.moteur == routeur.LOCAL and d.outils and not d.carte
    assert not _decider(texte, actions=False).outils  # lot 5 fermé : comme au lot 4


@pytest.mark.parametrize("texte", [
    "en local : supprime le fichier /tmp/a.py",
    "en local : envoie le résultat des tests à Paul",
    "en local : installe le programme nmap",
    "lance les tests du projet",  # sans « en local » : Claude, comme avant
])
def test_routage_reste_hors_des_actions_locales(texte):
    assert not _decider(texte).outils


# ================================================================== 8. Réglages › Commandes autorisées

def test_reglage_commandes_hud_seul(racine):
    from astrel.settings import ITEMS, reglages
    assert ITEMS["local.commandes"].kind == "action"
    v = reglages.view("local.commandes")
    assert any(a["id"] == "modifier" and a["input"]["multiline"] for a in v["actions"])
    texte = json.dumps({"commandes": {"ls": {"argv": ["ls"], "dossier": str(racine)}}})
    for via in ("socket", None):
        r = reglages.action("local.commandes", "modifier", {"value": texte, "_via": via})
        assert r["ok"] is False and not commandes_locales.FICHIER.exists()
    r = reglages.action("local.commandes", "modifier", {"value": texte, "_via": "hud"})
    assert r["ok"] and list(commandes_locales.charger()) == ["ls"]
    r = reglages.action("local.commandes", "modifier", {"value": '{"commandes": {"x": {"argv": "ls"}}}', "_via": "hud"})
    assert r["ok"] is False and list(commandes_locales.charger()) == ["ls"]  # une faute n'écrase rien
    lignes = reglages.view("local.commandes")["value"]["lines"]
    assert lignes[0]["label"] == "ls"
    r = reglages.action("local.commandes", "vider", {"_via": "hud"})
    assert r["ok"] and commandes_locales.charger() == {}


def test_reglage_refuse_a_claude():
    j = object.__new__(core.Astrel)
    j._off = False
    r = j._on_command({"cmd": "settings_action", "key": "local.commandes", "action": "vider", "_from_claude": True,
                       "_via": "hud"})
    assert r["ok"] is False and "Claude" in r["error"]
