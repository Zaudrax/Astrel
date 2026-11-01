"""Mode plan (15/10, docs/CONTRAT-PLANS-2026-10-15.md) : dépôt par le planificateur,
découpage en sections et points, relecture point par point, révision, validation par
l'utilisateur SEULE (jamais par Claude), mode automatique, tours « plan » jamais dits."""
import json
import time

import pytest

from astrel import agents, brain, config, core, events, mcp_server, silence
from astrel.events import bus
from astrel.plans import BODY_MAX, plans, split
from astrel.settings import ITEMS, reglages
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)

PLAN = """# Ajout du mode plan

Contexte en une phrase.

## Étapes
1. Écrire le module plans.
2. Brancher les commandes du cœur
   avec le refus à Claude.
3. Ajouter les tests.

## Fichiers
- astrel/plans.py : nouveau.
  - sous-point gardé avec son parent
- astrel/core.py : commandes.

## Risques
Intro des risques.

- Prompt trop long.

Un paragraphe à part.

## Lots
- Un seul codeur.
```
code - pas un point
```

## Vérification
- pytest -q
"""

ITEM_FIELDS = {"id", "title", "channel", "status", "version", "points", "remarks", "created", "updated", "by"}
PLAN_FIELDS = ITEM_FIELDS | {"request", "body", "sections", "thread", "reject_reason"}
HUD_COMMANDS = ("plans_get", "plan_get", "plan_comment", "plan_revise", "plan_validate", "plan_reject")


def submit(**kw):
    args = {"title": "Mode plan", "body": PLAN, "request": "fais un plan pour le mode plan", **kw}
    return plans.submit(args.pop("title"), args.pop("body"), args.pop("request"), **args)


@pytest.fixture
def turns(astrel):
    """Les tours envoyés au cerveau par le cœur (`_plan_turn` les lance dans un fil)."""
    j, _, _ = astrel
    calls = []
    j.handle = lambda text, source="voix", channel="", shown=None, joined=None: calls.append(
        {"text": text, "source": source, "channel": channel, "shown": shown})
    return calls


def wait_for(calls, n=1):
    deadline = time.time() + 2
    while len(calls) < n and time.time() < deadline:
        time.sleep(0.01)
    return calls


# ------------------------------------------------------------------ découpage

def test_decoupage_en_sections_et_points():
    sections = split(PLAN)
    assert [s["title"] for s in sections] == ["Ajout du mode plan", "Étapes", "Fichiers", "Risques", "Lots",
                                              "Vérification"]
    assert sections[0]["intro"] == "Contexte en une phrase." and sections[0]["points"] == []
    steps = sections[1]["points"]
    assert [p["id"] for p in steps] == ["p1", "p2", "p3"]
    assert steps[0]["text"] == "1. Écrire le module plans."  # numérotation gardée
    assert steps[1]["text"] == "2. Brancher les commandes du cœur\navec le refus à Claude."
    files = sections[2]["points"]
    assert [p["id"] for p in files] == ["p4", "p5"] and "sous-point gardé" in files[0]["text"]
    risks = sections[3]
    assert risks["intro"] == "Intro des risques."
    assert [p["text"] for p in risks["points"]] == ["Prompt trop long.", "Un paragraphe à part."]
    lots = sections[4]["points"]
    assert len(lots) == 1 and "code - pas un point" in lots[0]["text"]  # un bloc de code reste entier
    assert sections[5]["points"] == [{"id": "p9", "text": "pytest -q"}]


# ------------------------------------------------------------------ dépôt, stockage, numérotation

def test_depot_ecrit_json_et_md_et_publie(published):
    r = submit(channel="studio")
    assert r == {"ok": True, "id": "P1", "version": 1, "status": "pending", "points": 9, "new": True}
    data = json.loads((config.PLANS_DIR / "P1.json").read_text(encoding="utf-8"))
    assert data["status"] == "pending" and data["versions"][0]["body"].startswith("# Ajout")
    md = (config.PLANS_DIR / "P1.md").read_text(encoding="utf-8")
    assert md.startswith("# Plan P1 : Mode plan (version 1)") and "Demande de l'utilisateur : fais un plan" in md
    assert not list(config.PLANS_DIR.glob("*.tmp"))  # écriture atomique
    sticky = bus.last("plans")
    assert "plans" in events.STICKY and sticky["mode"] == "manual" and sticky["pending"] == 1
    assert set(sticky["items"][0]) == ITEM_FIELDS and sticky["items"][0]["by"] == ""
    ev = of(published, "plan")[-1]
    assert ev["new"] is True and set(ev["plan"]) >= PLAN_FIELDS and ev["plan"]["channel"] == "studio"
    assert ev["plan"]["sections"][1]["points"][0] == {"id": "p1", "text": "1. Écrire le module plans."}


def test_numerotation_apres_relance():
    submit()
    submit()
    (config.PLANS_DIR / "P7.md").write_text("reste d'un plan", encoding="utf-8")  # un .md seul compte aussi
    (config.PLANS_DIR / "P5.json").write_text("{illisible", encoding="utf-8")
    plans.reset()  # relance d'Astrel
    assert [i["id"] for i in plans.describe()["items"]] == ["P2", "P1"]  # le fichier illisible est ignoré
    assert submit()["id"] == "P8"


@pytest.mark.parametrize("kw, why", [
    ({"title": "  "}, "titre vide"),
    ({"body": ""}, "plan vide"),
    ({"body": "- x\n" * (BODY_MAX // 4 + 1)}, "trop long"),
    ({"body": "Juste un paragraphe, aucune liste."}, "aucun point"),
])
def test_depot_refuse(kw, why):
    r = submit(**kw)
    assert not r["ok"] and why in r["error"]
    assert not config.PLANS_DIR.exists() or not list(config.PLANS_DIR.iterdir())


# ------------------------------------------------------------------ relecture, révision, validation

def test_remarque_revision_nouvelle_version_validation():
    submit()
    assert plans.comment("P1", "p2", "Ajoute le test du refus.")["ok"]
    assert not plans.comment("P1", "p99", "x")["ok"] and not plans.comment("P1", "", " ")["ok"]
    assert plans.get("P1")["remarks"] == 1
    r = plans.revise("P1")
    assert r["ok"] and r["count"] == 1 and plans.get("P1")["status"] == "revising"
    assert "point p2 « 2. Brancher les commandes du cœur avec le refus à Claude. »" in r["message"]
    assert "Ajoute le test du refus." in r["message"] and 'plan_submit(plan_id="P1"' in r["message"]
    assert "[SILENT]" in r["message"] and "\n" not in r["shown"]
    assert not plans.revise("P1")["ok"]  # plus rien à envoyer
    v = plans.validate("P1", 1)
    assert not v["ok"] and "révision est en cours" in v["error"]
    assert plans.reply("P1", "Bien vu, c'est ajouté.", "p2")["ok"]
    r2 = submit(plan_id="P1", body=PLAN + "- test du refus à Claude\n")
    assert r2["version"] == 2 and r2["status"] == "pending" and not r2["new"]
    stale = plans.validate("P1", 1)
    assert not stale["ok"] and "version périmée" in stale["error"] and stale["version"] == 2
    plans.comment("P1", "p1", "Garde le style du projet.")  # jamais envoyée : rejoint le .md
    ok = plans.validate("P1", "2")
    assert ok["ok"] and ok["remarks"] == 1 and ok["by"] == "utilisateur"
    assert ok["message"].startswith("Plan P1 validé : lance UN SEUL codeur qui lit ")
    assert str(config.PLANS_DIR / "P1.md") in ok["message"]
    md = (config.PLANS_DIR / "P1.md").read_text(encoding="utf-8")
    assert "(version 2)" in md and "## Remarques de l'utilisateur à respecter" in md and "Garde le style" in md
    full = plans.get("P1")
    assert full["status"] == "validated" and full["by"] == "utilisateur" and full["remarks"] == 0
    assert [t["who"] for t in full["thread"]] == ["utilisateur", "astrel", "utilisateur"]
    assert all(t["sent"] for t in full["thread"]) and full["thread"][0]["version"] == 1
    assert not plans.validate("P1", 2)["ok"]  # déjà validé
    assert not submit(plan_id="P1")["ok"]  # plan clos : pas de version 3


def test_rejet():
    submit()
    assert plans.reject("P1", "Trop large, découpe d'abord.")["ok"]
    full = plans.get("P1")
    assert full["status"] == "rejected" and full["reject_reason"] == "Trop large, découpe d'abord."
    assert full["by"] == "utilisateur"
    assert not plans.validate("P1", 1)["ok"] and not plans.comment("P1", "p1", "x")["ok"]


def test_plan_list_tient_dans_la_reponse_du_canal():
    for _ in range(30):
        submit(title="Un titre assez long pour peser dans la réponse du canal de contrôle " * 2)
    r = plans.compact()
    assert r["total"] == 30 and r["plans"][0]["id"] == "P30" and len(r["plans"]) <= 20
    assert len(json.dumps(r, ensure_ascii=False).encode()) < 4096


# ------------------------------------------------------------------ commandes du cœur

@pytest.mark.parametrize("name", HUD_COMMANDS)
def test_commandes_du_hud_refusees_a_claude(astrel, turns, name):
    j, _, _ = astrel
    submit()
    r = j.on_command({"cmd": name, "plan_id": "P1", "version": 1, "point": "p1", "text": "x", "reason": "x",
                      "_from_claude": True, "_via": "socket"})
    assert r["ok"] is False and "refusé" in r["error"]
    assert plans.get("P1")["status"] == "pending" and plans.get("P1")["thread"] == [] and turns == []


def test_validation_par_le_hud_lance_un_tour_plan(astrel, turns, published):
    j, _, _ = astrel
    submit(channel="studio")
    assert j.on_command({"cmd": "plans_get"})["items"][0]["id"] == "P1"
    got = j.on_command({"cmd": "plan_get", "plan_id": "P1", "id": 17})  # « id » : celui de la requête WebSocket
    assert got["ok"] and set(got["plan"]) >= PLAN_FIELDS
    assert j.on_command({"cmd": "plan_comment", "plan_id": "P1", "point": "p3", "text": "Note."})["ok"]
    assert turns == []  # une remarque seule n'appelle pas le modèle
    r = j.on_command({"cmd": "plan_validate", "plan_id": "P1", "version": 1, "_via": "hud"})
    assert r["ok"] and "message" not in r
    call = wait_for(turns)[0]
    assert call["source"] == "plan" and call["channel"] == "studio"
    assert call["text"].startswith("Plan P1 validé : lance UN SEUL codeur")
    assert call["shown"] == "Plan P1 validé : lancement d'un codeur."


def test_question_immediate_et_revision(astrel, turns):
    j, _, _ = astrel
    submit()
    j.on_command({"cmd": "plan_comment", "plan_id": "P1", "point": "p1", "text": "Pourquoi ?", "ask": True})
    ask = wait_for(turns)[0]
    assert ask["source"] == "plan" and "Pourquoi ?" in ask["text"] and "plan_reply" in ask["text"]
    assert plans.get("P1")["remarks"] == 0  # déjà partie
    assert not j.on_command({"cmd": "plan_revise", "plan_id": "P1"})["ok"]  # rien d'autre à envoyer
    j.on_command({"cmd": "plan_comment", "plan_id": "P1", "point": "", "text": "Découpe en deux lots."})
    assert j.on_command({"cmd": "plan_revise", "plan_id": "P1"})["ok"]
    rev = wait_for(turns, 2)[1]
    assert rev["text"].startswith("Révision du plan P1") and rev["shown"] == "Plan P1 : révision demandée (1 remarque)."


def test_validation_refusee_sous_arret_d_urgence(astrel, turns):
    j, _, _ = astrel
    submit()
    config.ESTOP_FILE.write_text("x", encoding="utf-8")
    r = j.on_command({"cmd": "plan_validate", "plan_id": "P1", "version": 1})
    assert not r["ok"] and "arrêt d'urgence" in r["error"] and plans.get("P1")["status"] == "pending"


def test_commandes_au_terminal(astrel, turns):
    j, _, _ = astrel
    submit()
    assert j.on_command({"cmd": "plan_comment P1 p2 une remarque"})["remark"]["point"] == "p2"
    assert j.on_command({"cmd": "plan_comment P1 sur tout le plan"})["remark"]["point"] == ""
    assert not j.on_command({"cmd": "plan_validate P1 3"})["ok"]
    assert j.on_command({"cmd": "plan_validate P1 1"})["ok"]


def test_outils_cote_claude(astrel, turns, published):
    j, _, _ = astrel
    r = j.on_command({"cmd": "plan_submit", "title": "T", "body": PLAN, "_from_claude": True})
    assert r["ok"] and r["id"] == "P1" and "Rends seulement « Plan P1 déposé »" in r["message"]
    assert "page Plans" in of(published, "notice")[-1]["text"] and turns == []  # manuel : rien ne part
    assert not j.on_command({"cmd": "plan_submit", "title": "T", "body": PLAN, "channel": "zzz"})["ok"]
    assert j.on_command({"cmd": "plan_reply", "plan_id": "P1", "text": "Réponse", "_from_claude": True})["ok"]
    listed = j.on_command({"cmd": "plan_list", "_from_claude": True})
    assert listed["plans"][0]["id"] == "P1" and listed["mode"] == "manual"


def test_mode_automatique(astrel, turns):
    j, _, _ = astrel
    r = j.on_command({"cmd": "settings_set", "key": "plan_mode", "value": "auto"})
    assert r["ok"] and bus.last("plans")["mode"] == "auto"
    r = j.on_command({"cmd": "plan_submit", "title": "T", "body": PLAN, "_from_claude": True})
    assert r["status"] == "validated" and "validé (mode automatique)" in r["message"]
    full = plans.get("P1")
    assert full["status"] == "validated" and full["by"] == "auto" and full["validated_by"] == "auto"
    call = wait_for(turns)[0]
    assert call["source"] == "plan" and call["text"].startswith("Plan P1 validé (mode automatique) :")
    config.ESTOP_FILE.write_text("x", encoding="utf-8")  # arrêt d'urgence : déposé, pas lancé
    r = j.on_command({"cmd": "plan_submit", "title": "T2", "body": PLAN, "_from_claude": True})
    assert r["status"] == "pending" and "arrêt d'urgence" in r["message"] and len(turns) == 1


def test_mode_auto_refuse_a_claude_par_les_reglages(astrel):
    j, _, _ = astrel
    r = j.on_command({"cmd": "settings_set", "key": "plan_mode", "value": "auto", "_from_claude": True})
    assert not r["ok"] and reglages.value("plan_mode") == "manual"


# ------------------------------------------------------------------ tours « plan » : jamais dits, pas dans le chat

def test_tour_plan_jamais_dit_et_silencieux(astrel, published):
    j, said, chimes = astrel
    assert "plan" in core._UNASKED and "plan" in silence.AUTO_SOURCES
    j.handle("Révision du plan P1 …", source="plan", channel="studio", shown="Plan P1 : révision demandée (1 remarque).")
    shown = [ev for ev in of(published, "transcript") if ev.get("source") == "plan"]
    assert len(shown) == 1 and shown[0]["text"] == "Plan P1 : révision demandée (1 remarque)."
    assert j._turn_source == "plan"
    j._on_stream(core.BrainEvent("text", text="[SILENT]"))
    j._on_stream(core.BrainEvent("turn_end", text="[SILENT]"))
    assert said == [] and chimes == [] and of(published, "reply") == []
    j.handle("Plan P2 validé : lance UN SEUL codeur…", source="plan", shown="Plan P2 validé : lancement d'un codeur.")
    j._on_stream(core.BrainEvent("text", text="Je lance le codeur sur le plan P2. "))
    j._on_stream(core.BrainEvent("turn_end"))
    assert said == []  # affiché, jamais dit


# ------------------------------------------------------------------ outils MCP, réglage, prompt, agent

def test_outils_mcp(monkeypatch):
    sent = []
    monkeypatch.setattr(mcp_server.state, "send_command", lambda raw, timeout=0: sent.append(raw) or '{"ok": true}')
    mcp_server.plan_submit("Mode plan", "## Étapes\n1. Créer l'élément", request="é" * 10)
    assert "Créer l'élément" in sent[-1] and "\\u00e9" not in sent[-1]  # ensure_ascii=False
    with pytest.raises(mcp_server.ToolError, match="trop long"):
        mcp_server.plan_submit("T", "x" * (BODY_MAX + 1))
    with pytest.raises(mcp_server.ToolError, match="octets"):
        mcp_server.plan_submit("T", "\U0001F600" * 2500)  # 6000 caractères permis, mais 10 Ko d'octets
    mcp_server.plan_reply("P1", "Réponse", "p2")
    assert json.loads(sent[-1]) == {"cmd": "plan_reply", "plan_id": "P1", "text": "Réponse", "point": "p2"}
    mcp_server.plan_list()
    assert json.loads(sent[-1]) == {"cmd": "plan_list"}
    for forbidden in ("plan_validate", "plan_reject", "plan_comment", "plan_revise"):
        assert not hasattr(mcp_server, forbidden)


def test_reglage_plan_mode(monkeypatch):
    item = ITEMS["plan_mode"]
    assert (item.group, item.kind, item.applies, item.env) == ("cerveau", "choice", "hot", "ASTREL_PLAN_MODE")
    assert [v for v, _ in item.choices] == ["manual", "auto"] and reglages.value("plan_mode") == "manual"
    monkeypatch.setenv("ASTREL_PLAN_MODE", "auto")
    monkeypatch.setattr(config, "PLAN_MODE", "auto")
    assert reglages.locked("plan_mode") and reglages.value("plan_mode") == "auto"


def test_consignes_du_prompt_et_du_planificateur():
    p = brain.VOICE_SYSTEM_PROMPT
    assert "page Plans" in p and "« Plan Pn validé »" in p and "plan_reply" in p and "« fais un plan »" in p
    plan = agents.load_agents()["planificateur"]
    assert "mcp__astrel__plan_submit" in plan["tools"] and "Plan Pn déposé" in plan["prompt"]
    # Plan P13 : rubriques dans la fiche de métier du chef de projet, lue au lancement.
    full = plan["prompt"] + "".join(p.read_text(encoding="utf-8") for p in agents.cited_fiches(plan["prompt"]))
    for rubric in ("## Étapes", "## Fichiers", "## Risques", "## Lots", "## Vérification", "## Propriétaires",
                   "## Objections non levées"):
        assert rubric in full
