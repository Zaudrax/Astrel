"""06/11/2026 (lot 9 du moteur local, docs/chantiers/HYBRIDE-QWEN.md « Lot 9 ») : décision de l'utilisateur après son essai
de 09:02 (« pas d'accès aux réseaux externes… machine isolée », « je ne connais pas le chemin » de ses consignes) :
Qwen a Internet et ses outils EN PERMANENCE (sauf « rien ne sort ») et peut lire son code et ses consignes.

- chemin par défaut : tout tour local par le CLI avec outils quand le banc (lecture + web) a réussi ; repli direct
  (banc, hors ligne, Total, levée, panne du CLI) et « /reflechir » direct, avec la raison dans les consignes ;
- historique de la conversation locale dans le message du CLI ; relecture du lot 8 sur la réponse du CLI ;
- consignes : outils, emplacement des consignes (FR/EN, mêmes règles) ;
- garde-fou : lecture de son code permise, données d'Astrel (runtime/, logs/, vault/, memory/) et secrets refusés
  SANS carte au CLI local ; Claude inchangé ;
- filtre : « je n'ai pas accès à Internet » retiré quand les outils web étaient ouverts ;
- propositions sur ses propres fichiers (racine d'Astrel), fichiers protégés toujours refusés."""
from __future__ import annotations

import io
import json
import re
import threading
from pathlib import Path

import pytest

from astrel import config, core, guard, hors_ligne, moteur_local, outils_local, propositions_local, sortie_locale
from astrel.moteur_local import ErreurLocale


@pytest.fixture
def prenom_lea(monkeypatch):
    monkeypatch.setattr(sortie_locale, "prenom", lambda: "Lea")


# ================================================================== 1. chemin par défaut du tour local


class _CLI:
    base = "qwen3.5:9b"

    def __init__(self, morceaux=("réponse du CLI",), erreur=None):
        self.appels, self.morceaux, self.erreur = [], list(morceaux), erreur
        self.derniere = {}

    def repondre(self, demande, langue="fr", **kw):
        self.appels.append({"demande": demande, **kw})
        for m in self.morceaux:
            yield m
        if self.erreur:
            raise self.erreur


class _Direct:
    tag = "qwen3.5:9b"

    def __init__(self):
        self.appels, self.hist = [], [{"role": "user", "content": "avant"}, {"role": "assistant", "content": "ok"}]

    def repondre(self, message, langue="fr", **kw):
        self.appels.append({"message": message, **kw})
        yield "réponse directe"

    def historique(self):
        return list(self.hist)

    def relire(self, *a, **kw):
        return "relu"

    def reflexion_max_s(self):
        return 60.0


def _coeur(monkeypatch, web="", lecture="", total=False, lift=False):
    j = object.__new__(core.Astrel)
    j._total, j._lift = total, lift
    cli, direct = _CLI(), _Direct()
    j.__dict__["_outils_locaux_obj"], j.__dict__["_moteur_local_obj"] = cli, direct
    monkeypatch.setattr(outils_local, "raison_indisponible", lambda *a: lecture)
    monkeypatch.setattr(outils_local, "raison_actions_indisponible", lambda *a: "fermé")
    monkeypatch.setattr(outils_local, "raison_web_indisponible", lambda *a: web)
    monkeypatch.setattr(outils_local, "raison_navigateur_indisponible", lambda *a: "" if not web else web)
    from astrel import searxng_local
    monkeypatch.setattr(searxng_local, "assurer", lambda: "")
    return j, cli, direct


def test_tour_local_par_defaut_passe_par_le_cli_avec_outils(monkeypatch):
    j, cli, direct = _coeur(monkeypatch)
    flux, avec = j._local_flux("dans quel fichier sont tes consignes ?", "general", False, threading.Event())
    assert avec and list(flux) == ["réponse du CLI"] and not direct.appels
    a = cli.appels[0]
    assert a["web"] is True and a["navigateur"] is False  # pas de site interactif dans la demande
    assert a["historique"] == direct.hist and callable(a["relire"])  # conversation continue, relecture du lot 8
    assert a["relire"]("d", "b", "s", True, False) == "relu"


def test_tour_par_defaut_navigateur_seulement_pour_un_site_interactif(monkeypatch):
    j, cli, _ = _coeur(monkeypatch)
    list(j._local_flux("va sur le site de la SNCF et clique sur horaires", "general", False, threading.Event())[0])
    assert cli.appels[0]["navigateur"] is True


@pytest.mark.parametrize("cas, attendu", [("banc", "banc"), ("total", "total"), ("levee", "levee"),
                                          ("hors_ligne", "hors_ligne")])
def test_repli_direct_avec_la_raison(monkeypatch, cas, attendu):
    j, cli, direct = _coeur(monkeypatch, web="fermé", total=cas == "total", lift=cas == "levee")
    monkeypatch.setattr(hors_ligne, "actif", lambda: cas == "hors_ligne")
    flux, avec = j._local_flux("as-tu accès à internet ?", "general", False, threading.Event())
    assert not avec and list(flux) == ["réponse directe"] and not cli.appels
    assert direct.appels[0]["sans_web"] == attendu


def test_reflechir_reste_sur_le_moteur_direct(monkeypatch):
    j, cli, direct = _coeur(monkeypatch)
    j._local_detail = lambda texte: None
    flux, avec = j._local_flux("combien de jours entre le 1er janvier et le 14 juillet ?", "general", False,
                               threading.Event(), reflexion=True)
    assert not avec and list(flux) == ["réponse directe"] and not cli.appels
    assert direct.appels[0]["reflexion"] is True and direct.appels[0]["sans_web"] == "reflexion"


def test_interrupteur_rend_le_comportement_d_avant(monkeypatch):
    j, cli, direct = _coeur(monkeypatch)
    monkeypatch.setattr(outils_local, "PAR_DEFAUT", False)
    flux, avec = j._local_flux("bonjour", "general", False, threading.Event())
    assert not avec and list(flux) == ["réponse directe"]
    flux, avec = j._local_flux("lis a.py", "general", True, threading.Event())  # demande qui lit : outils
    assert avec and list(flux) == ["réponse du CLI"]


def test_panne_du_cli_avant_tout_texte_repli_sur_le_moteur_direct(monkeypatch):
    from astrel.events import bus
    vus = []
    monkeypatch.setattr(bus, "publish", lambda kind, **kw: vus.append((kind, kw)))
    j, cli, direct = _coeur(monkeypatch)
    cli.morceaux, cli.erreur = [], ErreurLocale("process du modèle local non reconnu comme Claude : outils refusés")
    flux, avec = j._local_flux("bonjour", "general", False, threading.Event())
    assert list(flux) == ["réponse directe"] and direct.appels[0]["sans_web"] == "panne"
    assert j.__dict__.get("_local_repli_fait") is True
    assert any(k == "notice" and "réponse sans outils" in kw["text"] for k, kw in vus)
    # Après un texte : l'erreur reste une erreur (pas deux réponses mêlées)
    j, cli, direct = _coeur(monkeypatch)
    cli.erreur = ErreurLocale("délai dépassé")
    flux, _ = j._local_flux("bonjour", "general", False, threading.Event())
    with pytest.raises(ErreurLocale):
        list(flux)
    assert not direct.appels


def test_demande_explicitement_lue_sans_repli(monkeypatch):
    """Une demande qui LIT (routeur : outils) garde l'erreur du CLI : une réponse sans outils serait fausse."""
    j, cli, direct = _coeur(monkeypatch)
    cli.morceaux, cli.erreur = [], ErreurLocale("panne")
    flux, _ = j._local_flux("lis a.py", "general", True, threading.Event())
    with pytest.raises(ErreurLocale):
        list(flux)
    assert not direct.appels


# ================================================================== 2. CLI : historique et relecture


def test_historique_borne_et_mis_dans_le_message():
    m = moteur_local.MoteurLocal(tag="qwen3.5:9b")
    for i in range(5):
        m._garder(f"demande {i} " + "x" * 3000, f"réponse {i}")
    h = m.historique()
    assert h and h[0]["role"] == "user" and sum(len(x["content"]) for x in h) <= moteur_local.HISTORIQUE_CLI_CARS
    assert h[-1]["content"] == "réponse 4"
    msg = outils_local.avec_historique("et maintenant ?", [{"role": "user", "content": "tu as Internet ?"},
                                                           {"role": "assistant", "content": "Oui."}], "fr")
    assert msg.startswith("Échanges précédents") and "[Utilisateur] tu as Internet ?" in msg and "[Astrel] Oui." in msg
    assert msg.endswith("Nouvelle demande :\net maintenant ?")
    assert outils_local.avec_historique("q", [], "fr") == "q"
    assert outils_local.avec_historique("q", [{"role": "user", "content": "a"}], "en").startswith("Previous exchanges")


class _Entree(io.StringIO):
    def close(self):
        self.vu = self.getvalue()


class _Proc:
    def __init__(self, lignes):
        self.pid, self.stdin = 4343, _Entree()
        self.stdout = io.StringIO("".join(json.dumps(x) + "\n" for x in lignes))

    def poll(self):
        return 0


def _delta(t):
    return {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}}}


def _outil(i, nom, entree):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": i, "name": nom, "input": entree}]}}


def _resultat(i, texte, erreur=False):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": i,
                                                     "content": [{"type": "text", "text": texte}], "is_error": erreur}]}}


FIN = {"type": "result", "is_error": False, "result": "x"}


def _outils(lignes, procs):
    def lancer(cmd, **kw):
        p = _Proc(lignes)
        procs.append((cmd, p))
        return p
    return outils_local.OutilsLocaux(base="qwen3.5:9b", lancer=lancer, reconnu=lambda pid: True,
                                     preparer=lambda b: "astrel-qwen3.5-9b")


@pytest.fixture
def sans_kill(monkeypatch):
    from astrel.platform import systeme
    monkeypatch.setattr(systeme, "tuer_arbre", lambda *a: None)
    monkeypatch.setattr(hors_ligne, "actif", lambda: False)


def test_relecture_sur_le_chemin_cli(tmp_path, sans_kill):
    lignes = [_outil("t1", "Read", {"file_path": "/x/notes.txt"}), _resultat("t1", "Le port est 8443."),
              _delta("Le serveur écoute sur le port 8080. "), _delta("C'est tout."), FIN]
    procs, recu = [], []

    def relire(demande, brouillon, sources, web, navigateur, recherche=None):
        recu.append((demande, brouillon, sources, web, navigateur))
        assert recherche is False  # 07/11 : une lecture de fichier n'est pas une recherche
        return "Le serveur écoute sur le port 8443."
    o = _outils(lignes, procs)
    sorties = list(o.repondre("quel port ?", projet=tmp_path, historique=[{"role": "user", "content": "avant"}],
                              relire=relire))
    assert sorties == ["Le serveur écoute sur le port 8443."]  # un seul morceau : rien avant la relecture
    d, b, s, web, nav = recu[0]
    assert d == "quel port ?" and b == "Le serveur écoute sur le port 8080. C'est tout." and "8443" in s
    assert web is False and nav is False
    assert o.derniere["relecture"] == "corrigé" and o._sources == []  # résultats d'outils jamais gardés
    assert procs[0][1].stdin.vu.startswith("Échanges précédents") and procs[0][1].stdin.vu.endswith("quel port ?")


def test_relecture_ok_garde_le_brouillon_et_panne_aussi(tmp_path, sans_kill):
    lignes = [_delta("Canberra."), FIN]
    o = _outils(lignes, [])
    assert list(o.repondre("capitale ?", projet=tmp_path, relire=lambda *a, **k: "Canberra.")) == ["Canberra."]
    assert o.derniere["relecture"] == "gardé"

    def panne(*a, **k):
        raise ErreurLocale("Ollama ne répond pas")
    assert list(o.repondre("capitale ?", projet=tmp_path, relire=panne)) == ["Canberra."]


def test_sans_relecture_le_flux_reste_en_direct(tmp_path, sans_kill):
    o = _outils([_delta("Un. "), _delta("Deux."), FIN], [])
    assert list(o.repondre("q", projet=tmp_path)) == ["Un. ", "Deux."]


class _Http:
    def __init__(self, texte):
        self.texte, self.corps = texte, []

    def get(self, url, timeout=None):
        raise OSError("pas de /api/ps")

    def stream(self, methode, url, json=None, timeout=None):  # noqa: A002
        self.corps.append(json)
        lignes = [{"message": {"content": self.texte}}, {"done": True, "eval_count": 3, "eval_duration": 1e9}]

        class R:
            status_code = 200

            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def iter_lines(s):
                return [__import__("json").dumps(x) for x in lignes]
        return R()

    def close(self):
        pass


def test_moteur_relire_donne_les_sources_et_les_consignes_du_cli(prenom_lea):
    http = _Http("OK")
    m = moteur_local.MoteurLocal(tag="qwen3.5:9b", client=lambda: http)
    assert m.relire("quelle version ?", "Debian 13.", "fr", sources="Debian 13 « Trixie » est sortie.", web=True) \
        == "Debian 13."
    corps = http.corps[-1]  # 07/11 : « quelle version ? » ne nomme rien : pas de contrôle du nom
    assert len(http.corps) == 1 and corps["think"] is False
    systeme, question = corps["messages"][0]["content"], corps["messages"][1]["content"]
    assert systeme == moteur_local.consignes("fr", outils=True, web=True)
    assert "Debian 13 « Trixie » est sortie." in question and "Brouillon :\nDebian 13." in question
    assert "tu n'as pas Internet" in question  # outils web ouverts : « pas d'Internet » est une erreur
    http2 = _Http("Le port est 8443.")
    m2 = moteur_local.MoteurLocal(tag="qwen3.5:9b", client=lambda: http2)
    assert m2.relire("q", "Le port est 8080.", "en", sources="") == "Le port est 8443."
    q2 = http2.corps[0]["messages"][1]["content"]
    # 07/11 : sans résultat d'outil, la relecture du lot 8 telle quelle (rien sur des outils à commenter)
    assert q2 == moteur_local._RELECTURE["en"].format(q="q", r="Le port est 8080.", **moteur_local.formes("en"))


def test_relecture_outils_memes_regles_fr_en():
    fr, en = moteur_local._RELECTURE_OUTILS["fr"], moteur_local._RELECTURE_OUTILS["en"]
    assert sorted(re.findall(r"\{\w+\}", fr.replace("{de}", "{nom}"))) == sorted(re.findall(r"\{\w+\}", en))
    assert "OK" in fr and "OK" in en


# ================================================================== 3. consignes : outils et connaissance de soi


@pytest.mark.parametrize("sans_web", ["", "hors_ligne", "reflexion", "total", "levee", "banc", "panne"])
def test_consignes_sans_outil_disent_pourquoi_memes_regles(prenom_lea, sans_web):
    fr, en = moteur_local.consignes("fr", sans_web=sans_web), moteur_local.consignes("en", sans_web=sans_web)
    assert fr.count("\n- ") == en.count("\n- ")
    if sans_web:
        assert moteur_local._SANS_WEB["fr"][sans_web] in fr and moteur_local._SANS_WEB["en"][sans_web] in en
        assert "dis clairement que non pour ce tour" in fr and "say clearly that you do not in this turn" in en
    else:
        assert "Dans ce tour, tu n'as ni Internet" not in fr
    assert set(moteur_local._SANS_WEB["fr"]) == set(moteur_local._SANS_WEB["en"])
    if sans_web == "hors_ligne":
        assert "je peux chercher" not in fr and "I can search" not in en  # rien ne sort : il ne cherchera pas


@pytest.mark.parametrize("outils, web", [(False, False), (True, False), (True, True)])
def test_consignes_disent_ou_sont_ses_consignes(prenom_lea, outils, web):
    for lang in ("fr", "en"):
        c = moteur_local.consignes(lang, outils, web=web)
        assert f"{config.ROOT.as_posix()}/astrel/moteur_local.py" in c and "consignes" in c
        assert f"{config.ROOT.as_posix()}/docs/chantiers/HYBRIDE-QWEN.md" in c
        lire = moteur_local._SOI_LIRE[lang][1 if outils else 0]
        assert lire in c
    assert "Lis-les avec Read" in moteur_local.consignes("fr", True)
    assert "Tu ne peux pas les lire dans ce tour." in moteur_local.consignes("fr")


def test_consignes_web_disent_les_outils_et_jamais_pas_d_internet(prenom_lea):
    fr, en = moteur_local.consignes("fr", True, web=True), moteur_local.consignes("en", True, web=True)
    assert "recherche web, lecture de pages et de PDF, lecture des fichiers des projets" in fr
    assert "avant de dire que tu ne sais pas ou que tu n'as pas accès" in fr
    assert "Ne dis jamais que tu n'as pas Internet" in fr and "Never say that you have no Internet" in en
    assert "web search, reading web pages and PDFs, reading the projects' files" in en
    assert "Ne dis jamais que tu n'as pas Internet" not in moteur_local.consignes("fr", True)  # sans le web : non
    assert "aussi pour tes propres consignes ou ton code" in outils_local.SYSTEME_ACTIONS["fr"]
    assert "also for your own instructions or code" in outils_local.SYSTEME_ACTIONS["en"]


def test_racine_d_astrel_ouverte_en_lecture_au_cli_local(tmp_path):
    cmd = outils_local.commande("t", tmp_path, "fr", tmp_path / "r.json")
    dirs = [cmd[i + 1] for i, x in enumerate(cmd) if x == "--add-dir"]
    assert dirs == [str(tmp_path), str(config.ROOT)]
    assert "--bare" not in cmd and "--restricted" in cmd
    cmd = outils_local.commande("t", config.ROOT, "fr", tmp_path / "r.json")  # projet = la racine : une fois
    assert [cmd[i + 1] for i, x in enumerate(cmd) if x == "--add-dir"] == [str(config.ROOT)]


# ================================================================== 3b. garde-fou : lectures du CLI local


@pytest.fixture
def cli_local(monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(guard.LOCAL_CLI_DIR))


@pytest.mark.parametrize("rel", ["astrel/moteur_local.py", "docs/chantiers/HYBRIDE-QWEN.md", "CLAUDE.md",
                                 "astrel/guard.py", "hud/src/main.js"])
def test_cli_local_lit_le_code_et_la_doc_d_astrel(cli_local, rel):
    p = str(config.ROOT / rel)
    assert guard.refused("Read", {"file_path": p}) == ""
    assert not guard.assess("Read", {"file_path": p}, mode="prudent").needs_confirm  # aucune carte


@pytest.mark.parametrize("cible", [
    lambda: config.RUNTIME / "session.json", lambda: config.RUNTIME / "vault" / "x.md",
    lambda: config.HUD_TOKEN_FILE, lambda: next(x for x in guard.SECRET_PATHS if x.name == "secrets.json"),
    lambda: config.MCP_CONFIG_FILE,
    lambda: config.LOGS / "astrel.log", lambda: config.ROOT / "vault" / "Moi.md",
    lambda: config.ROOT / "memory" / "m.db", lambda: guard.CLAUDE_CREDENTIALS, lambda: config.RUNTIME,
])
def test_cli_local_refuse_donnees_et_secrets_sans_carte(cli_local, cible):
    p = str(cible())
    assert guard.refused("Read", {"file_path": p}), p
    assert guard.refused("Grep", {"pattern": "x", "path": p}), p
    assert guard.refused("Glob", {"pattern": p.rstrip("/") + "/**/*"}), p


def test_cli_local_glob_de_la_racine_permis_et_claude_inchange(cli_local, monkeypatch):
    assert guard.refused("Glob", {"pattern": "**/*.py", "path": str(config.ROOT)}) == ""
    assert guard.refused("Grep", {"pattern": "def consignes", "path": str(config.ROOT)}) == ""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")  # le cerveau Claude : rien ne change (jugement habituel)
    assert guard.refused("Read", {"file_path": str(config.RUNTIME / "session.json")}) == ""
    assert guard._refus_lecture_locale("Read", {"file_path": str(config.HUD_TOKEN_FILE)}) == ""
    assert guard.classify("Read", {"file_path": str(config.HUD_TOKEN_FILE)}).needs_confirm  # Claude : carte


def test_cli_local_autre_dossier_de_config_non_concerne(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert guard._refus_lecture_locale("Read", {"file_path": str(config.RUNTIME / "session.json")}) == ""


# ================================================================== 3c. propositions sur ses propres fichiers


def test_proposition_sur_ses_consignes_deposee_dans_la_racine(monkeypatch, tmp_path):
    from astrel import mcp_local
    monkeypatch.setitem(mcp_local._ARGS, "projet", tmp_path)
    monkeypatch.setitem(mcp_local._ARGS, "commandes", None)
    assert mcp_local._projet_de(str(config.ROOT / "astrel" / "moteur_local.py")) == config.ROOT.resolve()
    assert mcp_local._projet_de(str(tmp_path / "a.py")) == tmp_path
    assert mcp_local._projet_de("a.py") == tmp_path
    monkeypatch.setitem(mcp_local._ARGS, "commandes", tmp_path / "liste.json")  # banc : le projet jetable seul
    assert mcp_local._projet_de(str(config.ROOT / "astrel" / "moteur_local.py")) == tmp_path


def test_application_refuse_toujours_les_fichiers_proteges():
    """moteur_local.py n'est ni protégé par le garde-fou ni dans l'empreinte du cœur : une proposition sur lui
    s'applique au HUD (l'utilisateur relit le diff) ; le garde-fou, ses hooks, le HUD et les secrets jamais."""
    assert propositions_local._juge(config.ROOT / "astrel" / "moteur_local.py") == ""
    for rel in ("astrel/guard.py", "astrel/confirm.py", "hooks/permission_hook.py", "hud/src/main.js",
                "astrel/totalgate.py", "config/secrets.json"):
        assert propositions_local._juge(config.ROOT / rel), rel
    from astrel import totalgate
    assert config.ROOT / "astrel" / "moteur_local.py" not in totalgate.core_files()
    assert config.ROOT / "astrel" / "coeur" / "local_tour.py" in totalgate.core_files()


def test_perimetres_d_une_proposition_comprennent_la_racine():
    j = object.__new__(core.Astrel)
    assert Path(config.ROOT) in j._perimetres_canal("")


# ================================================================== 4. filtre « pas d'Internet »


@pytest.mark.parametrize("texte", [
    "Oui, exactement : je fonctionne sans connexion Internet active, sur ta machine isolée.",
    "Non, je n'ai pas accès à Internet.",
    "Je n'ai aucun accès aux réseaux externes.",
    "Non, pas d'accès à Internet.",
    "Je ne peux pas accéder à Internet.",
    "Je fonctionne uniquement sur cette machine sans connexion externe.",
])
def test_filtre_retire_pas_d_internet_quand_le_web_etait_ouvert(prenom_lea, texte):
    out = sortie_locale.filtrer(texte + " Je peux chercher pour toi.", "as-tu accès à internet ?", "fr", web=True)
    assert out == sortie_locale.CORRECTION_WEB["fr"] + " Je peux chercher pour toi."
    assert sortie_locale.filtrer(texte, "as-tu accès à internet ?", "fr", web=False) == texte  # vrai sans le web


def test_filtre_pas_d_internet_une_seule_correction_et_en_anglais(prenom_lea):
    out = sortie_locale.filtrer("Je n'ai pas accès à Internet. Je reste hors ligne. Voici la réponse.", "q", "fr",
                                web=True)
    assert out == sortie_locale.CORRECTION_WEB["fr"] + " Voici la réponse."
    out = sortie_locale.filtrer("I don't have internet access. Here it is.", "q", "en", web=True)
    assert out == sortie_locale.CORRECTION_WEB["en"] + " Here it is."


@pytest.mark.parametrize("texte", [
    "Organic Maps fonctionne sans Internet une fois la carte téléchargée.",
    "Sur une machine isolée du réseau, installe les mises à jour par clé USB.",
    "Le serveur n'a pas d'accès à Internet : il faut un mandataire.",
    "Oui, j'ai accès à Internet par mes outils.",
])
def test_filtre_laisse_ce_qui_ne_le_concerne_pas(prenom_lea, texte):
    assert sortie_locale.filtrer(texte, "q", "fr", web=True) == texte


def test_filtre_du_flux_cli_recoit_le_web(tmp_path, sans_kill, monkeypatch):
    vus = []
    vrai = sortie_locale.FiltreSortie

    def espion(*a, **kw):
        vus.append(kw.get("web"))
        return vrai(*a, **kw)
    monkeypatch.setattr(sortie_locale, "FiltreSortie", espion)
    o = _outils([_delta("Je n'ai pas accès à Internet. Voici."), FIN], [])
    assert "".join(o.repondre("q", projet=tmp_path)) == "Je n'ai pas accès à Internet. Voici."  # web fermé
    assert vus == [False]


def test_sortie_du_cli_local_bornee():
    """06/11 (banc : une boucle de 15 outils a fini en « exceeded the 32000 output token maximum » après 156 s)."""
    env = outils_local.environnement("astrel-qwen3.5-9b", base={})
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == str(outils_local.SORTIE_MAX_JETONS) == "8192"


# ================================================================== 07/11 : longueur et nom proche (banc final)


class _HttpSuite(_Http):
    """Réponses successives du modèle (verdict du nom, réécriture, relecture, raccourci…)."""

    def __init__(self, textes):
        super().__init__("")
        self.textes = list(textes)

    def stream(self, methode, url, json=None, timeout=None):  # noqa: A002
        self.texte = self.textes.pop(0) if self.textes else "OK"
        return super().stream(methode, url, json, timeout)


@pytest.mark.parametrize("q, n", [("Réponds en moins de vingt mots : pourquoi le ciel est-il bleu ?", 20),
                                  ("Juste le mot : le contraire de « chaud » ?", 3),
                                  ("Answer in 15 words: why is the sky blue?", 15),
                                  ("Donne-moi un conseil pour mieux dormir.", moteur_local.MOTS_DEFAUT),
                                  ("Explique en détail le protocole TCP.", None)])
def test_limite_de_mots_lue_dans_la_question(q, n):
    assert moteur_local.limite_mots(q) == n


def test_reponse_trop_longue_raccourcie_une_fois(prenom_lea):
    long = " ".join(["mot"] * 150) + "."
    http = _HttpSuite(["OK", "Une réponse courte."])
    m = moteur_local.MoteurLocal(tag="qwen3.5:9b", client=lambda: http)
    assert m.relire("Donne-moi un conseil pour mieux dormir.", long, "fr") == "Une réponse courte."
    assert "90 mots au plus" in http.corps[1]["messages"][1]["content"] and m.derniere["raccourci"]
    http = _HttpSuite(["OK", "Toujours " + long])  # raccourci raté (plus long) : la réponse reste
    m = moteur_local.MoteurLocal(tag="qwen3.5:9b", client=lambda: http)
    assert m.relire("Donne un conseil.", long, "fr") == long
    http = _HttpSuite(["OK"])  # courte : aucun appel de plus
    m = moteur_local.MoteurLocal(tag="qwen3.5:9b", client=lambda: http)
    assert m.relire("Donne un conseil.", "Dors à heure fixe.", "fr") == "Dors à heure fixe." and len(http.corps) == 1


def test_nom_proche_reecrit_en_rien_trouve(prenom_lea):
    q = "Quels sont les horaires de la mairie de Saint-Brévin-sur-Lot ?"
    brouillon = "La mairie ouvre de 8 h 30 à 12 h (https://www.saint-brevin.fr/)."
    sources = "Mairie de Saint-Brevin-les-Pins : du lundi au vendredi, 8 h 30 - 12 h."
    http = _HttpSuite(["ABSENT", "Je n'ai rien trouvé sur la mairie de Saint-Brévin-sur-Lot ; j'ai trouvé "
                                 "Saint-Brevin-les-Pins, qui n'est pas la même commune."])
    m = moteur_local.MoteurLocal(tag="qwen3.5:9b", client=lambda: http)
    out = m.relire(q, brouillon, "fr", sources=sources, web=True)
    assert out.startswith("Je n'ai rien trouvé sur la mairie de Saint-Brévin-sur-Lot") and len(http.corps) == 2
    assert m.derniere["relecture"] == "rien trouvé" and sources in http.corps[0]["messages"][1]["content"]
    # nom présent : la relecture habituelle (« OK ») garde le brouillon
    http = _HttpSuite(["PRESENT", "OK"])
    m = moteur_local.MoteurLocal(tag="qwen3.5:9b", client=lambda: http)
    assert m.relire(q, brouillon, "fr", sources=sources, web=True) == brouillon
    # 07/11 : tour web SANS recherche ni page (fichiers lus) : pas de contrôle du nom
    http = _HttpSuite(["OK"])
    m = moteur_local.MoteurLocal(tag="qwen3.5:9b", client=lambda: http)
    assert m.relire("Que calcule la fonction tva ?", "Le prix TTC.", "fr", sources="def tva(prix): …", web=True,
                    recherche=False) == "Le prix TTC." and len(http.corps) == 1
    # déjà une abstention, ou sans le web, ou sans résultat d'outil : pas de contrôle du nom
    for b, s, w in (("Je n'ai rien trouvé sur cette mairie.", sources, True), (brouillon, sources, False),
                    (brouillon, "", True)):
        http = _HttpSuite(["OK"])
        m = moteur_local.MoteurLocal(tag="qwen3.5:9b", client=lambda: http)
        assert m.relire(q, b, "fr", sources=s, web=w) == b and len(http.corps) == 1


def test_controles_memes_regles_fr_en():
    for d in (moteur_local._NOM_EXACT, moteur_local._RIEN_TROUVE, moteur_local._RACCOURCIR):
        assert sorted(re.findall(r"\{\w+\}", d["fr"].replace("{de}", "{nom}"))) == \
            sorted(re.findall(r"\{\w+\}", d["en"]))


@pytest.mark.parametrize("q, oui", [("Que veut dire le sigle SPRV-24 ?", True),
                                    ("Que contient l'article L. 9999-1 du Code du travail ?", True),
                                    ("Résume le roman « les lanternes » de quelqu'un.", True),
                                    ("Combien coûte la Freebox Quantum Ultra ?", True),
                                    ("Donne-moi un conseil pour mieux dormir.", False),
                                    ("Salut ! Tu peux me dire combien font 12 fois 12 ?", False),
                                    ("Bonjour Astrel, donne-moi une idée de dîner.\n\n[Contrôle d'Astrel : x]", False)])
def test_controle_du_nom_seulement_si_la_question_nomme_quelque_chose(prenom_lea, q, oui):
    assert moteur_local.nomme(q) is oui
    assert moteur_local.nomme("Lea, donne-moi un conseil.") is False  # le prénom du profil ne compte pas


def test_question_generale_jamais_reecrite_en_rien_trouve(prenom_lea):
    http = _HttpSuite(["OK"])  # seule la relecture habituelle : le contrôle du nom n'est pas demandé
    m = moteur_local.MoteurLocal(tag="qwen3.5:9b", client=lambda: http)
    b = "Couche-toi à heure fixe."
    assert m.relire("Donne-moi un conseil pour mieux dormir.", b, "fr", sources="x", web=True, recherche=True) == b
    assert len(http.corps) == 1 and "nom_exact" not in m.derniere
