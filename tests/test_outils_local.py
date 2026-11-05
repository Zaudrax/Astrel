"""Moteur local AVEC outils de lecture, lot 4 (04/11/2026, docs/chantiers/HYBRIDE-QWEN.md) : ligne du CLI
(jamais --bare), environnement, banc, ouverture (ni Total ni levée), tour en flux, process compté parmi ceux
de Claude, routage « lecture seule », garde-fou intact."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading

import pytest

from astrel import config, core, guard, outils_local, routeur, state
from astrel.moteur_local import ErreurLocale

# ------------------------------------------------------------------ ligne du CLI et réglages


def test_commande_lecture_seule_et_jamais_bare(tmp_path):
    cmd = outils_local.commande("astrel-qwen3.5-9b", tmp_path, "fr", tmp_path / "r.json")
    assert "--bare" not in cmd and "--dangerously-skip-permissions" not in cmd and "--mcp-config" not in cmd
    for opt in ("--restricted", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
        assert opt in cmd
    assert cmd[cmd.index("--tools") + 1] == "Read,Grep,Glob"
    assert cmd[cmd.index("--permission-prompts") + 1] == "none"
    assert cmd[cmd.index("--add-dir") + 1] == str(tmp_path)
    assert cmd[cmd.index("--settings") + 1] == str(tmp_path / "r.json")
    assert cmd[cmd.index("--model") + 1] == "astrel-qwen3.5-9b"


def test_reglages_branchent_le_vrai_hook_seul():
    r = outils_local.reglages_hook()
    assert set(r["hooks"]) == {"PreToolUse"} and r["autoMemoryEnabled"] is False
    (entree,) = r["hooks"]["PreToolUse"]
    assert entree["matcher"] == "*"
    assert [h["command"] for h in entree["hooks"]] == [config.hook_command(config.HOOK_SCRIPT)]


def test_environnement_sans_session_parente_ni_cle(monkeypatch):
    base = {"PATH": "/usr/bin", "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "x", "CLAUDE_CODE_MESSAGING_SOCKET": "/s",
            "ANTHROPIC_API_KEY": "sk-ant-vraie", "ANTHROPIC_BASE_URL": "https://api.anthropic.com"}
    env = outils_local.environnement("astrel-qwen3.5-9b", base)
    assert "CLAUDECODE" not in env and "CLAUDE_CODE_SESSION_ID" not in env and "CLAUDE_CODE_MESSAGING_SOCKET" not in env
    assert env["ANTHROPIC_BASE_URL"] == config.OLLAMA_URL and env["ANTHROPIC_API_KEY"] == "ollama"
    assert env["CLAUDE_CONFIG_DIR"] == str(outils_local.CONFIG_CLI)
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1" and env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "astrel-qwen3.5-9b"
    from astrel.platform import proc
    assert env[proc.BRAIN_VAR] == proc.BRAIN_TOKEN  # reconnu comme Claude hors cgroup (marqueur)


# ------------------------------------------------------------------ modèle dérivé


class _Rep:
    def __init__(self, code, data):
        self.status_code, self._data = code, data

    def json(self):
        return self._data


class _Http:
    def __init__(self, rep):
        self.rep, self.corps = rep, None

    def post(self, url, json=None, timeout=None):  # noqa: A002
        self.corps = (url, json)
        if isinstance(self.rep, Exception):
            raise self.rep
        return self.rep


def test_modele_derive_contexte_fixe_et_toute_la_carte():
    http = _Http(_Rep(200, {"status": "success"}))
    assert outils_local.preparer_modele("qwen3.5:9b", "http://127.0.0.1:11434", http) == "astrel-qwen3.5-9b"
    url, corps = http.corps
    assert url.endswith("/api/create") and corps["from"] == "qwen3.5:9b"
    assert corps["parameters"] == {"num_ctx": 32768, "num_gpu": 999, "temperature": 0.6, "presence_penalty": 0.0}


@pytest.mark.parametrize("rep", [_Rep(500, {}), OSError("refus")])
def test_modele_derive_echec_repli_sur_la_base(rep):
    assert outils_local.preparer_modele("qwen3.5:9b", "http://127.0.0.1:11434", _Http(rep)) == "qwen3.5:9b"


@pytest.mark.parametrize("url,base", [("http://10.0.0.5:11434", "qwen3.5:9b"), ("http://127.0.0.1:11434", "gpt-oss:120b-cloud")])
def test_modele_derive_refuse_distant_et_cloud(url, base):
    http = _Http(_Rep(200, {"status": "success"}))
    with pytest.raises(ErreurLocale):
        outils_local.preparer_modele(base, url, http)
    assert http.corps is None  # rien d'envoyé


# ------------------------------------------------------------------ banc et ouverture


def _banc(tag="astrel-qwen3.5-9b", **kw):
    data = {"tag": tag, "ok": True, "cli": "0.0.0-test", **kw}
    outils_local.BANC_DIR.mkdir(parents=True, exist_ok=True)
    outils_local.fichier_banc(tag).write_text(json.dumps(data), encoding="utf-8")


def test_banc_reussi_exige_tout():
    assert not outils_local.banc_reussi("astrel-qwen3.5-9b")  # pas de banc
    _banc(ok=False)
    assert not outils_local.banc_reussi("astrel-qwen3.5-9b")
    _banc(cli="2.0.0")  # autre version du CLI
    assert not outils_local.banc_reussi("astrel-qwen3.5-9b")
    _banc(tag="autre")
    assert not outils_local.banc_reussi("astrel-qwen3.5-9b")
    _banc()
    assert outils_local.banc_reussi("astrel-qwen3.5-9b")


def test_ouverture_ni_total_ni_levee_ni_sans_banc():
    assert "banc" in outils_local.raison_indisponible("qwen3.5:9b", False, False)
    _banc()
    assert outils_local.raison_indisponible("qwen3.5:9b", False, False) == ""
    assert "Sans confirmation" in outils_local.raison_indisponible("qwen3.5:9b", True, False)
    assert "levée" in outils_local.raison_indisponible("qwen3.5:9b", False, True)
    assert outils_local.raison_indisponible("x:cloud", False, False)


# ------------------------------------------------------------------ tour en flux


class _Entree(io.StringIO):
    def close(self):  # garde le texte lisible après la fermeture de l'entrée standard
        self.ferme = True


class FauxProc:
    def __init__(self, lignes, pid=4242):
        self.pid, self.stdin = pid, _Entree()
        self.stdout = io.StringIO("".join(json.dumps(l) + "\n" for l in lignes))
        self.tue = False
        self.args = None

    def poll(self):
        return 0 if self.tue or self.stdout.tell() == len(self.stdout.getvalue()) else None


def _delta(texte):
    return {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": texte}}}


FLUX = [
    {"type": "system", "subtype": "init"},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]}},
    _delta("Le bug : "), _delta("<think>caché</think>"), _delta("a - b au lieu de a + b."),
    {"type": "stream_event", "parent_tool_use_id": "x", "event": {"type": "content_block_delta",
                                                                 "delta": {"type": "text_delta", "text": "SOUS-AGENT"}}},
    {"type": "result", "is_error": False, "result": "Le bug : a - b au lieu de a + b."},
]


def _outils(tmp_path, lignes, reconnu=True, suivis=None):
    p = FauxProc(lignes)

    def lancer(cmd, **kw):
        p.args = (cmd, kw)
        return p

    suivis = suivis if suivis is not None else []
    o = outils_local.OutilsLocaux(base="qwen3.5:9b", suivre=lambda pid, present: suivis.append((pid, present)),
                                  reconnu=lambda pid: reconnu, lancer=lancer, preparer=lambda b: "astrel-qwen3.5-9b")
    return o, p, suivis


def test_tour_en_flux_reflexion_et_sous_agents_retires(tmp_path, monkeypatch):
    from astrel.platform import systeme
    monkeypatch.setattr(systeme, "tuer_arbre", lambda *a: None)
    o, p, suivis = _outils(tmp_path, FLUX)
    texte = "".join(o.repondre("trouve le bug", projet=tmp_path))
    assert texte == "Le bug : a - b au lieu de a + b."
    assert p.stdin.getvalue() == "trouve le bug"  # la demande passe par l'entrée standard, pas la ligne
    cmd, kw = p.args
    assert kw["cwd"] != str(config.ROOT) and not str(kw["cwd"]).startswith(str(config.ROOT))
    assert suivis == [(4242, True), (4242, False)]  # compté parmi ceux de Claude le temps du tour
    assert o.derniere["outils"] == 1


def test_process_non_reconnu_outils_refuses(tmp_path, monkeypatch):
    from astrel.platform import systeme
    tues = []
    monkeypatch.setattr(systeme, "tuer_arbre", lambda pid, sig=None: tues.append(pid))
    o, p, suivis = _outils(tmp_path, FLUX, reconnu=False)
    p.poll = lambda: None
    with pytest.raises(ErreurLocale, match="non reconnu"):
        list(o.repondre("x", projet=tmp_path))
    assert tues == [4242] and suivis[-1] == (4242, False) and p.stdin.getvalue() == ""  # rien envoyé


def test_erreur_du_modele_et_flux_sans_resultat(tmp_path, monkeypatch):
    from astrel.platform import systeme
    monkeypatch.setattr(systeme, "tuer_arbre", lambda *a: None)
    o, _, _ = _outils(tmp_path, [{"type": "result", "is_error": True, "result": "modèle introuvable"}])
    with pytest.raises(ErreurLocale, match="modèle introuvable"):
        list(o.repondre("x", projet=tmp_path))
    o, _, _ = _outils(tmp_path, [_delta("début")])
    with pytest.raises(ErreurLocale, match="sans réponse"):
        list(o.repondre("x", projet=tmp_path))


def test_stop_arrete_le_tour(tmp_path, monkeypatch):
    from astrel.platform import systeme
    monkeypatch.setattr(systeme, "tuer_arbre", lambda *a: None)
    stop = threading.Event()
    stop.set()
    o, _, _ = _outils(tmp_path, FLUX)
    assert list(o.repondre("x", projet=tmp_path, stop=stop)) == []


def test_adresse_distante_refusee(tmp_path):
    o = outils_local.OutilsLocaux(base="qwen3.5:9b", url="http://192.168.1.10:11434", lancer=lambda *a, **k: pytest.fail())
    with pytest.raises(ErreurLocale, match="hors de cette machine"):
        list(o.repondre("x", projet=tmp_path))


# ------------------------------------------------------------------ « vient de Claude » : plusieurs pids


def test_from_claude_accepte_plusieurs_ancetres():
    enfant = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        assert state.from_claude(enfant.pid, (999_999, os.getpid()), service_cg="") is True
    finally:
        enfant.kill()
        enfant.wait()


def test_claude_pid_ajoute_les_cli_locaux():
    j = object.__new__(core.Astrel)
    j.brain = type("B", (), {"_proc": type("P", (), {"pid": 100})()})()
    assert j._claude_pid() == 100
    j.__dict__["_pids_outils_locaux"] = {300, 200}
    assert j._claude_pid() == (100, 200, 300)


# ------------------------------------------------------------------ routage : lecture seule


def _decider(texte, lecture=True, pieces=()):
    etat = routeur.Etat(lecture_locale=lecture, scan=lambda t, p: [])
    return routeur.decider(texte, "", pieces, {"moteur_mode": "auto"}, etat)


@pytest.mark.parametrize("texte", [
    "en local : lis le fichier /tmp/a.py et explique le bug",
    "analyse les logs de sshd dans /var/log/auth.log",
    "en local : audite le code du dossier ~/Projets/site pour les failles XSS",
    "locally: read the config files and explain them",
])
def test_lecture_seule_avec_outils(texte):
    d = _decider(texte)
    assert d.moteur == routeur.LOCAL and d.outils and not d.carte


@pytest.mark.parametrize("texte", [
    "en local : lis le fichier config et envoie-le à Paul",   # un envoi
    "en local : cherche sur internet la CVE-2024-1234",       # le web
    "en local : supprime le fichier /tmp/a.py",              # une action
    "explique une injection SQL",                              # rien à lire
    "avec Claude : lis le fichier x.py",                       # Claude explicite
])
def test_pas_d_outils_hors_lecture_seule(texte):
    assert not _decider(texte).outils


def test_outils_fermes_comportement_du_lot_3():
    assert not _decider("analyse les logs de sshd dans /var/log/auth.log", lecture=False).outils
    assert not _decider("en local : lis le fichier /tmp/a.py", pieces=["a.png"]).outils


# ------------------------------------------------------------------ tour local du cœur


class _Flux:
    def __init__(self, nom):
        self.nom, self.appels = nom, []

    def repondre(self, demande, langue="fr", projet=None, stop=None, **kw):  # 05/11 (lot 5) : actions, canal
        self.appels.append((demande, projet, kw.get("actions")))
        yield f"réponse {self.nom}"

    base = "qwen3.5:9b"


def test_tour_local_choisit_les_outils_seulement_s_ils_sont_ouverts(monkeypatch, tmp_path):
    j = object.__new__(core.Astrel)
    j._total, j._lift = False, False
    outils, sans = _Flux("outils"), _Flux("sans")
    j.__dict__["_outils_locaux_obj"], j.__dict__["_moteur_local_obj"] = outils, sans
    monkeypatch.setattr(outils_local, "raison_indisponible", lambda base, total, levee: "" if not (total or levee) else "non")
    flux, avec = j._local_flux("lis a.py", "general", True, threading.Event())
    assert avec and list(flux) == ["réponse outils"] and outils.appels[0][1]  # un dossier de projet donné
    j._total = True  # Total activé entre la décision et la réponse : plus d'outils
    flux, avec = j._local_flux("lis a.py", "general", True, threading.Event())
    assert not avec and list(flux) == ["réponse sans"]
    flux, avec = j._local_flux("bonjour", "general", False, threading.Event())
    assert not avec


# ------------------------------------------------------------------ le garde-fou reste le gardien


def test_garde_fou_juge_les_lectures_du_local(monkeypatch, tmp_path):
    """Le hook branché (le vrai) refuse ou demande ce qu'il refuse à Claude : secret d'Astrel, zone confidentielle."""
    v = guard.classify("Read", {"file_path": str(sorted(guard.SECRET_PATHS)[0])})
    assert v.needs_confirm and v.level == "critique"
    zone = tmp_path / "confidentiel"
    zone.mkdir()
    monkeypatch.setattr(guard, "zones_confidentielles", lambda: [zone.resolve()])
    assert guard.classify("Read", {"file_path": str(zone / "notes.txt")}).needs_confirm
    assert guard.classify("Grep", {"pattern": "x", "path": str(tmp_path)}).needs_confirm  # parent de la zone
    assert "Bash" not in outils_local.OUTILS.split(",") and "Write" not in outils_local.OUTILS.split(",")
