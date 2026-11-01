"""Tours automatiques silencieux (15/10, HERMES D6, D7, A8 ; agent-coeur 8, 50) :
un réflexe ou une fin de tâche de fond sans rien à dire répond « [SILENT] » et
Astrel ne dit ni n'affiche rien ; un tour de réflexe ne crée pas d'autre réflexe."""
import pytest

from astrel import brain, core, silence
from astrel.reports import reports
from test_reports import ch, astrel, of, published  # noqa: F401 (fixtures)


@pytest.fixture
def direct(monkeypatch):
    monkeypatch.setattr(reports, "_mode", "direct")


def stream(j, *chunks, final=""):
    for c in chunks:
        j._on_stream(core.BrainEvent("text", text=c))
    j._on_stream(core.BrainEvent("turn_end", text=final))


# ------------------------------------------------------------------ le marqueur

def test_marqueur():
    assert silence.could_become_marker("") and silence.could_become_marker("\n [sil")
    assert not silence.could_become_marker("[SILENCE") and not silence.could_become_marker("Rien")
    assert silence.take_marker("\n[silent]\nsuite") == (True, "\nsuite")
    assert silence.take_marker("Il dit [SILENT]") == (False, "Il dit [SILENT]")
    assert silence.strip_marker(" [SILENT] ") == "" and silence.strip_marker("Fait. [Silent]") == "Fait."


# ------------------------------------------------------------------ dans le cœur

def test_reflexe_silencieux_ni_dit_ni_affiche(astrel, published, direct):
    j, said, chimes = astrel
    j.handle("Regarde si le build est fini", "réflexe", "general")
    stream(j, "[SIL", "ENT", "]", final="[SILENT]")
    assert said == [] and chimes == []
    assert of(published, "reply_delta") == [] and of(published, "reply") == []


def test_tour_de_fond_silencieux_sans_compte_rendu(astrel, published, direct):
    j, said, chimes = astrel
    j._on_stream(core.BrainEvent("message_start"))
    stream(j, "\n", "[SILENT]", "\n", final="[SILENT]")
    assert said == [] and chimes == []
    assert of(published, "reply_delta") == [] and of(published, "reply") == []
    assert not reports._items  # rien « à lire »


def test_ce_qui_ressemble_au_marqueur_repart(astrel, published, direct):
    """« [SIL… » retenu un instant, puis rendu tel quel quand ce n'est pas le marqueur."""
    j, said, _ = astrel
    j.handle("Point batterie", "réflexe", "general")
    stream(j, "[", "1] La batterie est à 20 %. ")
    assert said == ["[1] La batterie est à 20 %."]
    assert of(published, "reply")[-1]["text"] == "[1] La batterie est à 20 %."


def test_marqueur_jamais_lu_a_voix_haute(astrel, published, direct):
    j, said, _ = astrel
    j.handle("Point du soir", "réflexe", "general")
    stream(j, "Rien de neuf, je reste discret. ", "[SILENT]")
    assert said == ["Rien de neuf, je reste discret."]
    assert of(published, "reply")[-1]["text"] == "Rien de neuf, je reste discret."


def test_un_tour_de_utilisateur_n_est_jamais_filtre(astrel, published):
    j, said, _ = astrel
    j.handle("Écris-moi le mot entre crochets", "clavier", "general")
    stream(j, "[SILENT]")
    assert said == ["[SILENT]"] and of(published, "reply")[-1]["text"] == "[SILENT]"


@pytest.mark.parametrize("auto", ["plan", "réflexe"])
def test_message_automatique_pendant_un_tour_de_utilisateur(astrel, published, auto):
    """Un message de plan (ou un réflexe) rejoint le tour de l'utilisateur : le tour
    reste le sien (dit), mais le « [SILENT] » répondu au message n'est ni dit ni affiché."""
    j, said, _ = astrel
    j.handle("Où en est le build ?", "voix", "general")
    j.handle("[Plan P3 : question du codeur]", auto, "general")
    assert j._turn_source == "voix"
    stream(j, "Le build est fini. ", "[SIL", "ENT]")
    assert said == ["Le build est fini."]
    assert of(published, "reply")[-1]["text"] == "Le build est fini."
    assert all("SIL" not in ev["text"] for ev in of(published, "reply_delta"))
    # Tour suivant, sans message automatique : de nouveau jamais filtré.
    j.handle("Écris-moi le mot entre crochets", "clavier", "general")
    stream(j, final="[SILENT]")
    assert of(published, "reply")[-1]["text"] == "[SILENT]"


def test_texte_final_seul_marqueur_pas_dit(astrel, published, direct):
    """Sans flux partiel, le texte n'arrive que dans le résultat du tour."""
    j, said, _ = astrel
    j.handle("Vérifie la boîte", "réflexe", "general")
    stream(j, final="[SILENT]")
    assert said == [] and of(published, "reply") == []


# ------------------------------------------------------------------ pas de cascade

def test_un_reflexe_ne_cree_pas_de_reflexe(astrel):
    j, _, _ = astrel
    rule = {"id": "r2", "name": "Encore", "when": {"type": "disk_below", "gb": 1}, "do": {"type": "notify", "text": "x"}}
    j.handle("Tous les lundis, fais le point", "réflexe", "general")
    assert j.on_command({"cmd": "reflex_allowed"})["allowed"] is False
    refused = j.on_command({"cmd": "reflex_create", "rule": rule, "_from_claude": True})
    assert refused["ok"] is False and "cascade" in refused["error"]
    assert j.on_command({"cmd": "reflex_enable", "id": "r2", "enabled": True, "_from_claude": True})["ok"] is False
    # l'utilisateur (HUD) garde la main, et désactiver reste permis.
    assert j.on_command({"cmd": "reflex_create", "rule": rule})["ok"] is True
    assert j.on_command({"cmd": "reflex_enable", "id": "r2", "enabled": False, "_from_claude": True})["ok"] is True
    # l'utilisateur parle pendant le tour : le tour devient le sien, plus de refus.
    j.handle("Et crée-moi un réflexe disque", "voix", "general")
    assert j.on_command({"cmd": "reflex_allowed"})["allowed"] is True


def test_outil_mcp_refuse_la_cascade(monkeypatch):
    from astrel import mcp_server
    from astrel.senses import reflex

    monkeypatch.setattr(mcp_server, "_core", lambda cmd: {"ok": True, "allowed": False, "reason": "pas de cascade"})
    with pytest.raises(Exception, match="pas de cascade"):  # ToolError pour Claude
        mcp_server.reflex_create("r3", "Encore", {"type": "disk_below", "gb": 1}, {"type": "notify", "text": "x"})
    with pytest.raises(Exception, match="pas de cascade"):
        mcp_server.reflex_enable("batterie", True)
    assert all(r["id"] != "r3" for r in reflex.load_rules())
    assert "désactivé" in mcp_server.reflex_enable("batterie", False)
    monkeypatch.setattr(mcp_server, "_core", lambda cmd: {"ok": True, "allowed": True, "reason": ""})
    mcp_server.reflex_create("r3", "Encore", {"type": "disk_below", "gb": 1}, {"type": "notify", "text": "x"})
    assert any(r["id"] == "r3" for r in reflex.load_rules())


def test_consigne_dans_le_prompt():
    p = brain.VOICE_SYSTEM_PROMPT
    assert "[SILENT]" in p and "Pas de question dans le vide" in p and "ne crée ni ne réactive aucun réflexe" in p
