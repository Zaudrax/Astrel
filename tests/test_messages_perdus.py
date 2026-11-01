"""16/10, bug signalé par l'utilisateur : des messages affichés dans le HUD n'ont jamais atteint
le cerveau. Journal du 16/10 (12 h 31) : trois phrases DICTÉES (jamais envoyées, c'est la
dictée), puis passage au silencieux PENDANT l'écoute de la dictée ; la phrase suivante
(19 s, adressée à Astrel) a été dictée elle aussi, donc perdue.

Corrigé : une phrase entendue après un changement de mode suit le NOUVEAU mode ; un message
que le cœur n'a pas pu transmettre est marqué « non envoyé » (le HUD offre « Renvoyer »).
Et deux défauts mineurs des pièces jointes : une pièce refusée ne bloque plus les autres,
et le dépôt (`staged`) n'est plus vidé à tort."""
import base64

import pytest

from astrel import attachments, core
from astrel.events import bus
from test_coupures_pieces import events, hud, j, piece  # noqa: F401 (fixtures)
from test_reports import ch, astrel, of, published  # noqa: F401 (fixtures)


def ecoute(j, *steps):
    """Remplace l'écoute : chaque étape rend une phrase, après une action éventuelle
    (changement de mode en pleine écoute). Puis l'écoute est fermée (sinon la dictée
    attendrait DICTATION_IDLE_S)."""
    queue = list(steps)

    def listen_once(timeout=None, abort=None):
        if not queue:
            j._end_listen.set()
            return ""
        action, text = queue.pop(0)
        if action:
            action()
        return text

    j.listen_once = listen_once


@pytest.fixture
def dicte(j, monkeypatch):
    monkeypatch.setattr(j, "_copy_dictation", lambda: True, raising=False)
    return j


# ------------------------------------------------------------------ mode changé pendant l'écoute

def test_dictee_puis_silencieux_pendant_l_ecoute_la_phrase_part(dicte, events):
    j = dicte
    j._usage = "dictee"
    ecoute(j, (None, "On va commencer à aller dans le dur."),
           (lambda: j.on_command(hud("usage", mode="silencieux")), "Ah, j'ai un souci avec la dictée."))
    j.conversation(followup=False)
    assert j.usage == "silencieux"
    assert [t for t, _ in j.sent] == ["Ah, j'ai un souci avec la dictée."]  # partie chez Claude
    dictated = [e["text"] for e in events if e["type"] == "transcript" and e.get("source") == "dictée"]
    assert dictated == ["On va commencer à aller dans le dur."]  # la première reste une dictée
    assert j._dictation_text == "On va commencer à aller dans le dur."


def test_dictee_puis_conversation_pendant_l_ecoute(dicte):
    j = dicte
    j._usage = "dictee"
    ecoute(j, (lambda: j.on_command(hud("usage", mode="conversation")), "Quelle heure est-il ?"))
    j.conversation(followup=False)
    assert [t for t, _ in j.sent] == ["Quelle heure est-il ?"]


def test_conversation_puis_dictee_pendant_l_ecoute_rien_ne_part(dicte, events):
    j = dicte
    ecoute(j, (lambda: j.on_command(hud("usage", mode="dictee")), "Bonjour à tous."))
    j.conversation(followup=True)
    assert j.sent == []
    assert j._dictation_text == "Bonjour à tous."
    assert [e["source"] for e in events if e["type"] == "transcript"] == ["dictée"]


def test_dictee_sans_changement_de_mode_reste_une_dictee(dicte):
    j = dicte
    j._usage = "dictee"
    ecoute(j, (None, "Première phrase."), (None, "Deuxième phrase."))
    j.conversation(followup=False)
    assert j.sent == [] and j._dictation_text == "Première phrase. Deuxième phrase."


def test_dictee_rend_vide_sans_changement(dicte):
    j = dicte
    j._usage = "dictee"
    ecoute(j, (None, "Une phrase."))
    j.on_command(hud("usage", mode="dictee"))
    assert j._dictation() == ""


# ------------------------------------------------------------------ envoi raté : « non envoyé »

def test_envoi_rate_marque_le_message_non_envoye(astrel, published):
    j, _, _ = astrel

    def panne(*a, **k):
        raise RuntimeError("cerveau non démarré")

    j.brain.send = panne
    j.handle("Est-ce que tu avais fini les chantiers ?", "clavier", "general")
    shown = of(published, "transcript")[-1]
    unsent = of(published, "unsent")
    assert unsent and unsent[-1]["mid"] == shown["mid"] and "non démarré" in unsent[-1]["error"]
    kept = [e for e in bus.snapshot()["history"] if e.get("type") == "transcript" and e.get("mid") == shown["mid"]]
    assert kept and kept[0]["unsent"] is True  # un HUD qui se reconnecte le voit « non envoyé »


def test_envoi_reussi_rien_de_marque(astrel, published):
    j, _, _ = astrel
    j.handle("Bonjour", "clavier", "general")
    assert of(published, "unsent") == []


# ------------------------------------------------------------------ pièces jointes : refus une à une

def b64(data):
    return base64.b64encode(data).decode()


def test_prepare_partiel_garde_les_pieces_valides(monkeypatch, tmp_path):
    monkeypatch.setattr(attachments, "ATTACH_DIR", tmp_path / "att")
    p = attachments.prepare([{"id": "v", "name": "vide.txt", "data": ""},
                             {"id": "ok", "name": "note.txt", "type": "text/plain", "data": b64(b"bonjour")}],
                            partial=True)
    assert [s["name"] for s in p.shown] == ["note.txt"]
    assert p.refused == [{"id": "v", "name": "vide.txt", "error": "vide.txt : fichier vide"}]
    with pytest.raises(ValueError):  # sans `partial` : inchangé, une pièce refusée refuse tout
        attachments.prepare([{"name": "vide.txt", "data": ""}])


def test_phrase_dite_une_piece_vide_ne_bloque_pas_l_autre(j, events):
    j.on_command(hud("attach_stage", attachments=[piece("vide", "vide.txt", b""), piece("a1")]))
    ecoute(j, (None, "Regarde."))
    j.conversation(followup=True)
    text, joined = j.sent[0]
    assert text == "Regarde." and [s["name"] for s in joined.shown] == ["note.txt"]
    assert [e["ids"] for e in events if e["type"] == "attachments_sent"] == [["a1"]]  # seule la valide quitte la barre
    assert [i["id"] for i in j._attach["staged"]] == ["vide"] and "vide" not in j._attach["spent"]
    assert any(e["type"] == "notice" and "vide.txt" in e["text"] for e in events)


def test_entree_une_piece_refusee_seule_la_valide_part(j):
    j.on_command(hud("attach_stage", attachments=[piece("vide", "vide.txt", b""), piece("a1")]))
    typed = []
    j.handle = lambda text, source, channel, joined=None: typed.append((text, joined))
    r = j.on_command(hud("say", text="tiens", attachments=[piece("vide", "vide.txt", b""), piece("a1")]))
    assert r["ok"] and r["attachments"] == [{"name": "note.txt", "image": False}]
    assert [x["id"] for x in r["refused"]] == ["vide"]
    # Le dépôt garde la pièce refusée (encore dans la barre), pas celle partie.
    assert [i["id"] for i in j._attach["staged"]] == ["vide"]
    assert "a1" in j._attach["spent"] and "vide" not in j._attach["spent"]


def test_entree_toutes_refusees_le_depot_n_est_pas_vide(j):
    gros = piece("big", "gros.bin", b"0" * (attachments.MAX_FILE_BYTES + 1))
    j.on_command(hud("attach_stage", attachments=[gros]))
    j.handle = lambda *a, **kw: pytest.fail("rien ne devait partir")
    r = j.on_command(hud("say", text="regarde", attachments=[gros]))
    assert not r["ok"] and "gros.bin" in r["error"]
    # Avant le 16/10 : `staged` vidé ici alors que la pièce restait dans la barre.
    assert [i["id"] for i in j._attach["staged"]] == ["big"] and "big" not in j._attach["spent"]


def test_entree_refus_ne_fait_pas_revenir_une_piece_retiree(j):
    """La pièce refusée revient au dépôt, sans doublon ni chemin (dépôt = contenu du HUD)."""
    j.on_command(hud("attach_stage", attachments=[piece("vide", "vide.txt", b"")]))
    j.on_command(hud("say", text="x", attachments=[piece("vide", "vide.txt", b"")]))
    j.on_command(hud("say", text="x", attachments=[piece("vide", "vide.txt", b"")]))
    assert [i["id"] for i in j._attach["staged"]] == ["vide"]


# ------------------------------------------------------------------ relecture du 16/10

def panne(*a, **k):
    raise RuntimeError("cerveau non démarré")


def test_envoi_rate_rend_les_pieces_jointes_entree(astrel, published, monkeypatch, tmp_path):
    """Entrée : la pièce marquée « partie » puis l'envoi qui échoue. Avant, elle restait
    « partie » (le HUD l'avait ôtée de la barre, une nouvelle Entrée la jetait) : perdue."""
    j, _, _ = astrel
    monkeypatch.setattr(attachments, "ATTACH_DIR", tmp_path / "att")
    fresh, claimed = j._claim_attachments([piece("a1")])
    assert claimed == ["a1"] and "a1" in j._attach["spent"]
    joined = attachments.prepare(fresh, partial=True)
    assert joined.ids == ["a1"]
    j.brain.send = panne
    j.handle("Regarde.", "clavier", "general", joined=joined)
    assert "a1" not in j._attach["spent"]
    assert of(published, "unsent")[-1]["attachments"] == ["a1"]  # le HUD la remet dans la barre
    again, _ = j._claim_attachments([piece("a1")])  # Entrée de nouveau : elle part
    assert [i["id"] for i in again] == ["a1"]


def test_envoi_rate_rend_les_pieces_jointes_phrase_dite(astrel, published, monkeypatch, tmp_path):
    j, _, _ = astrel
    monkeypatch.setattr(attachments, "ATTACH_DIR", tmp_path / "att")
    j._stage_attachments(hud("attach_stage", attachments=[piece("a1"), piece("a2")]))
    joined = j._take_staged()
    assert sorted(j._attach["spent"]) == ["a1", "a2"]
    j.brain.send = panne
    j.handle("Regarde.", joined=joined)
    assert list(j._attach["spent"]) == []
    assert of(published, "unsent")[-1]["attachments"] == ["a1", "a2"]
    # Le HUD les redépose : elles repartent avec la phrase suivante.
    j._stage_attachments(hud("attach_stage", attachments=[piece("a1"), piece("a2")]))
    assert [i["id"] for i in j._attach["staged"]] == ["a1", "a2"]


def test_envoi_reussi_les_pieces_restent_parties(astrel, published, monkeypatch, tmp_path):
    j, _, _ = astrel
    monkeypatch.setattr(attachments, "ATTACH_DIR", tmp_path / "att")
    fresh, _ = j._claim_attachments([piece("a1")])
    j.handle("Regarde.", "clavier", "general", joined=attachments.prepare(fresh, partial=True))
    assert "a1" in j._attach["spent"] and of(published, "unsent") == []


def test_envoi_rate_sans_piece_jointe_rien_a_rendre(astrel, published):
    j, _, _ = astrel
    j.brain.send = panne
    j.handle("Bonjour", "clavier", "general")
    assert "attachments" not in of(published, "unsent")[-1]


def historique(mid):
    return [e for e in bus.snapshot()["history"] if e.get("type") == "transcript" and e.get("mid") == mid]


@pytest.mark.parametrize("source, unsent", [("voix", True), ("dictée", False)])
def test_renvoi_efface_non_envoye_de_l_historique(j, events, source, unsent):
    """« Renvoyer » (message raté) ou « Envoyer » (dictée) : un HUD rechargé ne doit plus
    proposer de le renvoyer, sinon un clic crée un doublon."""
    bus.publish("transcript", who="user", text="Tu avais fini ?", source=source, channel="general", mid="m-old")
    if unsent:
        bus.update_history(lambda ev: ev.get("type") == "transcript" and ev.get("mid") == "m-old", unsent=True)
    r = j.on_command(hud("say", text="Tu avais fini ?", channel="general", resend_of="m-old"))
    assert r["ok"]
    kept = historique("m-old")
    assert kept and kept[0]["resent"] is True and not kept[0].get("unsent")
    assert [e["mid"] for e in events if e["type"] == "resent"] == ["m-old"]  # les autres HUD ouverts suivent


def test_dictees_versees_dans_la_barre_partent_avec_le_lien(j, events):
    """17/10 : lien collé + deux dictées versées dans la barre du HUD = UN message ; le HUD
    nomme les deux dictées (`resend_of` en liste), aucune ne reste « à envoyer »."""
    for mid, text in (("d1", "Résume cette vidéo."), ("d2", "Et dis-moi ce que tu en penses.")):
        bus.publish("transcript", who="user", text=text, source="dictée", channel="general", mid=mid)
    r = j.on_command(hud("say", channel="general", resend_of=["d1", "d2", "d1", ""],
                         text="https://exemple.fr/video Résume cette vidéo. Et dis-moi ce que tu en penses."))
    assert r["ok"]
    assert all(historique(m)[0]["resent"] is True for m in ("d1", "d2"))
    assert [e["mid"] for e in events if e["type"] == "resent"] == ["d1", "d2"]


def test_renvoi_refuse_ne_touche_pas_l_historique(j, events):
    bus.publish("transcript", who="user", text="x", source="voix", channel="general", mid="m-old")
    bus.update_history(lambda ev: ev.get("type") == "transcript" and ev.get("mid") == "m-old", unsent=True)
    r = j.on_command(hud("say", text="", channel="general", resend_of="m-old"))
    assert not r["ok"] and historique("m-old")[0]["unsent"] is True


def test_renvoi_venant_de_claude_ne_touche_pas_l_historique(j, events):
    from test_coupures_pieces import claude
    bus.publish("transcript", who="user", text="x", source="voix", channel="general", mid="m-old")
    bus.update_history(lambda ev: ev.get("type") == "transcript" and ev.get("mid") == "m-old", unsent=True)
    j.on_command(claude("say", text="x", channel="general", resend_of="m-old"))
    assert historique("m-old")[0]["unsent"] is True and not any(e["type"] == "resent" for e in events)
