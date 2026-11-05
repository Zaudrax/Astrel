"""Retours des premiers essais réels du moteur local Qwen (05/11/2026, docs/chantiers/HYBRIDE-QWEN.md, section
« Retours des essais du 05/11 ») : « Envoyer à Claude » après une demande routée seule au local (sensible, avec
outils) ; consignes du local (mêmes règles FR/EN, rien d'inventé, pas de salutation) ; réflexion activée
(décision de l'utilisateur) ; faits exacts donnés au LOCAL seulement ; blocs de texte séparés ; dossiers cités ouverts
en lecture ; zone confidentielle libre pour le VRAI CLI local, toujours demandée pour Claude."""
from __future__ import annotations

import io
import json
import re
import threading
import time
from pathlib import Path

import pytest

from astrel import config, guard, moteur_local, outils_local, routeur, sensible
from astrel.coeur import local_tour
from faux_ollama import FauxOllama
from test_local_vers_claude import IBAN, _attendre, _cmd, events, j  # noqa: F401 (fixtures)

ROOT = Path(__file__).resolve().parent.parent

# ================================================================== 1. « Envoyer à Claude » (demande routée seule)


def _tour(j, events, texte, outils=None):
    """Un vrai tour (routage du cœur, Ollama simulé). `outils` : faux CLI local (lot 4) ouvert."""
    j._outils_locaux_actions_raison = lambda: "fermés pour le test"
    with FauxOllama(chat=["Réponse ", "de Qwen."]) as f:
        j.__dict__["_moteur_local_obj"] = moteur_local.MoteurLocal(url=f.url, tag="qwen3.5:9b",
                                                                   memoire=lambda: {"ram_dispo": 8 * 2**30})
        if outils is not None:
            j._outils_locaux_raison = lambda: ""
            j.__dict__["_outils_locaux_obj"] = outils
        j.handle(texte, source="clavier", channel="general")
        assert _attendre(lambda: not j._local_actif())
        corps = [b for _m, path, b in f.requests if path == "/api/chat"]
    tr = [e for e in events if e.get("type") == "transcript" and e.get("engine") == "local"][-1]
    rep = [e for e in events if e.get("type") == "reply" and e.get("engine") == "local"][-1]
    assert rep["reply_to"] == tr["mid"]
    return tr["mid"], rep, corps


def test_demande_sensible_routee_seule_gardee_pour_claude_puis_carte(j, events, monkeypatch):
    """Essai réel : « vérifie ce RIB : FR76 … » (pas de préfixe, routé AU LOCAL parce que sensible). Gardée en
    mémoire (bornée, jamais sur disque) : « Envoyer à Claude » repasse par la carte du lot 3, rien ne part avant."""
    monkeypatch.setattr(sensible, "zones", lambda: [])
    mid, rep, corps = _tour(j, events, f"vérifie ce RIB : {IBAN}")
    assert rep.get("vers_claude") is True
    assert _cmd(j, "local_demandes")["mids"] == [mid]
    r = _cmd(j, "local_vers_claude", mid=mid)
    assert r == {"ok": True, "mid": mid, "carte": True}
    time.sleep(0.1)
    assert j.envoyes == []
    carte = [e for e in events if e.get("type") == "choice" and e.get("choice_kind") == "routage"][-1]
    assert j._choice_answer({"choice_id": carte["id"], "value": "Envoyer à Claude", "_via": "hud"})["ok"]
    assert _attendre(lambda: j.envoyes)
    # 3. les faits du cœur sont allés au LOCAL, jamais à Claude
    assert "Contrôle d'Astrel" in corps[0]["messages"][-1]["content"]
    assert j.envoyes == [f"vérifie ce RIB : {IBAN}"] and "Contrôle" not in j.envoyes[0]


class _CLI:
    """Faux CLI local (lot 4) : note ce qu'il reçoit."""
    base = "qwen3.5:9b"

    def __init__(self):
        self.appels = []

    def repondre(self, demande, langue="fr", projet=None, stop=None, actions=False, canal="", lecture=()):
        self.appels.append({"demande": demande, "projet": projet, "lecture": list(lecture)})
        yield "Le dossier contient notes.txt."


def test_demande_avec_outils_routee_seule_gardee_et_dossier_cite_ouvert(j, events, monkeypatch, tmp_path):
    """Essai réel : « lis le fichier qui est dans <zone> » (outils de lecture du lot 4). Gardée pour « Envoyer à
    Claude » (carte : la zone est sensible) ; le dossier cité est ouvert en lecture au CLI local (défaut 5)."""
    zone = tmp_path / "Essai-zone"
    zone.mkdir()
    (zone / "notes.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(sensible, "zones", lambda: [zone.resolve()])
    monkeypatch.setattr(guard, "zones_confidentielles", lambda: [zone.resolve()])
    cli = _CLI()
    mid, rep, _ = _tour(j, events, f"lis le fichier qui est dans {zone}", outils=cli)
    assert cli.appels and cli.appels[0]["lecture"] == [zone.resolve()]
    assert rep.get("vers_claude") is True and rep["text"] == "Le dossier contient notes.txt."
    assert _cmd(j, "local_vers_claude", mid=mid)["carte"] is True and j.envoyes == []


# ================================================================== 2. consignes du moteur local

_REPERES = re.compile(r"\bRead, Grep, Glob\b|\b(?:DONNÉE|DATA|Astrel|Lea|recherche_web|lire_page|browser_\w+)\b")


@pytest.fixture
def prenom_lea(monkeypatch):
    """05/11 (prénom en réglage) : un prénom de TEST, jamais celui du profil de la machine (le dépôt public n'en a
    pas, et sa neutralisation remplace le vrai)."""
    from astrel import sortie_locale
    monkeypatch.setattr(sortie_locale, "prenom", lambda: "Lea")


@pytest.mark.parametrize("outils, web, navigateur", [(False, False, False), (True, False, False),
                                                     (True, True, False), (True, True, True)])
def test_consignes_memes_regles_en_francais_et_en_anglais(prenom_lea, outils, web, navigateur):
    fr = moteur_local.consignes("fr", outils, web=web, navigateur=navigateur)
    en = moteur_local.consignes("en", outils, web=web, navigateur=navigateur)
    assert fr.count("\n- ") == en.count("\n- ") >= 8  # une règle = les deux langues (05/11, lot 8 : 8 règles)
    # lot 8 : exemples (option `EXEMPLES`) : les mêmes dans les deux langues
    fx, ex = moteur_local.consignes("fr", outils, exemples=True), moteur_local.consignes("en", outils, exemples=True)
    assert fx.count("\nAstrel : ") == ex.count("\nAstrel: ") >= 3
    assert sorted(_REPERES.findall(fr)) == sorted(x.replace("DATA", "DONNÉE") for x in _REPERES.findall(en))
    assert moteur_local.FAIT_PREFIXE["fr"] in fr and moteur_local.FAIT_PREFIXE["en"] in en
    assert outils_local.SYSTEME["fr"] == moteur_local.consignes("fr", True)
    assert moteur_local.SYSTEME["en"] == moteur_local.consignes("en")


def test_consignes_corrigent_les_defauts_vus_le_05_11(prenom_lea):
    """Salutation, présentation, « pas d'Internet » redit, applications inventées, dossier interne cité."""
    for lang, mots in (("fr", ("pas de salutation", "pas de présentation", "seulement si Lea le demande",
                               "N'invente jamais", "je ne sais pas", "chemin interne", "court", "nombres en chiffres",
                               "ne se donne que s'il vient de Lea ou d'un outil de ce tour")),
                       ("en", ("no greeting", "no introduction", "only if Lea asks", "Never make up",
                               "I don't know", "internal folder or path", "short", "numbers in digits",
                               "only given if it comes from Lea or from a tool of this turn"))):
        for outils in (False, True):
            c = moteur_local.consignes(lang, outils)
            for mot in mots:
                assert mot in c, (lang, outils, mot)
            assert "mode LOCAL" not in c and "LOCAL mode" not in c  # il se présentait « en mode LOCAL »
    assert "dossier courant" in outils_local.SYSTEME["fr"] and "current folder" in outils_local.SYSTEME["en"]
    cmd = outils_local.commande("t", Path("/tmp"), "fr", Path("/tmp/r.json"), Path("/tmp/m.json"))
    assert cmd[cmd.index("--system-prompt") + 1].endswith(outils_local.SYSTEME_ACTIONS["fr"])
    assert outils_local.SYSTEME_ACTIONS["fr"].startswith("\n- ") and outils_local.SYSTEME_ACTIONS["en"].startswith("\n- ")


def test_consignes_sans_prenom_et_elision(monkeypatch):
    """05/11 (prénom en réglage, projet open source) : sans prénom au profil, « ton utilisateur » / « your user » ;
    « d'Anne », « qu'Anne » ; aucun « il » ni « his » (le prénom ne dit pas le genre)."""
    from astrel import sortie_locale
    monkeypatch.setattr(sortie_locale, "prenom", lambda: "")
    for outils in (False, True):
        fr, en = moteur_local.consignes("fr", outils), moteur_local.consignes("en", outils)
        assert "l'assistant personnel de ton utilisateur" in fr and "seulement si ton utilisateur le demande" in fr
        assert "your user's personal assistant" in en and "only if your user asks" in en
        assert " his " not in en and "ce qu'il demande" not in fr
    assert "que ton utilisateur t'a autorisées" in outils_local.SYSTEME_ACTIONS["fr"]
    a = moteur_local.consignes("fr", True, prenom="Anne")
    assert "l'assistant personnel d'Anne" in a and "dossiers qu'Anne t'a ouverts" in a and "de Anne" not in a
    assert "Voici une question d'Anne" in moteur_local._RELECTURE["fr"].format(q="q", r="r", **moteur_local.formes(
        "fr", "Anne"))
    ex = moteur_local.consignes("en", exemples=True, prenom="")
    assert ex.count("\nUser: ") >= 3


def test_aucun_prenom_ecrit_dans_le_code_du_moteur_local():
    """05/11 (prénom en réglage) : le prénom ne vient que du profil ; aucune chaîne du code de ces modules (hors
    docstrings et commentaires) ne contient celui de la machine de référence."""
    import ast
    from astrel import config
    noms = {str(config.PROFILE.get("nom") or "").strip()} - {""}
    neutre = ROOT / "tools" / "neutraliser_export.py"
    if neutre.exists():
        m = re.search(r'^PRENOM = "([^"]+)"', neutre.read_text(encoding="utf-8"), re.M)
        if m:
            noms.add(m.group(1))
    if not noms:
        pytest.skip("aucun prénom connu sur cette machine")
    for mod in ("moteur_local", "sortie_locale", "outils_local", "faits_local"):
        arbre = ast.parse((ROOT / "astrel" / f"{mod}.py").read_text(encoding="utf-8"))
        docs = {id(n.body[0].value) for n in ast.walk(arbre) if isinstance(n, (ast.Module, ast.FunctionDef,
                ast.ClassDef, ast.AsyncFunctionDef)) and n.body and isinstance(n.body[0], ast.Expr)
                and isinstance(n.body[0].value, ast.Constant)}
        for n in ast.walk(arbre):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
                for nom in noms:
                    assert nom.lower() not in n.value.lower(), (mod, n.value[:80])


# ================================================================== réflexion (décision du 05/11)


def test_reflexion_du_cli_local_jamais_coupee_ni_rendue(tmp_path, monkeypatch):
    """Le CLI envoie `thinking: adaptive` (relevé le 05/11), qu'Ollama honore : rien ne la coupe dans
    l'environnement ; ses morceaux `thinking_delta` ne sont jamais rendus comme réponse."""
    env = outils_local.environnement("astrel-qwen3.5-9b", {"PATH": "/usr/bin", "MAX_THINKING_TOKENS": "0"})
    assert "MAX_THINKING_TOKENS" not in env  # hérité de la machine, « 0 » la couperait
    from astrel.platform import systeme
    monkeypatch.setattr(systeme, "tuer_arbre", lambda *a: None)
    flux = [{"type": "stream_event", "event": {"type": "content_block_delta",
                                               "delta": {"type": "thinking_delta", "thinking": "je pense"}}},
            _delta("Réponse."), {"type": "result", "is_error": False, "result": "Réponse."}]
    o = _outils(flux)
    assert "".join(o.repondre("x", projet=tmp_path)) == "Réponse."


# ================================================================== 3. faits exacts (local seulement)


def test_faits_iban_carte_nir_sans_le_numero_complet():
    f = sensible.faits(f"vérifie ce RIB : {IBAN}")
    assert f.startswith("[Contrôle d'Astrel :") and "IBAN FR76…189 : clé de contrôle valide" in f
    assert "clé RIB 89 valide" in f and "longueur conforme pour FR (27" in f
    assert IBAN not in f and IBAN.replace(" ", "") not in f and "0112" not in f
    faux = sensible.faits("FR76 3000 6000 0112 3456 7890 188")
    assert "INVALIDE" in faux
    assert sensible.faits("carte 4111 1111 1111 1111") .count("clé de Luhn valide") == 1
    assert "1111 1111" not in sensible.faits("carte 4111 1111 1111 1111")
    assert "sécurité sociale 1 85… : clé valide" in sensible.faits("nir 1 85 05 78 006 084 91")
    assert sensible.faits("rendez-vous le 12 03 2026 à 14 h, réf. AB12") == ""
    assert sensible.faits(f"check {IBAN}", "en").startswith("[Astrel check: IBAN FR76…189: valid")
    # les chiffres de l'IBAN ne sont pas lus comme une carte
    assert "numéro de carte" not in sensible.faits(f"{IBAN}")


def test_faits_jamais_vers_claude(j, events, monkeypatch):
    """Demande sensible que l'utilisateur envoie à Claude (« avec Claude : ») : carte, puis le texte TEL QUEL."""
    monkeypatch.setattr(sensible, "zones", lambda: [])
    j._outils_locaux_actions_raison = lambda: "fermés"
    j.handle(f"avec Claude : vérifie {IBAN}", source="clavier", channel="general")
    carte = [e for e in events if e.get("type") == "choice" and e.get("choice_kind") == "routage"][-1]
    j._choice_answer({"choice_id": carte["id"], "value": "Envoyer à Claude", "_via": "hud"})
    assert _attendre(lambda: j.envoyes)
    assert "Contrôle" not in j.envoyes[0] and "check" not in j.envoyes[0]


# ================================================================== 4. blocs de texte séparés (tour avec outils)


class _Entree(io.StringIO):
    def close(self):
        pass


class _Proc:
    def __init__(self, lignes):
        self.pid, self.stdin = 4242, _Entree()
        self.stdout = io.StringIO("".join(json.dumps(x) + "\n" for x in lignes))

    def poll(self):
        return 0


def _delta(texte):
    return {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": texte}}}


def _debut(kind="text"):
    return {"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": kind}}}


def _outils(lignes):
    p = _Proc(lignes)
    return outils_local.OutilsLocaux(base="qwen3.5:9b", lancer=lambda cmd, **kw: p, reconnu=lambda pid: True,
                                     preparer=lambda b: "astrel-qwen3.5-9b")


def test_deux_blocs_de_texte_separes_par_un_saut_de_ligne(tmp_path, monkeypatch):
    """Essai réel : « …avant de pouvoir vous dire lesquels lire :Bon jour l'utilisateur ! » (deux blocs collés)."""
    from astrel.platform import systeme
    monkeypatch.setattr(systeme, "tuer_arbre", lambda *a: None)
    flux = [{"type": "stream_event", "event": {"type": "message_start"}}, _debut(), _delta("lesquels lire :"),
            _debut("tool_use"),
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t", "name": "Glob", "input": {}}]}},
            {"type": "stream_event", "event": {"type": "message_start"}}, _debut(), _delta("Voici "), _delta("la liste."),
            _debut(), _delta(" Fin."),  # déjà un blanc : rien d'ajouté
            {"type": "result", "is_error": False, "result": ""}]
    texte = "".join(_outils(flux).repondre("x", projet=tmp_path))
    assert texte == "lesquels lire :\nVoici la liste. Fin."


# ================================================================== 5. dossiers cités, zone libre pour le VRAI local


@pytest.fixture
def arbre(tmp_path, monkeypatch):
    work, zone = tmp_path / "Projets", tmp_path / "Documents" / "banque"
    (work / "Essai").mkdir(parents=True)
    (work / "Essai" / "a.txt").write_text("a", encoding="utf-8")
    zone.mkdir(parents=True)
    (tmp_path / "ailleurs").mkdir()
    secret = work / "Astrel" / "runtime"
    secret.mkdir(parents=True)
    (secret / "hud-token").write_text("t", encoding="utf-8")
    monkeypatch.setattr(guard, "SECRET_PATHS", [(secret / "hud-token").resolve()])
    return work.resolve(), zone.resolve(), tmp_path.resolve()


def test_dossiers_cites_dans_workdir_ou_une_zone_seulement(arbre):
    work, zone, racine = arbre
    texte = (f"compare {work / 'Essai' / 'a.txt'} avec {zone}/ et {racine / 'ailleurs'}, puis {work / 'Astrel'} "
             f"et {work / 'Astrel' / 'runtime'} et {work / 'absent'}.")
    d = outils_local.dossiers_cites(texte, projet=work / "Projet-du-canal", zones=[zone], workdir=work)
    assert d == [work / "Essai", zone]  # un fichier ouvre son dossier ; hors WORKDIR et zones, secrets : non
    assert outils_local.dossiers_cites(f"lis {work / 'Essai'}", projet=work / "Essai", zones=[], workdir=work) == []
    cmd = outils_local.commande("t", work / "P", "fr", Path("/tmp/r.json"), lecture=d)
    assert [cmd[i + 1] for i, x in enumerate(cmd) if x == "--add-dir"] == [str(work / "P"), str(work / "Essai"), str(zone)]


def test_dossiers_cites_jamais_un_secret_du_compte(arbre, monkeypatch):
    work, zone, racine = arbre
    monkeypatch.setattr(guard, "secret_du_compte", lambda p, us=None: p.name == "banque")
    assert outils_local.dossiers_cites(f"lis {zone}", zones=[zone], workdir=work) == []
    from types import SimpleNamespace
    monkeypatch.setattr(guard, "secret_du_compte", lambda p, us=None: False)
    large = racine / "Documents"  # zone large qui CONTIENT un secret du compte (~/.ssh…) : jamais ouverte
    monkeypatch.setattr(guard, "_US", SimpleNamespace(racines=(large / ".ssh",)))
    assert outils_local.dossiers_cites(f"lis {large}", zones=[large], workdir=work) == []
    monkeypatch.setattr(guard, "_US", SimpleNamespace(racines=(racine / "home" / ".ssh",)))
    assert outils_local.dossiers_cites(f"lis {large}", zones=[large], workdir=work) == [large]


def _registre(tmp_path, coeur, pids):
    f = tmp_path / "pids.json"
    f.write_text(json.dumps({"coeur": coeur, "pids": pids}), encoding="utf-8")
    return f


@pytest.fixture
def chaine(monkeypatch):
    """Ascendance simulée : hook 100 → sh 101 → CLI 102 → cœur 103 → 1 ; un CLI lancé par Claude : 202 → bash
    203 → cerveau 204 → cœur 103."""
    from astrel.platform import proc
    parents = {100: 101, 101: 102, 102: 103, 103: 1, 200: 201, 201: 202, 202: 203, 203: 204, 204: 103}
    monkeypatch.setattr(proc, "parent", lambda pid: parents.get(pid, 0))
    return {"CLAUDE_CONFIG_DIR": str(guard.LOCAL_CLI_DIR)}


def test_vrai_cli_local_reconnu_par_son_pid(chaine, tmp_path):
    f = _registre(tmp_path, 103, [102])
    assert guard._cli_local_reel(chaine, pid=100, fichier=f) is True
    assert guard._cli_local_reel({}, pid=100, fichier=f) is False  # pas son dossier de configuration
    assert guard._cli_local_reel(chaine, pid=100, fichier=tmp_path / "absent.json") is False
    assert guard._cli_local_reel(chaine, pid=100, fichier=_registre(tmp_path, 999, [102])) is False  # autre cœur
    # un CLI que Claude lance lui-même avec CLAUDE_CONFIG_DIR, même inscrit : il descend du cerveau, pas du cœur
    assert guard._cli_local_reel(chaine, pid=200, fichier=_registre(tmp_path, 103, [202])) is False
    # même le cerveau inscrit (pid repris) : trop loin du hook (hook → sh → CLI seulement)
    assert guard._cli_local_reel(chaine, pid=200, fichier=_registre(tmp_path, 103, [204])) is False


def test_zone_libre_pour_le_local_toujours_demandee_pour_claude(chaine, tmp_path, monkeypatch):
    zone = tmp_path / "zone"
    zone.mkdir()
    monkeypatch.setattr(guard, "zones_confidentielles", lambda: [zone.resolve()])
    lire = {"file_path": str(zone / "releve.txt")}
    # Claude (son cerveau : pas le dossier du CLI local) : question au plafond, comme avant
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    v = guard.classify("Read", lire)
    assert v.needs_confirm and guard.L_ZONE in v.desc
    # le dossier du CLI local SANS le pid inscrit par le cœur : toujours demandé
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(guard.LOCAL_CLI_DIR))
    monkeypatch.setattr(guard, "LOCAL_CLI_PIDS_FILE", tmp_path / "absent.json")
    assert guard.classify("Read", lire).needs_confirm
    # le VRAI CLI local (ascendance du hook réel de ce test remplacée) : libre
    import os
    monkeypatch.setattr(guard, "LOCAL_CLI_PIDS_FILE", _registre(tmp_path, 103, [102]))
    monkeypatch.setattr(os, "getpid", lambda: 100)
    assert not guard.classify("Read", lire).needs_confirm
    assert not guard.classify("Grep", {"pattern": "IBAN", "path": str(zone)}).needs_confirm
    # un secret reste jugé comme pour Claude, même pour le local
    assert guard.classify("Read", {"file_path": str(sorted(guard.SECRET_PATHS)[0])}).needs_confirm


def test_registre_des_pids_protege_et_ecrit_par_le_coeur(tmp_path):
    assert guard.LOCAL_CLI_PIDS_FILE.name == outils_local.PIDS_CLI.name == "outils-local-pids.json"
    assert (config.RUNTIME / "outils-local-pids.json").resolve() in guard.PROTECTED_PATHS
    v = guard.classify("Write", {"file_path": str(config.RUNTIME / "outils-local-pids.json"), "content": "{}"})
    assert v.needs_confirm and v.level == "critique"
    f = tmp_path / "pids.json"
    cible: set[int] = {7}
    suivre = outils_local.compter_pids(cible, f)
    assert json.loads(f.read_text())["pids"] == [7]  # écrit tout de suite (liste d'avant une relance effacée)
    suivre(4242, True)
    import os
    assert json.loads(f.read_text()) == {"coeur": os.getpid(), "pids": [7, 4242]}
    suivre(4242, False)
    assert json.loads(f.read_text())["pids"] == [7] and (f.stat().st_mode & 0o777) == 0o600
