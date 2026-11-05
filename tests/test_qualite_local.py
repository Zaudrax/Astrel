"""Lot 8 du moteur local (05/11/2026, docs/chantiers/HYBRIDE-QWEN.md, « Lot 8 — qualité ») : filtre de sortie
déterministe (R3), adresses non vues marquées (R1), faits exacts calculés par le cœur (R2), budget de réflexion en
jetons (R4), consignes en prose avec exemples (R3), environnement du CLI local (R6), banc de qualité (R0)."""
from __future__ import annotations

import chemins_machine  # chemins de la machine qui lance la suite
import datetime as dt
import json
import re
from pathlib import Path

import pytest

from astrel import faits_local, sortie_locale

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _prenom_de_test(monkeypatch):
    """05/11 (prénom en réglage) : un prénom de TEST, jamais celui du profil de la machine (le dépôt public n'en a
    pas, et sa neutralisation remplace le vrai)."""
    monkeypatch.setattr(sortie_locale, "prenom", lambda: "Lea")


# ================================================================== R3 : filtre de sortie

@pytest.mark.parametrize("texte, demande, attendu", [
    ("Bonjour Lea ! Voici la réponse. Le port est 443.", "port https ?", "Voici la réponse. Le port est 443."),
    ("Bonjour, je suis Astrel, ton assistant local. La capitale est Tokyo.", "capitale du japon", "La capitale est Tokyo."),
    ("En tant qu'Astrel tournant strictement en mode LOCAL, je peux te dire que 12 × 12 = 144.", "12 fois 12",
     "Je peux te dire que 12 × 12 = 144."),
    ("Je fonctionne en local, sans Internet. Je ne connais pas cette loi.", "loi", "Je ne connais pas cette loi."),
    ("Sans accès à Internet, je ne peux pas vérifier. Je ne sais pas. Comme je n'ai pas accès à Internet, non.", "prix",
     "Je ne peux pas vérifier. Je ne sais pas. Comme je n'ai pas accès à Internet, non."),
    ("Je ne sais pas, je n'ai pas accès à Internet. Sans Internet je ne peux rien vérifier.", "prix",
     "Je ne sais pas, je n'ai pas accès à Internet."),
    ("Je ne connais pas cette loi (sans connexion internet).", "loi", "Je ne connais pas cette loi."),
    ("Le fichier /tmp/astrel-local-ab12cd/reglages.json et /tmp/astrel-local-x9 sont techniques.", "lis",
     "Le fichier reglages.json et le dossier technique sont techniques."),
    ("Voici, Lea. Fin.", "x", "Voici. Fin."),
    ("Salut ! 144.", "Salut ! 12 fois 12", "144."),
])
def test_filtre_retire_les_defauts_vus_le_05_11(texte, demande, attendu):
    assert sortie_locale.filtrer(texte, demande) == attendu


@pytest.mark.parametrize("texte, demande", [
    ("Je suis Astrel, ton assistant sur ce PC.", "qui es-tu ?"),
    ("Je fonctionne en local, sans Internet : rien ne sort.", "tu as accès à Internet ?"),
    (f"Le fichier {chemins_machine.PROJETS}/Essai/notes.txt contient le code.", "lis notes.txt"),
    ("Je suis désolé, je ne connais pas cette loi.", "loi"),
    ("Bonjour !", "salut"),  # rien d'autre : la salutation reste (jamais une réponse vide)
])
def test_filtre_garde_ce_qui_est_demande_ou_utile(texte, demande):
    assert sortie_locale.filtrer(texte, demande) == texte


def test_filtre_ne_touche_pas_au_code_et_marche_en_flux():
    texte = "Voici :\n```\nBonjour /tmp/astrel-local-a/b\n```\nFin."
    assert sortie_locale.filtrer(texte, "code") == texte
    f = sortie_locale.FiltreSortie("capitale")
    morceaux = [f.feed(c) for c in ["Bon", "jour L", "ea ! La ca", "pitale est Tokyo.", " Voilà"]]
    assert morceaux[:3] == ["", "", ""]  # rien ne part avant la fin de la première phrase
    assert "".join(morceaux) + f.flush() == "La capitale est Tokyo. Voilà"


def test_filtre_sans_prenom_ne_retire_que_les_salutations_generiques(monkeypatch):
    """05/11 (prénom en réglage) : sans prénom au profil, « Bonjour ! » part, un nom inconnu reste (le filtre ne le
    devine pas), aucune apostrophe n'est retirée ; un prénom donné à la main prime."""
    monkeypatch.setattr(sortie_locale, "prenom", lambda: "")
    assert sortie_locale.filtrer("Bonjour ! Voici la réponse.", "x") == "Voici la réponse."
    assert sortie_locale.filtrer("Hello there, it is Tokyo.", "capital", "en") == "It is Tokyo."
    assert sortie_locale.filtrer("Bonjour Lea ! Voici la réponse.", "x") == "Bonjour Lea ! Voici la réponse."
    assert sortie_locale.filtrer("Voici, Lea. Fin.", "x") == "Voici, Lea. Fin."
    assert sortie_locale.filtrer("Bonjour Anne ! Voici, Anne. Fin.", "x", prenom_="Anne") == "Voici. Fin."


def test_prenom_vient_du_profil(monkeypatch):
    from astrel import config
    monkeypatch.undo()  # le vrai `prenom`, sur un profil de test
    monkeypatch.setattr(config, "PROFILE", {"nom": "  Zoé\n<b>x</b> [y] "})
    assert sortie_locale.prenom() == "Zoé b x /b y"
    monkeypatch.setattr(config, "PROFILE", {})
    assert sortie_locale.prenom() == ""


def test_filtre_en_anglais():
    assert sortie_locale.filtrer("Hello Lea! As a local model, I think Tokyo.", "capital", "en") == \
        "I think Tokyo."


# ================================================================== R1 : adresses non vues

def test_adresse_non_vue_marquee_vue_laissee():
    t = sortie_locale.marquer_urls(
        "Consulte legifrance.gouv.fr ou https://www.impots.gouv.fr/accueil. Vu : [doc](https://ex.com/a). "
        "ASP.NET, app.ini, 3.14, x@gmail.com", ["voir https://ex.com/a"])
    assert "legifrance.gouv.fr (adresse non vérifiée)" in t
    assert "https://www.impots.gouv.fr/accueil (adresse non vérifiée)." in t
    assert "[doc](https://ex.com/a). " in t  # vue dans le tour
    assert t.endswith("ASP.NET, app.ini, 3.14, x@gmail.com")
    assert sortie_locale.marquer_urls(t, ["voir https://ex.com/a"]) == t  # pas de double marque


def test_adresse_marquee_en_anglais_et_jamais_dans_le_code():
    assert sortie_locale.marquer_urls("See example.org.", [], "en") == "See example.org (unverified address)."
    code = "```\ncurl https://example.org\n```"
    assert sortie_locale.marquer_urls(code, []) == code


def test_lien_markdown_marque_apres_la_parenthese():
    assert sortie_locale.marquer_urls("Voir [le site](https://faux.fr).", []) == \
        "Voir [le site](https://faux.fr) (adresse non vérifiée)."


# ================================================================== R2 : faits exacts

AUJ = dt.date(2026, 11, 5)


@pytest.mark.parametrize("demande, attendu", [
    ("Quel jour de la semaine était le 1er janvier 2000 ?", "le 1er janvier 2000 est un samedi"),
    ("Quel jour de la semaine était le 14 juillet 1789 ?", "le 14 juillet 1789 est un mardi"),
    ("Combien de jours entre le 1er février 2024 et le 1er mars 2024 ?", "29 jours entre"),
    ("Combien font 17 fois 23 ?", "17 × 23 = 391"),
    ("Combien font 15 % de 240 ?", "15 % de 240 = 36"),
    ("Combien font 2,5 fois 4 ?", "2,5 × 4 = 10"),
    ("Quel jour sommes-nous aujourd'hui ?", "aujourd'hui, nous sommes le jeudi 5 novembre 2026"),
    ("Le numéro de carte 4111 1111 1111 1112 passe-t-il la clé de Luhn ?", "…1112 : clé de Luhn INVALIDE"),
    ("Vérifie cet IBAN : FR76 3000 6000 0112 3456 7890 189", "IBAN FR76…189 : clé de contrôle valide"),
])
def test_faits_calcules(demande, attendu):
    f = faits_local.faits(demande, "fr", AUJ)
    assert attendu in f and f.startswith("[Contrôle d'Astrel :")


@pytest.mark.parametrize("demande", [
    "Le port 8080 / 443 est ouvert", "On a 3 - 1 enfants", "Explique TCP", "Le 31 février 2024 est-il valide ?",
    "4111 1111 1111 1112",
])
def test_rien_de_devine(demande):
    f = faits_local.faits(demande, "fr", AUJ)
    assert "INVALIDE" not in f and "÷" not in f and "−" not in f and "février 2024 est" not in f


def test_faits_en_anglais_et_numero_masque():
    assert "January 1, 2000 is a Saturday" in faits_local.faits("What day was January 1, 2000?", "en", AUJ)
    assert "4111 1111 1111 1112" not in faits_local.faits("carte 4111 1111 1111 1112", "fr", AUJ)


# ================================================================== R0 : banc de qualité

def test_banc_de_qualite_complet_et_notation_deterministe():
    import importlib.util

    data = json.loads((ROOT / "tests" / "fixtures" / "banc_qualite_local.json").read_text(encoding="utf-8"))
    items = data["items"]
    assert len(items) >= 80 and len({i["id"] for i in items}) == len(items)
    cats = {i["cat"] for i in items}
    assert {"faits", "pieges", "verificateurs", "cyber", "forme", "brievete", "agent"} <= cats
    spec = importlib.util.spec_from_file_location("banc_qualite_local", ROOT / "tools" / "banc_qualite_local.py")
    banc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(banc)
    for i in items:  # chaque motif compile, macros comprises
        for a in [*(i.get("attendu") or []), *(i.get("points") or []), *(i.get("interdit") or [])]:
            banc._motif(a["re"] if isinstance(a, dict) else a, data["motifs"])
    par_id = {i["id"]: i for i in items}
    assert banc.noter(par_id["piege-01"], "Je ne sais pas ce qu'est OpenBanking ID.", data["motifs"])["ok"]
    assert not banc.noter(par_id["piege-01"], "OpenBanking ID est l'application de ta banque.", data["motifs"])["ok"]
    assert banc.noter(par_id["verif-07"], "C'était un samedi.", data["motifs"])["ok"]
    assert not banc.noter(par_id["verif-07"], "C'était un lundi.", data["motifs"])["ok"]
    f = banc.noter(par_id["forme-02"], "Bonjour l'utilisateur ! C'est Tokyo.", data["motifs"])
    assert not f["ok"] and f["forme"]["salutation"] and f["forme"]["prenom"]
    assert not banc.noter(par_id["agent-05"], "ZORGLUB-4217", data["motifs"], outils_lus=0)["ok"]
    assert banc.noter(par_id["agent-05"], "ZORGLUB-4217", data["motifs"], outils_lus=1)["ok"]


# ================================================================== R6 et R3 : CLI local

def test_cli_local_sans_en_tete_d_attribution():
    """Relevé le 05/11 (CLI 2.1.285, faux Ollama) : sans la variable, le prompt système commence par
    « x-anthropic-billing-header: cc_version=… » et casse le cache de préfixe d'Ollama."""
    from astrel import outils_local

    env = outils_local.environnement("astrel-qwen3.5-9b", {"PATH": "/usr/bin", "CLAUDE_CODE_ATTRIBUTION_HEADER": "1"})
    assert env["CLAUDE_CODE_ATTRIBUTION_HEADER"] == "0"
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "astrel-qwen3.5-9b" and env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


class _Entree:
    def write(self, x):
        pass

    def close(self):
        pass


class _Proc:
    def __init__(self, lignes):
        import io
        self.pid, self.stdin = 4242, _Entree()
        self.stdout = io.StringIO("".join(json.dumps(x) + "\n" for x in lignes))

    def poll(self):
        return 0


def _delta(texte):
    return {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": texte}}}


FIN = {"type": "result", "is_error": False, "result": "x"}


def _outils(*flux, appels=None):
    from astrel import outils_local
    procs = iter([_Proc(f) for f in flux])

    def lancer(cmd, **kw):
        if appels is not None:
            appels.append(cmd)
        return next(procs)
    return outils_local.OutilsLocaux(base="qwen3.5:9b", lancer=lancer, reconnu=lambda pid: True,
                                     preparer=lambda b: "astrel-qwen3.5-9b")


def test_cli_local_sortie_filtree_et_adresses_vues(tmp_path, monkeypatch):
    from astrel.platform import systeme
    monkeypatch.setattr(systeme, "tuer_arbre", lambda *a: None)
    outil = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
                                                      "content": "voir https://doc.exemple.org/page"}]}}
    o = _outils([outil, _delta("Bonjour Lea ! "), _delta("Le fichier /tmp/astrel-local-ab/x.txt cite "),
                 _delta("https://doc.exemple.org/page."), FIN])
    assert "".join(o.repondre("lis", projet=tmp_path)) == "Le fichier x.txt cite https://doc.exemple.org/page."
    assert o.derniere["urls_vues"] == ["https://doc.exemple.org/page"]


def test_cli_local_sans_texte_relance_une_fois(tmp_path, monkeypatch):
    """Lot 8 (R6, Ollama #18916) : un tour fini sans aucun texte repart une fois avec « Réponds maintenant. »."""
    from astrel.platform import systeme
    monkeypatch.setattr(systeme, "tuer_arbre", lambda *a: None)
    appels: list = []
    o = _outils([FIN], [_delta("Réponse."), FIN], appels=appels)
    assert "".join(o.repondre("question", projet=tmp_path)) == "Réponse."
    assert len(appels) == 2 and o.derniere["relance"] is True
