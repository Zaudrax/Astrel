"""Plan P8, lot 1 : reprise qui part sans le capteur REFLEX, réglage `reprise_auto`, et
offre de modèle à 80 % (carte, jamais appliquée seule, jamais envoyée à Claude)."""
import json
import time

import pytest

from astrel import config, core, modeles_locaux, reprise
from astrel.choices import choices
from astrel.settings import reglages
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)
from conftest import remplacer_coeur


@pytest.fixture(autouse=True)
def _pas_de_vrai_cerveau(monkeypatch):
    monkeypatch.setattr(config, "BRAIN_RESTART_DELAY_S", 0)
    monkeypatch.setattr(core.Brain, "restart", lambda self, fresh=False: None)


@pytest.fixture
def sent(astrel, monkeypatch):
    j, _, _ = astrel
    out = []
    monkeypatch.setattr(j, "handle", lambda text, source="voix", channel="", **k: out.append((text, source)))
    monkeypatch.setattr(core.sensors, "is_on", lambda name: False)  # REFLEX éteint, comme chez un ami
    return out


def reset_passe(seconds_ago):
    d = reprise.state.info()
    d["reset_at"] = time.time() - seconds_ago
    config.REPRISE_FILE.write_text(json.dumps(d), encoding="utf-8")


# ------------------------------------------------------------------ reprise sans REFLEX (étapes 1-2)

def test_reprise_part_du_tick_sans_capteur_reflex(astrel, sent):
    """`rate_limit_event` « rejected » injecté comme le CLI l'envoie, capteur REFLEX éteint."""
    j, _, _ = astrel
    ev = {"type": "rate_limit_event", "rate_limit_info": {
        "status": "rejected", "rateLimitType": "five_hour", "resetsAt": int(time.time() + 3600),
        "unifiedWindows": {"five_hour": {"utilization": 1.0}}}}
    for out in j.brain._translate(ev, {}, False):
        j._on_brain_event(out)
    assert reprise.state.closed()
    j._reprise_tick()
    assert sent == []  # fenêtre fermée
    reset_passe(5)
    j._reprise_tick()
    assert sent == []  # réinitialisation + REPRISE_DELAY_S pas encore passés
    reset_passe(config.REPRISE_DELAY_S + 1)
    j._reprise_tick()
    time.sleep(0.1)
    assert sent == [(reprise.PROMPT, "réflexe")]
    j._reprise_tick()
    assert j._reprise_fire()["reprise"] == "rien à reprendre"  # le réflexe en doublon ne relance rien
    time.sleep(0.1)
    assert len(sent) == 1


def test_reprise_repoussee_puis_relancee_par_le_tick(astrel, sent):
    j, _, _ = astrel
    j._limit_reached(time.time() + 3600, "cinq_heures")
    reset_passe(config.REPRISE_DELAY_S + 1)
    j._begin_turn("fond")
    j._reprise_tick()
    assert sent == [] and j._reprise_next_try > time.time() + 50
    j._end_turn(followup=False)
    j._reprise_tick()
    assert sent == []  # attend la minute
    j._reprise_next_try = 0.0
    j._reprise_tick()
    time.sleep(0.1)
    assert sent == [(reprise.PROMPT, "réflexe")]


def test_reprise_auto_coupee(astrel, sent, published):
    j, _, _ = astrel
    reglages.set("reprise_auto", False)
    j._limit_reached(time.time() + 3600, "cinq_heures")
    assert "je te préviendrai seulement" in [n for n in of(published, "notice") if n.get("limit")][-1]["text"]
    reset_passe(config.REPRISE_DELAY_S + 1)
    j._reprise_tick()
    time.sleep(0.1)
    assert sent == [] and not reprise.state.info().get("pending")
    assert "je ne relance rien" in of(published, "notice")[-1]["text"]


def test_prompt_de_reprise_sans_note():
    assert "CHANTIERS-EN-COURS" in reprise.PROMPT and "historique récent" in reprise.PROMPT


# ------------------------------------------------------------------ offre de modèle (étapes 3-4)

def quota(pct, resets="1760000000"):
    return {"status": "allowed", "windows": {"five_hour": {"utilization": pct}}, "resets_at": resets,
            "reset_at": time.time() + 3600}


def offers():
    return [c for c in choices.pending() if c.get("kind") == "offre"]


@pytest.fixture
def frais(monkeypatch):
    """Des questions d'un autre test ne doivent pas compter."""
    from astrel import choices as choices_mod

    fresh = choices_mod.Choices()
    monkeypatch.setattr(choices_mod, "choices", fresh)
    remplacer_coeur(monkeypatch, "choices", fresh)
    return fresh


def test_offre_a_80_pourcent_une_fois_par_fenetre(astrel, frais, monkeypatch):
    j, _, _ = astrel
    j.brain.model = "sonnet"
    asked = []
    monkeypatch.setattr(j, "handle", lambda *a, **k: asked.append(a))
    j._guard_quota(quota(79))
    assert frais.pending() == []
    j._guard_quota(quota(80))
    (card,) = frais.pending()
    assert card["kind"] == "offre" and card["options"] == ["Passer sur Haiku", "Rester sur Sonnet", "Ne plus proposer"]
    assert "80 %" in card["question"]
    j._guard_quota(quota(83))
    assert len(frais.pending()) == 1  # une seule carte par fenêtre
    j._guard_quota(quota(81, resets="1760018000"))
    assert len(frais.pending()) == 2 or frais.pending()[0]["id"] != card["id"]  # fenêtre suivante : nouvelle carte
    assert j.brain.model == "sonnet" and asked == []  # jamais appliquée seule, rien envoyé à Claude


def test_offre_ne_remplace_pas_la_question_de_claude(astrel, frais):
    j, _, _ = astrel
    j.brain.model = "opus"
    claude_q = frais.ask("Quel nom pour le projet ?", ["A", "B"], channel="")
    j._guard_quota(quota(90))
    assert claude_q["id"] in {c["id"] for c in frais.pending()} and len(frais.pending()) == 2
    assert [c for c in frais.pending() if c["kind"] == "offre"][0]["options"][0] == "Passer sur Sonnet"


def test_clic_passer_change_le_modele_sans_tour_de_claude(astrel, frais, monkeypatch, tmp_path):
    j, _, _ = astrel
    monkeypatch.setattr(config, "MODEL_FILE", tmp_path / "model.txt")
    j.brain.model = "sonnet"
    calls, sent_to_claude = [], []
    monkeypatch.setattr(j.brain, "set_model", lambda m: (calls.append(m), setattr(j.brain, "model", m)))
    j.brain.send = lambda *a, **k: sent_to_claude.append(a)
    j._guard_quota(quota(82))  # sous l'alerte de 85 % (point de reprise), qui, elle, parle à Claude
    card = frais.pending()[0]
    refused = j.on_command({"cmd": "choice_answer", "choice_id": card["id"], "value": "Passer sur Haiku",
                            "_from_claude": True})
    assert refused["ok"] is False and calls == []  # Claude ne répond pas à la place de l'utilisateur
    res = j.on_command({"cmd": "choice_answer", "choice_id": card["id"], "value": "Passer sur Haiku"})
    time.sleep(0.1)
    assert res["ok"] and res["offre"] == "switch:haiku" and calls == ["haiku"] and sent_to_claude == []


def test_ne_plus_proposer_et_reglage_coupe(astrel, frais):
    j, _, _ = astrel
    j.brain.model = "sonnet"
    j._guard_quota(quota(85))
    card = frais.pending()[0]
    assert j._choice_answer({"choice_id": card["id"], "value": "Ne plus proposer"})["offre"] == "never"
    assert reglages.value("quota_offer") == "off"
    j._guard_quota(quota(95, resets="autre"))
    assert frais.pending() == []
    reglages.set("quota_offer", "propose")
    reglages.set("quota_offer_pct", 90)
    j._guard_quota(quota(85, resets="encore"))
    assert frais.pending() == []  # seuil réglable


def test_haiku_vers_local_seulement_si_pret(astrel, frais, monkeypatch):
    j, _, _ = astrel
    j.brain.model = "haiku"
    j._guard_quota(quota(85))
    assert frais.pending() == []  # aucun modèle local choisi : rien à proposer
    monkeypatch.setattr(modeles_locaux.local, "ready", lambda: "qwen3.5:4b")
    j._guard_quota(quota(85, resets="w2"))
    card = frais.pending()[0]
    assert card["options"][0] == "Passer en mode léger" and "sans outil" in card["question"]


def test_offre_retenue_d_une_relance_a_l_autre(astrel, frais, monkeypatch):
    """Relecture du 19/10 : la fenêtre déjà proposée est retenue (runtime/local-model.json) ;
    une relance dans la même fenêtre ne repropose pas la carte."""
    j, _, _ = astrel
    j.brain.model = "sonnet"
    j._guard_quota(quota(80))
    (first,) = [c for c in frais.pending() if c.get("kind") == "offre"]
    assert json.loads(config.LOCAL_MODEL_FILE.read_text())["offer_for"] == "1760000000"
    monkeypatch.setattr(j, "_offer_for", None)  # comme après une relance : rien en mémoire
    monkeypatch.setattr(j, "_offer_loaded", False)
    j._guard_quota(quota(84))
    assert [c["id"] for c in [c for c in frais.pending() if c.get("kind") == "offre"]] == [first["id"]]  # même fenêtre : pas de seconde carte
    j._guard_quota(quota(81, resets="1760018000"))
    assert [c["id"] for c in [c for c in frais.pending() if c.get("kind") == "offre"]] != [first["id"]] and len([c for c in frais.pending() if c.get("kind") == "offre"]) == 1  # fenêtre suivante : nouvelle carte
