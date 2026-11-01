"""Canaux du HUD (30/09) : un fil de discussion par projet. La session Claude
reste UNE conversation ; seul l'affichage est rangé, par un champ `channel`
sur chaque message publié."""
import json
import os

import pytest

from astrel import config, core
from astrel.channels import Channels, parse
from astrel.events import bus
from test_pump import brain_events, make_astrel
from conftest import remplacer_coeur

CONF = {
    "default": "general",
    "channels": [
        {"id": "general", "label": "Général", "color": "#3ad6ff"},
        {"id": "studio", "label": "Studio", "color": "#34e3a4", "keywords": ["bracelet"]},
        {"id": "compta", "label": "Compta"},
    ],
}


@pytest.fixture
def ch(tmp_path, monkeypatch):
    f = tmp_path / "channels.json"
    f.write_text(json.dumps(CONF), encoding="utf-8")
    monkeypatch.setattr(config, "CHANNELS_FILE", f)
    monkeypatch.setattr(config, "CHANNEL_FILE", tmp_path / "channel.txt")
    c = Channels()
    remplacer_coeur(monkeypatch, "channels", c)
    return c


@pytest.fixture
def published():
    out = []
    unsub = bus.subscribe(out.append)
    yield out
    unsub()


def of(events, kind):
    return [ev for ev in events if ev["type"] == kind]


# ------------------------------------------------------------------ la liste

def test_fichier_abime_garde_un_canal():
    items, default = parse("pas un objet")
    assert [c.id for c in items] == ["general"] and default == "general"


def test_liste_normalisee():
    items, default = parse({"default": "inconnu", "channels": [
        "Général", {"label": "Nouveau Projet"}, {"id": "studio", "color": "rouge"}, {"id": "studio"}, 42]})
    assert [c.id for c in items] == ["general", "nouveau-projet", "studio"]  # doublon et entrée invalide ignorés
    assert default == "general"
    assert all(c.color.startswith("#") for c in items)  # couleur invalide remplacée
    assert "nouveau projet" in items[1].keywords  # le nom compte comme mot-clé


def test_projet_ajoute_a_chaud(ch, published):
    assert ch.find("Compta") == "compta" and not ch.find("Boutique")
    conf = {**CONF, "channels": CONF["channels"] + [{"id": "boutique", "label": "Boutique"}]}
    config.CHANNELS_FILE.write_text(json.dumps(conf), encoding="utf-8")
    st = config.CHANNELS_FILE.stat()
    os.utime(config.CHANNELS_FILE, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    ch.refresh()
    assert ch.find("boutique") == "boutique"
    assert [c["id"] for c in of(published, "channels")[-1]["items"]][-1] == "boutique"


def test_canal_ouvert_retenu(ch):
    assert ch.select("Studio")["ok"]
    assert config.CHANNEL_FILE.read_text(encoding="utf-8").strip() == "studio"
    assert Channels().active == "studio"  # survit à une relance
    assert not ch.select("nimporte")["ok"]


# ------------------------------------------------------------------ rattachement

def test_canal_ouvert_par_defaut(ch):
    ch.select("studio")
    assert ch.route("lance les tests") == ("studio", False)
    # Depuis un canal de projet, NOMMER un autre projet ne suffit pas à changer de fil…
    assert ch.route("est-ce qu'Compta utilise la même base ?") == ("studio", False)


def test_s_adresser_a_un_canal(ch):
    ch.select("studio")
    # … mais COMMENCER par son nom, oui, et le canal ouvert suit.
    assert ch.route("Compta, relance la recherche") == ("compta", True)
    assert ch.active == "compta"
    assert ch.route("pour studio lance les tests")[0] == "studio"
    assert ch.route("#general quelle heure est-il")[0] == "general"
    ch.select("studio")
    assert ch.route("Compta avance bien ?")[0] == "studio"  # sans ponctuation ni préposition : on en parle


def test_depuis_general_un_seul_projet_nomme(ch):
    assert ch.route("où en est le bracelet ?") == ("studio", True)
    ch.select("general")
    assert ch.route("compare Studio et Compta") == ("general", False)  # deux projets : ambigu
    assert ch.route("quel temps fait-il ?") == ("general", False)


def test_reflexe_ne_deplace_pas_le_canal_ouvert(ch):
    ch.select("compta")
    assert ch.route("briefing du matin", follow=False) == ("general", False)
    assert ch.route("Studio : état du build", follow=False)[0] == "studio"
    assert ch.active == "compta"


# ------------------------------------------------------------------ dans le cœur

def test_message_et_reponse_dans_le_meme_canal(ch, published, monkeypatch):
    j, _ = make_astrel(monkeypatch)
    j.brain.send = lambda *a, **k: None
    j._send_lock = __import__("threading").Lock()
    j.handle("Lance les tests", "clavier", "studio")
    j._on_stream(core.BrainEvent("text", text="Les tests passent. "))
    j._on_stream(core.BrainEvent("turn_end"))
    user = of(published, "transcript")[-1]
    assert user["channel"] == "studio" and user["mid"]
    assert {ev["channel"] for ev in of(published, "reply_delta")} == {"studio"}
    reply = of(published, "reply")[-1]
    assert reply["channel"] == "studio" and reply["reply_to"] == user["mid"]


def test_message_ailleurs_en_plein_tour_coupe_la_reponse(ch, published, monkeypatch):
    """Le CLI intègre un message envoyé en plein tour au tour en cours : la
    suite de la réponse va dans le canal de ce message, le début reste où il était."""
    j, _ = make_astrel(monkeypatch)
    j.brain.send = lambda *a, **k: None
    j._send_lock = __import__("threading").Lock()
    j.handle("Où en est Studio ?", "clavier", "studio")
    j._on_stream(core.BrainEvent("text", text="Studio avance bien. "))
    j.handle("Compta, relance la recherche", "clavier", "studio")
    j._on_stream(core.BrainEvent("text", text="Je relance Compta. "))
    j._on_stream(core.BrainEvent("turn_end"))
    replies = of(published, "reply")
    assert [(r["channel"], r["text"]) for r in replies[-2:]] == [
        ("studio", "Studio avance bien."), ("compta", "Je relance Compta.")]
    assert replies[-2]["partial"] and ch.active == "compta"


def test_tour_de_fond_dans_le_canal_de_sa_tache(ch, published, monkeypatch):
    """Enregistrement réel : une tâche de fond lancée depuis Compta finit alors
    que l'utilisateur est passé sur Studio. Le compte rendu va dans Compta."""
    j, _ = make_astrel(monkeypatch)
    j.brain.send = lambda *a, **k: None
    j._send_lock = __import__("threading").Lock()
    j.handle("Lance la commande en fond", "clavier", "compta")
    first_end = True
    for ev in brain_events():
        j._on_stream(ev)
        if ev.kind == "turn_end" and first_end:
            first_end = False
            ch.select("studio")  # l'utilisateur change d'onglet pendant que la tâche tourne
    assert of(published, "tasks")[0]["items"][0]["channel"] == "compta"
    assert of(published, "notice")[-1]["channel"] == "compta"
    last = of(published, "reply")[-1]
    assert last["channel"] == "compta" and "finie" in last["text"]
    assert ch.active == "studio"  # le tour de fond ne change pas l'onglet ouvert


def test_deplacer_un_echange(ch, published, monkeypatch):
    j, _ = make_astrel(monkeypatch)
    j.brain.send = lambda *a, **k: None
    j._send_lock = __import__("threading").Lock()
    j.handle("Lance les tests", "clavier", "general")
    j._on_stream(core.BrainEvent("text", text="C'est fait. "))
    j._on_stream(core.BrainEvent("turn_end"))
    mid = of(published, "transcript")[-1]["mid"]
    assert j.on_command({"cmd": "channel_move", "mid": mid, "channel": "Studio"}) == {
        "ok": True, "channel": "studio", "moved": 2}  # le message ET sa réponse
    history = [ev for ev in bus.snapshot()["history"] if mid in (ev.get("mid"), ev.get("reply_to"))]
    assert {ev["channel"] for ev in history} == {"studio"}  # un HUD qui se reconnecte les voit à leur place
    assert of(published, "channel_move")[-1] == {**of(published, "channel_move")[-1], "mid": mid, "channel": "studio"}
    assert not j.on_command({"cmd": "channel_move", "mid": mid, "channel": "inconnu"})["ok"]


def test_commandes_canal(ch, monkeypatch):
    j, _ = make_astrel(monkeypatch)
    assert j.on_command({"cmd": "canal compta"})["active"] == "compta"  # --send "canal compta"
    listing = j.on_command({"cmd": "channels"})
    assert listing["active"] == "compta" and [c["id"] for c in listing["channels"]] == ["general", "studio", "compta"]


# ------------------------------------------------------------------ l'ordre des onglets (19/10)

def test_reclasser_les_canaux_ecrit_l_ordre(ch, published):
    raw = {**CONF, "_aide": "à garder"}
    config.CHANNELS_FILE.write_text(json.dumps(raw), encoding="utf-8")
    res = ch.reorder(["compta", "general", "studio"])
    assert res == {"ok": True, "order": ["compta", "general", "studio"], "changed": True}
    data = json.loads(config.CHANNELS_FILE.read_text(encoding="utf-8"))
    assert [e["id"] for e in data["channels"]] == ["compta", "general", "studio"]  # l'ordre du tableau fait foi
    assert data["_aide"] == "à garder" and data["channels"][2]["keywords"] == ["bracelet"]  # le reste est intact
    assert [c["id"] for c in of(published, "channels")[-1]["items"]] == ["compta", "general", "studio"]  # autres HUD
    assert [c.id for c in Channels().items()] == ["compta", "general", "studio"]  # relu après une relance


def test_reclasser_garde_le_canal_par_defaut(ch):
    assert ch.reorder(["studio", "compta"])["order"] == ["studio", "compta", "general"]  # absent : remis à la fin
    assert ch.default == "general" and ch.describe()["default"] == "general"
    assert ch.reorder(["Studio", "Compta", "Général"])["changed"] is False  # noms acceptés, rien à réécrire


def test_reclasser_refuse_un_id_inconnu_ou_en_double(ch):
    before = config.CHANNELS_FILE.read_text(encoding="utf-8")
    for bad in (["compta", "inconnu"], ["compta", "compta"], "compta,general", [42], None):
        assert ch.reorder(bad)["ok"] is False
    assert config.CHANNELS_FILE.read_text(encoding="utf-8") == before


def test_reclasser_reserve_au_hud_de_utilisateur(ch, monkeypatch):
    j, _ = make_astrel(monkeypatch)
    refused = j.on_command({"cmd": "channel_reorder", "order": ["compta", "studio", "general"], "_from_claude": True})
    assert refused["ok"] is False and [c.id for c in ch.items()] == ["general", "studio", "compta"]
    done = j.on_command({"cmd": "channel_reorder", "order": ["compta", "studio", "general"], "_from_claude": False})
    assert done["ok"] is True and [c.id for c in ch.items()] == ["compta", "studio", "general"]


def test_reclasser_en_rafale_ne_perd_aucun_canal(ch):
    """Relecture du 19/10 : chaque commande du HUD a son thread (Alt+→ maintenu). Les
    écritures croisées perdaient des canaux et « _aide » ; plus de .tmp résiduel."""
    import random
    import threading

    config.CHANNELS_FILE.write_text(json.dumps({**CONF, "_aide": "à garder"}), encoding="utf-8")
    ids = ["general", "studio", "compta"]
    errors: list = []

    def hammer(seed: int) -> None:
        rnd = random.Random(seed)
        for _ in range(25):
            order = ids[:]
            rnd.shuffle(order)
            try:
                res = ch.reorder(order)
                if not res["ok"]:
                    errors.append(res)
            except Exception as exc:  # FileNotFoundError sur tmp.replace avant le correctif
                errors.append(repr(exc))

    threads = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    data = json.loads(config.CHANNELS_FILE.read_text(encoding="utf-8"))
    assert sorted(e["id"] for e in data["channels"]) == sorted(ids) and data["_aide"] == "à garder"
    assert [p.name for p in config.CHANNELS_FILE.parent.iterdir() if p.suffix == ".tmp"] == []


@pytest.mark.parametrize("content", ["", '{"channels": [{"id": "general"', "[1, 2]", '{"default": "general"}'])
def test_fichier_illisible_jamais_reecrit(ch, content, caplog):
    config.CHANNELS_FILE.write_text(content, encoding="utf-8")
    # La liste lue retombe sur « Général » seul : c'est elle qu'il ne faut JAMAIS réécrire.
    for res in (ch.create("Nouveau"), ch.edit("general", label="Accueil")):
        assert res["ok"] is False and "illisible" in res["error"]
    assert ch.reorder(["general"]).get("changed") is not True and not ch.delete("studio")["ok"]
    assert config.CHANNELS_FILE.read_text(encoding="utf-8") == content  # rien d'écrasé
    assert "canaux NON modifiés" in caplog.text
