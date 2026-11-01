"""Reprise après coupure (15/10, idée 6) : une seule reprise, jamais de rejeu
après une action, jamais sur une limite, annulée si l'utilisateur reprend la main."""
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from astrel import config, core, reprise
from astrel.senses import reflex
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)

LIMIT = "You've hit your session limit · resets 4:10pm (Europe/Paris)"
PANNE = "API Error: 529 overloaded"


@pytest.fixture(autouse=True)
def _pas_de_vrai_cerveau(monkeypatch):
    """Un tour en erreur relance le cerveau s'il est mort (`_on_turn_error`) : celui des
    tests n'a pas de process, la relance lancerait un VRAI CLI (et 2 s d'attente)."""
    monkeypatch.setattr(config, "BRAIN_RESTART_DELAY_S", 0)
    monkeypatch.setattr(core.Brain, "restart", lambda self, fresh=False: None)


def rules():
    return [r for r in reflex.load_rules() if r["id"] == reprise.REFLEX_ID]


def limite_levee():
    """La réinitialisation est passée (le fichier fait foi)."""
    d = reprise.state.info()
    d["reset_at"] = time.time() - 1
    config.REPRISE_FILE.write_text(json.dumps(d), encoding="utf-8")


def test_classement_des_erreurs():
    assert reprise.classify(LIMIT) == "limit"
    assert reprise.classify("API Error: 529 {\"type\":\"overloaded_error\"}") == "transient"
    assert reprise.classify("API Error: Connection error.") == "transient"
    assert reprise.classify("Invalid API key · Please run /login") == "auth"
    assert reprise.classify("Le moteur s'est arrêté.") == ""
    assert reprise.parse_reset(LIMIT) > time.time()


def test_un_seul_reflexe_meme_apres_relance(astrel, published):
    j, _, _ = astrel
    reset = time.time() + 3600
    j._limit_reached(reset, "cinq_heures")
    j._limit_reached(reset, "cinq_heures")  # tours de fond qui échouent l'un après l'autre
    assert len(rules()) == 1 and rules()[0]["when"] == {"type": "at", "ts": round(reset + config.REPRISE_DELAY_S)}
    assert len([n for n in of(published, "notice") if n.get("limit")]) == 1
    j._reprise_boot()  # relance d'Astrel : rien de plus
    assert len(rules()) == 1 and reprise.state.closed()


def test_reflexes_ask_geles_puis_relances(astrel):
    j, _, _ = astrel
    j._limit_reached(time.time() + 3600, "cinq_heures")
    assert j.on_command({"cmd": "say", "text": "briefing", "source": "réflexe", "reflex": "b"}) == \
        {"ok": True, "held": True}
    assert reprise.state.held_count() == 1


def test_reprise_lancee_une_fois_et_nettoyee(astrel, monkeypatch):
    j, _, _ = astrel
    sent = []
    monkeypatch.setattr(j, "handle", lambda text, source="voix", **k: sent.append((text, source)))
    reprise.state.on_limit(time.time() + 100, "cinq_heures", now=time.time())
    d = reprise.state.info()
    d["reset_at"] = time.time() - 1
    config.REPRISE_FILE.write_text(__import__("json").dumps(d), encoding="utf-8")
    assert j._reprise_fire()["reprise"] == "lancée"
    time.sleep(0.1)
    assert sent == [(reprise.PROMPT, "réflexe")]
    assert not config.REPRISE_FILE.exists() and rules() == []
    assert j._reprise_fire()["reprise"] == "rien à reprendre"  # réflexe en double : rien


def test_reprise_annulee_si_utilisateur_reprend(astrel):
    j, _, _ = astrel
    j._limit_reached(time.time() + 3600, "cinq_heures")
    j._begin_turn("voix")
    j._on_stream(core.BrainEvent("turn_end", text="ok"))
    assert not reprise.state.info().get("pending") and rules() == []


def test_pas_de_rejeu_si_outil_lance(astrel, published):
    j, said, _ = astrel
    j.handle("fais le build", source="clavier")
    j._on_stream(core.BrainEvent("tool_start", tool="Bash", tool_id="1", text="build"))
    j._on_stream(core.BrainEvent("error", text="API Error: 529 overloaded"))
    assert j._retry_job is None and said == []
    assert any("peut-être déjà tourné" in n["text"] for n in of(published, "notice"))


def test_rejeu_si_rien_fait_et_jamais_sur_limite(astrel, monkeypatch):
    j, said, _ = astrel
    later = []
    monkeypatch.setattr(j, "_later", lambda d, job: later.append(d))
    j.handle("quelle heure", source="clavier")
    j._on_stream(core.BrainEvent("error", text="API Error: 529 overloaded"))
    assert later == [15.0] and j._retry_job["prompt"].endswith("quelle heure") and said == []
    j.handle("autre chose", source="clavier")
    assert j._retry_job is None  # nouveau message : l'ancien n'est pas rejoué
    j._on_stream(core.BrainEvent("error", text=LIMIT))
    assert later == [15.0] and j._retry_job is None and reprise.state.closed()


# ------------------------------------------------------------------ restes du lot 4 (15/10)

def test_rejeu_envoye_puis_abandon_dit_apres_trois_echecs(astrel, published, monkeypatch):
    j, said, _ = astrel
    sent, later = [], []
    j.brain.send = lambda prompt, images=None: sent.append(prompt)
    monkeypatch.setattr(j, "_later", lambda d, job: later.append((d, job)))
    j.handle("quelle heure", source="clavier")
    for n, delay in enumerate(config.RETRY_DELAYS_S):
        j._on_stream(core.BrainEvent("error", text=PANNE))
        assert later[-1][0] == delay and later[-1][1]["attempt"] == n + 1 and said == []
        j._replay(later[-1][1])  # le délai est passé : le tour est vraiment renvoyé
        assert len(sent) == n + 2 and sent[-1] == sent[0] and j._turn_active and j._turn_retry == n + 1
    j._on_stream(core.BrainEvent("error", text=PANNE))  # 3 rejeux sans succès : abandon
    assert len(later) == 3 and j._retry_job is None
    assert said == [PANNE]  # dit à voix haute, comme toute erreur non prise en charge
    assert of(published, "notice")[-1]["level"] == "error"


def test_reflexe_gele_relance_apres_la_reprise(astrel, monkeypatch):
    j, _, _ = astrel
    sent = []
    monkeypatch.setattr(j, "handle", lambda text, source="voix", channel="", **k: sent.append((text, source, channel)))
    j._limit_reached(time.time() + 3600, "cinq_heures")
    assert j.on_command({"cmd": "say", "text": "briefing du soir", "source": "réflexe", "reflex": "b",
                         "channel": "Compta"})["held"]
    j._reprise_tick()
    assert sent == []  # fenêtre fermée : rien ne part
    limite_levee()
    assert j._reprise_fire()["reprise"] == "lancée"
    time.sleep(0.1)
    j._reprise_tick()  # la reprise est partie : le réflexe gelé suit, dans son canal
    assert sent == [(reprise.PROMPT, "réflexe", ""), ("briefing du soir", "réflexe", "Compta")]
    assert reprise.state.held_count() == 0 and not config.REPRISE_FILE.exists()
    j._reprise_tick()
    assert len(sent) == 2  # relancé une seule fois


def test_reprise_repoussee_si_tour_en_cours(astrel, monkeypatch):
    j, _, _ = astrel
    sent = []
    monkeypatch.setattr(j, "handle", lambda text, source="voix", **k: sent.append(text))
    j._limit_reached(time.time() + 3600, "cinq_heures")
    limite_levee()
    j._begin_turn("fond")  # une tâche de fond a relancé un tour
    t0 = time.time()
    assert j._reprise_fire()["reprise"] == "repoussée"
    assert len(rules()) == 1 and t0 + 59 <= rules()[0]["when"]["ts"] <= time.time() + 61
    assert reprise.state.info()["pending"] and sent == []
    j._end_turn(followup=False)
    assert j._reprise_fire()["reprise"] == "lancée"
    time.sleep(0.1)
    assert sent == [reprise.PROMPT] and rules() == []


def test_parse_reset_avec_date():
    paris = ZoneInfo("Europe/Paris")
    now = datetime(2026, 10, 15, 16, 0, tzinfo=paris).timestamp()
    got = reprise.parse_reset("You've hit your weekly limit · resets Oct 20, 9am (Europe/Paris)", now)
    assert datetime.fromtimestamp(got, paris) == datetime(2026, 10, 20, 9, 0, tzinfo=paris)
    dec = datetime(2026, 12, 30, 12, 0, tzinfo=paris).timestamp()
    got = reprise.parse_reset("resets Jan 2, 10:30am (Europe/Paris)", dec)  # « Jan 2 » lu fin décembre
    assert datetime.fromtimestamp(got, paris) == datetime(2027, 1, 2, 10, 30, tzinfo=paris)
    assert reprise.parse_reset("resets Feb 31, 9am", now) == 0.0  # date impossible : illisible
    assert reprise.parse_reset("Claude AI usage limit reached|1760000000") == 1760000000
