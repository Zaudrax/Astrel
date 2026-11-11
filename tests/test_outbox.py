"""File des envois en attente (outbox) et brouillons sortants (09/11, carte T88).

Invariants vérifiés : l'envoi reste explicite (rien ne part sans « Accepter ») ; éditer change le
texte envoyé ; réviser regénère le brouillon par le moteur normal (aperçu avant envoi) ; accepter
envoie, refuser jette ; la file liste les en-attente sans jamais envoyer seule ; aucune réponse
WhatsApp automatique ; les décisions sont refusées à Claude, le dépôt lui est permis (il PROPOSE)."""
from __future__ import annotations

import json

import pytest

from astrel import config, core
from astrel.coeur import outbox as co
from astrel.outbox import Outbox, PENDING, REVISING, SENT, REFUSED


@pytest.fixture
def sends():
    """Canal factice : enregistre chaque (destinataire, texte) qu'on lui remet à envoyer."""
    return []


@pytest.fixture
def ob(sends):
    o = Outbox()
    o.register_channel("whatsapp", "WhatsApp", lambda to, text: sends.append((to, text)) or {"ok": True})
    return o


# ------------------------------------------------------------------ dépôt

def test_stage_cree_un_brouillon_en_attente(ob):
    r = ob.stage("whatsapp", "  Salut, on se voit demain ?  ", to="Léa")
    assert r["ok"] is True
    d = ob.get(r["id"])
    assert d["state"] == PENDING and d["text"] == "Salut, on se voit demain ?" and d["to"] == "Léa"
    assert ob.count_pending() == 1


def test_stage_refuse_canal_inconnu_et_message_vide(ob):
    assert ob.stage("courriel", "coucou")["ok"] is False  # pas de canal « courriel » branché
    assert ob.stage("whatsapp", "   ")["ok"] is False
    assert ob.count_pending() == 0


# ------------------------------------------------------------------ le dépôt n'envoie jamais

def test_deposer_editer_reviser_n_envoie_rien(ob, sends):
    did = ob.stage("whatsapp", "brouillon")["id"]
    ob.edit(did, "brouillon édité")
    ob.revise(did, "rends-le plus court", regen=lambda d, c: None)
    assert sends == []  # RIEN n'a été remis au canal tant que l'utilisateur n'a pas accepté
    assert ob.pending() and ob.view()  # la file les montre pourtant


# ------------------------------------------------------------------ éditer change le texte envoyé

def test_editer_change_le_texte_puis_accepter_envoie_la_version_editee(ob, sends):
    did = ob.stage("whatsapp", "version brute")["id"]
    ob.edit(did, "version formelle")
    assert ob.accept(did)["ok"] is True
    assert sends == [("", "version formelle")]  # c'est le texte ÉDITÉ qui part
    assert ob.get(did)["state"] == SENT


# ------------------------------------------------------------------ réviser regénère

def test_reviser_passe_en_revision_et_appelle_le_moteur(ob):
    did = ob.stage("whatsapp", "texte initial")["id"]
    vus = []
    ob.revise(did, "plus chaleureux", regen=lambda d, c: vus.append((d["id"], d["text"], c)))
    assert ob.get(did)["state"] == REVISING
    assert vus == [(did, "texte initial", "plus chaleureux")]  # le moteur reçoit le brouillon + la consigne


def test_apply_revision_remplace_le_texte_et_revient_en_attente(ob):
    did = ob.stage("whatsapp", "texte initial")["id"]
    ob.revise(did, "plus court", regen=lambda d, c: None)
    r = ob.apply_revision(did, "texte réécrit")
    assert r["ok"] is True
    d = ob.get(did)
    assert d["state"] == PENDING and d["text"] == "texte réécrit" and d["revisions"] == 1


def test_apply_revision_refuse_hors_revision(ob):
    did = ob.stage("whatsapp", "texte")["id"]  # en attente, pas en révision
    assert ob.apply_revision(did, "réécrit en douce")["ok"] is False
    assert ob.get(did)["text"] == "texte"  # Claude ne réécrit pas un brouillon que l'utilisateur regarde


def test_revise_remet_en_attente_si_le_moteur_echoue(ob):
    did = ob.stage("whatsapp", "texte")["id"]
    def boom(d, c):
        raise RuntimeError("moteur injoignable")
    assert ob.revise(did, "consigne", regen=boom)["ok"] is False
    assert ob.get(did)["state"] == PENDING  # pas coincé en « révision »


# ------------------------------------------------------------------ accepter / refuser

def test_accepter_envoie_une_seule_fois(ob, sends):
    did = ob.stage("whatsapp", "à envoyer")["id"]
    assert ob.accept(did)["ok"] is True
    assert sends == [("", "à envoyer")]
    assert ob.accept(did)["ok"] is False  # déjà envoyé : pas de second envoi
    assert len(sends) == 1


def test_refuser_jette_sans_envoyer(ob, sends):
    did = ob.stage("whatsapp", "à jeter")["id"]
    assert ob.refuse(did, "finalement non")["ok"] is True
    assert ob.get(did)["state"] == REFUSED
    assert sends == []
    assert ob.accept(did)["ok"] is False  # on ne peut plus l'envoyer


def test_accept_garde_le_brouillon_si_le_canal_echoue():
    o = Outbox()
    o.register_channel("whatsapp", "WhatsApp", lambda to, text: {"ok": False, "error": "pont coupé"})
    did = o.stage("whatsapp", "texte")["id"]
    r = o.accept(did)
    assert r["ok"] is False and "pont coupé" in r["error"]
    assert o.get(did)["state"] == PENDING  # reste en attente, réessayable


def test_vue_socket_tient_dans_4096_octets(ob):
    for i in range(30):
        ob.stage("whatsapp", "x" * 5000 + f" {i}")  # bien plus gros que la limite du canal de contrôle
    vue = ob.view(socket=True)
    assert len(vue) <= 10
    payload = json.dumps({"ok": True, "brouillons": vue}, ensure_ascii=False).encode()
    assert len(payload) < 4096


def test_la_file_liste_sans_envoyer(ob, sends):
    ob.stage("whatsapp", "un")
    ob.stage("whatsapp", "deux")
    vue = ob.view()
    assert [e["state"] for e in vue] == [PENDING, PENDING] and len(vue) == 2
    assert sends == []  # juste lister ne déclenche aucun envoi


# ------------------------------------------------------------------ wiring du cœur (OutboxCoeur)

def _coeur(monkeypatch, sends, *, offline=False, estop=False, tmp_path=None):
    """Astrel minimal avec une file neuve et un canal WhatsApp factice."""
    o = Outbox()
    o.register_channel("whatsapp", "WhatsApp", lambda to, text: sends.append((to, text)) or {"ok": True})
    monkeypatch.setattr(co, "outbox", o)
    monkeypatch.setattr(co.hors_ligne, "actif", lambda: offline)
    monkeypatch.setattr(co.audit, "record", lambda *a, **k: None)
    if tmp_path is not None:
        monkeypatch.setattr(co.config, "ESTOP_FILE", tmp_path / ("ESTOP" if estop else "pas-d-estop"))
        if estop:
            (tmp_path / "ESTOP").write_text("x")
    j = object.__new__(core.Astrel)
    j._off = False
    return j, o


def test_depot_permis_a_claude_decisions_refusees(monkeypatch, sends):
    j, o = _coeur(monkeypatch, sends)
    # Claude PROPOSE : permis.
    r = j._on_command({"cmd": "outbox_stage", "text": "coucou", "channel": "whatsapp", "_from_claude": True})
    assert r["ok"] is True and o.count_pending() == 1
    did = r["id"]
    # Claude ne peut ni accepter, ni refuser, ni éditer, ni réviser, ni lister.
    for name in ("outbox_accept", "outbox_refuse", "outbox_edit", "outbox_revise", "outbox_get"):
        rr = j._on_command({"cmd": name, "outbox_id": did, "_from_claude": True, "_via": "socket"})
        assert rr == {"ok": False, "error": co.OUTBOX_REFUS}, name
    assert sends == []  # et surtout : rien n'est parti


def test_accepter_bloque_hors_ligne(monkeypatch, sends):
    j, o = _coeur(monkeypatch, sends, offline=True)
    did = o.stage("whatsapp", "texte")["id"]
    r = j._on_command({"cmd": "outbox_accept", "outbox_id": did, "_via": "hud"})
    assert r["ok"] is False and "rien ne sort" in r["error"]
    assert sends == []


def test_reviser_bloque_sous_estop_et_hors_ligne(monkeypatch, sends, tmp_path):
    j, o = _coeur(monkeypatch, sends, estop=True, tmp_path=tmp_path)
    did = o.stage("whatsapp", "texte")["id"]
    r = j._on_command({"cmd": "outbox_revise", "outbox_id": did, "instruction": "plus court", "_via": "hud"})
    assert r["ok"] is False and "urgence" in r["error"]
    assert o.get(did)["state"] == PENDING  # pas de révision lancée


def test_reviser_lance_un_tour_du_moteur_normal(monkeypatch, sends, tmp_path):
    j, o = _coeur(monkeypatch, sends, tmp_path=tmp_path)
    tours = []
    monkeypatch.setattr(j, "handle", lambda text, **kw: tours.append((text, kw)), raising=False)
    did = o.stage("whatsapp", "bonjour")["id"]
    r = j._on_command({"cmd": "outbox_revise", "outbox_id": did, "instruction": "plus formel", "_via": "hud"})
    assert r["ok"] is True and o.get(did)["state"] == REVISING
    assert len(tours) == 1
    prompt, kw = tours[0]
    # Passe par le moteur normal (source l'utilisateur → routage/sensible), avec la consigne et le replace.
    assert kw["source"] == "clavier"
    assert "plus formel" in prompt and f'replace="{did}"' in prompt and "bonjour" in prompt


def test_accepter_via_hud_envoie(monkeypatch, sends, tmp_path):
    j, o = _coeur(monkeypatch, sends, tmp_path=tmp_path)
    did = o.stage("whatsapp", "à envoyer")["id"]
    r = j._on_command({"cmd": "outbox_accept", "outbox_id": did, "_via": "hud"})
    assert r["ok"] is True and sends == [("", "à envoyer")]


def test_stage_avec_replace_est_une_revision(monkeypatch, sends):
    j, o = _coeur(monkeypatch, sends)
    did = o.stage("whatsapp", "texte")["id"]
    o.revise(did, "plus court", regen=lambda d, c: None)
    r = j._on_command({"cmd": "outbox_stage", "text": "réécrit", "replace": did, "_from_claude": True})
    assert r["ok"] is True
    assert o.get(did)["state"] == PENDING and o.get(did)["text"] == "réécrit"
