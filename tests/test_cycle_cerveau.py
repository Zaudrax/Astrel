"""Cerveau qui tombe proprement (04/11, carte T46) : recul 2/5/15/60 s, une annonce par épisode, Claude Code
déconnecté sans relance en boucle, messages gardés pendant une relance puis envoyés ou « non envoyés »,
demande interrompue PROPOSÉE (jamais rejouée), cause de l'arrêt lue dans le flux du CLI (sonde réelle
du CLI 2.1.285 : tests/fixtures/auth/)."""
import io
import json
import queue
import threading
from pathlib import Path

import pytest

from astrel import attente as attente_mod
from astrel import brain as brain_mod
from astrel import config, core, cycle_cerveau
from astrel.brain import Brain, BrainEvent
from astrel.cycle_cerveau import ABSENT, AUTH, OK, RELANCE, CycleCerveau
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)

FIX = Path(__file__).parent / "fixtures" / "auth"
EOF_EV = BrainEvent("error", text="Le moteur s'est arrêté.", data={"eof": True, "rc": 1, "stderr": "", "vecu_s": 3})


class Horloge:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Vivant:
    """Faux process du cerveau : vivant tant que `rc` est None."""
    pid = 4242

    def __init__(self) -> None:
        self.rc = None

    def poll(self):
        return self.rc


@pytest.fixture
def cerveau(astrel, monkeypatch):
    """Astrel de test dont le cerveau se relance sans process réel ; `starts` compte les relances."""
    j, said, chimes = astrel
    horloge = Horloge()
    j.__dict__["_cycle_obj"] = CycleCerveau(horloge=horloge)
    j._running = True
    starts, sent = [], []

    def restart(fresh=False):
        starts.append(fresh)
        j.brain._proc = Vivant()

    j.brain.restart = restart
    j.brain.send = lambda text, images=None: sent.append(text)
    return j, said, chimes, starts, sent, horloge


def notices(published):
    return [e["text"] for e in of(published, "notice")]


# ------------------------------------------------------------------ recul et annonces

def test_eof_repetes_recul_2_5_15_60_et_une_seule_annonce_complete(cerveau, published, relances_cerveau):
    j, said, chimes, starts, _sent, _h = cerveau
    for _ in range(6):
        j._on_turn_error(EOF_EV)
    assert [d for d, _fn in relances_cerveau] == [2.0, 5.0, 15.0, 60.0, 60.0, 60.0]
    assert chimes.count("error") == 1 and len([s for s in said if "moteur" in s]) == 1  # carillon et phrase : 1re
    n = notices(published)
    assert len(n) == 4 and "relance dans 2 s" in n[0] and "retombe sans cesse" in n[3]
    assert starts == []  # rien relancé dans le fil `_pump` : tout passe par le minuteur
    relances_cerveau[-1][1]()  # seule la dernière génération relance
    relances_cerveau[0][1]()
    assert starts == [False] and j._cycle.etat == OK


def test_tour_reussi_remet_le_recul_a_zero(cerveau, published, relances_cerveau):
    j, *_ = cerveau
    j._on_turn_error(EOF_EV)
    j._on_turn_error(EOF_EV)
    j._cycle_reussi()
    assert "Le moteur est reparti." in notices(published)
    j._on_turn_error(EOF_EV)
    assert [d for d, _ in relances_cerveau] == [2.0, 5.0, 2.0]


def test_process_longtemps_vivant_repart_au_premier_palier(cerveau, relances_cerveau):
    j, *_ = cerveau
    j._on_turn_error(EOF_EV)
    j._on_turn_error(EOF_EV)
    j._on_turn_error(BrainEvent("error", text="x", data={"eof": True, "vecu_s": config.RELANCE_STABLE_S + 1}))
    assert [d for d, _ in relances_cerveau] == [2.0, 5.0, 2.0]


def test_relance_manuelle_perime_la_relance_programmee(cerveau, relances_cerveau):
    j, *_rest = cerveau
    starts = cerveau[3]
    j._on_turn_error(EOF_EV)
    j._cerveau_relancer(fresh=True)  # « nouvelle conversation » pendant l'attente
    relances_cerveau[0][1]()
    assert starts == [True]  # la relance programmée ne relance pas une seconde fois


def test_arret_d_astrel_aucune_relance(cerveau, relances_cerveau):
    j, *_ = cerveau
    starts = cerveau[3]
    j._on_turn_error(EOF_EV)
    j._cerveau_arreter()
    relances_cerveau[0][1]()
    j._on_turn_error(EOF_EV)
    assert starts == [] and len(relances_cerveau) == 1


def test_erreur_de_tour_sans_fin_de_flux_ne_relance_pas(cerveau, relances_cerveau, published):
    j, *_ = cerveau
    j._on_turn_error(BrainEvent("error", text="La tâche a échoué.", data={}))
    assert relances_cerveau == [] and "La tâche a échoué." in notices(published)


# ------------------------------------------------------------------ lancement qui lève

def test_popen_qui_leve_compte_une_chute_sans_exception(cerveau, relances_cerveau):
    j, *_ = cerveau

    def leve(fresh=False):
        raise OSError("plus de mémoire")

    j.brain.restart = leve
    j._on_turn_error(EOF_EV)
    relances_cerveau[0][1]()  # ne lève pas
    assert [d for d, _ in relances_cerveau] == [2.0, 5.0] and j._cycle.etat == RELANCE


def test_cli_introuvable_pas_de_boucle(cerveau, relances_cerveau, published):
    j, *_ = cerveau

    def absent(fresh=False):
        raise FileNotFoundError("claude")

    j.brain.restart = absent
    j._on_turn_error(EOF_EV)
    relances_cerveau[0][1]()
    assert j._cycle.etat == ABSENT and len(relances_cerveau) == 1
    assert sum("introuvable" in n for n in notices(published)) == 1


def test_demarrage_qui_leve_ne_casse_pas_le_boot(cerveau, relances_cerveau):
    j, *_ = cerveau

    def leve(fresh=False):
        raise RuntimeError("passerelle cassée")

    j.brain.start = leve
    assert j._cerveau_demarrer() is False
    assert [d for d, _ in relances_cerveau] == [2.0]


def test_relance_sous_le_verrou_d_envoi(cerveau, relances_cerveau):
    j, *_ = cerveau
    vu = []

    def restart(fresh=False):
        vu.append(j._send_lock.locked())
        j.brain._proc = Vivant()

    j.brain.restart = restart
    j._on_turn_error(EOF_EV)
    relances_cerveau[0][1]()
    assert vu == [True]


# ------------------------------------------------------------------ messages pendant une relance

def test_message_pendant_relance_garde_puis_envoye_une_fois(cerveau, published, relances_cerveau):
    j, _said, _chimes, starts, sent, _h = cerveau
    j._on_turn_error(EOF_EV)
    published.clear()
    j.handle("Quelle heure est-il ?", source="clavier")
    assert sent == [] and len(of(published, "transcript")) == 1
    assert any("partira dès qu'il est prêt" in n for n in notices(published))
    relances_cerveau[0][1]()
    assert starts == [False] and len(sent) == 1 and "Quelle heure est-il ?" in sent[0]
    assert len(of(published, "transcript")) == 1  # déjà affiché : pas une seconde fois


def test_message_pendant_relance_trop_longue_non_envoye(cerveau, published, relances_cerveau):
    j, _said, _chimes, _starts, sent, horloge = cerveau
    j._on_turn_error(EOF_EV)
    j.handle("Résume mes mails.", source="clavier")
    mid = of(published, "transcript")[-1]["mid"]
    horloge.t += config.RELANCE_GARDE_S + 1
    j._cycle_tick()
    un = of(published, "unsent")
    assert sent == [] and un and un[-1]["mid"] == mid
    relances_cerveau[0][1]()
    assert sent == []  # jamais envoyé après coup


def test_message_automatique_garde_dix_minutes_puis_abandonne(cerveau, published, relances_cerveau):
    j, _said, _chimes, _starts, sent, horloge = cerveau
    j._on_turn_error(EOF_EV)
    j.handle("Fais le point du matin.", source="réflexe")
    horloge.t += config.RELANCE_GARDE_S + 1
    j._cycle_tick()
    assert j._cycle.gardes() == 1  # un réflexe attend plus longtemps
    horloge.t += config.RELANCE_GARDE_AUTO_S
    j._cycle_tick()
    assert j._cycle.gardes() == 0 and sent == []
    assert any("Message automatique abandonné" in n for n in notices(published))


def test_moteur_tombe_en_plein_tour_demande_proposee_jamais_rejouee(cerveau, published, relances_cerveau):
    j, _said, _chimes, _starts, sent, _h = cerveau
    j.handle("Lance la sauvegarde.", source="voix")
    mid = of(published, "transcript")[-1]["mid"]
    j._on_stream(BrainEvent("tool_start", tool="Bash", tool_id="t1", data={"input": {"command": "ls"}}))
    j._on_stream(EOF_EV)
    items = json.loads(config.ATTENTE_FILE.read_text(encoding="utf-8"))["items"]
    assert [it["mid"] for it in items] == [mid] and items[0]["propose"] is True
    assert [e["mid"] for e in of(published, "unsent")] == [mid]
    assert any("Renvoyer" in n and "vérifie" in n for n in notices(published))
    relances_cerveau[0][1]()
    assert len(sent) == 1  # l'envoi d'origine seulement : jamais rejoué
    # Un tour de fond ne l'efface pas ; un tour de l'utilisateur, si (il l'a vue dans le fil).
    j._attente_fin_tour("fond")
    assert config.ATTENTE_FILE.exists()
    j._attente_fin_tour("clavier")
    assert not config.ATTENTE_FILE.exists()


def test_proposition_reproposee_apres_relance_d_astrel(monkeypatch):
    a = attente_mod.attente
    a.noter("m1", "Prépare le devis.", "clavier")
    assert [it["mid"] for it in a.proposer_courants()] == ["m1"]
    assert a.proposer_courants() == []  # déjà proposée
    monkeypatch.setattr(attente_mod, "BOOT", "demarrage-suivant")
    monkeypatch.setattr(a, "_items", None)
    assert [it["mid"] for it in a.a_proposer()] == ["m1"]


def test_erreur_ordinaire_tient_la_demande_pour_traitee(cerveau):
    j, *_ = cerveau
    j.handle("Ouvre le dossier.", source="clavier")
    j._on_stream(BrainEvent("error", text="La tâche a échoué.", data={}))
    assert not config.ATTENTE_FILE.exists()


# ------------------------------------------------------------------ authentification

class FauxCompte:
    def __init__(self, connecte: bool) -> None:
        self.connecte = connecte
        self.lectures = 0

    def refresh(self, wait=False):
        self.lectures += 1

    def state(self):
        return {"loggedIn": self.connecte}


@pytest.fixture
def compte(monkeypatch):
    from astrel import compte as compte_mod

    faux = FauxCompte(False)
    monkeypatch.setattr(compte_mod, "compte", faux)
    from astrel.coeur import cycle

    # La sonde tourne dans un fil : ici, tout de suite (déterministe).
    monkeypatch.setattr(cycle, "_lancer_sonde", lambda fn: fn())
    return faux


AUTH_EV = BrainEvent("error", text="Not logged in · Please run /login", data={"api_error": "authentication_failed"})


def test_auth_refusee_une_annonce_aucune_relance_en_boucle(cerveau, published, relances_cerveau, compte):
    j, said, chimes, starts, _sent, _h = cerveau
    j._on_turn_error(AUTH_EV)
    j._on_turn_error(AUTH_EV)
    assert j._cycle.etat == AUTH and relances_cerveau == [] and starts == []
    assert sum("n'est plus connecté" in n for n in notices(published)) == 1 and chimes.count("error") == 1
    assert len([s for s in said if "connecté" in s]) == 1


def test_auth_message_garde_puis_envoye_une_fois_connecte(cerveau, published, compte):
    j, _said, _chimes, starts, sent, horloge = cerveau
    j._on_turn_error(AUTH_EV)
    horloge.t += 20
    j.handle("Bonjour", source="clavier")
    assert sent == [] and starts == [] and any("pas connecté" in n for n in notices(published))
    compte.connecte = True  # l'utilisateur a fait /login dans un terminal
    horloge.t += config.AUTH_SONDE_S
    j._cycle_tick()
    assert starts == [False] and len(sent) == 1 and "Bonjour" in sent[0]
    assert any("de nouveau connecté" in n for n in notices(published))
    j._cycle_reussi()
    assert j._cycle.etat == OK and j._cycle.echecs == 0


def test_auth_connecte_mais_toujours_refuse_pas_de_boucle(cerveau, compte):
    j, _said, _chimes, starts, _sent, horloge = cerveau
    compte.connecte = True
    j._on_turn_error(AUTH_EV)  # sonde : connecté → un essai de relance
    assert starts == [False]
    j._on_turn_error(AUTH_EV)  # toujours refusé
    horloge.t += config.AUTH_SONDE_S
    j._cycle_tick()
    assert starts == [False]  # pas de nouvel essai avant AUTH_ESSAI_S
    horloge.t += config.AUTH_ESSAI_S
    j._cycle_tick()
    assert starts == [False, False]


def test_texte_auth_classe_sans_cause_structuree(cerveau, compte):
    j, *_ = cerveau
    j._on_turn_error(BrainEvent("error", text="Invalid API key · Please run /login", data={}))
    assert j._cycle.etat == AUTH


def test_eof_stderr_non_connecte_est_auth(cerveau, relances_cerveau, compte):
    j, *_ = cerveau
    j._on_turn_error(BrainEvent("error", text="Le moteur s'est arrêté.", data={"eof": True, "auth": True}))
    assert j._cycle.etat == AUTH and relances_cerveau == []


# ------------------------------------------------------------------ flux du CLI (sonde réelle)

def _traduire(lignes):
    b = Brain()
    out = []
    for line in lignes:
        out += list(b._translate(json.loads(line) if isinstance(line, str) else line, {}, False))
    return b, out


def test_flux_non_connecte_du_cli_reel():
    """Sonde du CLI 2.1.285 sans compte : init, message synthétique, résultat en erreur."""
    _b, out = _traduire(FIX.joinpath("non-connecte-stream.jsonl").read_text(encoding="utf-8").splitlines())
    kinds = [e.kind for e in out]
    assert "text" not in kinds  # le message synthétique n'est pas une réponse à dire
    err = [e for e in out if e.kind == "error"][0]
    assert err.data["api_error"] == "authentication_failed" and "Not logged in" in err.text
    assert core._erreur_auth(err) and core._erreur_a_proposer(err)


def test_reprise_inconnue_oublie_la_session(monkeypatch):
    oublis = []
    monkeypatch.setattr(brain_mod, "forget_session", lambda: oublis.append(1))
    _b, out = _traduire(FIX.joinpath("resume-inconnu-stream.jsonl").read_text(encoding="utf-8").splitlines())
    err = [e for e in out if e.kind == "error"][0]
    assert "No conversation found" in err.text and oublis


def test_fin_de_flux_porte_la_cause_et_auth_stricte(monkeypatch):
    oublis = []
    monkeypatch.setattr(brain_mod, "forget_session", lambda: oublis.append(1))
    b = Brain()
    b._resuming, b.session_id = "abc", ""
    ev = list(b._translate({"type": "_eof", "rc": 1, "stderr": "Not logged in · Please run /login",
                            "vecu_s": 0.4}, {}, False))[0]
    assert ev.data["eof"] and ev.data["auth"] and ev.data["rc"] == 1 and oublis == []  # session gardée
    ev = list(b._translate({"type": "_eof", "stderr": "MCP server home: oauth 403 not logged in"}, {}, False))[0]
    assert ev.data["auth"] is False and oublis == [1]  # une ligne MCP n'est pas l'auth de Claude Code


def test_lecteur_joint_la_cause_a_la_fin_de_flux():
    class Faux:
        stdout = io.StringIO('{"type":"system","subtype":"status"}\n')

    q: queue.Queue = queue.Queue()
    Brain._read_stdout(Faux(), q, None, lambda: {"rc": 2, "stderr": "boom", "vecu_s": 1.0})
    assert q.get_nowait()["type"] == "system"
    assert q.get_nowait() == {"type": "_eof", "rc": 2, "stderr": "boom", "vecu_s": 1.0}


def test_planifier_ne_tourne_pas_pendant_les_tests(relances_cerveau):
    cycle_cerveau.planifier(0, lambda: (_ for _ in ()).throw(AssertionError("minuteur réel")))
    assert len(relances_cerveau) == 1


def test_cycle_pur_etats_et_generations():
    c = CycleCerveau(horloge=Horloge())
    d = c.tombe(1.0)
    assert d.delai == config.BRAIN_RESTART_DELAY_S and c.relance_due(d.generation)
    c.annuler()
    assert not c.relance_due(d.generation) and c.etat == OK
    assert c.auth() is True and c.auth() is False and c.bloque()
    assert c.reussi() is True and c.etat == OK and not c.bloque()
    c.arret()
    assert c.tombe(1.0) is None and c.auth() is False
