"""Plan P41 (27/10, demande de l'utilisateur : « des messages se perdent dans d'autres canaux comme
Général ») : le canal « Astrel » s'affiche « Astrel » (id `astrel` inchangé) et ce qui n'a pas de
projet (carte, compte rendu, relance d'agent, tour de réflexe ou d'équipe, fin de tâche lancée
depuis Général) va dans le canal OUVERT du HUD ; ce qui a un projet reste dans son canal."""
import chemins_machine  # chemins de la machine qui lance la suite
import json

from astrel import config, core
from astrel.channels import MIGRATION_ASTREL, Channels, strip_vocative
from test_choices import ch, fresh, astrel, of, published  # noqa: F401  (fixtures)
from test_pump import brain_events
from test_reports import fond_turn

AVEC_ASTREL = {
    "default": "general",
    "channels": [
        {"id": "general", "label": "Général"},
        {"id": "astrel", "label": "Astrel", "keywords": ["relève", "HUD", "Astrel"],
         "workdir": f"{chemins_machine.RACINE}"},
        {"id": "studio", "label": "Studio", "keywords": ["bracelet"]},
        {"id": "compta", "label": "Compta"},
    ],
}


def _with_astrel(ch):
    config.CHANNELS_FILE.write_text(json.dumps(AVEC_ASTREL), encoding="utf-8")
    ch._mtime = -1.0  # relire tout de suite
    return ch


# ------------------------------------------------------------------ renommage « Astrel » → « Astrel »

def test_migration_renomme_l_affichage_seulement(ch, published):
    _with_astrel(ch).select("astrel")
    assert ch.migrate() == {"ok": True, "changed": True}
    data = json.loads(config.CHANNELS_FILE.read_text(encoding="utf-8"))
    entry = next(e for e in data["channels"] if e["id"] == "astrel")
    assert entry["label"] == "Astrel" and entry["keywords"] == ["relève", "HUD", "Astrel"]  # id et mots gardés
    assert entry["workdir"] == f"{chemins_machine.RACINE}" and data["_migrations"] == [MIGRATION_ASTREL]
    assert ch.active == "astrel"  # runtime/channel.txt n'a pas à changer
    assert ch.find("Astrel") == ch.find("Astrel") == "astrel"  # outils channel_* : les deux noms marchent
    assert [c["label"] for c in of(published, "channels")[-1]["items"]][1] == "Astrel"  # HUD prévenu


def test_migration_faite_une_seule_fois(ch):
    _with_astrel(ch)
    ch.migrate()
    assert ch.edit("astrel", label="Astrel")["ok"]  # l'utilisateur revient à « Astrel » : choix respecté
    assert ch.migrate() == {"ok": True, "changed": False}
    assert ch.items()[1].label == "Astrel"


def test_migration_sans_canal_astrel_ne_touche_a_rien_d_autre(ch):
    before = json.loads(config.CHANNELS_FILE.read_text(encoding="utf-8"))
    assert ch.migrate() == {"ok": True, "changed": False}
    after = json.loads(config.CHANNELS_FILE.read_text(encoding="utf-8"))
    assert after["channels"] == before["channels"] and after["_migrations"] == [MIGRATION_ASTREL]


def test_migration_fichier_illisible_jamais_reecrit(ch):
    config.CHANNELS_FILE.write_text("{abîmé", encoding="utf-8")
    assert ch.migrate()["ok"] is False
    assert config.CHANNELS_FILE.read_text(encoding="utf-8") == "{abîmé"


# ------------------------------------------------------------------ le nom de l'assistant en tête

def test_parler_a_astrel_ne_change_pas_de_canal(ch):
    _with_astrel(ch).migrate()
    ch.select("studio")
    for text in ("Astrel, regarde ça", "Hey Astrel relance le build", "Astrel, ouvre le dossier", "astrel"):
        assert ch.route(text) == ("studio", False), text
    assert ch.route("canal Astrel, relance la relève") == ("astrel", True)  # viser le canal reste possible
    assert ch.route("#astrel regarde")[0] == "astrel"
    ch.select("general")
    assert ch.route("Astrel, quel temps fait-il ?") == ("general", False)  # plus rangé dans Astrel
    assert ch.route("Astrel, où en est le bracelet ?") == ("studio", True)  # le projet nommé compte encore
    ch.select("general")
    assert ch.route("le HUD d'Astrel rame")[0] == "astrel"  # en parler (pas en tête) : son canal


def test_strip_vocative():
    names = ["hey astrel", "astrel", "astrel"]
    assert strip_vocative("astrel, relance", names) == "relance"
    assert strip_vocative("ok astrel : fais-le", names) == "fais-le"
    assert strip_vocative("astrelle est là", names) == "astrelle est là"  # pas un autre mot
    assert strip_vocative("pour astrel relance", names) == "pour astrel relance"


# ------------------------------------------------------------------ rien ne se perd dans Général

def test_attention(ch):
    ch.select("compta")
    assert ch.attention() == ch.attention("general") == ch.attention("Général") == "compta"
    assert ch.attention("inconnu") == "compta"  # canal retiré
    assert ch.attention("Studio") == "studio"  # un projet reste dans son canal


def test_compte_rendu_et_carte_sans_projet_dans_le_canal_ouvert(astrel, fresh, published, ch):
    j, _, _ = astrel
    ch.select("compta")
    assert j.on_command({"cmd": "report", "text": "Fini."})["canal"] == "compta"  # tour par défaut : Général
    assert j.on_command({"cmd": "report", "text": "Fini.", "channel": "Général"})["canal"] == "compta"
    assert j.on_command({"cmd": "report", "text": "Fini.", "channel": "Studio"})["canal"] == "studio"
    assert j.on_command({"cmd": "ask_choice", "question": "On y va ?", "options": ["Oui", "Non"]})["ok"]
    assert of(published, "choice")[-1]["channel"] == "compta"
    j.handle("Où en est Studio ?", "clavier", "studio")  # échange dans un projet : il y reste
    ch.select("compta")
    assert j.on_command({"cmd": "report", "text": "Studio fini."})["canal"] == "studio"


def test_offre_de_modele_dans_le_canal_ouvert(astrel, fresh, published, ch):
    j, _, _ = astrel
    j.brain.model = "sonnet"
    ch.select("studio")
    j._offer_model({"reset_at": 0}, 0.8)
    assert of(published, "choice")[-1]["channel"] == "studio"  # avant : "" → Général dans le HUD


def test_tour_d_equipe_ou_de_reflexe_dans_le_canal_ouvert(astrel, published, ch):
    j, _, _ = astrel
    ch.select("compta")
    j.handle("Le codeur a fini : lance le testeur.", "equipe", "")
    j._on_stream(core.BrainEvent("text", text="Je lance le testeur. "))
    j._on_stream(core.BrainEvent("turn_end"))
    assert of(published, "transcript")[-1]["channel"] == "compta"
    assert of(published, "reply")[-1]["channel"] == "compta"
    assert ch.active == "compta"  # le canal ouvert ne bouge pas
    j.handle("Studio : état du build", "réflexe", "")  # un réflexe qui nomme son projet y va
    assert of(published, "transcript")[-1]["channel"] == "studio" and ch.active == "compta"


def _run_background(j, ch, text, source, channel, switch_to):
    j.handle(text, source, channel)
    first_end = True
    for ev in brain_events():
        j._on_stream(ev)
        if ev.kind == "turn_end" and first_end:
            first_end = False
            ch.select(switch_to)  # l'utilisateur change d'onglet pendant que la tâche tourne


def test_tache_lancee_depuis_general_rend_compte_dans_le_canal_ouvert(astrel, published, ch):
    j, _, _ = astrel
    ch.select("general")
    _run_background(j, ch, "Lance la commande en fond", "clavier", "general", "studio")
    last = of(published, "reply")[-1]
    assert last["channel"] == "studio" and "finie" in last["text"]  # avant : Général, onglet fermé
    assert of(published, "notice")[-1]["channel"] == "studio"  # le toast y mène aussi
    assert ch.active == "studio"


def test_tache_d_un_reflexe_suit_le_canal_ouvert_a_sa_fin(astrel, published, ch):
    j, _, _ = astrel
    ch.select("compta")
    _run_background(j, ch, "Lance la commande en fond", "réflexe", "", "studio")
    assert of(published, "tasks")[0]["items"][0]["channel"] == "general"  # tâche sans projet
    assert of(published, "reply")[0]["channel"] == "compta"  # le tour du réflexe : canal ouvert alors
    assert of(published, "reply")[-1]["channel"] == "studio"  # sa fin : canal ouvert à ce moment


def test_tour_de_fond_inconnu_dans_le_canal_ouvert(astrel, published, ch):
    j, _, _ = astrel
    ch.select("studio")
    fond_turn(j)  # aucune tâche connue (relance du cœur entre-temps) : avant, Général
    rep = of(published, "reply")[-1]
    assert rep["channel"] == "studio" and rep["report"] is True
    assert j.on_command({"cmd": "report", "text": "Suite."})["canal"] == "studio"
    ch.select("compta")
    assert j.on_command({"cmd": "report", "text": "Encore."})["canal"] == "compta"  # tour sans projet : suit


def test_nouvelle_relance_lit_le_canal_retenu(ch):
    _with_astrel(ch).migrate()
    ch.select("astrel")
    assert Channels().active == "astrel"  # runtime/channel.txt garde l'id technique
