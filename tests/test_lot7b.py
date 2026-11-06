"""Lot 7 partie B du moteur local (05/11/2026, docs/chantiers/HYBRIDE-QWEN.md « Lot 7 — partie B ») : outils web et
navigateur BRANCHÉS dans le CLI local. Garde-fou (vrai CLI local, outils du navigateur permis, adresse de
`browser_navigate` dans le registre du tour), registre partagé écrit par le serveur `web_local`, ligne du CLI
(`--disallowedTools`, consignes), ouverture par le banc, mandataire du tour démarré, coupé et arrêté, pages montrées
dans le fil, routeur, Stop tout, « rien ne sort », SearXNG lancé au besoin."""
from __future__ import annotations

import chemins_machine  # chemins de la machine qui lance la suite
import io
import json
import os
import stat
import threading
from pathlib import Path

import pytest

from conftest import mode_prive

from astrel import config, guard, hors_ligne, mcp_web_local, outils_local, routeur, searxng_local

# Les 25 outils de @playwright/mcp 0.0.82 (version fixée de mcp-servers/package.json, `tools/list` du 05/11).
OUTILS_PLAYWRIGHT_0082 = {
    "browser_close", "browser_resize", "browser_console_messages", "browser_handle_dialog", "browser_emulate_media",
    "browser_evaluate", "browser_file_upload", "browser_drop", "browser_find", "browser_fill_form",
    "browser_press_key", "browser_type", "browser_navigate", "browser_navigate_back", "browser_network_requests",
    "browser_network_request", "browser_run_code_unsafe", "browser_take_screenshot", "browser_snapshot",
    "browser_click", "browser_drag", "browser_hover", "browser_select_option", "browser_tabs", "browser_wait_for"}


@pytest.fixture
def registre(tmp_path, monkeypatch):
    f = tmp_path / "registre-web.json"
    f.write_text(json.dumps({"adresses": ["https://example.com/", "https://fr.wikipedia.org/wiki/Paris?x=1"]}))
    monkeypatch.setenv(guard.REGISTRE_WEB_ENV, str(f))
    return f


# ================================================================== garde-fou

def test_meme_liste_d_outils_du_navigateur_partout():
    assert set(guard.NAVIGATEUR_LOCAL_OUTILS) == set(outils_local.NAVIGATEUR_OUTILS)
    assert len(outils_local.NAVIGATEUR_OUTILS) == 8
    assert set(outils_local.NAVIGATEUR_OUTILS) | set(outils_local.NAVIGATEUR_CACHES) == OUTILS_PLAYWRIGHT_0082
    assert not set(outils_local.NAVIGATEUR_OUTILS) & set(outils_local.NAVIGATEUR_CACHES)
    assert "browser_run_code_unsafe" in outils_local.NAVIGATEUR_CACHES and "browser_evaluate" in outils_local.NAVIGATEUR_CACHES


@pytest.mark.parametrize("outil", ["mcp__web_local__recherche_web", "mcp__web_local__lire_page",
                                   "mcp__playwright_local__browser_navigate", "mcp__playwright_local__browser_click"])
def test_outils_web_refuses_hors_du_vrai_cli_local(outil, registre, monkeypatch):
    """Le cerveau Claude (ou un CLI qu'il lancerait, même avec CLAUDE_CONFIG_DIR) : refusé par le hook."""
    monkeypatch.setattr(guard, "_cli_local_reel", lambda *a, **k: False)
    why = guard.refused(outil, {"url": "https://example.com/", "requete": "x"})
    assert "n'existe que pour le modèle local" in why


def test_vrai_cli_local_outils_permis_caches_refuses(registre, monkeypatch):
    monkeypatch.setattr(guard, "_cli_local_reel", lambda *a, **k: True)
    assert guard.refused("mcp__web_local__recherche_web", {"requete": "x"}) == ""
    assert guard.refused("mcp__web_local__lire_page", {"url": "https://n-importe.fr/"}) == ""  # registre : le serveur
    assert "inconnu du serveur web" in guard.refused("mcp__web_local__autre", {})
    for cache in outils_local.NAVIGATEUR_CACHES:
        assert guard.refused(f"mcp__playwright_local__{cache}", {}), cache
    for permis in set(outils_local.NAVIGATEUR_OUTILS) - {"browser_navigate"}:
        assert guard.refused(f"mcp__playwright_local__{permis}", {"element": "x"}) == "", permis


@pytest.mark.parametrize("url, refuse", [
    ("https://example.com", False), ("https://EXAMPLE.com:443/", False), ("https://example.com/#haut", False),
    ("https://fr.wikipedia.org/wiki/Paris?x=1", False),
    ("https://fr.wikipedia.org/wiki/Paris?x=2", True), ("https://fr.wikipedia.org/wiki/Paris", True),
    ("https://exfil.example.net/?d=ZORGLUB", True), ("http://example.com/", True), ("javascript:alert(1)", True),
    ("", True), (None, True), ("https://user:pw@example.com/", True),
])
def test_browser_navigate_seulement_une_adresse_vue_dans_le_tour(url, refuse, registre, monkeypatch):
    monkeypatch.setattr(guard, "_cli_local_reel", lambda *a, **k: True)
    why = guard.refused("mcp__playwright_local__browser_navigate", {"url": url})
    assert bool(why) is refuse, (url, why)


def test_browser_navigate_sans_registre_ou_registre_lien_refuse(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "_cli_local_reel", lambda *a, **k: True)
    monkeypatch.delenv(guard.REGISTRE_WEB_ENV, raising=False)
    assert guard.refused("mcp__playwright_local__browser_navigate", {"url": "https://example.com/"})
    vrai = tmp_path / "vrai.json"
    vrai.write_text(json.dumps({"adresses": ["https://example.com/"]}))
    lien = tmp_path / "lien.json"
    lien.symlink_to(vrai)
    monkeypatch.setenv(guard.REGISTRE_WEB_ENV, str(lien))
    assert guard.refused("mcp__playwright_local__browser_navigate", {"url": "https://example.com/"})
    monkeypatch.setenv(guard.REGISTRE_WEB_ENV, "registre-relatif.json")
    assert guard.refused("mcp__playwright_local__browser_navigate", {"url": "https://example.com/"})


def test_le_coeur_rejuge_une_carte_sans_entree(monkeypatch):
    """confirm.py (cœur) appelle `refused(tool)` sans l'entrée : il ne voit pas les ancêtres du hook, qui a déjà
    jugé ; un outil caché du navigateur reste refusé, le reste suit le jugement habituel (web_local : sensible)."""
    monkeypatch.setattr(guard, "_cli_local_reel", lambda *a, **k: False)
    assert guard.refused("mcp__web_local__recherche_web") == ""
    assert guard.refused("mcp__playwright_local__browser_evaluate")
    assert guard.refused("mcp__playwright_local__browser_run_code_unsafe")


def test_cle_adresse():
    assert guard._cle_adresse("HTTPS://Example.COM") == "https://example.com/"
    assert guard._cle_adresse("http://x.fr:80?a=1#f") == "http://x.fr/?a=1"
    assert guard._cle_adresse("https://x.fr:8443/a") == "https://x.fr:8443/a"
    assert guard._cle_adresse("ftp://x.fr/") == "" and guard._cle_adresse("https://a@b.fr/") == ""


# ================================================================== registre partagé (serveur web_local)

def test_registre_ecrit_en_0600_avec_les_formes_normalisees(tmp_path, monkeypatch):
    tour = mcp_web_local.web_local.Tour()
    monkeypatch.setattr(mcp_web_local, "_TOUR", tour)
    tour.adresses.ajouter("HTTPS://Exemple.FR", "utilisateur")
    tour.adresses.ajouter("https://exemple.fr/a?b=1#c", "page")
    f = tmp_path / "registre-web.json"
    mcp_web_local.ecrire_registre(f)
    assert json.loads(f.read_text()) == {"adresses": ["https://exemple.fr/", "https://exemple.fr/a?b=1"]}
    assert mode_prive(f)  # 05/11 : pas de bits de mode sous Windows
    monkeypatch.setattr(guard, "_cli_local_reel", lambda *a, **k: True)
    monkeypatch.setenv(guard.REGISTRE_WEB_ENV, str(f))
    assert guard.refused("mcp__playwright_local__browser_navigate", {"url": "https://exemple.fr/a?b=1"}) == ""


def test_outil_ecrit_le_registre_apres_chaque_appel(tmp_path, monkeypatch):
    tour = mcp_web_local.web_local.Tour()
    monkeypatch.setattr(mcp_web_local, "_TOUR", tour)
    f = tmp_path / "registre-web.json"
    monkeypatch.setitem(mcp_web_local._ARGS, "registre", f)

    def rechercher(requete, n, tour=None, langue="fr"):
        tour.adresses.ajouter("https://trouve.example.org/page", "recherche")
        return [{"titre": "t", "url": "https://trouve.example.org/page", "extrait": ""}]
    monkeypatch.setattr(mcp_web_local.web_local, "rechercher", rechercher)
    mcp_web_local.recherche_web("essai")
    assert "https://trouve.example.org/page" in json.loads(f.read_text())["adresses"]


def test_declarations_avec_registre_et_rien_hors_ligne(tmp_path, monkeypatch):
    d = mcp_web_local.declarations(tmp_path / "a.json", "en", registre=tmp_path / "r.json")
    args = d["web_local"]["args"]
    assert args[args.index("--registre-fichier") + 1] == str(tmp_path / "r.json") and "--banc-pages" not in args
    assert args[args.index("--langue") + 1] == "en"
    monkeypatch.setattr(hors_ligne, "actif", lambda: True)
    assert mcp_web_local.declarations(tmp_path / "a.json", "fr", navigateur=True, proxy_port=1234) == {}


def test_page_fixe_du_banc_seulement_avec_l_option(monkeypatch):
    monkeypatch.setitem(mcp_web_local._ARGS, "banc_pages", {})
    assert mcp_web_local._client_banc("https://piege.example.com/banc-astrel") is None
    monkeypatch.setitem(mcp_web_local._ARGS, "banc_pages", {"https://piege.example.com/banc-astrel": "<p>x</p>"})
    assert mcp_web_local._client_banc("https://piege.example.com/banc-astrel") is not None
    assert mcp_web_local._client_banc("https://autre.example.com/") is None  # le reste : vrai réseau, toutes les règles


# ================================================================== ligne du CLI local

def test_commande_web_et_navigateur(tmp_path):
    c = outils_local.commande("t", tmp_path, "fr", tmp_path / "r.json", tmp_path / "m.json", web=True,
                              navigateur=True, actions=False)
    caches = c[c.index("--disallowedTools") + 1].split(",")
    assert set(caches) == {f"mcp__playwright_local__{o}" for o in outils_local.NAVIGATEUR_CACHES}
    systeme = c[c.index("--system-prompt") + 1]
    assert "recherche_web" in systeme and "browser_navigate" in systeme and "liste_commandes" not in systeme
    assert "ni aller sur le web" not in systeme and "avec Internet" in systeme
    assert "--bare" not in c and "--dangerously-skip-permissions" not in c and c[c.index("--tools") + 1] == outils_local.OUTILS
    c = outils_local.commande("t", tmp_path, "en", tmp_path / "r.json", tmp_path / "m.json", web=True, actions=True)
    assert "--disallowedTools" not in c
    systeme = c[c.index("--system-prompt") + 1]
    assert "recherche_web" in systeme and "browser_navigate" not in systeme and systeme.endswith(
        outils_local.SYSTEME_ACTIONS["en"])
    c = outils_local.commande("t", tmp_path, "fr", tmp_path / "r.json")
    assert "recherche_web" not in c[c.index("--system-prompt") + 1] and "--mcp-config" not in c


def test_mcp_config_fusionne_les_serveurs(tmp_path):
    web = {"web_local": {"type": "stdio", "command": "x", "args": []}}
    assert set(outils_local.mcp_config(tmp_path, actions=False, web=web)["mcpServers"]) == {"web_local"}
    assert set(outils_local.mcp_config(tmp_path, web=web)["mcpServers"]) == {"astrel_local", "web_local"}
    assert set(outils_local.mcp_config(tmp_path)["mcpServers"]) == {"astrel_local"}


def test_environnement_registre_du_tour_jamais_herite(tmp_path):
    env = outils_local.environnement("t", {"PATH": "/usr/bin", "ASTREL_LOCAL_REGISTRE_WEB": "/tmp/faux.json"})
    assert "ASTREL_LOCAL_REGISTRE_WEB" not in env
    env = outils_local.environnement("t", {"PATH": "/usr/bin"}, registre=tmp_path / "r.json")
    assert env["ASTREL_LOCAL_REGISTRE_WEB"] == str(tmp_path / "r.json")


# ================================================================== ouverture par le banc

def _banc(tmp_path, monkeypatch, **res):
    monkeypatch.setattr(outils_local, "BANC_DIR", tmp_path)
    monkeypatch.setattr(outils_local, "version_cli", lambda: "2.1.285")
    tag = outils_local.tag_derive("qwen3.5:9b")
    outils_local.fichier_banc(tag).write_text(json.dumps({"tag": tag, "cli": "2.1.285", "ok": True, **res}))
    monkeypatch.setattr(hors_ligne, "actif", lambda: False)


def test_web_ouvert_seulement_si_ses_epreuves_ont_reussi(tmp_path, monkeypatch):
    _banc(tmp_path, monkeypatch)
    assert "épreuves web" in outils_local.raison_web_indisponible("qwen3.5:9b", False, False)
    _banc(tmp_path, monkeypatch, web_ok=True)
    assert outils_local.raison_web_indisponible("qwen3.5:9b", False, False) == ""
    assert "navigateur" in outils_local.raison_navigateur_indisponible("qwen3.5:9b", False, False)
    assert "Sans confirmation" in outils_local.raison_web_indisponible("qwen3.5:9b", True, False)
    assert "levée" in outils_local.raison_web_indisponible("qwen3.5:9b", False, True)
    monkeypatch.setattr(hors_ligne, "actif", lambda: True)
    assert "rien ne sort" in outils_local.raison_web_indisponible("qwen3.5:9b", False, False)
    _banc(tmp_path, monkeypatch, web_ok=True, navigateur_ok=True)
    assert outils_local.raison_navigateur_indisponible("qwen3.5:9b", False, False) == ""
    monkeypatch.setattr(config, "ESTOP_FILE", tmp_path / "ESTOP")
    (tmp_path / "ESTOP").write_text("x")
    assert "arrêt d'urgence" in outils_local.raison_navigateur_indisponible("qwen3.5:9b", False, False)


# ================================================================== un tour du CLI local avec le web

class _Entree:
    def write(self, x):
        pass

    def close(self):
        pass


class _Proc:
    def __init__(self, lignes):
        self.pid, self.stdin = 4343, _Entree()
        self.stdout = io.StringIO("".join(json.dumps(x) + "\n" for x in lignes))

    def poll(self):
        return 0


def _delta(t):
    return {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}}}


FIN = {"type": "result", "is_error": False, "result": "x"}


class _Mandataire:
    def __init__(self):
        self.port, self.coupe, self.arrete = 40404, "", False

    def couper(self, raison):
        self.coupe = raison

    def stop(self):
        self.arrete = True

    def bilan(self):
        return {"connexions": 3, "refus": 1}


def _outils(lignes, vus, mandataire=None):
    def lancer(cmd, **kw):
        cwd = Path(kw["cwd"])
        vus.append({"cmd": cmd, "env": kw["env"], "mcp": json.loads((cwd / "mcp.json").read_text())
                    if (cwd / "mcp.json").exists() else None,
                    "adresses": json.loads((cwd / "adresses.json").read_text()) if (cwd / "adresses.json").exists()
                    else None, "mode": stat.S_IMODE(os.stat(cwd / "adresses.json").st_mode)
                    if (cwd / "adresses.json").exists() else None})
        return _Proc(lignes)
    return outils_local.OutilsLocaux(base="qwen3.5:9b", lancer=lancer, reconnu=lambda pid: True,
                                     preparer=lambda b: "astrel-qwen3.5-9b",
                                     mandataire=(lambda: mandataire) if mandataire else None)


def _lire_page(i, url):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": i, "name": "mcp__web_local__lire_page",
                                                          "input": {"url": url}}]}}


def _resultat(i, texte, erreur=False):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": i, "content": texte,
                                                     "is_error": erreur}]}}


def test_tour_web_registre_adresses_et_pages_montrees(tmp_path, monkeypatch):
    from astrel.platform import systeme
    monkeypatch.setattr(systeme, "tuer_arbre", lambda *a: None)
    monkeypatch.setattr(hors_ligne, "actif", lambda: False)
    vus, pages = [], []
    lignes = [_lire_page("t1", "https://fr.wikipedia.org/wiki/Paris"), _resultat("t1", "Paris est la capitale."),
              _lire_page("t2", "https://inventee.example.org/"), _resultat("t2", "adresse jamais vue", True),
              _delta("Paris est la capitale (https://fr.wikipedia.org/wiki/Paris)."), FIN]
    o = _outils(lignes, vus)
    texte = "".join(o.repondre("lis https://fr.wikipedia.org/wiki/Paris", projet=tmp_path, web=True,
                               sur_page=pages.append))
    assert texte == "Paris est la capitale (https://fr.wikipedia.org/wiki/Paris)."
    assert pages == ["https://fr.wikipedia.org/wiki/Paris"]  # la page refusée n'est pas « ouverte »
    assert o.derniere["pages"] == pages and "https://fr.wikipedia.org/wiki/Paris" in o.derniere["urls_vues"]
    v = vus[0]
    assert set(v["mcp"]["mcpServers"]) == {"web_local"} and v["adresses"] == ["https://fr.wikipedia.org/wiki/Paris"]
    assert os.name == "nt" or v["mode"] == 0o600  # 05/11 : pas de bits de mode sous Windows
    args = v["mcp"]["mcpServers"]["web_local"]["args"]
    assert v["env"]["ASTREL_LOCAL_REGISTRE_WEB"] == args[args.index("--registre-fichier") + 1]
    assert "--disallowedTools" not in v["cmd"]


def test_tour_navigateur_mandataire_demarre_coupe_arrete(tmp_path, monkeypatch):
    from astrel.platform import systeme
    monkeypatch.setattr(systeme, "tuer_arbre", lambda *a: None)
    monkeypatch.setattr(hors_ligne, "actif", lambda: False)
    monkeypatch.setattr(config, "ESTOP_FILE", tmp_path / "ESTOP")
    monkeypatch.setattr(mcp_web_local, "declaration_playwright",
                        lambda port, sortie=None: {"type": "stdio", "command": "node", "args": ["--proxy-server",
                                                                                             f"http://127.0.0.1:{port}"]})
    m, vus = _Mandataire(), []
    pendant: list = []

    o = _outils([_delta("Fait."), FIN], vus, mandataire=m)
    gen = o.repondre("va sur le site https://example.com et clique", projet=tmp_path, web=True, navigateur=True)
    pendant.append(next(gen))
    o.couper("Stop tout")  # pendant le tour : délégué au mandataire du tour
    list(gen)
    assert m.coupe == "Stop tout" and m.arrete is True and o.derniere["navigateur"]["refus"] == 1
    serveurs = vus[0]["mcp"]["mcpServers"]
    assert set(serveurs) == {"web_local", "playwright_local"} and "40404" in serveurs["playwright_local"]["args"][1]
    assert "--disallowedTools" in vus[0]["cmd"]
    o.couper("encore")  # hors d'un tour : rien
    assert m.coupe == "Stop tout"


def test_tour_navigateur_jamais_sous_arret_d_urgence_ni_hors_ligne(tmp_path, monkeypatch):
    from astrel.platform import systeme
    monkeypatch.setattr(systeme, "tuer_arbre", lambda *a: None)
    monkeypatch.setattr(config, "ESTOP_FILE", tmp_path / "ESTOP")
    (tmp_path / "ESTOP").write_text("x")
    monkeypatch.setattr(hors_ligne, "actif", lambda: False)
    lance: list = []
    m, vus = _Mandataire(), []
    o = _outils([_delta("Non."), FIN], vus, mandataire=m)
    o._fabrique_mandataire = lambda: lance.append(1) or m
    list(o.repondre("navigue", projet=tmp_path, web=True, navigateur=True))
    assert not lance and "playwright_local" not in vus[0]["mcp"]["mcpServers"]
    monkeypatch.setattr(hors_ligne, "actif", lambda: True)  # « rien ne sort » : aucun serveur web du tout
    list(o.repondre("cherche", projet=tmp_path, web=True, navigateur=True))
    assert not lance and vus[1]["mcp"] is None and "recherche_web" not in vus[1]["cmd"][vus[1]["cmd"].index(
        "--system-prompt") + 1]


# ================================================================== routeur

def _etat(**k):
    return routeur.Etat(lecture_locale=True, scan=lambda t, p: [], **k)


@pytest.mark.parametrize("texte, outils, nav", [
    ("en local : cherche sur internet le prix du train Paris-Lille", True, False),
    ("en local : lis la page https://fr.wikipedia.org/wiki/Paris", True, False),
    ("en local : résume https://www.rfc-editor.org/rfc/rfc9110", True, False),
    ("en local : va sur le site de la mairie et remplis le formulaire", True, True),
    ("en local : ouvre le navigateur sur example.com", True, True),
    ("locally: search the web for the Debian release date", True, False),
    ("en local : envoie un mail à Paul", False, False),
    (f"en local : lis le fichier {chemins_machine.PROJETS}/notes.txt et dis où cliquer", True, False),
    ("en local : quelle est la capitale du Japon ?", False, False),
])
def test_routeur_demande_locale_web(texte, outils, nav):
    d = routeur.decider(texte, etat=_etat(web_local=True, navigateur_local=True))
    assert d.moteur == routeur.LOCAL and not d.carte
    assert (d.outils, d.navigateur) == (outils, nav), texte


def test_routeur_sans_web_ouvert_comme_avant_et_navigateur_ferme():
    d = routeur.decider("en local : cherche sur internet le prix du train", etat=_etat())
    assert d.moteur == routeur.LOCAL and not d.outils
    d = routeur.decider("en local : va sur le site de la mairie et remplis le formulaire",
                        etat=_etat(web_local=True, navigateur_local=False))
    assert d.moteur == routeur.LOCAL and not d.navigateur  # navigateur fermé : jamais ouvert pour autant
    d = routeur.decider("en local : lis la page https://example.com", etat=_etat(web_local=True))
    assert d.outils and not d.navigateur
    # rien de demandé : toujours Claude (ce qui n'est classé nulle part va à Claude)
    d = routeur.decider("cherche sur internet le prix du train", etat=_etat(web_local=True, navigateur_local=True))
    assert d.moteur == routeur.CLAUDE


def test_routeur_donnee_sensible_et_web_reste_local_sans_carte():
    """Décision de l'utilisateur (05/11) : Internet en permanence, même avec une donnée sensible dans la demande."""
    etat = routeur.Etat(lecture_locale=True, web_local=True, scan=lambda t, p: ["IBAN"])
    d = routeur.decider("cherche sur internet à quelle banque appartient cet IBAN FR76…", etat=etat)
    assert d.moteur == routeur.LOCAL and not d.carte and d.outils
    etat = routeur.Etat(lecture_locale=True, scan=lambda t, p: ["IBAN"])  # web fermé : la carte, comme avant
    assert routeur.decider("cherche sur internet à quelle banque appartient cet IBAN FR76…", etat=etat).carte


# ================================================================== tour local du cœur

class _Bus:
    def __init__(self):
        self.ev = []

    def publish(self, kind, **data):
        self.ev.append((kind, data))


def test_pages_ouvertes_dans_le_fil_et_sous_la_reponse(monkeypatch):
    from astrel.coeur import local_tour

    bus = _Bus()
    monkeypatch.setattr(local_tour, "bus", bus)
    monkeypatch.setattr(local_tour.langue, "anglais", lambda: False)
    monkeypatch.setattr(hors_ligne, "actif", lambda: False)

    class T(local_tour.TourLocalCoeur):
        _turn_active, _in_conversation = False, False

        class speaker:
            busy = False

        class status:
            @staticmethod
            def set(*a, **k):
                pass

        def say(self, *a, **k):
            pass

        def _rest_state(self):
            return "idle"

        def _followup_s(self):
            return 0

        def _local_flux(self, demande, ch, outils, stop, reflexion=None, navigateur=False, sur_page=None):
            def flux():
                sur_page("https://fr.wikipedia.org/wiki/Paris")
                yield "Paris est la capitale."
            return flux(), True

    t = T()
    t._local_etat()["actif"] = True
    t._tour_local_reponse("lis la page", "voix", "general", "m1")
    notices = [d for k, d in bus.ev if k == "notice"]
    assert notices and notices[0]["text"] == "Le modèle local a ouvert : https://fr.wikipedia.org/wiki/Paris"
    reply = [d for k, d in bus.ev if k == "reply"][0]
    assert reply["text"].endswith("Pages ouvertes par le modèle local :\n- https://fr.wikipedia.org/wiki/Paris")
    deltas = "".join(d["text"] for k, d in bus.ev if k == "reply_delta")
    assert "Pages ouvertes" not in deltas  # jamais dit ni écrit en flux


def test_stop_tout_et_rien_ne_sort_coupent_le_navigateur():
    from astrel.coeur import local_tour

    class T(local_tour.TourLocalCoeur):
        pass

    t, m = T(), _Mandataire()
    o = outils_local.OutilsLocaux(base="qwen3.5:9b")
    o._mandataire = m
    t.__dict__["_outils_locaux_obj"] = o
    e = t._local_etat()
    e["actif"] = True
    t._local_estop()
    assert m.coupe.startswith("arrêt d'urgence") and e["stop"].is_set()
    src = (Path(__file__).resolve().parent.parent / "astrel" / "coeur" / "hors_ligne.py").read_text(encoding="utf-8")
    assert "self._local_web_couper(hors_ligne.REFUS)" in src
    src = (Path(__file__).resolve().parent.parent / "astrel" / "core.py").read_text(encoding="utf-8")
    assert "self._local_estop()" in src


# ================================================================== SearXNG lancé par le cœur

def test_searxng_assurer(monkeypatch, tmp_path):
    monkeypatch.setattr(searxng_local, "repond", lambda delai=1.5: True)
    assert searxng_local.assurer() == ""
    monkeypatch.setattr(searxng_local, "repond", lambda delai=1.5: False)
    monkeypatch.setattr(hors_ligne, "actif", lambda: True)
    assert "rien ne sort" in searxng_local.assurer()
    monkeypatch.setattr(hors_ligne, "actif", lambda: False)
    monkeypatch.setattr(searxng_local, "VENV", tmp_path / "absente")
    assert "pas installé" in searxng_local.assurer()


def test_searxng_lance_par_enfants_popen_et_attendu(monkeypatch, tmp_path):
    from astrel.platform import enfants

    monkeypatch.setattr(searxng_local, "installe", lambda: True)
    monkeypatch.setattr(searxng_local, "_port_pris", lambda: False)
    monkeypatch.setattr(searxng_local, "_proc", None)
    monkeypatch.setattr(config, "LOGS", tmp_path)
    monkeypatch.setattr(hors_ligne, "actif", lambda: False)
    etat = {"n": 0}
    monkeypatch.setattr(searxng_local, "repond", lambda delai=1.5: etat["n"] >= 3 or bool(etat.update(n=etat["n"] + 1)))
    lances = []

    class P:
        pid, returncode = 999, None

        def poll(self):
            return None

    monkeypatch.setattr(enfants, "popen", lambda cmd, **kw: lances.append((cmd, kw)) or P())
    monkeypatch.setattr(searxng_local.time, "sleep", lambda s: None)
    assert searxng_local.assurer(attente_s=5) == ""
    cmd, kw = lances[0]
    assert cmd[-1] == "--lancer" and cmd[1] == searxng_local.SCRIPT.as_posix()  # 05/11 : en « / » (Git Bash)
    assert not any(k.startswith(("ANTHROPIC_", "CLAUDE")) for k in kw["env"])
    assert mode_prive(tmp_path / "searxng.log")
    assert searxng_local.lance_par_le_coeur()
    monkeypatch.setattr(searxng_local, "_proc", None)


def test_rien_ne_sort_couvre_le_mandataire():
    titres = {s[0]: s for s in hors_ligne.SORTIES}
    assert "proxy_local" in titres["Recherche web du modèle local"][2]
    assert threading  # (fil du mandataire : tests/test_proxy_local.py)
