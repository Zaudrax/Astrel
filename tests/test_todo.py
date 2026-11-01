"""Todo par projet (26/10, plan P38 lot 1) : une source sur disque (`runtime/todo.json`, écrite
avant la réponse) et un journal en ajout seul, quatre colonnes + abandon replié, doublons
refusés, commandes du cœur et outils MCP `todo_*`."""
import json

import pytest

from astrel import mcp_server, todo as todo_mod
from astrel.events import STICKY
from astrel.plans import plans
from astrel.todo import todo
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)

CANAUX = [("general", "Général"), ("boutique", "Boutique"), ("portfolio", "Portfolio")]


@pytest.fixture(autouse=True)
def isole(tmp_path, monkeypatch):
    monkeypatch.setattr(todo_mod, "TODO_FILE", tmp_path / "todo.json")
    monkeypatch.setattr(todo_mod, "JOURNAL_FILE", tmp_path / "todo-journal.jsonl")
    todo.reset()
    yield tmp_path
    todo.reset()


def add(titre="Corriger l'onboarding", projet="Boutique", **kw):
    return todo.add(projet, titre, canaux=CANAUX, **kw)


def disque(tmp_path):
    return json.loads((tmp_path / "todo.json").read_text(encoding="utf-8"))


def journal(tmp_path):
    return [json.loads(x) for x in (tmp_path / "todo-journal.jsonl").read_text(encoding="utf-8").splitlines()]


# ------------------------------------------------------------------ stockage

def test_ajout_ecrit_le_disque_et_le_journal_avant_la_reponse(isole):
    r = add(note="Le bouton suivant\nne répond pas", attend="toi", plan="p36")
    assert r["ok"] and r["id"] == "T1"
    cartes = disque(isole)["cartes"]
    assert [c["id"] for c in cartes] == ["T1"]
    c = cartes[0]
    assert (c["projet"], c["etat"], c["attend"], c["plan"]) == ("boutique", "planifier", "utilisateur", "P36")
    assert c["note"] == "Le bouton suivant\nne répond pas"
    assert [e["op"] for e in journal(isole)] == ["ajout"]


def test_numerotation_reprise_apres_relance(isole):
    add("Première")
    add("Deuxième tâche bien différente")
    todo.reset()
    assert add("Troisième sujet encore autre")["id"] == "T3"


def test_fichier_change_sous_le_coeur_est_relu(isole):
    add("Première")
    data = disque(isole)
    data["cartes"][0]["titre"] = "Changée à la main"
    (isole / "todo.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert todo.get("T1")["titre"] == "Changée à la main"


def test_fichier_illisible_reconstruit_depuis_le_journal(isole):
    add("Première")
    todo.move("T1", "en cours")
    (isole / "todo.json").write_text("{abîmé", encoding="utf-8")
    todo.reset()
    c = todo.get("T1")
    assert c and c["etat"] == "cours"
    assert add("Une autre chose à faire")["id"] == "T2"


def test_echec_d_ecriture_rend_l_erreur_et_n_ajoute_rien(isole, monkeypatch):
    def boom(self):
        raise OSError("disque plein")

    save = todo_mod.Todo._save
    monkeypatch.setattr(todo_mod.Todo, "_save", boom)
    r = add()
    assert not r["ok"] and "disque plein" in r["error"]
    monkeypatch.setattr(todo_mod.Todo, "_save", save)
    assert todo.liste(canaux=CANAUX)["cartes"] == []
    assert add()["id"] == "T1"  # le numéro n'est pas consommé


# ------------------------------------------------------------------ règles

@pytest.mark.parametrize("projet, why", [("Général", "pas dans Général"), ("", "projet manquant"),
                                         ("Inconnu", "projet inconnu")])
def test_projet_refuse(projet, why):
    r = add(projet=projet)
    assert not r["ok"] and why in r["error"]


def test_projet_par_nom_ou_id_sans_accent_ni_casse():
    assert add(projet="BOUTIQUE")["carte"]["projet"] == "boutique"
    assert add("Autre sujet sans rapport", projet="portfolio")["carte"]["projet"] == "portfolio"


@pytest.mark.parametrize("a, b, dup", [
    ("Corriger l'onboarding", "corriger onboarding", True),
    ("Corriger l'onboarding de l'app", "Corriger l'onboarding de l'appli", True),
    ("Publier la version 2", "Publier la version 3", False),
    ("Écrire le post LinkedIn", "Refaire le logo", False),
])
def test_doublons_proches(a, b, dup):
    assert todo_mod.proche(a, b) is dup


def test_ajout_refuse_un_doublon_ouvert_du_meme_projet():
    add()
    r = add("corriger onboarding")
    assert not r["ok"] and r["doublon"]["id"] == "T1"
    assert add("corriger onboarding", projet="Portfolio")["ok"]  # autre projet : permis
    assert add("corriger onboarding", force=True)["ok"]           # l'utilisateur confirme : permis
    todo.move("T1", "fini")
    todo.move("T3", "fini")
    assert add("corriger onboarding")["ok"]                        # l'ancienne est finie


@pytest.mark.parametrize("mot, etat", [("à planifier", "planifier"), ("À lancer", "lancer"),
                                       ("en cours", "cours"), ("terminé", "fini"), ("abandonné", "abandonne"),
                                       ("n'importe", "")])
def test_etats_dits_a_la_voix(mot, etat):
    assert todo_mod.etat_de(mot) == etat


def test_abandon_replie_avec_raison_jamais_supprime(isole):
    add()
    assert "sans raison" in todo.move("T1", "abandonne")["error"]
    r = todo.move("T1", "abandonne", "Remplacé par la refonte")
    assert r["ok"] and r["carte"]["raison"] == "Remplacé par la refonte"
    assert [c["id"] for c in disque(isole)["cartes"]] == ["T1"]
    liste = todo.liste(canaux=CANAUX)
    assert liste["cartes"] == [] and liste["projets"]["boutique"] == {"abandonne": 1}
    assert todo.liste("Boutique", "abandonne", canaux=CANAUX)["cartes"][0]["raison"]
    repris = todo.move("T1", "lancer")
    assert repris["ok"] and "raison" not in repris["carte"]
    assert [h.get("a") for h in todo.get("T1")["hist"]] == ["planifier", "abandonne", "lancer"]
    assert [e["op"] for e in journal(isole)] == ["ajout", "abandon", "deplace"]


def test_modification_champ_par_champ():
    add(note="ancienne", attend="utilisateur")
    r = todo.edit("T1", note="", attend="", plan="P38", canaux=CANAUX)
    c = todo.get("T1")
    assert r["ok"] and (c["note"], c["attend"], c["plan"], c["titre"]) == ("", "", "P38", "Corriger l'onboarding")
    assert not todo.edit("T1", plan="38")["ok"]
    assert not todo.edit("T1", titre=" ")["ok"]
    assert todo.edit("T1", plan="P38")["inchange"]
    assert todo.edit("T1", projet="Portfolio", canaux=CANAUX)["carte"]["projet"] == "portfolio"
    assert "inconnue" in todo.edit("T99", note="x")["error"]


def test_statut_du_plan_lu_a_l_affichage_jamais_recopie(isole, monkeypatch):
    monkeypatch.setattr(plans, "describe", lambda: {"items": [{"id": "P38", "status": "validated"}]})
    add(plan="P38")
    assert todo.liste("Boutique", canaux=CANAUX)["cartes"][0]["plan_statut"] == "validated"
    assert "plan_statut" not in disque(isole)["cartes"][0]


def test_liste_courte_tient_dans_le_canal_de_controle():
    for i in range(80):
        todo.add("Boutique", f"Tâche numéro {i} avec un titre assez long pour peser é" + "x" * 60, force=True,
                 canaux=CANAUX)
    r = todo.liste(canaux=CANAUX)
    assert len(json.dumps(r, ensure_ascii=False).encode()) < 4096
    assert r["tronque"] > 0 and r["projets"]["boutique"]["planifier"] == 80


def test_vue_sticky_avec_compteurs_sans_historique(published):
    assert "todo" in STICKY
    add()
    ev = of(published, "todo")[-1]
    assert [e["id"] for e in ev["etats"]] == ["planifier", "lancer", "cours", "fini", "abandonne"]
    assert [p["id"] for p in ev["projets"]] == ["boutique", "portfolio"]  # Général écarté
    assert ev["projets"][0]["compteurs"]["planifier"] == 1
    assert "hist" not in ev["cartes"][0]


# ------------------------------------------------------------------ cœur et outils MCP

def test_commandes_du_coeur_depuis_claude(astrel, published):
    j, _, _ = astrel
    r = j.on_command({"cmd": "todo_add", "projet": "Studio", "titre": "Démo écouteurs", "_from_claude": True})
    assert r["ok"] and r["carte"]["projet"] == "studio"
    assert todo.get("T1")["hist"][0]["par"] == "claude"
    assert j.on_command({"cmd": "todo_move", "id": "t1", "etat": "en cours", "_from_claude": True})["ok"]
    assert j.on_command({"cmd": "todo_list", "projet": "studio"})["compteurs"]["cours"] == 1
    assert j.on_command({"cmd": "todo_done", "id": "1"})["carte"]["etat"] == "fini"
    assert j.on_command({"cmd": "todo_drop", "id": "T1", "raison": "doublon"})["carte"]["etat"] == "abandonne"
    assert j.on_command({"cmd": "todo_edit", "id": "T1", "note": "vu"})["ok"]
    assert j.on_command({"cmd": "todo_get", "id": "T1"})["carte"]["note"] == "vu"
    full = j.on_command({"cmd": "todos_get"})
    assert [p["id"] for p in full["projets"]] == ["studio", "compta"]
    assert not j.on_command({"cmd": "todo_add", "projet": "Général", "titre": "x"})["ok"]


def test_outils_mcp_envoient_au_coeur(monkeypatch):
    sent = []
    monkeypatch.setattr(mcp_server.state, "send_command",
                        lambda raw, timeout=0: sent.append(json.loads(raw)) or '{"ok": true}')
    mcp_server.todo_add("Portfolio", "Refaire la page é", note="n", attend="utilisateur", plan="P38")
    mcp_server.todo_list("Boutique")
    mcp_server.todo_move("T2", "cours")
    mcp_server.todo_edit("T2", note="")
    mcp_server.todo_done("T2")
    mcp_server.todo_drop("T2", "plus utile")
    assert [s["cmd"] for s in sent] == ["todo_add", "todo_list", "todo_move", "todo_edit", "todo_done", "todo_drop"]
    assert sent[0]["titre"] == "Refaire la page é" and sent[0]["force"] is False
    assert sent[3] == {"cmd": "todo_edit", "id": "T2", "note": ""}  # champs absents : inchangés
    assert set(todo_mod.COMMANDES) >= {s["cmd"] for s in sent}


def test_carte_retouchee_a_la_main_incomplete_ignoree_ou_completee(isole):
    data = {"top": 3, "cartes": [{"id": "T1", "etat": "cours", "projet": "boutique", "titre": "Gardée"},
                                 {"id": "T2", "etat": "cours", "titre": "Sans projet"},
                                 {"id": "T3", "etat": "inconnu", "projet": "boutique", "titre": "x"}]}
    (isole / "todo.json").write_text(json.dumps(data), encoding="utf-8")
    r = todo.liste("Boutique", canaux=CANAUX)
    assert [c["id"] for c in r["cartes"]] == ["T1"]
    assert todo.get("T1")["note"] == "" and add("Nouvelle")["id"] == "T4"


# ------------------------------------------------------------------ corrections après le testeur (fiche 2)

ENFANT = """
import sys
from pathlib import Path
from astrel import todo as t
t.TODO_FILE, t.JOURNAL_FILE = Path(sys.argv[1]), Path(sys.argv[2])
t.AUTO = False  # lot 3 : ni note du coffre ni fiches de la machine (le conftest n'agit pas ici)
canaux = [("boutique", "Boutique")]
for i in range(40):
    r = t.todo.add("boutique", f"Processus {sys.argv[3]} tâche {i}", force=True, canaux=canaux)
    assert r["ok"], r
"""


def test_deux_processus_n_attribuent_jamais_le_meme_id(isole):
    """D1 : le verrou de fichier tient entre deux VRAIS processus (40 ajouts chacun)."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(todo_mod.__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": str(root)}
    args = [str(isole / "todo.json"), str(isole / "todo-journal.jsonl")]
    procs = [subprocess.Popen([sys.executable, "-c", ENFANT, *args, name], cwd=root, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) for name in ("A", "B")]
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err.decode()
    ids = [c["id"] for c in disque(isole)["cartes"]]
    assert len(ids) == len(set(ids)) == 80
    assert len({e["carte"]["id"] for e in journal(isole)}) == 80
    assert not list(isole.glob("*.tmp"))


def test_journal_tronque_l_ajout_suivant_reste_lisible(isole):
    """D2 : une dernière ligne coupée est close avant l'ajout suivant."""
    add("Première")
    with open(isole / "todo-journal.jsonl", "a", encoding="utf-8") as f:
        f.write('{"t": 1, "op": "ajout", "carte": {"id": "T')  # coupure en pleine écriture
    add("Deuxième sujet bien distinct")
    (isole / "todo.json").unlink()
    todo.reset()
    assert todo.get("T2") and todo.get("T2")["titre"] == "Deuxième sujet bien distinct"


@pytest.mark.parametrize("invisible", ["​", "‍", "﻿", "­", "⁠", "​‍﻿"])
def test_caracteres_invisibles_retires(invisible):
    """D3 : titre ou raison faits de caractères invisibles = vides ; ailleurs, retirés."""
    assert "titre vide" in add(invisible * 3)["error"]
    r = add(f"Corri{invisible}ger l'onboarding", note=f"a{invisible}b")
    assert r["carte"]["titre"] == "Corriger l'onboarding" and todo.get(r["id"])["note"] == "ab"
    assert not add("Corriger l'onboarding")["ok"]  # doublon vu malgré l'invisible
    assert "sans raison" in todo.move(r["id"], "abandonne", invisible * 2)["error"]


def test_carte_illisible_journalisee_et_signalee_sans_reconstruction(isole, caplog):
    """D4 : ignorée, journalisée, comptée dans todo_list ; le fichier n'est pas réécrit."""
    data = {"top": 2, "cartes": [{"id": "T1", "etat": "cours", "projet": "boutique", "titre": "Gardée"},
                                 {"id": "T2", "etat": "??", "projet": "boutique", "titre": "x"}]}
    (isole / "todo.json").write_text(json.dumps(data), encoding="utf-8")
    before = (isole / "todo.json").read_text(encoding="utf-8")
    with caplog.at_level("WARNING", logger="astrel.todo"):
        r = todo.liste("Boutique", canaux=CANAUX)
    assert r["ignorees"] == 1 and [c["id"] for c in r["cartes"]] == ["T1"]
    assert "T2" in caplog.text
    assert (isole / "todo.json").read_text(encoding="utf-8") == before
    assert not (isole / "todo-journal.jsonl").exists()


# ------------------------------------------------------------------ relecture (fiche 4)

def test_carte_illisible_gardee_brute_apres_une_ecriture(isole):
    """M1 : une carte invalide mise à la main n'est jamais détruite par l'écriture suivante."""
    add("Première")
    data = disque(isole)
    brute = {"id": "T50", "etat": "??", "projet": "boutique", "titre": "à réparer"}
    data["cartes"].append(brute)
    (isole / "todo.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    r = add("Deuxième sujet bien distinct")
    assert r["id"] == "T51"  # l'id de la carte illisible n'est pas réattribué
    data = disque(isole)
    assert data["ignorees_brutes"] == [brute] and "T50" not in [c["id"] for c in data["cartes"]]
    todo.reset()
    add("Troisième sujet encore autre")
    assert disque(isole)["ignorees_brutes"] == [brute]
    assert todo.liste("Boutique", canaux=CANAUX)["ignorees"] == 1


@pytest.mark.parametrize("remplissage", ["ㅤ", "⠀", "ᅟ", "ᅠ", "ﾠ", "឴", "឵",
                                         "᠎"])
def test_remplissages_invisibles_comptent_comme_vides(remplissage):
    """B1 : titre ou raison faits de remplissages = vides ; dans un vrai texte, gardés."""
    assert "titre vide" in add(remplissage * 3)["error"]
    r = add(f"Logo {remplissage} final")
    import unicodedata

    kept = unicodedata.category(remplissage) != "Cf"  # U+180E est Cf : retiré comme les autres invisibles
    assert r["ok"] and (remplissage in r["carte"]["titre"]) is kept
    assert "titre vide" in todo.edit(r["id"], titre=remplissage)["error"]
    assert "sans raison" in todo.move(r["id"], "abandonne", remplissage * 2)["error"]


# ---------------------------------------------------------------- T26 : priorité dans une colonne

def test_priorite_glissee_puis_lue_par_astrel(isole, tmp_path):
    a, b, c = (add(t, force=True)["id"] for t in ("Premier arrivé", "Deuxième", "Troisième"))
    add("Ailleurs", projet="Portfolio")
    assert [x["id"] for x in todo.liste("Boutique", "planifier")["cartes"]] == [a, b, c]  # jamais classées : ordre d'arrivée
    assert todo.ordonner([c, a, b]) == {"ok": True, "ordre": [c, a, b]}
    assert [x["id"] for x in todo.liste("Boutique", "planifier")["cartes"]] == [c, a, b]
    assert [x["rang"] for x in todo.vue(CANAUX)["cartes"] if x["projet"] == "boutique"] == [1, 2, 0]
    assert "priorité de l'utilisateur" in todo.instantane() and todo.instantane().index(c) < todo.instantane().index(a)
    d = add("Arrivée après le classement", force=True)["id"]
    assert [x["id"] for x in todo.liste("Boutique", "planifier")["cartes"]] == [c, a, b, d]  # la nouvelle vient après
    assert todo.ordonner([c, a, b])["inchange"] is True
    # journal : une entrée par carte reclassée, la reconstruction garde l'ordre
    ops = [e for e in journal(tmp_path) if e["op"] == "ordre"]
    assert sorted(e["carte"]["id"] for e in ops) == sorted([a, b, c])
    assert todo.reconstruire()[c]["rang"] == 0


@pytest.mark.parametrize("ids, erreur", [
    ([], "ordre illisible"), ("T1", "ordre illisible"), (["T1", "T1"], "sans doublon"), (["x"], "ordre illisible"),
    (["T99"], "carte inconnue"),
])
def test_priorite_refusee(isole, ids, erreur):
    add("Une carte")
    r = todo_mod.commande("todo_ordre", {"ids": ids}, CANAUX)
    assert r["ok"] is False and erreur in r["error"]


def test_priorite_d_une_seule_colonne_ouverte(isole):
    a = add("Un", force=True)["id"]
    b = add("Deux", etat="lancer", force=True)["id"]
    c = add("Trois", projet="Portfolio")["id"]
    f = add("Finie", etat="fini", force=True)["id"]
    for ids in ([a, b], [a, c], [f]):
        assert "même colonne" in todo.ordonner(ids)["error"]
