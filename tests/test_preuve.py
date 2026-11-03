"""Preuve de fin de mission (02/11, plan P43) : le vérificateur `preuve.py`, seul.

Schéma strict, ouverture sûre dans un périmètre HOSTILE (liens, liens durs, fichiers spéciaux,
racine remplacée, autre montage, fichier qui change pendant la lecture), délai, arrêt, lecture
seule, rien de lancé, et un rapport qui ne contient jamais le contenu d'un fichier."""
import hashlib
import json
import os
import socket
import subprocess
import types

import pytest

from astrel import config, guard, preuve
from conftest import chemin_interne as interne

# 03/11 (CI Windows) : un chemin absolu hors du projet sur tous les systèmes (Windows : lecteur courant).
DEHORS = os.path.abspath("/etc/passwd")


@pytest.fixture
def projet(tmp_path):
    p = (tmp_path / "projet").resolve()
    p.mkdir()
    return p


@pytest.fixture(autouse=True)
def rien_ne_se_lance(monkeypatch):
    """Le vérificateur ne lance AUCUN programme : toute tentative fait échouer le test."""
    def interdit(*a, **k):
        raise AssertionError("le vérificateur a tenté de lancer un programme")
    for nom in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, nom, interdit)
    monkeypatch.setattr(os, "system", interdit)
    monkeypatch.setattr(os, "popen", interdit)


def instantane(projet, critere, ancrage=None):
    scope = [str(projet)]
    c = preuve.valider_critere(critere, scope)
    anc = preuve.ancrer(c, scope) if ancrage is None else ancrage
    return {"mission_id": "m-test", "generation": 1, "verification_id": "m-test:1", "critere": c,
            "critere_sha": preuve.empreinte_critere(c), "origine": "utilisateur", "scope": scope, "ancrage": anc}


def verifier(projet, *controles, figer=(), ancrage=None, **kw):
    return preuve.verifier(instantane(projet, {"controles": list(controles), "figer": list(figer)}, ancrage), **kw)


def codes(rapport):
    return [(c["etat"], c["code"]) for c in rapport["controles"]]


def existe(chemin, genre="fichier", **k):
    return {"type": "fichier_existe", "chemin": str(chemin), "genre": genre, **k}


# ------------------------------------------------------------------ schéma strict

@pytest.mark.parametrize("critere, motif", [
    ({"controles": [], "x": 1}, "champ inconnu"),
    ({"controles": []}, "1 à 10"),
    ({"controles": [{"type": "commande", "cmd": "pytest"}]}, "type de contrôle inconnu"),
    ({"controles": [{"type": "fichier_existe", "chemin": "{P}/a"}]}, "il manque genre"),
    ({"controles": [{"type": "fichier_existe", "chemin": "{P}/a", "genre": "fichier", "shell": "x"}]}, "champ inconnu"),
    ({"controles": [{"type": "fichier_existe", "chemin": "a", "genre": "fichier"}]}, "chemin absolu"),
    ({"controles": [{"type": "fichier_existe", "chemin": "{P}/../etc/passwd", "genre": "fichier"}]}, "« . » et « .. »"),
    ({"controles": [{"type": "fichier_existe", "chemin": DEHORS, "genre": "fichier"}]}, "hors du périmètre"),
    ({"controles": [{"type": "fichier_existe", "chemin": "{P}/a\x00b", "genre": "fichier"}]}, "contrôle"),
    ({"controles": [{"type": "fichier_existe", "chemin": "{P}/a", "genre": "fichier", "taille_min": True}]}, "entier"),
    ({"controles": [{"type": "fichier_existe", "chemin": "{P}/a", "genre": "lien"}]}, "fichier ou dossier"),
    ({"controles": [{"type": "fichier_contient", "chemin": "{P}/a", "texte": ""}]}, "texte de 1"),
    ({"controles": [{"type": "fichier_contient", "chemin": "{P}/a", "texte": {"x": 1}}]}, "texte de 1"),
    ({"controles": [{"type": "empreinte", "chemin": "{P}/a", "sha256": "ABC"}]}, "64 caractères"),
    ({"controles": [{"type": "dossier_contient", "chemin": "{P}", "attendus": ["/etc"]}]}, "relatif"),
    ({"controles": [{"type": "dossier_contient", "chemin": "{P}", "attendus": ["../x"]}]}, "« . » et « .. »"),
    ({"controles": [{"type": "git_head", "depot": "{P}", "branche": "a..b"}]}, "branche"),
    ({"version": True, "controles": [{"type": "fichier_existe", "chemin": "{P}/a", "genre": "fichier"}]}, "version"),
    ({"version": 2, "controles": [{"type": "fichier_existe", "chemin": "{P}/a", "genre": "fichier"}]}, "version"),
    ({"controles": [{"type": "fichier_existe", "chemin": "{P}/a", "genre": "fichier"}] * 11}, "1 à 10"),
    ({"controles": [{"type": "dossier_contient", "chemin": "{P}", "attendus": ["n" * 100] * 50}]}, "trop long"),
    ("pas du json", "illisible"),
    (["liste"], "objet"),
])
def test_schema_strict(projet, critere, motif):
    texte = json.dumps(critere) if not isinstance(critere, str) else critere
    # 03/11 (CI Windows) : le chemin entre dans un texte JSON, ses « \ » échappés.
    critere = json.loads(texte.replace("{P}", json.dumps(str(projet))[1:-1])) if not isinstance(critere, str) else critere
    with pytest.raises(preuve.CritereInvalide, match=motif):
        preuve.valider_critere(critere, [str(projet)])


def test_schema_normalise_et_empreinte_stable(projet):
    c = preuve.valider_critere(json.dumps({"controles": [existe(f"{projet}//a/")]}), [str(projet)])
    assert c == {"version": 1, "controles": [existe(interne(projet / "a"))], "figer": []}
    autre = {"figer": [], "controles": [{"genre": "fichier", "chemin": interne(projet / "a"), "type": "fichier_existe"}],
             "version": 1}
    assert preuve.empreinte_critere(c) == preuve.empreinte_critere(autre)  # JSON canonique


def test_injection_shell_traitee_comme_un_nom(projet):
    # Windows refuse « : » dans un nom et un nom fini par une espace : la cible sans son lecteur, ${IFS} pour l'espace.
    cible = os.path.splitdrive(projet.as_posix())[1]
    r = verifier(projet, existe(f"{projet}/x; rm -rf ~"), existe(f"{projet}/$(touch${{IFS}}{cible}/pwned)"))
    assert codes(r) == [("echec", "absent"), ("echec", "absent")] and r["statut"] == "echec"
    assert not (projet / "pwned").exists()


# ------------------------------------------------------------------ les cinq contrôles

def test_controles_qui_reussissent(projet):
    (projet / "RAPPORT.md").write_text("Tous les tests passent : OK\n")
    (projet / "dist").mkdir()
    (projet / "dist" / "index.html").write_text("<html>")
    sha = hashlib.sha256((projet / "RAPPORT.md").read_bytes()).hexdigest()
    r = verifier(projet, existe(projet / "RAPPORT.md", taille_min=5), existe(projet / "dist", "dossier"),
                 {"type": "fichier_contient", "chemin": str(projet / "RAPPORT.md"), "texte": "OK"},
                 {"type": "fichier_contient", "chemin": str(projet / "RAPPORT.md"), "texte": "ÉCHEC", "absent": True},
                 {"type": "empreinte", "chemin": str(projet / "RAPPORT.md"), "sha256": sha},
                 {"type": "dossier_contient", "chemin": str(projet / "dist"), "attendus": ["index.html"]})
    assert r["statut"] == "verifiee" and r["verifie"] is True, r
    assert all(c["etat"] == "ok" for c in r["controles"])


def test_controles_qui_echouent(projet):
    (projet / "a.txt").write_text("rien")
    (projet / "d").mkdir()
    r = verifier(projet, existe(projet / "manque"), existe(projet / "d"), existe(projet / "a.txt", taille_min=100),
                 {"type": "fichier_contient", "chemin": str(projet / "a.txt"), "texte": "OK"},
                 {"type": "fichier_contient", "chemin": str(projet / "a.txt"), "texte": "rien", "absent": True},
                 {"type": "empreinte", "chemin": str(projet / "a.txt"), "sha256": "0" * 64},
                 {"type": "dossier_contient", "chemin": str(projet / "d"), "attendus": ["x", "y/z"]})
    assert codes(r) == [("echec", "absent"), ("echec", "genre"), ("echec", "taille"), ("echec", "texte_manquant"),
                        ("echec", "texte_present"), ("echec", "empreinte_differente"), ("echec", "manquants")]
    assert r["statut"] == "echec" and r["verifie"] is False


# ------------------------------------------------------------------ périmètre hostile

def test_lien_symbolique_au_bout_ou_dans_un_parent(projet, tmp_path):
    dehors = tmp_path / "dehors"
    dehors.mkdir()
    (dehors / "s.txt").write_text("secret")
    (projet / "lien.txt").symlink_to(dehors / "s.txt")
    (projet / "sous").symlink_to(dehors, target_is_directory=True)
    r = verifier(projet, existe(projet / "lien.txt"), existe(projet / "sous" / "s.txt"),
                 {"type": "fichier_contient", "chemin": str(projet / "lien.txt"), "texte": "secret"})
    assert codes(r) == [("bloque", "lien")] * 3 and r["statut"] == "bloquee"


def test_lien_dur_vers_un_fichier_hors_perimetre(projet, tmp_path):
    (tmp_path / "secret.txt").write_text("jeton")
    os.link(tmp_path / "secret.txt", projet / "copie.txt")
    r = verifier(projet, {"type": "fichier_contient", "chemin": str(projet / "copie.txt"), "texte": "jeton"})
    assert codes(r) == [("bloque", "lien_dur")]


def test_fifo_et_socket_jamais_ouverts(projet):
    if len(str(projet / "sock")) > 100:
        pytest.skip("dossier temporaire trop long pour une socket Unix (macOS)")
    os.mkfifo(projet / "tube")  # une FIFO ouverte en lecture bloquerait : jamais ouverte
    s = socket.socket(socket.AF_UNIX)
    try:
        s.bind(str(projet / "sock"))
        r = verifier(projet, {"type": "fichier_contient", "chemin": str(projet / "tube"), "texte": "x"},
                     existe(projet / "sock"))
    finally:
        s.close()
    assert codes(r) == [("bloque", "special"), ("bloque", "special")]


def test_racine_remplacee_depuis_le_lancement(projet):
    (projet / "a").write_text("x")
    inst = instantane(projet, {"controles": [existe(projet / "a")]})
    inst["ancrage"]["racines"][0]["ino"] += 1  # le dossier n'est plus celui du lancement
    assert codes(preuve.verifier(inst)) == [("bloque", "racine")]


def test_changement_de_systeme_de_fichiers(projet, monkeypatch):
    (projet / "monte").mkdir()
    (projet / "monte" / "a").write_text("x")
    # 03/11 (CI Windows) : Unix examine « monte » par `stat(dir_fd=…)`, Windows par `lstat(chemin entier)`.
    for nom in ("stat", "lstat"):
        def decale(chemin, *a, _vrai=getattr(os, nom), **k):
            st = _vrai(chemin, *a, **k)
            if os.path.basename(str(chemin)) != "monte":
                return st
            champs = {c: getattr(st, c) for c in dir(st) if c.startswith("st_")}
            return types.SimpleNamespace(**{**champs, "st_dev": st.st_dev + 1})
        monkeypatch.setattr(preuve.os, nom, decale)
    assert codes(verifier(projet, existe(projet / "monte" / "a"))) == [("bloque", "montage")]


def test_fichier_modifie_pendant_la_lecture(projet, monkeypatch):
    f = projet / "log.txt"
    f.write_text("début\n")
    vrai = os.read
    refus = []

    def read(fd, n):
        try:
            with open(f, "a") as w:  # un processus de fond écrit pendant la vérification
                w.write("encore\n")
        except PermissionError:
            refus.append(1)
        return vrai(fd, n)
    monkeypatch.setattr(preuve.os, "read", read)
    r = verifier(projet, {"type": "fichier_contient", "chemin": str(f), "texte": "début"})
    if os.name == "nt":  # 03/11 : ouvert sans partage en écriture, l'écrivain est refusé, la lecture reste entière
        assert refus and codes(r) == [("ok", "texte_trouve")]
    else:
        assert codes(r) == [("bloque", "instable")]


def test_fichier_trop_gros_et_delai(projet, monkeypatch):
    (projet / "gros").write_bytes(b"x" * 2048)
    monkeypatch.setattr(preuve, "MAX_CONTIENT", 1024)
    r = verifier(projet, {"type": "fichier_contient", "chemin": str(projet / "gros"), "texte": "x"})
    assert codes(r) == [("bloque", "trop_gros")]
    temps = iter([0.0, 0.0, 100.0, 100.0, 100.0, 100.0, 100.0])
    r = verifier(projet, existe(projet / "gros"), horloge=lambda: next(temps, 100.0))
    assert codes(r) == [("bloque", "delai")]


def test_secret_et_runtime_refuses(projet, monkeypatch):
    (projet / "cle.txt").write_text("x")
    r = verifier(projet, existe(projet / "cle.txt"), lecture_permise=lambda chemin: not chemin.endswith("cle.txt"))
    assert codes(r) == [("bloque", "protege")]
    # le vrai juge : le garde-fou pour un secret, et tout `runtime/`
    assert not preuve._lecture_permise(str(guard.SECRET_PATHS[0]))
    assert not preuve._lecture_permise(str(config.RUNTIME.resolve() / "mission.json"))
    assert preuve._lecture_permise(str(projet / "cle.txt"))


def test_plateforme_sans_dir_fd_toujours_bloquee(projet, monkeypatch):
    (projet / "a").write_text("x")
    monkeypatch.setattr(preuve, "_PORTABLE", False)
    monkeypatch.setattr(preuve, "_Ouvrir", preuve._Ouverture)  # Windows a son ouverture propre (test_preuve_windows)
    assert codes(verifier(projet, existe(projet / "a"), ancrage={})) == [("bloque", "plateforme")]


def test_le_rapport_ne_contient_jamais_le_contenu(projet):
    jeton = "sk-ant-JETON-TRES-SECRET-0123456789"
    (projet / ".env").write_text(f"CLE={jeton}\n")
    (projet / "notes").mkdir()
    r = verifier(projet, {"type": "fichier_contient", "chemin": str(projet / ".env"), "texte": "absent-ici"},
                 {"type": "empreinte", "chemin": str(projet / ".env"), "sha256": "1" * 64},
                 existe(projet / ".env", taille_min=1))
    brut = json.dumps(r, ensure_ascii=False)
    assert jeton not in brut and "CLE=" not in brut
    vrai_sha = hashlib.sha256((projet / ".env").read_bytes()).hexdigest()
    assert vrai_sha not in brut and vrai_sha[:12] in brut  # 12 caractères seulement
    assert jeton not in preuve.resume_echec(r)


def test_la_verification_n_ecrit_rien(projet):
    (projet / "a").write_text("x")
    (projet / "d").mkdir()
    avant = {p: (p.stat().st_mtime_ns, p.stat().st_ctime_ns) for p in projet.rglob("*")}
    verifier(projet, existe(projet / "a"), {"type": "dossier_contient", "chemin": str(projet / "d"), "attendus": ["z"]},
             {"type": "empreinte", "chemin": str(projet / "a"), "sha256": "0" * 64})
    assert {p: (p.stat().st_mtime_ns, p.stat().st_ctime_ns) for p in projet.rglob("*")} == avant
    assert sorted(projet.rglob("*")) == sorted(avant)


def test_critere_modifie_ou_arret(projet):
    (projet / "a").write_text("x")
    inst = instantane(projet, {"controles": [existe(projet / "a")]})
    inst["critere"]["controles"][0]["chemin"] = str(projet / "autre")  # modifié après le lancement
    r = preuve.verifier(inst)
    assert r["statut"] == "bloquee" and codes(r) == [("bloque", "critere_modifie")]
    r = verifier(projet, existe(projet / "a"), doit_arreter=lambda: True)
    assert r["statut"] == "interrompue" and r["verifie"] is False


# ------------------------------------------------------------------ fichiers figés

def test_fichiers_figes(projet):
    t = projet / "test_x.py"
    t.write_text("assert calcul() == 4\n")
    inst = instantane(projet, {"controles": [existe(t)], "figer": [str(t)]})
    assert inst["ancrage"]["figer"][0]["sha256"] == hashlib.sha256(t.read_bytes()).hexdigest()
    assert preuve.verifier(inst)["statut"] == "verifiee"
    t.write_text("assert True\n")  # test affaibli pour remplir le critère
    assert codes(preuve.verifier(inst))[-1] == ("echec", "fige_modifie")
    t.unlink()
    assert codes(preuve.verifier(inst))[-1] == ("echec", "fige_supprime")
    with pytest.raises(preuve.CritereInvalide, match="figer illisible"):
        instantane(projet, {"controles": [existe(t)], "figer": [str(projet / "absent.py")]})


# ------------------------------------------------------------------ git, lu sans git

def depot(projet, ref="refs/heads/main", oid="a" * 40, packed=False):
    g = projet / ".git"
    (g / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (g / "HEAD").write_text(f"ref: {ref}\n" if ref else oid + "\n")
    if ref and not packed:
        (g / ref).write_text(oid + "\n")
    if packed:
        (g / "packed-refs").write_text(f"# pack-refs with: peeled\n{oid} {ref}\n")
    return g


def test_git_head_branche_et_commit(projet):
    depot(projet)
    git = {"type": "git_head", "depot": str(projet)}
    assert codes(verifier(projet, {**git, "branche": "main"})) == [("ok", "head")]
    assert codes(verifier(projet, {**git, "branche": "dev"})) == [("echec", "autre_branche")]
    inst = instantane(projet, {"controles": [{**git, "avance": True}]})
    assert inst["ancrage"]["heads"] == [{"depot": interne(projet), "sha": "a" * 40}]
    assert codes(preuve.verifier(inst)) == [("echec", "pas_de_commit")]
    (projet / ".git" / "refs" / "heads" / "main").write_text("b" * 40 + "\n")  # un commit de plus
    r = preuve.verifier(inst)
    assert codes(r) == [("ok", "head")] and r["controles"][0]["infos"]["head"] == "b" * 12


def test_git_head_detache_et_packed_refs(projet):
    depot(projet, ref="", oid="c" * 40)
    assert verifier(projet, {"type": "git_head", "depot": str(projet)})["controles"][0]["infos"] == \
        {"head": "c" * 12, "branche": ""}
    (projet / ".git" / "HEAD").unlink()
    depot(projet, oid="d" * 40, packed=True)
    r = verifier(projet, {"type": "git_head", "depot": str(projet), "branche": "main"})
    assert codes(r) == [("ok", "head")] and r["controles"][0]["infos"]["head"] == "d" * 12


def test_git_worktree_qui_pointe_hors_perimetre(projet, tmp_path):
    (projet / ".git").write_text(f"gitdir: {tmp_path}/ailleurs/.git\n")
    assert codes(verifier(projet, {"type": "git_head", "depot": str(projet)})) == [("bloque", "hors_perimetre")]
    (projet / ".git").write_text("gitdir: ../../etc\n")
    assert codes(verifier(projet, {"type": "git_head", "depot": str(projet)})) == [("bloque", "hors_perimetre")]


def test_git_head_illisible_ou_trafique(projet):
    g = depot(projet)
    (g / "HEAD").write_text("ref: refs/heads/../../../etc/passwd\n")
    assert codes(verifier(projet, {"type": "git_head", "depot": str(projet)})) == [("bloque", "git")]
    (g / "HEAD").unlink()
    (g / "HEAD").symlink_to("/etc/hostname")
    assert codes(verifier(projet, {"type": "git_head", "depot": str(projet)})) == [("bloque", "lien")]


# ------------------------------------------------------------------ syntaxe du formulaire du HUD

def test_lignes_du_hud(projet):
    texte = f"""# critère de la mission
existe docs/RAPPORT.md
dossier {projet}/dist
contient README.md :: Tous les tests passent
sans journal.txt :: ERREUR
sha256 build/app bin {"ab" * 32}
contenu dist :: index.html, app.js
branche . main
commit .
figer tests/test_calcul.py
"""
    c = preuve.lire_lignes(texte, str(projet))
    p = str(projet)
    assert c["controles"] == [
        existe(f"{p}/docs/RAPPORT.md"), existe(f"{p}/dist", "dossier"),
        {"type": "fichier_contient", "chemin": f"{p}/README.md", "texte": "Tous les tests passent"},
        {"type": "fichier_contient", "chemin": f"{p}/journal.txt", "texte": "ERREUR", "absent": True},
        {"type": "empreinte", "chemin": f"{p}/build/app bin", "sha256": "ab" * 32},
        {"type": "dossier_contient", "chemin": f"{p}/dist", "attendus": ["index.html", "app.js"]},
        {"type": "git_head", "depot": p, "branche": "main"}, {"type": "git_head", "depot": p, "avance": True}]
    assert c["figer"] == [f"{p}/tests/test_calcul.py"]
    assert preuve.valider_critere(c, [p])["controles"][4]["chemin"] == interne(f"{p}/build/app bin")


@pytest.mark.parametrize("texte, motif", [
    ("lance pytest -q", "inconnu"), ("contient README.md", "chemin :: "), ("sha256 x", "chemin valeur"),
    ("existe", "chemin manquant"), ("existe a\n" * 31, "30 lignes"),
])
def test_lignes_du_hud_refusees(projet, texte, motif):
    with pytest.raises(preuve.CritereInvalide, match=motif):
        preuve.lire_lignes(texte, str(projet))
    assert "lance" not in json.dumps(preuve.decrire(None))


def test_lignes_relatives_qui_sortent_du_perimetre(projet):
    c = preuve.lire_lignes("existe ../voisin/secret.txt", str(projet))
    with pytest.raises(preuve.CritereInvalide, match="« . » et « .. »"):
        preuve.valider_critere(c, [str(projet)])


def test_lignes_du_hud_en_anglais(projet):
    c = preuve.lire_lignes("exists a\ndir b\ncontains a :: OK\nlacks a :: KO\nhas b :: x\nbranch . main\nfreeze a",
                           str(projet))
    assert [x["type"] for x in c["controles"]] == ["fichier_existe", "fichier_existe", "fichier_contient",
                                                   "fichier_contient", "dossier_contient", "git_head"]
    assert c["controles"][3]["absent"] is True and c["figer"] == [f"{projet}/a"]


# ------------------------------------------------------------------ revue indépendante du 02/11

def test_entrees_mal_formees_refusees_proprement(projet):
    with pytest.raises(preuve.CritereInvalide, match="type de contrôle inconnu"):
        preuve.valider_critere({"controles": [{"type": []}]}, [str(projet)])
    (projet / ".git").write_text("gitdir: /x\x00y\n")
    r = verifier(projet, {"type": "git_head", "depot": str(projet)})
    assert codes(r) == [("bloque", "git")]
    (projet / ".git").unlink()
    (projet / ".git").mkdir()
    (projet / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (projet / ".git" / "commondir").write_text("..\x01\n")
    assert codes(verifier(projet, {"type": "git_head", "depot": str(projet)})) == [("bloque", "git")]


@pytest.mark.skipif(os.name == "nt", reason="table des montages de /proc : Linux ; sous Windows, un dossier de "
                    "montage est un point d'analyse, refusé « lien » sans être traversé")
def test_point_de_montage_sous_la_racine_jamais_stat(projet, monkeypatch):
    (projet / "fuse").mkdir()
    monkeypatch.setattr(preuve, "_points_de_montage", lambda: frozenset({str(projet / "fuse")}))
    vrai = os.stat

    def stat(chemin, *a, **k):
        assert chemin != "fuse", "un point de montage a été stat-é"
        return vrai(chemin, *a, **k)
    monkeypatch.setattr(preuve.os, "stat", stat)
    assert codes(verifier(projet, existe(projet / "fuse" / "a"), ancrage={})) == [("bloque", "montage")]


def test_points_de_montage_lus_sur_ce_systeme():
    assert "/" in preuve._points_de_montage() or not os.path.exists("/proc/self/mountinfo")


# ------------------------------------------------------------------ lot C : commande observée (03/11)

def commande(projet, cmd=".venv/bin/python -m pytest -q"):
    return {"type": "commande_observee", "dossier": str(projet), "commande": cmd}


def obs_de(projet, *entrees, ecriture=0, outil=""):
    attendu = preuve.commande_attendue(commande(projet))
    return {"seq": 99, "ecriture": ecriture, "ecriture_outil": outil,
            "commandes": [{"cmd": attendu, "id": f"t{i}", "seq": seq, "etat": etat, "code": code}
                          for i, (seq, etat, code) in enumerate(entrees)]}


def verifier_cmd(projet, obs):
    inst = instantane(projet, {"controles": [commande(projet)]})
    inst["observations"] = obs
    return preuve.verifier(inst)


def test_commande_attendue_porte_le_dossier(projet, tmp_path):
    c = preuve.valider_critere({"controles": [commande(tmp_path / "a b")]}, [str(tmp_path)])["controles"][0]
    # Git Bash (Windows) reçoit « C:/… » (`preuve.affiche`)
    assert preuve.commande_attendue(c) == f"cd '{tmp_path.as_posix()}/a b' && .venv/bin/python -m pytest -q"


@pytest.mark.parametrize("c, motif", [
    ({"type": "commande_observee", "dossier": os.path.dirname(DEHORS), "commande": "true"}, "hors du périmètre"),
    ({"type": "commande_observee", "dossier": "{p}", "commande": "true\nrm -rf /"}, "caractère de contrôle"),
    ({"type": "commande_observee", "dossier": "{p}", "commande": "x" * 301}, "1 à 300"),
    ({"type": "commande_observee", "dossier": "{p}", "commande": "   "}, "vide"),
    ({"type": "commande_observee", "dossier": "{p}"}, "il manque commande"),
    ({"type": "commande_observee", "dossier": "{p}", "commande": "true", "shell": True}, "champ inconnu"),
])
def test_commande_observee_schema(projet, c, motif):
    c = {k: (v.format(p=projet) if isinstance(v, str) and k == "dossier" else v) for k, v in c.items()}
    with pytest.raises(preuve.CritereInvalide, match=motif):
        preuve.valider_critere({"controles": [c]}, [str(projet)])


@pytest.mark.parametrize("entrees, ecriture, attendu", [
    ([], 0, ("echec", "commande_absente")),
    ([(5, "en_cours", "")], 0, ("echec", "commande_en_cours")),
    ([(5, "echec", "Exit code 1")], 0, ("echec", "commande_echouee")),
    ([(5, "ok", "")], 7, ("echec", "commande_perimee")),
    ([(5, "ok", "")], 3, ("ok", "commande_reussie")),
    ([(5, "ok", ""), (8, "echec", "Exit code 2")], 0, ("echec", "commande_echouee")),  # la DERNIÈRE fait foi
    ([(5, "echec", "Exit code 2"), (8, "ok", "")], 6, ("ok", "commande_reussie")),
])
def test_commande_observee_codes(projet, entrees, ecriture, attendu):
    r = verifier_cmd(projet, obs_de(projet, *entrees, ecriture=ecriture, outil="Edit"))
    assert codes(r) == [attendu]
    assert r["statut"] == ("verifiee" if attendu[0] == "ok" else "echec")


def test_commande_observee_autre_commande_ne_compte_pas(projet):
    obs = obs_de(projet, (5, "ok", ""))
    obs["commandes"][0]["cmd"] += " || true"
    assert codes(verifier_cmd(projet, obs)) == [("echec", "commande_absente")]


def test_commande_observee_ne_lance_rien_meme_sans_observation(projet):
    inst = instantane(projet, {"controles": [commande(projet)]})  # pas de clé « observations »
    assert codes(preuve.verifier(inst)) == [("echec", "commande_absente")]


def test_commande_observee_en_lignes(projet):
    brut = preuve.lire_lignes("commande . :: .venv/bin/python -m pytest -q\ncommand sous :: npm test", str(projet))
    c = preuve.valider_critere(brut, [str(projet)])["controles"]
    assert c[0] == commande(interne(projet)) and c[1]["dossier"] == interne(projet / "sous")
    assert "réussit après ta dernière modification" in preuve.decrire({"controles": c, "figer": []})[0]
