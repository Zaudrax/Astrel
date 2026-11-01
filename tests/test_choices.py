"""Questions à choix cliquables dans le HUD (15/10, idée 9) : l'outil MCP rend la
main tout de suite, la réponse revient par `choice_answer` et part au cerveau
comme un message de l'utilisateur, dans le canal de la question."""
import threading

import pytest

from astrel import core
from astrel.choices import Choices, clean_options
from astrel.events import Bus
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)
from conftest import remplacer_coeur


@pytest.fixture
def fresh(monkeypatch):
    c = Choices()
    remplacer_coeur(monkeypatch, "choices", c)
    return c


# ------------------------------------------------------------------ l'état, sans HUD

def test_options_nettoyees():
    assert clean_options(["  Oui,\n tout de suite ", "oui, TOUT de suite", "", 3, "Non\x00", "x" * 300]) == [
        "Oui, tout de suite", "Non", "x" * 120]
    assert clean_options("Oui") == []


def test_question_posee_et_evenement_du_contrat(fresh, published):
    r = fresh.ask("On publie la version 2 ?", ["Oui", "Non", "Plus tard"], "studio")
    assert r["ok"] and r["id"]
    ev = of(published, "choice")[-1]
    assert ev["id"] == r["id"] and ev["question"] == "On publie la version 2 ?"
    assert ev["options"] == [{"label": "Oui", "recommended": True}, {"label": "Non", "recommended": False},
                             {"label": "Plus tard", "recommended": False}]
    assert ev["allow_other"] is True and ev["channel"] == "studio" and ev["answered"] is False


@pytest.mark.parametrize("options", [["Seul"], ["A", "a"], ["A", "B", "C", "D", "E"], None, "A,B"])
def test_nombre_d_options_refuse(fresh, options):
    assert not fresh.ask("Question ?", options, "general")["ok"]


def test_question_vide_refusee(fresh):
    assert not fresh.ask("   ", ["A", "B"], "general")["ok"]


def test_reponse_bornee_et_unique(fresh, published):
    cid = fresh.ask("Quelle option ?", ["A", "B"], "general")["id"]
    assert not fresh.answer(cid, "   ")["ok"]
    r = fresh.answer(cid, "Autre : " + "z" * 900)
    assert r["ok"] and len(r["value"]) == 500 and r["channel"] == "general"
    last = of(published, "choice_answered")[-1]
    assert last["id"] == cid and last["value"] == r["value"] and "cancelled" not in last
    assert r["libre"] is True and last["libre"] is True
    assert not fresh.answer(cid, "B")["ok"]  # second clic : ignoré
    assert not fresh.answer("inconnu", "B")["ok"]
    assert not fresh.answer(None, "B")["ok"]
    assert len(of(published, "choice_answered")) == 1


def test_une_question_par_canal(fresh, published):
    first = fresh.ask("Première ?", ["A", "B"], "studio")["id"]
    other = fresh.ask("Ailleurs ?", ["A", "B"], "compta")["id"]
    second = fresh.ask("Seconde ?", ["C", "D"], "studio")["id"]
    cancelled = of(published, "choice_answered")[-1]
    assert cancelled["id"] == first and cancelled["cancelled"] and cancelled["value"] == ""
    assert not fresh.answer(first, "A")["ok"]  # annulée
    assert {c["id"] for c in fresh.pending()} == {other, second}


def test_rejeu_d_une_question_en_attente_et_de_sa_reponse(fresh, monkeypatch):
    """Un HUD qui se reconnecte revoit la question en attente, même si 300 appels
    d'outils l'ont chassée du journal, puis la voit répondue."""
    b = Bus()
    monkeypatch.setattr("astrel.choices.bus", b)
    cid = fresh.ask("On y va ?", ["Oui", "Non"], "general")["id"]
    for i in range(400):
        b.publish("tool", tool="Bash", id=f"t{i}")
    history = b.snapshot()["history"]
    assert history[0]["type"] == "choice" and history[0]["id"] == cid and not history[0]["answered"]
    assert len([ev for ev in history if ev["type"] == "choice"]) == 1
    fresh.answer(cid, "Oui")
    history = b.snapshot()["history"]
    assert not [ev for ev in history if ev["type"] == "choice"]  # plus épinglée une fois répondue
    assert history[-1]["type"] == "choice_answered" and history[-1]["value"] == "Oui"


def test_rejeu_marque_la_question_repondue(fresh, monkeypatch):
    b = Bus()
    monkeypatch.setattr("astrel.choices.bus", b)
    cid = fresh.ask("On y va ?", ["Oui", "Non"], "general")["id"]
    fresh.answer(cid, "Non")
    replayed = [ev for ev in b.snapshot()["history"] if ev["type"] == "choice"]
    assert replayed[0]["answered"] is True and replayed[0]["value"] == "Non"


# ------------------------------------------------------------------ par le cœur

def _capture_handle(j):
    calls, done = [], threading.Event()

    def handle(text, source="voix", channel="", **_):
        calls.append((text, source, channel))
        done.set()

    j.handle = handle
    return calls, done


def test_commande_ask_choice_rend_la_main_sans_rien_dire(astrel, fresh, published, ch):
    j, said, _ = astrel
    r = j.on_command({"cmd": "ask_choice", "question": "On publie ?", "options": ["Oui", "Non"],
                      "channel": "Compta"})
    assert r["ok"] and r["id"] and "message" in r
    assert of(published, "choice")[-1]["channel"] == "compta"
    assert said == []  # la question n'est pas dite
    assert not j.on_command({"cmd": "ask_choice", "question": "Q ?", "options": ["A", "B"], "channel": "zzz"})["ok"]
    assert not j.on_command({"cmd": "ask_choice", "question": "Q ?", "options": ["A"]})["ok"]


def test_la_reponse_part_au_cerveau_comme_un_message_de_utilisateur(astrel, fresh, ch):
    j, _, _ = astrel
    calls, done = _capture_handle(j)
    cid = j.on_command({"cmd": "ask_choice", "question": "On publie ?", "options": ["Oui", "Non"],
                        "channel": "studio"})["id"]
    # Le HUD écrase « id » par l'identifiant de sa requête WebSocket : « choice_id » prime.
    r = j.on_command({"cmd": "choice_answer", "choice_id": cid, "id": 17, "value": "Oui"})
    assert r == {"ok": True, "id": cid}
    assert done.wait(2)
    assert calls == [(f"Réponse à la question {cid} « On publie ? » : Oui", "clavier", "studio")]
    assert not j.on_command({"cmd": "choice_answer", "choice_id": cid, "value": "Non"})["ok"]  # déjà répondu


def test_id_seul_accepte(astrel, fresh, ch):
    j, _, _ = astrel
    calls, done = _capture_handle(j)
    cid = j.on_command({"cmd": "ask_choice", "question": "Q ?", "options": ["A", "B"]})["id"]
    assert j.on_command({"cmd": "choice_answer", "id": cid, "value": "texte libre"})["ok"]
    assert done.wait(2) and calls[0][0] == f"Réponse à la question {cid} « Q ? » (texte libre) : texte libre"


def test_claude_ne_repond_pas_a_la_place_de_utilisateur(astrel, fresh, ch):
    j, _, _ = astrel
    calls, _ = _capture_handle(j)
    cid = j.on_command({"cmd": "ask_choice", "question": "Q ?", "options": ["A", "B"]})["id"]
    r = j.on_command({"cmd": "choice_answer", "choice_id": cid, "value": "A", "_from_claude": True})
    assert not r["ok"] and calls == []
    assert fresh.pending()  # toujours en attente de l'utilisateur


def test_outil_mcp_non_bloquant(monkeypatch):
    from astrel import mcp_server

    sent = []
    monkeypatch.setattr(mcp_server, "_core", lambda cmd: sent.append(cmd) or {"ok": True, "id": "abc"})
    assert mcp_server.ask_choice("On y va ?", ["Oui", "Non"], "Studio") == {"ok": True, "id": "abc"}
    assert sent == [{"cmd": "ask_choice", "question": "On y va ?", "options": ["Oui", "Non"], "channel": "Studio"}]


# ------------------------------------------------------------------ plan P21 (a/b/c + Autre)

def test_libelle_ou_texte_libre(fresh, published):
    """`libre` : vrai si la réponse n'est aucun des libellés (casse et blancs ignorés)."""
    cid = fresh.ask("Quand ?", ["Lundi", "Mardi"], "general")["id"]
    r = fresh.answer(cid, "  lundi ")
    assert r["ok"] and r["libre"] is False
    assert of(published, "choice_answered")[-1]["libre"] is False
    cid = fresh.ask("Quand ?", ["Lundi", "Mardi"], "general")["id"]
    assert fresh.answer(cid, "Lundi matin\navant 10 h")["libre"] is True


def test_remplace_rend_l_id_de_l_ancienne(fresh, published):
    first = fresh.ask("Première ?", ["A", "B"], "studio")
    assert "remplace" not in first
    other = fresh.ask("Ailleurs ?", ["A", "B"], "compta")
    assert "remplace" not in other
    second = fresh.ask("Seconde ?", ["C", "D"], "studio")
    assert second["remplace"] == first["id"]
    fresh.answer(second["id"], "C")
    third = fresh.ask("Troisième ?", ["E", "F"], "studio")
    assert "remplace" not in third  # la précédente est répondue : rien à remplacer
    # Une carte d'offre du cœur ne remplace pas une question de Claude (et inversement).
    offre = fresh.ask("Changer de modèle ?", ["Oui", "Non"], "studio", kind="offre")
    assert "remplace" not in offre


def test_rejeu_garde_libre(fresh, monkeypatch):
    b = Bus()
    monkeypatch.setattr("astrel.choices.bus", b)
    cid = fresh.ask("On y va ?", ["Oui", "Non"], "general")["id"]
    fresh.answer(cid, "Demain")
    replayed = [ev for ev in b.snapshot()["history"] if ev["type"] == "choice"]
    assert replayed[0]["answered"] is True and replayed[0]["libre"] is True


def test_reponse_rappelle_la_question_bornee(astrel, fresh, ch):
    j, _, _ = astrel
    calls, done = _capture_handle(j)
    question = "Ligne un\n" + "mot " * 60
    cid = j.on_command({"cmd": "ask_choice", "question": question, "options": ["Oui", "Non"]})["id"]
    assert j.on_command({"cmd": "choice_answer", "choice_id": cid, "value": "Non"})["ok"]
    assert done.wait(2)
    text = calls[0][0]
    recalled = text.split("« ", 1)[1].split(" »", 1)[0]
    assert "\n" not in recalled and len(recalled) == core.CHOICE_RECALL and recalled.endswith("…")
    assert text.startswith(f"Réponse à la question {cid} « Ligne un mot mot") and text.endswith(" » : Non")


def test_ask_choice_signale_remplacement_et_absence(astrel, fresh, ch, monkeypatch):
    j, _, _ = astrel
    first = j.on_command({"cmd": "ask_choice", "question": "A ?", "options": ["Oui", "Non"]})
    assert "remplace" not in first and "absent" not in first
    assert f"Réponse à la question {first['id']} « … » : … " in first["message"]
    monkeypatch.setattr(j, "_away", lambda: True)
    second = j.on_command({"cmd": "ask_choice", "question": "B ?", "options": ["Oui", "Non"]})
    assert second["remplace"] == first["id"] and first["id"] in second["message"]
    assert second["absent"] is True and "ABSENT" in second["message"]


def test_reponse_a_claude_tient_dans_le_canal_de_controle(astrel, fresh, ch):
    """Le canal de contrôle rend 4096 octets au plus : message, remplacement et absence compris."""
    import json

    j, _, _ = astrel
    j.on_command({"cmd": "ask_choice", "question": "é" * 1000, "options": ["x" * 150] * 1 + ["y" * 150] * 1})
    j._away = lambda: True
    r = j.on_command({"cmd": "ask_choice", "question": "é" * 1000, "options": ["x" * 150, "y" * 150]})
    assert r["ok"] and len(json.dumps(r, ensure_ascii=False).encode()) < 4096


def test_codeur_n_a_pas_ask_choice():
    """Plan P21 (D6) : la réponse irait à Astrel, pas au sous-agent."""
    from astrel import agents

    assert "mcp__astrel__ask_choice" in agents.load_agents()["codeur"]["disallowedTools"]


def test_consigne_de_l_outil():
    from astrel import mcp_server

    doc = mcp_server.ask_choice.__doc__
    for mot in ("a) b) c)", "Autre", "PREMIÈRE", "(texte libre)", "remplace", "absent"):
        assert mot in doc, mot
