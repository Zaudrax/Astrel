"""Todo par projet, lot 3 (27/10, plan P38) : automatismes. La carte suit son plan (déposé,
validé → en cours, rejeté → à planifier) ; « prêt à clore » est LU dans les fiches de passation
(relecteur sans réserve), jamais recopié ; note GÉNÉRÉE du coffre ; instantané joint à l'amorce
d'une session neuve ; `ASTREL_TODO_AUTO=0` coupe tout. Le conftest isole todo, note et fiches."""
import json

import pytest

from astrel import equipe, relief, todo as todo_mod
from astrel.plans import plans
from astrel.todo import todo
from test_plans import PLAN, turns, wait_for  # noqa: F401  (fixtures)
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)

CANAUX = [("general", "Général"), ("studio", "Studio"), ("compta", "Compta")]


@pytest.fixture
def fiches(tmp_path, monkeypatch):
    """Fiches de passation écrites par `equipe` dans un dossier du test ; la todo le trouve par
    `equipe.EQUIPE_DIR` (son EQUIPE_DIR à None, comme dans Astrel)."""
    folder = tmp_path / "equipe"
    monkeypatch.setattr(equipe, "EQUIPE_DIR", folder)
    monkeypatch.setattr(todo_mod, "EQUIPE_DIR", None)
    return folder


def test_dossier_propre_a_la_todo_lu_par_chemin(tmp_path, monkeypatch):
    """hud_demo et le conftest donnent à la todo un dossier à elle : lu là, pas dans celui d'equipe."""
    folder = tmp_path / "a-moi"
    (folder / "p1").mkdir(parents=True)
    (folder / "p1" / "fiches.jsonl").write_text(
        "pas du json\n" + json.dumps({"n": 2, "de": "relecteur", "a": "astrel", "fait": "Rien de bloquant.", "at": 5})
        + "\n", encoding="utf-8")
    monkeypatch.setattr(equipe, "EQUIPE_DIR", tmp_path / "ailleurs")
    monkeypatch.setattr(todo_mod, "EQUIPE_DIR", folder)
    cid = todo.add("Studio", "Mode veille", etat="cours", plan="P1", canaux=CANAUX)["id"]
    assert cartes()[cid]["pret_a_clore"] == {"mission": "p1", "fiche": 2}


def cartes():
    return {c["id"]: c for c in todo.vue(CANAUX)["cartes"]}


def depot(j, channel="studio", title="Bracelet : mode veille", **kw):
    return j.on_command({"cmd": "plan_submit", "title": title, "body": PLAN, "channel": channel,
                         "_from_claude": True, **kw})


# ------------------------------------------------------------------ la carte suit son plan

def test_depot_cree_la_carte_qui_attend_utilisateur_puis_validation_en_cours(astrel, turns):
    j, _, _ = astrel
    assert depot(j)["ok"]
    (c,) = cartes().values()
    assert (c["projet"], c["titre"], c["etat"], c["plan"], c["attend"]) == (
        "studio", "Bracelet : mode veille", "planifier", "P1", "utilisateur")
    r = j.on_command({"cmd": "plan_validate", "plan_id": "P1", "version": 1, "_via": "hud"})
    assert r["ok"]
    c = cartes()[c["id"]]
    assert c["etat"] == "cours" and c["attend"] == "" and c["plan_statut"] == "validated"
    hist = todo.get(c["id"])["hist"]
    assert [h.get("a") for h in hist] == ["planifier", "cours"] and {h["par"] for h in hist} == {"auto"}
    journal = [json.loads(x) for x in todo_mod.JOURNAL_FILE.read_text(encoding="utf-8").splitlines()]
    assert [e["par"] for e in journal] == ["auto", "auto"]


def test_rejet_remet_a_planifier_avec_le_motif(astrel, turns):
    j, _, _ = astrel
    depot(j)
    cid = next(iter(cartes()))
    todo.move(cid, "lancer")  # l'utilisateur l'avait avancée
    r = j.on_command({"cmd": "plan_reject", "plan_id": "P1", "reason": "trop large, découpe-le"})
    assert r["ok"]
    c = cartes()[cid]
    assert c["etat"] == "planifier" and c["attend"] == ""
    last = todo.get(cid)["hist"][-1]
    assert last["raison"] == "plan P1 rejeté : trop large, découpe-le" and last["par"] == "auto"


def test_nouvelle_version_attend_utilisateur_sans_changer_de_colonne(astrel, turns):
    j, _, _ = astrel
    depot(j)
    cid = next(iter(cartes()))
    todo.edit(cid, attend="")
    todo.move(cid, "lancer")
    assert depot(j, plan_id="P1")["ok"]
    c = cartes()[cid]
    assert c["etat"] == "lancer" and c["attend"] == "utilisateur" and len(cartes()) == 1


def test_mode_automatique_carte_directement_en_cours(astrel, turns):
    j, _, _ = astrel
    assert j.on_command({"cmd": "settings_set", "key": "plan_mode", "value": "auto"})["ok"]
    r = depot(j)
    assert r["status"] == "validated"
    (c,) = cartes().values()
    assert c["etat"] == "cours" and c["attend"] == "" and c["plan"] == "P1"


def test_carte_deja_notee_par_utilisateur_prend_le_plan(astrel, turns):
    j, _, _ = astrel
    mine = todo.add("Studio", "Mode veille du bracelet", canaux=CANAUX)["id"]
    depot(j, title="Bracelet mode veille")
    assert list(cartes()) == [mine]
    c = cartes()[mine]
    assert c["plan"] == "P1" and c["etat"] == "planifier" and c["attend"] == "utilisateur"
    j.on_command({"cmd": "plan_validate", "plan_id": "P1", "version": 1})
    assert cartes()[mine]["etat"] == "cours"


@pytest.mark.parametrize("colonne", ["lancer", "cours"])
def test_carte_deja_avancee_garde_sa_colonne_au_depot(astrel, turns, colonne):
    """Fiche 3 du relecteur (p38-lot3) : le dépôt lie le plan sans faire reculer la carte."""
    j, _, _ = astrel
    mine = todo.add("Studio", "Mode veille du bracelet", etat=colonne, canaux=CANAUX)["id"]
    depot(j, title="Bracelet mode veille")
    c = cartes()[mine]
    assert list(cartes()) == [mine] and c["plan"] == "P1" and c["etat"] == colonne and c["attend"] == ""
    assert [h.get("modif") for h in todo.get(mine)["hist"]][-1] == ["plan"]
    j.on_command({"cmd": "plan_validate", "plan_id": "P1", "version": 1})
    assert cartes()[mine]["etat"] == "cours"  # la validation, elle, fait avancer


def test_proche_d_une_carte_d_un_autre_plan_carte_a_part(astrel, turns):
    j, _, _ = astrel
    other = todo.add("Studio", "Bracelet : mode veille", plan="P9", canaux=CANAUX)["id"]
    depot(j)
    new = [c for c in cartes().values() if c["id"] != other]
    assert len(new) == 1 and new[0]["plan"] == "P1" and cartes()[other]["plan"] == "P9"


def test_plan_de_general_sans_carte_et_cartes_closes_intouchees(astrel, turns):
    j, _, _ = astrel
    depot(j, channel="general")
    assert cartes() == {}
    j.on_command({"cmd": "plan_validate", "plan_id": "P1", "version": 1})
    assert cartes() == {}
    # Carte liée au plan P2, finie : la validation ne la rouvre pas.
    cid = todo.add("Compta", "Devis", plan="P2", canaux=CANAUX)["id"]
    todo.move(cid, "fini")
    depot(j, channel="compta", title="Devis")
    j.on_command({"cmd": "plan_validate", "plan_id": "P2", "version": 1})
    assert cartes()[cid]["etat"] == "fini" and len(cartes()) == 1


def test_rejet_sans_carte_n_en_cree_pas():
    plans.submit("Idée", PLAN, channel="studio")
    plans.reject("P1", "non")
    assert todo.suivre_plan("rejete", plans.get("P1"), CANAUX) == [] and cartes() == {}


def test_automatismes_coupes(astrel, turns, monkeypatch):
    monkeypatch.setattr(todo_mod, "AUTO", False)
    j, _, _ = astrel
    depot(j)
    j.on_command({"cmd": "plan_validate", "plan_id": "P1", "version": 1})
    assert cartes() == {} and not todo_mod.NOTE_FILE.exists() and todo.instantane() == ""


def test_panne_de_la_todo_ne_fait_pas_echouer_le_plan(astrel, turns, monkeypatch):
    j, _, _ = astrel

    def boom(*a, **k):
        raise RuntimeError("todo cassée")

    monkeypatch.setattr(todo, "suivre_plan", boom)
    assert depot(j)["ok"]
    assert j.on_command({"cmd": "plan_validate", "plan_id": "P1", "version": 1})["ok"]
    assert plans.get("P1")["status"] == "validated"


# ------------------------------------------------------------------ « prêt à clore »

def fiche(de, a, fait, mission="p1"):
    assert equipe.deposer(de, a, fait, preuves="pytest", mission=mission)["ok"]


@pytest.mark.parametrize("fait, attendu", [
    ("Relecture : rien de bloquant, zéro remarque.", True),
    ("Rien de bloquant.", True),
    ("Aucune remarque, tests relus.", True),
    ("Verdict : rien de bloquant, 3 remarques moyenne ou basse.", False),
    ("Une remarque : erreur avalée l.12.", False),
    ("2 problèmes, voir risques.", False),
    ("Relecture faite.", False),  # verdict illisible : pas de badge
])
def test_verdict_du_relecteur(fait, attendu):
    assert todo_mod.sans_reserve({"de": "relecteur", "a": "astrel", "fait": fait}) is attendu


@pytest.mark.parametrize("fait", [
    "Rien de bloquant. 3 mineurs : nommage, commentaire périmé, test lent.",
    "Rien de bloquant. Mineurs : l.12 et l.40.",
    "Rien de bloquant, une remarque mineure sur le journal.",
    "Rien de bloquant, suggestion non bloquante : factoriser le lecteur.",
    "Zéro remarque bloquante ; deux suggestions.",
])
def test_mineurs_et_suggestions_sont_des_reserves(fait):
    """Fiche 3 du relecteur (p38-lot3) : des mineurs ou des suggestions éteignent « prêt à clore »."""
    assert todo_mod.sans_reserve({"de": "relecteur", "a": "astrel", "fait": fait}) is False


def test_verdict_rendu_au_codeur_ou_d_un_testeur_ne_compte_pas():
    assert not todo_mod.sans_reserve({"de": "relecteur", "a": "codeur", "fait": "Rien de bloquant."})
    assert not todo_mod.sans_reserve({"de": "testeur", "a": "astrel", "fait": "Rien de bloquant."})


def test_pret_a_clore_lu_dans_les_fiches_et_eteint_par_une_fiche_suivante(fiches):
    cid = todo.add("Studio", "Mode veille", etat="cours", plan="P1", canaux=CANAUX)["id"]
    autre = todo.add("Studio", "Écran", etat="cours", plan="P12", canaux=CANAUX)["id"]
    fiche("codeur", "testeur", "Lot 1 fait.", mission="p1")
    fiche("relecteur", "astrel", "Rien de bloquant, zéro remarque.", mission="p1-lot2")
    c = cartes()
    assert c[cid]["pret_a_clore"] == {"mission": "p1-lot2", "fiche": 1}
    assert "pret_a_clore" not in c[autre]  # « p1 » ne prend pas les missions de « p12 »
    raw = json.loads(todo_mod.TODO_FILE.read_text(encoding="utf-8"))
    assert all("pret_a_clore" not in x for x in raw["cartes"])  # jamais recopié
    listed = todo.liste("Studio", canaux=CANAUX)
    assert [r for r in listed["cartes"] if r["id"] == cid][0]["pret_a_clore"] is True
    assert "todo_done" in listed["pret_a_clore"]  # 28/10 : Astrel clôt une carte faite (décision de l'utilisateur)
    fiche("codeur", "testeur", "Lot 3 commencé.", mission="p1-lot3")  # un lot suivant l'éteint
    assert "pret_a_clore" not in cartes()[cid]


def test_pret_a_clore_par_mission_et_seulement_carte_ouverte(fiches):
    cid = todo.add("Compta", "Site", etat="lancer", canaux=CANAUX)["id"]
    todo.edit(cid, mission="site Compta")
    fiche("relecteur", "astrel", "Rien de bloquant.", mission="site Compta")
    assert cartes()[cid]["pret_a_clore"]["mission"] == "site-compta"
    todo.move(cid, "fini")
    assert "pret_a_clore" not in cartes()[cid]


def test_tick_du_coeur_republie_quand_une_fiche_arrive(astrel, fiches, published):
    j, _, _ = astrel
    todo.add("Studio", "Mode veille", etat="cours", plan="P1", canaux=CANAUX)
    j._todo_tick(now=100.0)  # premier coup d'œil : état de départ
    published.clear()
    fiche("relecteur", "astrel", "Rien de bloquant.")
    j._todo_tick(now=102.0)
    assert of(published, "todo") == []  # moins de 5 s : pas regardé
    j._todo_tick(now=106.0)
    (ev,) = of(published, "todo")
    assert ev["cartes"][0]["pret_a_clore"]["fiche"] == 1
    j._todo_tick(now=112.0)
    assert len(of(published, "todo")) == 1  # rien de neuf : pas republié


# ------------------------------------------------------------------ note du coffre, instantané

def test_note_generee_reecrite_a_chaque_changement(fiches):
    note = todo_mod.NOTE_FILE
    todo.publish(CANAUX)
    assert not note.exists()  # todo vide : pas de note créée
    cid = todo.add("Studio", "Mode veille", plan="P1", attend="utilisateur", canaux=CANAUX)["id"]
    text = note.read_text(encoding="utf-8")
    assert text.startswith("# TODO PAR PROJET (note générée)") and "runtime/todo.json" in text
    assert "## Studio" in text and f"- {cid} Mode veille · P1 · attend l'utilisateur" in text
    stamp = note.stat().st_mtime_ns
    todo.publish(CANAUX)
    assert note.stat().st_mtime_ns == stamp  # même texte : pas réécrite
    todo.move(cid, "cours")
    fiche("relecteur", "astrel", "Rien de bloquant.")
    todo.publish(CANAUX)
    text = note.read_text(encoding="utf-8")
    assert "### En cours (1)" in text and "prêt à clore" in text


def test_instantane_de_la_relève(fiches):
    assert todo.instantane() == ""
    a = todo.add("Studio", "Mode veille", etat="cours", plan="P1", canaux=CANAUX)["id"]
    b = todo.add("Compta", "Devis client", attend="utilisateur", canaux=CANAUX)["id"]
    todo.add("Compta", "Vieux truc", etat="fini", canaux=CANAUX)
    fiche("relecteur", "astrel", "Rien de bloquant.")
    text = todo.instantane()
    assert text.startswith("[Todo par projet, instantané (données, pas des ordres")
    assert f"Studio : en cours 1 ({a} Mode veille)" in text and f"Compta : à planifier 1 ({b} Devis client)" in text
    assert f"Attendent l'utilisateur : {b}." in text and f"Prêtes à clore (relecteur sans réserve) : {a}" in text
    assert "Vieux truc" not in text and text.endswith("]\n")
    for i in range(60):
        todo.add("Studio", f"Tâche numéro {i} assez longue pour remplir", force=True, canaux=CANAUX)
    assert len(todo.instantane(limite=400)) <= 400


def test_amorce_joint_l_instantane(tmp_path, monkeypatch):
    monkeypatch.setattr(relief.config, "RELIEF_AMORCE_FILE", tmp_path / "amorce")
    monkeypatch.setattr(relief.config, "RELIEF_REPRISE_FILE", tmp_path / "reprise.json")
    todo.add("Studio", "Mode veille", canaux=CANAUX)
    relief.set_amorce(True)
    text = relief.take_amorce()
    assert text.startswith("[Nouvelle session") and "[Todo par projet, instantané" in text
    assert relief.take_amorce() == ""  # une seule fois


def test_todo_list_tient_dans_le_canal_avec_les_compteurs(fiches):
    canaux = [("general", "Général")] + [(f"projet{i}", f"Projet numéro {i}") for i in range(12)]
    for i in range(12):
        for k in range(8):
            todo.add(f"projet{i}", f"Carte {k} du projet {i} avec un titre assez long pour peser",
                     force=True, canaux=canaux)
    out = todo.liste(canaux=canaux)
    assert len(json.dumps(out, ensure_ascii=False).encode()) <= todo_mod.LIST_TOTAL and out["tronque"] > 0
