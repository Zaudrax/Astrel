"""Rangement automatique de la todo (28/10, carte T31, demande de l'utilisateur : « il faut que ce soit
fait automatiquement tout le temps, par les agents, par toi, peu importe »). Sans modèle : l'état
d'une carte suit les agents qui la désignent ; jamais « fini » automatiquement ; « prêt à clore »
seulement sur la mission propre à la carte ; prochaine étape et améliorations lues dans les
fiches de passation ; point automatique posé dans le HUD, jamais dit. Le conftest isole tout."""
import json
import time

import pytest

from astrel import core, equipe, relief, todo as todo_mod
from astrel.todo import todo
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)

CANAUX = [("general", "Général"), ("astrel", "Astrel"), ("studio", "Studio")]
G = todo_mod.GRACE_S


@pytest.fixture
def fiches(tmp_path, monkeypatch):
    folder = tmp_path / "equipe"
    monkeypatch.setattr(equipe, "EQUIPE_DIR", folder)
    monkeypatch.setattr(todo_mod, "EQUIPE_DIR", None)
    return folder


def carte(cid):
    return todo.get(cid)


def add(titre, projet="Astrel", **kw):
    return todo.add(projet, titre, force=True, canaux=CANAUX, **kw)["id"]


def fiche(de, a, fait, mission, **kw):
    assert equipe.deposer(de, a, fait, preuves="pytest", mission=mission, **kw)["ok"]


# ------------------------------------------------------------------ quelle carte désigne une consigne

def test_mission_de_l_agent_et_carte_citee_en_exemple_ignoree():
    t10 = add("Boutons du HUD")
    t1 = add("Pilote Flow", plan="P41")
    t3 = add("Rangement automatique de la todo")
    consigne = (f"Mission todo-auto-{t3.lower()}, dossier Astrel. Constat : la carte {t10} (codeur, mission "
                f"hud-boutons-{t10.lower()}) restait à planifier ; la carte {t1} restait en cours. Voir P41.")
    with todo._lock:
        ids, mission = todo_mod.designations(consigne, todo._load())
    assert (ids, mission) == ([t3], f"todo-auto-{t3.lower()}") and t10 not in ids and t1 not in ids
    # mission sans carte : le plan cité en exemple ne la rattache pas (P41 n'a pourtant qu'une carte)
    with todo._lock:
        assert todo_mod.designations("Mission autre-chose-9. Voir P41.", todo._load())[0] == []
        assert todo_mod.designations("Mission p41-lot-b3. Lot B3 de P41.", todo._load())[0] == [t1]


@pytest.mark.parametrize("consigne, attendu", [
    ("Carte : T2. Corrige le bug.", ["T2"]),
    ("carte=T2", ["T2"]),
    ("Comme la carte T2, corrige le bug.", []),     # citée en passant : pas de deux-points
    ("Lot B3 du plan P41.", []),                     # P41 a plusieurs cartes : ambigu
    ("Lot 2 du plan P38 (mission p38-lot2).", ["T4"]),  # P38 n'a qu'une carte
    ("mission site-compta : relis le site", ["T5"]),    # mission enregistrée sur la carte
])
def test_designations(consigne, attendu):
    add("Un")
    add("Deux")
    add("Trois", plan="P41")
    add("Quatre", plan="P38")
    add("Cinq", mission="site Compta")
    add("Six", plan="P41", etat="fini")
    with todo._lock:
        assert todo_mod.designations(consigne, todo._load())[0] == attendu


# ------------------------------------------------------------------ règle 1 : un agent démarre

def test_agent_vivant_met_la_carte_en_cours_et_la_lie_a_sa_mission():
    cid = add("Boutons du HUD : retirer Lire aussitôt", etat="lancer")
    out = todo.ranger(["Mission hud-boutons-t10, codeur. Carte : " + cid], now=1000, depuis=0)
    c = carte(cid)
    assert c["etat"] == "cours" and c["mission"] == "hud-boutons-t10"
    last = c["hist"][-1]
    assert last["par"] == "auto" and last["de"] == "lancer" and last["raison"].startswith("un agent y travaille")
    assert out and all(r["ok"] for r in out)
    assert todo.ranger(["Carte : " + cid], now=1005, depuis=0) == []  # déjà en cours : rien


def test_identifiant_dans_le_nom_de_la_mission():
    for i in range(1, 10):
        add(f"Carte numéro {i}")
    cid = add("Boutons du HUD")  # T10
    todo.ranger(["Mission hud-boutons-t10 : retire les boutons."], now=1000, depuis=0)
    assert carte(cid)["etat"] == "cours" and carte("T1")["etat"] == "planifier"


@pytest.mark.parametrize("etat", ["fini", "abandonne"])
def test_carte_close_jamais_rouverte(etat):
    cid = add("Vieux chantier", etat="fini")
    if etat == "abandonne":
        todo.move(cid, "abandonne", "plus utile")
    todo.ranger([f"Carte : {cid}"], now=1000, depuis=0)
    assert carte(cid)["etat"] == etat


# ------------------------------------------------------------------ règle 2 : plus aucun agent

def test_dernier_agent_parti_retour_a_planifier_apres_le_delai():
    cid = add("Rangement", mission="todo-auto-t99")
    t0 = time.time()
    todo.ranger(["Mission todo-auto-t99"], now=t0, depuis=t0 - 1000)
    assert carte(cid)["etat"] == "cours"
    assert todo.ranger([], now=t0 + G - 1, depuis=t0 - 1000) == []  # délai de grâce
    assert carte(cid)["etat"] == "cours"
    todo.ranger([], now=t0 + G + 1, depuis=t0 - 1000)
    c = carte(cid)
    assert c["etat"] == "planifier" and c["hist"][-1]["raison"] == "aucun agent n'y travaille"
    assert c["hist"][-1]["par"] == "auto"


def test_carte_en_cours_sans_lien_ni_agent_rangee_attente_gardee():
    """Cas de T1 : codée, attend l'utilisateur, mise en cours à la main par Claude, personne n'y travaille."""
    cid = add("Pilote Flow : prêt pour le pilote réel", plan="P41", attend="utilisateur")
    add("Autre lot", plan="P41")
    todo.move(cid, "cours", par="claude")
    t0 = time.time()
    todo.ranger([], now=t0 + G + 1, depuis=t0 - 1000)
    c = carte(cid)
    assert c["etat"] == "planifier" and c["attend"] == "utilisateur"


def test_mise_en_cours_par_utilisateur_respectee():
    cid = add("Séance de diagnostic avec ma voix")
    todo.move(cid, "cours", par="utilisateur")
    t0 = time.time()
    todo.ranger([], now=t0 + 10 * G, depuis=0)
    assert carte(cid)["etat"] == "cours"


def test_demarrage_du_coeur_compte_comme_delai():
    cid = add("Chantier", etat="cours", par="auto")
    t0 = time.time() + 10 * G  # carte mise en cours il y a longtemps, cœur qui vient de démarrer
    todo.ranger([], now=t0, depuis=t0 - 5)
    assert carte(cid)["etat"] == "cours"
    todo.ranger([], now=t0 + G, depuis=t0 - 5)
    assert carte(cid)["etat"] == "planifier"


# ------------------------------------------------------------------ relève, quota, redémarrage

def test_agents_interrompus_par_la_releve_ou_le_quota_gardent_la_carte():
    cid = add("Rangement", mission="todo-auto-t99")
    t0 = time.time()
    todo.ranger(["Mission todo-auto-t99"], now=t0, depuis=0)
    # arrêté (relève, quota) : sa consigne est dans la liste des agents à relancer
    for k in range(1, 20):
        todo.ranger([], ["Mission todo-auto-t99"], now=t0 + k * G, depuis=0)
        assert carte(cid)["etat"] == "cours"
    # relancé : toujours en cours, sans aller-retour dans l'historique
    todo.ranger(["Mission todo-auto-t99"], now=t0 + 21 * G, depuis=0)
    assert [h.get("a") for h in carte(cid)["hist"]] == ["planifier", "cours"]


def test_releve_en_cours_rien_ne_recule():
    cid = add("Chantier", etat="cours", par="auto")
    t0 = time.time() + 10 * G
    todo.ranger([], now=t0, depuis=0, pause=True)
    assert carte(cid)["etat"] == "cours"


def test_jamais_fini_automatiquement(fiches):
    ids = [add(f"Tâche {i}", etat=e, mission=f"m{i}-x") for i, e in enumerate(["planifier", "lancer", "cours"])]
    fiche("relecteur", "astrel", "Rien de bloquant, zéro remarque.", "m2-x")  # prête à clore
    t0 = time.time()
    for k in range(6):
        vivants = [f"Mission m{k % 3}-x"] if k < 3 else []
        todo.ranger(vivants, now=t0 + k * 2 * G, depuis=0)
        assert all(carte(i)["etat"] != "fini" for i in ids)
    journal = [json.loads(x) for x in todo_mod.JOURNAL_FILE.read_text(encoding="utf-8").splitlines()]
    assert all(e["carte"]["etat"] != "fini" for e in journal if e["par"] == "auto")
    assert carte(ids[2])["etat"] == "cours"  # prête à clore : reste en cours, Astrel la clôt


def test_carte_deplacee_entre_temps_n_est_pas_ecrasee():
    cid = add("Chantier", etat="cours")
    todo.move(cid, "fini")
    r = todo.move(cid, "planifier", "aucun agent n'y travaille", par="auto", seulement_si=("cours",))
    assert r.get("inchange") and carte(cid)["etat"] == "fini"


def test_automatismes_coupes(monkeypatch):
    monkeypatch.setattr(todo_mod, "AUTO", False)
    cid = add("Chantier")
    assert todo.ranger([f"Carte : {cid}"], now=1000) == []
    assert carte(cid)["etat"] == "planifier"


# ------------------------------------------------------------------ « prêt à clore » : la mission propre à la carte

def test_lot_voisin_du_meme_plan_ne_rend_pas_pret(fiches):
    """28/10 : T4 et T5 (deux lots de P40) toutes deux « prêtes à clore » par la seule relecture du lot A4."""
    t4 = add("Images IA P40 lot A4 : cœur", plan="P40")
    t5 = add("Images IA P40 lot A5 : partie HUD", plan="P40")
    fiche("codeur", "testeur", "Lot A4 fait.", "p40-images-a4")
    fiche("relecteur", "astrel", "Rien de bloquant.", "p40-images-a4")
    vue = {c["id"]: c for c in todo.vue(CANAUX)["cartes"]}
    assert "pret_a_clore" not in vue[t4] and "pret_a_clore" not in vue[t5]
    todo.edit(t4, mission="p40-images-a4")
    vue = {c["id"]: c for c in todo.vue(CANAUX)["cartes"]}
    assert vue[t4]["pret_a_clore"]["mission"] == "p40-images-a4" and "pret_a_clore" not in vue[t5]
    fiche("relecteur", "astrel", "Rien de bloquant.", f"p40-hud-{t5.lower()}")  # identifiant dans la mission
    vue = {c["id"]: c for c in todo.vue(CANAUX)["cartes"]}
    assert vue[t5]["pret_a_clore"]["mission"] == f"p40-hud-{t5.lower()}"


def test_carte_finie_du_meme_plan_garde_l_ambiguite(fiches):
    t4 = add("Lot A4", plan="P40")
    t5 = add("Lot A5", plan="P40")
    todo.move(t4, "fini")
    fiche("relecteur", "astrel", "Rien de bloquant.", "p40-images-a4")
    assert "pret_a_clore" not in {c["id"]: c for c in todo.vue(CANAUX)["cartes"]}[t5]


def test_codeur_apres_le_relecteur_eteint_le_badge(fiches):
    cid = add("Chantier", mission="m1-x")
    fiche("relecteur", "astrel", "Rien de bloquant.", "m1-x")
    assert {c["id"]: c for c in todo.vue(CANAUX)["cartes"]}[cid].get("pret_a_clore")
    fiche("codeur", "testeur", "Nouvelle correction.", "m1-x")
    assert "pret_a_clore" not in {c["id"]: c for c in todo.vue(CANAUX)["cartes"]}[cid]


# ------------------------------------------------------------------ prochaine étape et améliorations

@pytest.mark.parametrize("de, a, fait, attendu", [
    ("codeur", "testeur", "Fait.", "test par le testeur (fiche 1)"),
    ("testeur", "relecteur", "Testé.", "relecture (fiche 1)"),
    ("relecteur", "astrel", "Rien de bloquant.", "prête à clore : relecture sans réserve (fiche 1)"),
    ("relecteur", "astrel", "2 remarques moyennes.", "corriger les remarques du relecteur (fiche 1)"),
    ("relecteur", "codeur", "Une remarque.", "corrections par le codeur (fiche 1)"),
    ("codeur", "astrel", "Quota à 95 %, arrêté.", "bloqué : le codeur a rendu la main (fiche 1)"),
])
def test_suite_d_apres_la_derniere_fiche(de, a, fait, attendu):
    assert todo_mod.suite_de({"n": 1, "de": de, "a": a, "fait": fait}) == attendu


def test_fiche_remplit_la_suite_et_cree_les_ameliorations(fiches):
    cid = add("Rangement automatique", etat="cours", mission="todo-auto-t99")
    add("Journal plus lisible", projet="Studio")  # même titre, AUTRE projet : pas un doublon
    assert todo.suivre_fiches(CANAUX) == []  # premier appel : état de départ
    fiche("codeur", "testeur", "Fait.", "todo-auto-t99")
    todo.suivre_fiches(CANAUX)
    assert carte(cid)["suite"] == "test par le testeur (fiche 1)"
    fiche("relecteur", "astrel", "Rien de bloquant, deux suggestions.", "todo-auto-t99",
          ameliorations=["Journal plus lisible", "Rangement : délai réglable", "  "])
    out = todo.suivre_fiches(CANAUX)
    assert carte(cid)["suite"] == "corriger les remarques du relecteur (fiche 2)"
    nouvelles = [c for c in todo.vue(CANAUX)["cartes"] if c.get("source")]
    # sans mission (relecture fiche 4) : l'origine est dans `source` et la note
    assert [(c["projet"], c["titre"], c["etat"], c["mission"], c["source"]) for c in nouvelles] == [
        ("astrel", "Journal plus lisible", "planifier", "", "relecteur todo-auto-t99 fiche 2"),
        ("astrel", "Rangement : délai réglable", "planifier", "", "relecteur todo-auto-t99 fiche 2")]
    assert len([r for r in out if r.get("id")]) >= 2
    # la même fiche relue (relance du cœur, autre fiche) ne refait pas de doublon
    fiche("relecteur", "astrel", "Encore.", "todo-auto-t99", ameliorations=["journal plus lisible"])
    todo.suivre_fiches(CANAUX)
    assert len([c for c in todo.vue(CANAUX)["cartes"] if c.get("source")]) == 2


def test_amelioration_jamais_en_cours_ni_prete_avec_l_agent_de_sa_mission(fiches):
    """Relecture fiche 4 (HAUTE) : liée à la mission d'origine, l'amélioration passait en cours avec cet
    agent, recevait sa suite et « prêt à clore » alors qu'elle n'était pas faite."""
    cid = add("Rangement automatique", etat="cours", mission="todo-auto-t99")
    todo.suivre_fiches(CANAUX)
    fiche("relecteur", "astrel", "Rien de bloquant, une suggestion.", "todo-auto-t99", ameliorations=["Délai réglable"])
    todo.suivre_fiches(CANAUX)
    (idee,) = [c for c in todo.vue(CANAUX)["cartes"] if c.get("source")]
    t0 = time.time()
    for k in range(3):
        todo.ranger(["Mission todo-auto-t99, codeur."], now=t0 + k * G, depuis=0)
    fiche("relecteur", "astrel", "Rien de bloquant, zéro remarque.", "todo-auto-t99")
    todo.suivre_fiches(CANAUX)
    vue = {c["id"]: c for c in todo.vue(CANAUX)["cartes"]}
    assert vue[idee["id"]]["etat"] == "planifier" and not vue[idee["id"]]["suite"]
    assert "pret_a_clore" not in vue[idee["id"]] and vue[cid]["pret_a_clore"]
    rows = {r["id"]: r for r in todo.liste("Astrel", canaux=CANAUX)["cartes"]}
    assert rows[idee["id"]]["source"] == "passation" and "source" not in rows[cid]
    assert "jamais un ordre" in todo.liste("Astrel", canaux=CANAUX)["passation"]


def test_suite_des_fiches_deja_la_au_demarrage_et_apres_un_lien(fiches):
    """Relecture fiche 4 : suite calculée aussi au premier appel et pour une carte liée après coup."""
    a = add("Déjà en route", mission="m1-x")
    fiche("codeur", "testeur", "Fait.", "m1-x")
    todo.suivre_fiches(CANAUX)  # démarrage du cœur
    assert carte(a)["suite"] == "test par le testeur (fiche 1)"
    b = add("Pas encore liée")
    fiche("testeur", "relecteur", "Testé.", "m2-x")
    todo.suivre_fiches(CANAUX)
    assert carte(b)["suite"] == ""
    todo.ranger([f"Mission m2-x. Carte : {b}"], now=time.time(), depuis=0)
    assert carte(b)["mission"] == "m2-x" and carte(b)["suite"] == "relecture (fiche 1)"


def test_point_sans_a_lire_en_silencieux_ou_dictee(astrel, published, monkeypatch):
    j, said, _ = astrel
    monkeypatch.setattr(j, "_floor_taken", lambda: "")
    add("Chantier", attend="utilisateur")
    for mode, attendu in (("silencieux", False), ("dictee", False), ("conversation", True)):
        monkeypatch.setattr(j, "_usage", mode)
        monkeypatch.setattr(j, "_todo_point_text", "", raising=False)
        monkeypatch.setattr(j, "_todo_point_at", 0.0, raising=False)
        t0 = time.time()
        todo.demander_point(None, quand=t0 - core.TODO_POINT_ATTENTE_S)
        published.clear()
        j._todo_point(t0, [])
        (rep,) = of(published, "report")
        assert rep["pending_read"] is attendu, mode
    assert said == []


def test_ameliorations_sans_carte_liee_rien_n_est_cree(fiches):
    todo.suivre_fiches(CANAUX)
    fiche("relecteur", "astrel", "Rien.", "mission-inconnue-9", ameliorations=["Idée"])
    todo.suivre_fiches(CANAUX)
    assert todo.vue(CANAUX)["cartes"] == []


def test_passation_garde_les_ameliorations(fiches):
    fiche("relecteur", "astrel", "Rien de bloquant.", "m1", ameliorations="Idée un\nIdée deux")
    (f,) = equipe.read("m1")
    assert f["ameliorations"] == ["Idée un", "Idée deux"]
    assert "- Améliorations (cartes de la todo) : Idée un ; Idée deux" in (fiches / "m1" / "journal.md").read_text(
        encoding="utf-8")


# ------------------------------------------------------------------ point automatique

def test_texte_du_point():
    a = add("Boutons du HUD", etat="cours")
    b = add("Pilote Flow", attend="utilisateur")
    c = add("Lot A5", etat="lancer")
    todo.edit(c, suite="relecture (fiche 3)")
    d = add("Lot B3")
    todo.edit(d, suite="bloqué : le codeur a rendu la main (fiche 2)")
    todo.add("Astrel", "Délai réglable", source="relecteur m fiche 2", canaux=CANAUX)
    text = todo.point(["astrel"])
    assert text.startswith("Point automatique, Astrel.")
    assert f"En cours : {a} Boutons du HUD." in text and f"Attend ta décision : {b} Pilote Flow." in text
    assert f"Bloqué : {d}, bloqué : le codeur a rendu la main (fiche 2)." in text
    assert f"Prochaines étapes : {c}, relecture (fiche 3)." in text
    assert "Améliorations en attente : 1 (" in text
    assert "/" not in text and "*" not in text  # ni chemin ni markdown
    assert todo.point(["studio"]) == ""


def test_releve_demande_un_point():
    relief.todo_block()
    assert todo.point_demande() == {"*"}


def _agent(j, tid, consigne, desc="Codeur"):
    j._agent_log[f"tu-{tid}"] = {"type": "codeur", "consigne": consigne, "files": [], "pending": {}}
    j._on_task({"id": tid, "phase": "start", "kind": "agent", "desc": desc, "tool_use_id": f"tu-{tid}"})


def test_coeur_range_au_demarrage_et_a_la_fin_d_un_agent_puis_pose_un_point(astrel, published, monkeypatch):
    j, said, _ = astrel
    monkeypatch.setattr(j, "_floor_taken", lambda: "")
    cid = todo.add("Studio", "Rangement automatique", canaux=CANAUX)["id"]
    t0 = time.time()
    j._todo_tick(now=t0)
    _agent(j, "t1", f"Mission todo-auto-{cid.lower()}, dossier Astrel.", desc="Codeur todo")
    j._todo_tick(now=t0 + 6)
    assert carte(cid)["etat"] == "cours" and carte(cid)["mission"] == f"todo-auto-{cid.lower()}"
    j._on_task({"id": "t1", "phase": "end", "status": "completed"})
    published.clear()
    j._todo_tick(now=t0 + 12)  # dernier agent du projet parti : point demandé, posé une minute après
    assert of(published, "report") == []
    j._todo_tick(now=t0 + 12 + core.TODO_POINT_ATTENTE_S)
    (rep,) = of(published, "report")
    assert rep["pending_read"] is True and rep["report_kind"] == "resultat"
    assert rep["text"].startswith("Point automatique, Studio.") and rep["channel"] == "studio"
    assert said == [] and carte(cid)["etat"] == "cours"
    j._todo_tick(now=t0 + 12 + G)
    assert carte(cid)["etat"] == "planifier"


def test_testeur_lance_juste_apres_le_codeur_annule_le_point(astrel, published, monkeypatch):
    j, _, _ = astrel
    monkeypatch.setattr(j, "_floor_taken", lambda: "")
    todo.add("Studio", "Rangement", mission="todo-auto-t99", canaux=CANAUX)
    t0 = time.time()
    j._todo_tick(now=t0)
    _agent(j, "t1", "Mission todo-auto-t99")
    j._todo_tick(now=t0 + 6)
    j._on_task({"id": "t1", "phase": "end", "status": "completed"})
    j._todo_tick(now=t0 + 12)
    _agent(j, "t2", "Mission todo-auto-t99", desc="Testeur")
    j._todo_tick(now=t0 + 18 + core.TODO_POINT_ATTENTE_S)
    assert of(published, "report") == [] and todo.point_demande() is None


def test_point_un_par_demi_heure_et_jamais_quand_utilisateur_parle(astrel, published, monkeypatch):
    j, said, _ = astrel
    parle = {"v": "l'utilisateur parle"}
    monkeypatch.setattr(j, "_floor_taken", lambda: parle["v"])
    add("Chantier", attend="utilisateur")
    t0 = time.time()
    todo.demander_point(None, quand=t0 - core.TODO_POINT_ATTENTE_S)
    j._todo_point(t0, [])
    assert of(published, "report") == [] and todo.point_demande() == {"*"}  # gardé pour plus tard
    parle["v"] = ""
    j._todo_point(t0 + 1, [])
    assert len(of(published, "report")) == 1
    add("Autre chantier", attend="utilisateur")
    todo.demander_point(None, quand=t0)
    j._todo_point(t0 + 60, [])
    assert len(of(published, "report")) == 1  # moins de 30 min
    j._todo_point(t0 + core.TODO_POINT_S + 2, [])
    assert len(of(published, "report")) == 2 and said == []


def test_agent_arrete_pour_la_releve_ne_declenche_ni_recul_ni_point(astrel, published, monkeypatch):
    j, _, _ = astrel
    monkeypatch.setattr(j, "_floor_taken", lambda: "")
    cid = todo.add("Studio", "Rangement", mission="todo-auto-t99", canaux=CANAUX)["id"]
    t0 = time.time()
    j._todo_tick(now=t0)
    _agent(j, "t1", "Mission todo-auto-t99")
    j._todo_tick(now=t0 + 6)
    relief.save_list([{"tid": "t1", "type": "codeur", "consigne": "Mission todo-auto-t99", "desc": "Codeur"}])
    j._on_task({"id": "t1", "phase": "end", "status": "killed"})
    published.clear()
    for k in range(1, 6):
        j._todo_tick(now=t0 + 6 + k * G)
    assert carte(cid)["etat"] == "cours" and of(published, "report") == []


def test_releve_point_une_minute_apres(astrel, published, monkeypatch):
    """Fiche 2 du testeur : après une relève, le point attendait 6 s au lieu d'une minute."""
    j, said, _ = astrel
    monkeypatch.setattr(j, "_floor_taken", lambda: "")
    todo.add("Studio", "Chantier", attend="utilisateur", canaux=CANAUX)
    t0 = time.time()
    j._todo_tick(now=t0)
    relief.todo_block()  # session neuve après une relève : point demandé maintenant
    j._todo_tick(now=t0 + 6)
    j._todo_tick(now=t0 + core.TODO_POINT_ATTENTE_S - 5)
    assert of(published, "report") == [] and todo.point_demande() == {"*"}
    j._todo_tick(now=time.time() + core.TODO_POINT_ATTENTE_S + 1)
    (rep,) = of(published, "report")
    assert rep["text"].startswith("Point automatique, tous les projets.") and said == []


@pytest.mark.parametrize("brut", ["\x1b[31mJournal plus lisible\x1b[0m", "Journal \x1b[1;31mplus\x1b[m lisible",
                                  "\x1b]0;titre\x07Journal plus lisible", "\x9b31mJournal plus lisible"])
def test_sequences_ansi_retirees_entieres(fiches, brut):
    """Fiche 2 du testeur : « [31m » restait dans le titre quand seul ESC était retiré."""
    add("Rangement", mission="todo-auto-t99")
    todo.suivre_fiches(CANAUX)
    fiche("relecteur", "astrel", "Rien.", "todo-auto-t99", ameliorations=[brut])
    todo.suivre_fiches(CANAUX)
    (c,) = [c for c in todo.vue(CANAUX)["cartes"] if c.get("source")]
    assert c["titre"] == "Journal plus lisible"
