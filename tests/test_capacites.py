"""Page « Capacités » du HUD (21/10, plan P22, lot 1) : `astrel/capacites.py`.

Le catalogue est généré depuis les sources ; ces tests le font échouer s'il vieillit : outil MCP
sans groupe, type de réflexe sans nom, phrase d'exemple qui n'est plus reconnue, raccourci
disparu de hotkeys.sh, lien vers un sujet du Guide qui n'existe plus, texte sans anglais.
Ils vérifient aussi qu'aucun secret ni chemin absolu ne sort.
"""
from __future__ import annotations

import chemins_machine  # chemins de la machine qui lance la suite
import ast
import json
import re
import sys
from pathlib import Path

import pytest

from astrel import capacites, claudeai, config, graph, i18n, sensors
from astrel.senses import reflex
from astrel.tools import connector
from test_reports import ch, astrel  # noqa: F401  (fixtures du cœur, comme tests/test_guide.py)

SENTINELLE = "VALEUR-SECRETE-DE-TEST-p22"


@pytest.fixture(autouse=True)
def _isole(tmp_path, monkeypatch):
    """Ni les réflexes, ni les capteurs, ni les compteurs, ni les secrets de la machine."""
    monkeypatch.setattr(reflex, "RULES_FILE", tmp_path / "reflexes.json")
    monkeypatch.setattr(sensors, "STATE_FILE", tmp_path / "sensors.json")
    monkeypatch.setattr(graph, "USAGE_FILE", tmp_path / "graph-usage.json")
    monkeypatch.setattr(config, "SECRETS_FILE", tmp_path / "secrets.json")
    # Skills et connecteurs claude.ai (lot 2) : ni les dossiers, ni les choix, ni la découverte de la machine.
    monkeypatch.setattr(capacites, "USER_SKILLS", tmp_path / "skills-user")
    monkeypatch.setattr(capacites, "PROJECT_SKILLS", tmp_path / "skills-projet")
    monkeypatch.setattr(claudeai, "SEEN_FILE", tmp_path / "connecteurs-vus.json")
    monkeypatch.setattr(config, "ASTREL_SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.delenv("ASTREL_SKILL_OVERRIDES", raising=False)
    monkeypatch.delenv("ASTREL_DENIED_MCP_SERVERS", raising=False)
    for key in connector.ALL_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("ASTREL_UI_LANGUAGE", raising=False)


def _groupes(data: dict) -> dict[str, dict]:
    return {g["id"]: g for g in data["groupes"]}


def _entree(data: dict, gid: str, nom: str) -> dict:
    return next(e for e in _groupes(data)[gid]["entrees"] if e["nom"] == nom)


# ------------------------------------------------------------------ format

def test_format_et_ordre():
    data = capacites.describe("fr")
    assert data["ok"] is True and data["lang"] == "fr"
    ids = [g["id"] for g in data["groupes"]]
    assert ids == [k for k in capacites.GROUPES if k in ids], "ordre de GROUPES"
    for g in data["groupes"]:
        assert g["titre"] and g["entrees"], g["id"]
        for e in g["entrees"]:
            assert isinstance(e["nom"], str) and e["nom"]
            assert set(e) <= {"nom", "texte", "etat", "gravite", "usage", "lien"}, e
            assert e.get("gravite", "libre") in ("libre", "sensible", "critique")
            assert "texte" not in e or len(e["texte"]) <= capacites.TEXTE_MAX + 200  # notices des modes


def test_un_groupe_seul_et_groupe_inconnu():
    one = capacites.describe("fr", groupe="bureau")
    assert [g["id"] for g in one["groupes"]] == ["bureau"]
    bad = capacites.describe("fr", groupe="nimporte")
    assert bad["ok"] is False and bad["error"]


def test_chaque_reponse_tient_dans_4096_octets():
    """Canal de contrôle : une réponse ≤ 4096 octets. Le tout dépasse : un groupe par appel."""
    for lang in ("fr", "en"):
        for g in capacites.describe(lang)["groupes"]:
            n = len(json.dumps({"ok": True, "lang": lang, "groupes": [g]}, ensure_ascii=False).encode())
            assert n < 4096, (lang, g["id"], n)
        assert len(json.dumps(capacites.sommaire(lang), ensure_ascii=False).encode()) < 4096


def test_sommaire_compte_les_entrees():
    full = _groupes(capacites.describe("fr"))
    som = capacites.sommaire("fr")
    assert [g["id"] for g in som["groupes"]] == list(full)
    for g in som["groupes"]:
        assert "entrees" not in g and g["n"] == len(full[g["id"]]["entrees"])


# ------------------------------------------------------------------ outils MCP

def _decorated(path: Path) -> int:
    return len(re.findall(r"^@server\.tool\(\)", path.read_text(encoding="utf-8"), re.M))


def test_tous_les_outils_mcp_lus_et_ranges():
    data = capacites.describe("fr")
    shown = [e["nom"] for g in data["groupes"] for e in g["entrees"] if "gravite" in e]
    for serveur, path in capacites.MCP_SOURCES:
        outils = capacites.outils_mcp(path)
        assert len(outils) == _decorated(path) > 5, serveur
        sans_groupe = [o for o, _ in outils if capacites.groupe_outil(serveur, o) == "autres"]
        assert not sans_groupe, f"outils MCP sans groupe (OUTILS_GROUPES) : {sans_groupe}"
        for outil, phrase in outils:
            assert phrase, f"{serveur}/{outil} : docstring vide"
            assert shown.count(outil) == 1, outil
    assert "autres" not in _groupes(data)


def test_gravite_lue_dans_le_garde_fou():
    data = capacites.describe("fr")
    assert _entree(data, "bureau", "power_action")["gravite"] == "critique"
    assert _entree(data, "bureau", "wifi_toggle")["gravite"] == "critique"
    assert _entree(data, "memoire", "memory_read")["gravite"] == "libre"
    assert _entree(data, "reflexes", "reflex_create")["gravite"] == "sensible"
    assert _entree(data, "connecteurs", "translate_text")["gravite"] == "sensible"


def test_list_screens_range_dans_bureau():
    """Plan P24 : l'outil lecture seule des écrans est rangé avec screen_capture."""
    assert capacites.groupe_outil("astrel", "list_screens") == "bureau"
    data = capacites.describe("fr", groupe="bureau")
    noms = [e["nom"] for e in _groupes(data)["bureau"]["entrees"]]
    assert noms.index("list_screens") == noms.index("screen_capture") + 1
    assert _entree(data, "bureau", "list_screens")["texte"].startswith("Liste les écrans branchés")
    assert "quel écran a été vu" in _entree(data, "bureau", "screen_capture")["texte"]


def test_show_media_range_dans_hud():
    """Plan P26 : l'outil qui montre des fichiers dans le fil est rangé avec post_report."""
    assert capacites.groupe_outil("astrel", "show_media") == "hud"
    assert "show_media" in [o for o, _ in capacites.outils_mcp(capacites.MCP_SOURCES[0][1])]
    assert _entree(capacites.describe("fr", groupe="hud"), "hud", "show_media")["texte"]


def test_premiere_phrase():
    pp = capacites.premiere_phrase
    assert pp("Lit une note (chemin relatif, ex. « Moi.md »). Suite.") == \
        "Lit une note (chemin relatif, ex. « Moi.md »)."
    assert pp("Volume de sortie : `level` en %,\n    ou `delta`. Sans argument : lit.") == \
        "Volume de sortie : level en %, ou delta."
    assert pp("Chef de projet. Avant un gros chantier, plan en lecture seule. Fin.") == \
        "Chef de projet. Avant un gros chantier, plan en lecture seule."
    long = pp("mot " * 80)
    assert len(long) <= capacites.TEXTE_MAX and long.endswith("…") and not long.endswith(" …")
    assert pp("Fin de phrase courte. " + "x" * 200).endswith("…")


def test_compteurs_d_usage():
    data = capacites.describe("fr", usage={"mcp:astrel:memory_read": 7, "tool:Bash": 12})
    assert _entree(data, "memoire", "memory_read")["usage"] == 7
    assert _entree(data, "natifs", "Bash")["usage"] == 12
    assert "usage" not in _entree(data, "memoire", "memory_list")
    graph.USAGE_FILE.write_text(json.dumps({"tool:Read": 3}), encoding="utf-8")
    assert _entree(capacites.describe("fr"), "natifs", "Read")["usage"] == 3


# ------------------------------------------------------------------ capteurs, agents, connecteurs

def test_capteurs_etat_vivant_sans_detail():
    vivants = [{"name": "EYES", "on": True, "status": "actif"},
               {"name": "WATCH", "on": True, "status": f"indisponible : {chemins_machine.HOME}/secret.txt"},
               {"name": "HOLO", "on": False, "status": "éteint"}]
    data = capacites.describe("fr", capteurs=vivants)
    noms = [e["nom"] for e in _groupes(data)["capteurs"]["entrees"]]
    assert set(graph.SENSORS) <= set(noms)
    assert _entree(data, "capteurs", "EYES")["etat"] == "actif"
    assert _entree(data, "capteurs", "WATCH")["etat"] == "allumé, indisponible"
    assert _entree(data, "capteurs", "HOLO")["etat"] == "éteint"
    assert "secret.txt" not in json.dumps(data, ensure_ascii=False)


def test_capteurs_sans_le_coeur_lisent_l_etat_voulu():
    sensors.STATE_FILE.write_text(json.dumps({"FOCUS": True}), encoding="utf-8")
    data = capacites.describe("fr")
    assert _entree(data, "capteurs", "FOCUS")["etat"] == "allumé"
    assert _entree(data, "capteurs", "EYES")["etat"] == "éteint"


def test_agents_lus_avec_role_sans_consigne():
    from astrel import agents
    defs = agents.load_agents()
    data = capacites.describe("fr")
    noms = {e["nom"] for e in _groupes(data)["agents"]["entrees"]}
    assert set(defs) <= noms
    assert _entree(data, "agents", "codeur")["etat"] == "opus · écriture"
    assert _entree(data, "agents", "testeur")["etat"].endswith("lecture seule")
    dump = json.dumps(data, ensure_ascii=False)
    assert "Première action" not in dump and "{metiers}" not in dump


def test_connecteurs_presence_seulement(monkeypatch):
    config.SECRETS_FILE.write_text(json.dumps({"DEEPL_API_KEY": SENTINELLE}), encoding="utf-8")
    monkeypatch.setenv("NAVITIA_API_KEY", SENTINELLE)
    for lang in ("fr", "en"):
        data = capacites.describe(lang)
        assert SENTINELLE not in json.dumps(data, ensure_ascii=False)
    data = capacites.describe("fr")
    assert _entree(data, "connecteurs", "Traduction DeepL")["etat"] == "configuré"
    assert _entree(data, "connecteurs", "Transports Navitia")["etat"] == "configuré"
    assert _entree(data, "connecteurs", "Agenda CalDAV")["etat"] == "non configuré"
    assert {e["nom"] for e in _groupes(data)["connecteurs"]["entrees"]} >= set(connector.CONNECTORS)


def test_aucun_chemin_absolu():
    for lang in ("fr", "en"):
        dump = json.dumps(capacites.describe(lang), ensure_ascii=False)
        for p in (str(config.ROOT), str(Path.home()), "/data/", "/home/"):
            assert p not in dump, p


# ------------------------------------------------------------------ réflexes

def test_types_de_reflexe_tous_nommes():
    quand, faire = capacites.types_reflexe()
    assert "time" in quand and "check" in quand and "ask" in faire, (quand, faire)
    assert set(quand) <= set(capacites.DECLENCHEURS), f"déclencheur sans nom : {set(quand) - set(capacites.DECLENCHEURS)}"
    assert set(faire) <= set(capacites.ACTIONS), f"action sans nom : {set(faire) - set(capacites.ACTIONS)}"
    n = len(_groupes(capacites.describe("fr"))["reflexes_types"]["entrees"])
    assert n == len(quand) + len(faire)


def test_regles_lues_sans_consigne_ni_ecriture():
    assert not reflex.RULES_FILE.exists()
    data = capacites.describe("fr")
    assert not reflex.RULES_FILE.exists(), "load_rules recrée le fichier : la page ne doit rien écrire"
    assert _entree(data, "reflexes", "Batterie faible")["etat"] == "activé"
    reflex.RULES_FILE.write_text(json.dumps([
        {"id": "x", "name": "Ma règle", "enabled": False,
         "when": {"type": "time", "at": "08:00"}, "do": {"type": "ask", "prompt": "CONSIGNE-PRIVEE"}},
        "pas une règle",
    ]), encoding="utf-8")
    data = capacites.describe("fr")
    e = _entree(data, "reflexes", "Ma règle")
    # 23/10 (plan P31) : nom affiché (« Astrel ») dans les textes connus du catalogue ; le nom de la règle, écrit
    # par l'utilisateur ou Claude, reste tel quel
    assert e == {"nom": "Ma règle", "texte": "À heure fixe · Astrel demande à Claude", "etat": "désactivé"}
    assert "CONSIGNE-PRIVEE" not in json.dumps(data, ensure_ascii=False)


# ------------------------------------------------------------------ voix, modes, raccourcis, gestes

def test_phrases_d_exemple_toujours_reconnues():
    from astrel import core
    for phrase, effet, verif in capacites.PHRASES:
        assert phrase.startswith("« ") and phrase.endswith(" »") and effet, phrase
        dite = phrase.strip("« »")
        kind, _, arg = verif.partition(":")
        if kind == "mode":
            assert core.mode_phrase(dite) == arg, phrase
        elif kind == "total":
            assert core.total_phrase(dite) == arg, phrase
        elif kind == "oui30":
            assert core._wants_30(dite), phrase
        elif kind == "veille":
            assert core.Astrel.is_stop(None, dite), phrase
        elif kind == "reveil":  # P36 : le nom affiché, reconnu par la voie texte
            from astrel import eveil
            assert eveil.trouve(dite, eveil.noms("Astrel")) == "Astrel"
        elif kind == "alias":
            assert config.WAKE_MODEL_NAME.startswith("hey_jarvis") and dite.lower() == "hey jarvis"
        else:
            pytest.fail(f"vérification inconnue : {verif}")


def test_modes_lus_comme_le_coeur():
    from astrel import core
    assert capacites._literal(capacites.CORE_FILE, "USAGE_NOTICES") == core.USAGE_NOTICES
    entrees = _groupes(capacites.describe("fr"))["modes"]["entrees"]
    assert len(entrees) == len(config.USAGE_MODES)
    for mode, e in zip(config.USAGE_MODES, entrees):
        assert mode in capacites.MODES_NOMS and e["texte"] == core.USAGE_NOTICES[mode]


def test_raccourcis_de_hotkeys_sh():
    lus = [t for t, _ in capacites.raccourcis()]
    poses = capacites.raccourcis_poses()
    assert len(lus) >= 5 and sorted(lus) == sorted(poses), (lus, poses)
    en = _groupes(capacites.describe("en"))["raccourcis"]["entrees"]
    assert "Ctrl+Alt+M" in [e["nom"] for e in en] and "Ctrl+Alt+X" in [e["nom"] for e in en]


def test_gestes_du_tableau_holo():
    tree = ast.parse(capacites.HOLO_FILE.read_text(encoding="utf-8"))
    actions = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "ACTIONS" for t in n.targets))
    gestes = capacites.gestes()
    assert len(gestes) >= len(actions) and all(g and e for g, e in gestes)


# ------------------------------------------------------------------ liens et langue

def test_liens_vers_le_guide_et_les_reglages():
    from astrel import settings
    from astrel.guide import Guide
    for lang in ("fr", "en"):
        sujets = {t["id"] for t in Guide().source(lang)["topics"]}
        data = capacites.describe(lang)
        for g in data["groupes"]:
            if "lien" in g:
                assert g["lien"]["type"] == "guide" and g["lien"]["id"] in sujets, (lang, g["id"])
            for e in g["entrees"]:
                lien = e.get("lien")
                if lien and lien["type"] == "guide":
                    assert lien["id"] in sujets
                elif lien:
                    assert lien["type"] == "reglages" and lien["id"] in {gid for gid, _ in settings.GROUPS}
        assert len(_groupes(data)["reglages"]["entrees"]) == len(settings.GROUPS)


def test_anglais_par_le_catalogue():
    data = capacites.describe("en")
    assert data["lang"] == "en"
    assert _entree(data, "natifs", "Bash")["texte"] == i18n.tr("Terminal : exécuter des commandes", "en") != \
        "Terminal : exécuter des commandes"
    # Les phrases vocales restent en français (Astrel comprend le français), règle du CLAUDE.md.
    assert [e["nom"] for e in _groupes(data)["voix"]["entrees"]] == [p for p, _e, _v in capacites.PHRASES]
    assert _entree(data, "agents", "codeur")["etat"].startswith("opus · ")


def test_tous_les_textes_ont_leur_anglais():
    """Ajout : `.venv/bin/python -m astrel.capacites --manquants`, puis astrel/locales/en.json."""
    i18n.reload()
    manquants = [t for t in capacites.textes_a_traduire() if not i18n.covered(t)]
    assert not manquants, f"{len(manquants)} textes sans anglais, ex. {manquants[:5]}"


def test_textes_a_traduire_sans_identifiants():
    textes = capacites.textes_a_traduire()
    assert "Ce que tu peux dire" in textes and "lecture seule" in textes and "Dictée" in textes
    for t in textes:
        assert t not in capacites.NON_TRADUITS and not re.fullmatch(r"[a-z]+_[a-z_]+", t), t
    assert not {p for p, _e, _v in capacites.PHRASES} & set(textes)  # phrases vocales : jamais traduites


def test_pas_dans_l_inventaire_du_hud():
    """Le module n'ajoute rien à tools/i18n_inventaire.py (test_i18n reste vert avant le lot 2) :
    sa couverture anglaise est vérifiée ici, par textes_a_traduire."""
    sys.path.insert(0, str(config.ROOT / "tools"))
    try:
        import i18n_inventaire
    finally:
        sys.path.pop(0)
    assert i18n_inventaire.scan_file(config.ROOT / "astrel" / "capacites.py", full=False) == []


# ------------------------------------------------------------------ entrées hostiles (fiche 2 du testeur)

def test_hotkeys_illisible_ne_casse_pas_la_page(tmp_path, monkeypatch):
    bad = tmp_path / "hotkeys.sh"
    bad.write_bytes(b"#!/bin/bash\n#   Ctrl+Alt+J   \xff\xfe parler\nset_key a b c \"<Super>j\"\n")
    monkeypatch.setattr(capacites, "HOTKEYS_FILE", bad)
    assert capacites.raccourcis() == [] and capacites.raccourcis_poses() == []
    data = capacites.describe("fr")
    assert data["ok"] is True and "raccourcis" not in _groupes(data)
    monkeypatch.setattr(capacites, "HOTKEYS_FILE", tmp_path / "absent.sh")
    assert capacites.describe("en")["ok"] is True


def test_reflexes_hostiles_ignores_sans_planter():
    reflex.RULES_FILE.write_text(json.dumps([
        {"id": "a", "name": "When en chaîne", "when": "time", "do": {"type": "ask"}},
        {"id": "b", "name": "Do en chaîne", "when": {"type": "time"}, "do": "ask"},
        {"id": "c", "name": "Do absent", "when": {"type": "time"}},
        {"id": "d", "name": ["pas", "un", "texte"], "when": {"type": ["x"]}, "do": {"type": None}},
        {"id": "e", "name": "Type inconnu", "when": {"type": "t" * 500}, "do": {"type": 3}, "enabled": "non"},
        {"name": None, "id": None, "when": {}, "do": {}},
        42, None, "texte",
    ]), encoding="utf-8")
    data = capacites.describe("fr")
    noms = [e["nom"] for e in _groupes(data)["reflexes"]["entrees"]]
    assert "When en chaîne" not in noms and "Do en chaîne" not in noms and "Do absent" not in noms
    assert _entree(data, "reflexes", "d")["texte"] == "? · ?"  # nom absent → id ; types illisibles → ?
    inconnu = _entree(data, "reflexes", "Type inconnu")
    assert len(inconnu["texte"]) <= capacites.TYPE_REGLE_MAX + 4 and inconnu["texte"].endswith(" · ?")
    assert inconnu["etat"] == "activé"  # valeur vraie = actif, comme le moteur des réflexes (`not rule.get("enabled")`)
    sans_cle = _entree(data, "reflexes", "d")
    assert sans_cle["etat"] == "désactivé"  # sans clé `enabled` le moteur ne la déclenche jamais
    reflex.RULES_FILE.write_text(json.dumps({"pas": "une liste"}), encoding="utf-8")
    assert capacites.describe("fr")["ok"] is True


def test_soixante_reflexes_aux_noms_geants_sous_4096_octets():
    reflex.RULES_FILE.write_text(json.dumps([
        {"id": f"r{i}", "name": f"Règle {i} " + "é" * 5000, "enabled": i % 2 == 0,
         "when": {"type": "check"}, "do": {"type": "ask", "prompt": "x"}} for i in range(60)
    ]), encoding="utf-8")
    for lang in ("fr", "en"):
        data = capacites.describe(lang)
        g = _groupes(data)["reflexes"]
        n = len(json.dumps({"ok": True, "lang": lang, "groupes": [g]}, ensure_ascii=False).encode())
        assert n < 4096, (lang, n)
        regles = [e for e in g["entrees"] if e["nom"].startswith("Règle")]
        assert regles and all(len(e["nom"]) <= capacites.NOM_REGLE_MAX for e in regles)
        reste = next(e for e in g["entrees"] if e["nom"].startswith("+"))
        assert int(reste["nom"][1:]) == 60 - len(regles)
        assert "reflex_create" in [e["nom"] for e in g["entrees"]]  # les outils restent
        assert set(g["entrees"][0]) <= {"nom", "texte", "etat"}  # marqueur interne retiré
        som = capacites.sommaire(lang)
        assert len(json.dumps(som, ensure_ascii=False).encode()) < 4096


def test_peu_de_reflexes_pas_de_ligne_plus():
    data = capacites.describe("fr")
    assert not [e for e in _groupes(data)["reflexes"]["entrees"] if e["nom"].startswith("+")]


# ------------------------------------------------------------------ skills et connecteurs claude.ai (lot 2)

def _skill(root: Path, nom: str, contenu: str = "---\nname: x\n---\n") -> None:
    (root / nom).mkdir(parents=True)
    (root / nom / "SKILL.md").write_text(contenu, encoding="utf-8")


def test_skills_et_claudeai_lus_sur_le_disque_sans_contenu_ni_chemin():
    """Question 3 du plan (demande : oui, en lecture) : noms et état pour Astrel, rien d'autre."""
    user, projet = capacites.USER_SKILLS, capacites.PROJECT_SKILLS
    _skill(user, "ma-skill", f"---\ndescription: {SENTINELLE} {chemins_machine.HOME}/secret\n---\n")
    _skill(user, ".cachee")
    _skill(projet, "skill-projet")
    (user / "sans-fichier").mkdir()
    (user / "fichier-seul").write_text("x", encoding="utf-8")
    claudeai.SEEN_FILE.write_text(json.dumps({"names": [
        "claude.ai Gmail", "claude.ai Vercel", "pas un connecteur", 42, "claude.ai a/b"]}), encoding="utf-8")
    config.ASTREL_SETTINGS_FILE.write_text(json.dumps({"values": {
        "connectors": {"claude.ai Gmail": False, "claude.ai Inconnu": True},
        "skills": {"ma-skill": "name-only", "absente": "off"}}}), encoding="utf-8")
    usage = {"mcp:claude_ai_Gmail:search_threads": 3, "mcp:claude_ai_Gmail:get_thread": 2, "mcp:claude_ai_X:y": 9}
    for lang in ("fr", "en"):
        data = capacites.describe(lang, usage=usage)
        dump = json.dumps(data, ensure_ascii=False)
        assert SENTINELLE not in dump and "/home/" not in dump and "SKILL.md" not in dump
        skills = {e["nom"]: e for e in _groupes(data)["skills"]["entrees"]}
        assert {"ma-skill", "skill-projet"} <= set(skills)
        assert not {".cachee", "sans-fichier", "fichier-seul", "absente"} & set(skills)
        claude = {e["nom"]: e for e in _groupes(data)["claudeai"]["entrees"]}
        assert set(claude) >= {"Gmail", "Vercel"} and "a/b" not in claude and "Inconnu" not in claude
        assert not [n for n in claude if "claude.ai" in n or "connecteur" in n]
    fr = capacites.describe("fr", usage=usage)
    assert _entree(fr, "skills", "ma-skill")["etat"] == "nom seul pour Astrel"
    assert _entree(fr, "skills", "skill-projet")["etat"] == "active"
    gmail = _entree(fr, "claudeai", "Gmail")
    assert gmail == {"nom": "Gmail", "etat": "bloqué pour Astrel", "usage": 5}
    # Vercel : bloqué par défaut (config.CLAUDE_DENIED_MCP_SERVERS), sauf choix contraire.
    assert _entree(fr, "claudeai", "Vercel")["etat"] == ("bloqué pour Astrel" if "claude.ai Vercel"
                                                         in config.CLAUDE_DENIED_MCP_SERVERS else "actif pour Astrel")
    assert [g["id"] for g in fr["groupes"]].index("claudeai") == [g["id"] for g in fr["groupes"]].index("connecteurs") + 1


def test_skills_et_claudeai_du_coeur_prioritaires_et_filtres():
    data = capacites.describe("fr", skills={"a": "off", "b": "user-invocable-only", "../evil": "on",
                                            "/abs": "on", "": "on", 3: "on", "c": "inconnu"},
                              claudeai={"claude.ai X": True, "claude.ai a/b": True, "Gmail": True, 7: True})
    assert [e["nom"] for e in _groupes(data)["skills"]["entrees"]] == ["a", "b", "c"]
    assert _entree(data, "skills", "a")["etat"] == "coupée pour Astrel"
    assert _entree(data, "skills", "b")["etat"] == "à la demande seulement"
    assert _entree(data, "skills", "c")["etat"] == "active"
    assert [e["nom"] for e in _groupes(data)["claudeai"]["entrees"]] == ["X"]
    vide = capacites.describe("fr", skills={}, claudeai={})
    assert "skills" not in _groupes(vide) and "claudeai" not in _groupes(vide)


def test_commande_du_coeur_refusee_a_claude_et_decoupee_au_socket(astrel, monkeypatch):
    """`capacites_get` (lot 2) : refusée à Claude comme guide_get ; le HUD reçoit tout, le canal de
    contrôle (≤ 4096 octets) le sommaire puis un groupe par appel ; états de la page Réglages."""
    from astrel.settings import reglages
    j, _, _ = astrel
    vus = {"skills": {"ma-skill": "off"}, "connectors": {"claude.ai Gmail": False}}
    monkeypatch.setattr(reglages, "value", lambda key: vus[key])
    refus = j.on_command({"cmd": "capacites_get", "_from_claude": True})
    assert refus["ok"] is False and "Claude" in refus["error"]
    tout = j.on_command({"cmd": "capacites_get", "lang": "en", "_via": "hud"})
    assert tout["ok"] and tout["lang"] == "en" and all("entrees" in g for g in tout["groupes"])
    assert _entree(tout, "skills", "ma-skill")["etat"] == i18n.tr("coupée pour Astrel", "en")
    assert _entree(tout, "claudeai", "Gmail")["etat"] == i18n.tr("bloqué pour Astrel", "en")
    som = j.on_command({"cmd": "capacites_get", "lang": "fr", "_via": "socket"})
    assert som["ok"] and all("entrees" not in g and g["n"] for g in som["groupes"])
    assert len(json.dumps(som, ensure_ascii=False).encode()) < 4096
    one = j.on_command({"cmd": "capacites_get", "lang": "fr", "groupe": "skills", "_via": "socket"})
    assert [g["id"] for g in one["groupes"]] == ["skills"]
    assert j.on_command({"cmd": "capacites_get", "groupe": "nimporte"})["ok"] is False


def test_reglages_capteurs_tous_les_capteurs(astrel):
    """23/10 (demande de l'utilisateur) : le panneau du HUD ne garde que WATCH ; Réglages › Capteurs les
    montre tous et les allume ou les éteint (même chemin que la carte : `sensors.set`)."""
    from astrel.sensors import sensors as capt
    from astrel.settings import reglages
    j, _, _ = astrel
    src = "".join(f.read_text(encoding="utf-8") for f in [config.ROOT / "astrel" / "core.py",
                                                          *(config.ROOT / "astrel" / "coeur").glob("*.py")])
    assert 'reglages.bind("capteurs", view=self._capteurs_view, run=self._capteurs_action)' in src
    reglages.bind("capteurs", view=j._capteurs_view, run=j._capteurs_action)  # le faux cœur ne lie pas les réglages
    avant = {i["name"]: i["on"] for i in capt.snapshot()}
    try:
        vue = reglages.view("capteurs")
        assert [ln["label"] for ln in vue["value"]["lines"]] == [
            j._CAPTEURS_NOMS.get(n, n) for n in avant]  # tous, nommés en mots
        holo = "off:HOLO" if avant["HOLO"] else "on:HOLO"
        assert holo in [a["id"] for a in vue["actions"]]
        r = j.on_command({"cmd": "settings_action", "key": "capteurs", "action": holo})
        assert r["ok"] and r["text"].startswith("Gestes : ")
        assert {i["name"]: i["on"] for i in capt.snapshot()}["HOLO"] is (not avant["HOLO"])
        assert j.on_command({"cmd": "settings_action", "key": "capteurs", "action": "on:INCONNU"})["ok"] is False
        assert j.on_command({"cmd": "settings_action", "key": "capteurs", "action": holo,
                             "_from_claude": True})["ok"] is False  # page Réglages : l'utilisateur seul
    finally:
        for name, on in avant.items():
            capt.set(name, on)


def test_cinq_cents_skills_sous_4096_octets():
    skills = {f"skill-{i:03d}-" + "x" * 80: "on" for i in range(500)}
    connecteurs = {f"claude.ai Connecteur {i} " + "é" * 90: True for i in range(80)}
    for lang in ("fr", "en"):
        data = capacites.describe(lang, skills=skills, claudeai=connecteurs)
        for gid, total in (("skills", 500), ("claudeai", 80)):
            g = _groupes(data)[gid]
            n = len(json.dumps({"ok": True, "lang": lang, "groupes": [g]}, ensure_ascii=False).encode())
            assert n < 4096, (lang, gid, n)
            reste = next(e for e in g["entrees"] if e["nom"].startswith("+"))
            assert int(reste["nom"][1:]) == total - (len(g["entrees"]) - 1)
            assert all(len(e["nom"]) <= capacites.CLAUDEAI_NOM_MAX for e in g["entrees"])
        assert len(json.dumps(capacites.sommaire(lang, skills=skills, claudeai=connecteurs),
                              ensure_ascii=False).encode()) < 4096
