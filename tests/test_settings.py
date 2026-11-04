"""Page « Réglages » du HUD (15/10, docs/CONTRAT-REGLAGES-2026-10-15.md) : description
générique, validation, fichier atomique, valeurs imposées par ASTREL_*, « en attente »
jusqu'au redémarrage, commandes refusées à Claude, lecture par le cerveau et mcp.json."""
import io
import json
import threading
import time

import pytest

from astrel import brain, config, core, guard, mcp_config, relief
from astrel.settings import ITEMS, SettingError, reglages
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)

# 17/10 (plan P4) : compte, premier lancement, voix et audio. `LIVE` : branchés par le cœur
# (grisés sans lui), ou sortie du son par pactl.
LIVE = {"audio.mic", "audio.voice", "audio.wake", "audio.output", "audio.followup", "audio.followup_s", "systeme.autostart", "systeme.bubble",
        "systeme.hotkeys"}
CONTRACT_KEYS = {"compte", "demarrage", "diagnostic", *LIVE, "audio.test", "stt_language",
                 "audio.calibrage",  # 02/11 : assistant de calibrage du micro (bouton du HUD)
                 "conversation_language",  # 02/11 : conversation anglaise (astrel/langue.py)
                 "hors_ligne", "confidentialite", "history_keep_days", "donnees", "maj", "whatsapp", "browser.main_visible", "browser.extra_visible", "browser.extra_count",
                 "images_mode", "images_ordre", "images_manuel", "video_choix", "connectors", "skills",
                 "plugins", "agent_alert_tokens", "agent_alert_s", "session_idle_reset_s",
                 "context_relief_tokens", "quota_alert_pct", "brain_effort", "plan_mode", "rappel",
                 "garde_fou_levee",
                 # 24/10 (plan P29 lot B) : cases de confirmation (résumé et boutons, une grille par groupe)
                 "confirmations", "confirmations.garde_fou", "confirmations.secrets", "confirmations.fichiers",
                 "confirmations.systeme", "confirmations.git", "confirmations.exterieur", "confirmations.autres",
                 # 19/10 (plan P8) : offre de modèle, reprise automatique, modèle local
                 "quota_offer", "quota_offer_pct", "reprise_auto", "quota_refresh",
                 "quota_arret", "quota_note_pct", "quota_stop_pct",  # 24/10 (plan P34)
                 "quota_reprise",  # 27/10 : relance des agents arrêtés pour le quota (auto, carte, rien)
                 "local.mode", "local.materiel", "local.modeles",
                 # 20/10 (plan P11) : fournisseur du cerveau, clés et passerelle, alerte de dépense
                 "fournisseur", "fournisseur.cles", "depense_alerte_usd",
                 # 20/10 : langue de l'interface (astrel/i18n.py)
                 "ui_language",
                 # 21/10 (plan P17) : clair ou sombre, accent (astrel/theme.py) ; 25/10 (plan P35) : plus
                 # de choix du thème, Premium seul
                 "theme_mode", "theme_accent",
                 # 22/10 (plan P23) : affichage Simple / Avancé du HUD
                 "affichage",
                 # 23/10 (demande de l'utilisateur, HUD allégé) : Réglages › Capteurs, tous les capteurs
                 "capteurs",
                 # 23/10 (plan P31) : nom affiché du produit (« Astrel » par défaut)
                 "assistant_name",
                 # 25/10 (plan P36) : mot d'éveil « Astrel » : ancien mot gardé ou non, variantes du nom
                 "wake_legacy", "wake_names",
                 # 24/10 (plan P32) : initiatives d'Astrel (brief du matin)
                 "initiatives_routines", "initiatives", "initiatives_brief", "initiatives_quota",
                 "initiatives_echeances", "initiatives_courrier", "initiatives_max_jour", "initiatives_calme",
                 # 28/10 (hud-boutons-t10) : accès retirés de la barre du HUD (comptes rendus, mission, extinction)
                 "reports", "mission", "systeme.eteindre"}
# Branché par le cœur (`reglages.bind`) : grisé « Réglable quand Astrel tourne » sans lui.
CORE_BOUND = {"audio.calibrage", "local.mode", "capteurs"}
COMMON = {"key", "label", "help", "kind", "value", "default", "applies", "pending", "locked", "lock_reason"}


def items(payload):
    return {i["key"]: i for g in payload["groups"] for i in g["items"]}


# ------------------------------------------------------------------ description

def test_description_suit_le_contrat():
    d = reglages.describe()
    assert [g["id"] for g in d["groups"]] == ["compte", "fournisseur", "demarrage", "voix", "capteurs", "initiatives", "apparence", "confidentialite", "maj", "systeme", "whatsapp", "navigateurs", "images", "connecteurs", "skills", "plugins", "seuils",
                                              "limite", "cerveau", "modele_local", "garde_fou"]
    found = items(d)
    assert set(found) == CONTRACT_KEYS and d["restart_needed"] is False
    for key, it in found.items():
        assert COMMON <= set(it), key
        assert it["applies"] in ("hot", "brain", "mcp") and it["pending"] is False
        assert it["locked"] is False or key in LIVE | CORE_BOUND, key
        if it["kind"] in ("int", "float"):
            assert {"min", "max", "step", "unit"} <= set(it) and it["min"] <= it["value"] <= it["max"], key
        if it["kind"] in ("flags", "tri"):
            assert set(it["labels"]) == set(it["value"]), key
    assert found["brain_effort"]["kind"] == "choice" and {"value": "high", "label": "Élevé"} in found["brain_effort"]["choices"]
    conn = found["connectors"]
    assert conn["value"]["claude.ai Vercel"] is False and conn["value"]["claude.ai Gmail"] is True
    assert conn["labels"]["claude.ai Gmail"] == "Gmail"
    assert found["skills"]["value"]["founder-cfo"] == "name-only" and found["skills"]["value"]["run"] == "on"
    assert found["plugins"]["value"]["caveman@synced"] is False
    assert found["browser.extra_visible"]["value"] is False and found["quota_alert_pct"]["value"] == 85
    # Jamais exposés ici : autonomie, Total, arrêt d'urgence. 28/10 (hud-boutons-t10, décision transmise par
    # le coordinateur) : « mission » n'est qu'un bouton exécuté par le HUD (fenêtre de mission, commande
    # `mission start` refusée à Claude), sans rien à exécuter côté cœur.
    assert not any(w in k for k in found for w in ("autonom", "total", "estop"))
    assert found["mission"]["kind"] == "action" and [a.get("hud") for a in found["mission"]["actions"]] == ["mission_start"]
    assert reglages.action("mission", "lancer")["ok"] is False


# ------------------------------------------------------------------ validation

@pytest.mark.parametrize("key, value, why", [
    ("browser.main_visible", 3, "oui ou non"),
    ("browser.extra_count", 4, "au plus 3"),
    ("browser.extra_count", 1.5, "entier"),
    ("agent_alert_tokens", "beaucoup", "nombre"),
    ("agent_alert_s", 10, "au moins 60"),
    ("agent_alert_tokens", True, "nombre"),
    ("brain_effort", "turbo", "valeur inconnue"),
    ("connectors", {"claude.ai Inconnu": True}, "inconnu"),
    ("connectors", {"claude.ai Gmail": "oui"}, "vrai ou faux"),
    ("connectors", ["claude.ai Gmail"], "objet"),
    ("skills", {"run": "peut-être"}, "état inconnu"),
    ("autonomy", "autonome", "réglage inconnu"),
])
def test_validation(key, value, why):
    with pytest.raises(SettingError, match=why) as exc:
        reglages.set(key, value)
    assert exc.value.key == key
    assert not config.ASTREL_SETTINGS_FILE.exists()  # rien d'enregistré


def test_valeurs_acceptees_et_normalisees():
    assert reglages.set("browser.extra_count", "2") == 2
    assert reglages.set("agent_alert_s", 120.0) == 120 and isinstance(reglages.value("agent_alert_s"), int)
    assert reglages.set("browser.main_visible", "non") is False
    full = reglages.value("skills")
    full["run"] = "off"
    assert reglages.set("skills", full)["run"] == "off"
    assert reglages.set("connectors", {"claude.ai Vercel": True})["claude.ai Gmail"] is True  # partiel : complété


# ------------------------------------------------------------------ skills découvertes (19/10)

def _skill(root, name, with_file=True):
    d = root / name
    d.mkdir(parents=True)
    if with_file:
        (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    return d


def _snapshot(root):
    return sorted((str(p.relative_to(root)), p.stat().st_mtime_ns) for p in root.rglob("*"))


def test_skills_decouvertes_a_chaud(tmp_path, monkeypatch):
    """Faux dossier de skills : chaque skill qui a un SKILL.md apparaît (« on »), une skill
    ajoutée ou retirée se voit au prochain affichage, le repli intégré au CLI reste."""
    from astrel import settings

    user, work = tmp_path / "home-skills", tmp_path / "travail"
    monkeypatch.setattr(settings, "USER_SKILLS", user)
    monkeypatch.setattr(settings, "PROJECT_SKILLS", settings.Path(".claude") / "skills")
    monkeypatch.setattr(config, "WORKDIR", work)
    _skill(user, "tdd")
    _skill(user, "ui-ux-pro-max")
    _skill(user, "founder-cfo")                      # aussi dans le repli et les défauts
    _skill(user, "synced", with_file=False)          # pas de SKILL.md : ignoré
    _skill(user, ".cache")                           # dossier caché : ignoré
    _skill(user, "nom avec espace")                  # caractères refusés
    (user / "fichier-seul").write_text("x", encoding="utf-8")
    _skill(work / ".claude" / "skills", "skill-du-projet")

    skills = items(reglages.describe())["skills"]["value"]
    assert skills["tdd"] == "on" and skills["ui-ux-pro-max"] == "on" and skills["skill-du-projet"] == "on"
    assert skills["founder-cfo"] == "name-only" and skills["simplify"] == "on"  # repli du CLI gardé
    assert not {"synced", ".cache", "nom avec espace", "fichier-seul"} & set(skills)
    assert list(skills) == sorted(skills)

    # Réglable : un nom découvert est accepté et part dans `skillOverrides`.
    skills["tdd"] = "off"
    assert reglages.set("skills", skills)["tdd"] == "off"
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "claude-settings.json")
    assert json.loads(brain.write_settings().read_text(encoding="utf-8"))["skillOverrides"]["tdd"] == "off"

    # Ajout puis retrait, sans relance : visible au prochain affichage.
    _skill(user, "copywriting")
    assert items(reglages.describe())["skills"]["value"]["copywriting"] == "on"
    import shutil
    shutil.rmtree(user / "ui-ux-pro-max")
    assert "ui-ux-pro-max" not in reglages.value("skills")
    assert reglages.value("skills")["tdd"] == "off"  # le choix des autres reste

    with pytest.raises(SettingError, match="inconnu"):
        reglages.set("skills", {"../etc": "off"})


def test_skills_dossiers_en_lecture_seule(tmp_path, monkeypatch):
    """Rien n'est créé, modifié ni effacé dans les dossiers de skills, même après un choix ;
    dossier absent : seul le repli reste."""
    from astrel import settings

    user = tmp_path / "home-skills"
    monkeypatch.setattr(settings, "USER_SKILLS", user)
    assert set(reglages.value("skills")) == {*settings.KNOWN_SKILLS, *config.CLAUDE_SKILL_OVERRIDES}
    _skill(user, "evaluation")
    before = _snapshot(user)
    full = reglages.value("skills")
    full["evaluation"] = "name-only"
    reglages.set("skills", full)
    reglages.describe()
    assert _snapshot(user) == before


# ------------------------------------------------------------------ fichier

def test_fichier_atomique_relu_et_defaut_retire():
    reglages.set("brain_effort", "medium")
    reglages.set("connectors", {"claude.ai Figma": True})
    path = config.ASTREL_SETTINGS_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["values"]["brain_effort"] == "medium" and data["values"]["connectors"]["claude.ai Figma"] is True
    assert not path.with_name(path.name + ".tmp").exists()
    reglages.reset()  # relance d'Astrel : relu sur le disque
    assert reglages.value("brain_effort") == "medium" and reglages.value("connectors")["claude.ai Figma"] is True
    reglages.set("brain_effort", "high")  # revenu au défaut : retiré, le défaut suit le code
    assert "brain_effort" not in json.loads(path.read_text(encoding="utf-8"))["values"]


def test_fichier_abime_ou_valeur_fausse_ignores():
    path = config.ASTREL_SETTINGS_FILE
    path.write_text("{pas du json", encoding="utf-8")
    assert reglages.value("brain_effort") == "high"
    reglages.reset()
    path.write_text(json.dumps({"values": {"browser.extra_count": 99, "brain_effort": "low"}}), encoding="utf-8")
    assert reglages.value("browser.extra_count") == config.BROWSER_EXTRA and reglages.value("brain_effort") == "low"


@pytest.mark.parametrize("abime", ['{"version":1,"values":{"history_keep_days":30,', "[1, 2]",
                                   '{"values": 3}'])
def test_fichier_abime_en_cours_de_session_jamais_ecrase(abime):
    """21/10 (fiche P17-5) : settings.json abîmé en cours de session (édition à la main
    interrompue), puis un clic dans les Réglages : avant, le fichier devenait {theme_mode: dark}
    et les autres choix étaient perdus. Maintenant : dernières valeurs valides gardées,
    écriture refusée avec un message clair, fichier intact ; une fois réparé, tout repart."""
    path = config.ASTREL_SETTINGS_FILE
    reglages.set("history_keep_days", 30)
    reglages.set("brain_effort", "medium")
    path.write_text(abime, encoding="utf-8")
    assert reglages.value("history_keep_days") == 30 and reglages.value("brain_effort") == "medium"
    with pytest.raises(SettingError, match="illisible et n'a pas été écrasé"):
        reglages.set("theme_mode", "dark")
    assert path.read_text(encoding="utf-8") == abime  # rien de réécrit
    assert reglages.value("theme_mode") == ITEMS["theme_mode"].default()
    path.write_text(json.dumps({"version": 1, "values": {"history_keep_days": 30, "brain_effort": "low"}}),
                    encoding="utf-8")  # réparé à la main
    reglages.set("theme_mode", "dark")
    # P35 : `theme: premium` toujours réécrit (compatibilité bulle d'avant P35, settings.LEGACY_WRITE)
    assert json.loads(path.read_text(encoding="utf-8"))["values"] == {
        "history_keep_days": 30, "brain_effort": "low", "theme_mode": "dark", "theme": "premium"}


def test_fichier_abime_au_demarrage_defauts_et_ecriture_refusee():
    path = config.ASTREL_SETTINGS_FILE
    path.write_text("{pas du json", encoding="utf-8")
    reglages.reset()
    with pytest.raises(SettingError, match="illisible"):
        reglages.set("history_keep_days", 10)
    assert path.read_text(encoding="utf-8") == "{pas du json"
    path.write_text("  \n", encoding="utf-8")  # vide : rien à perdre, écriture permise
    reglages.set("history_keep_days", 10)
    assert json.loads(path.read_text(encoding="utf-8"))["values"]["history_keep_days"] == 10


# ------------------------------------------------------------------ imposé, en attente

def test_variable_astrel_impose_la_valeur(monkeypatch):
    reglages.set("brain_effort", "low")
    monkeypatch.setenv("ASTREL_CLAUDE_EFFORT", "high")
    it = items(reglages.describe())["brain_effort"]
    assert it["locked"] and "ASTREL_CLAUDE_EFFORT" in it["lock_reason"]
    assert reglages.value("brain_effort") == config.CLAUDE_EFFORT  # la variable prime sur le choix
    with pytest.raises(SettingError, match="ASTREL_CLAUDE_EFFORT"):
        reglages.set("brain_effort", "medium")


def test_navigateur_coupe_grise_les_navigateurs(monkeypatch):
    monkeypatch.setattr(config, "BROWSER_ENABLED", False)
    found = items(reglages.describe())
    assert all(found[k]["locked"] for k in ("browser.main_visible", "browser.extra_visible", "browser.extra_count"))
    assert not found["connectors"]["locked"]


def test_en_attente_jusqu_au_redemarrage():
    reglages.mark_applied("brain")
    reglages.mark_applied("mcp")
    reglages.set("agent_alert_tokens", 2_000_000)  # à chaud : jamais en attente
    assert not reglages.restart_needed()
    # 23/10 (plan P28) : l'effort du cerveau s'applique À CHAUD, il n'est jamais en attente ;
    # le fournisseur reste le réglage « au prochain démarrage du cerveau ».
    reglages.set("brain_effort", "low")
    assert not reglages.pending("brain_effort") and not reglages.restart_needed()
    reglages.set("fournisseur", "cle_claude")
    reglages.set("browser.extra_visible", True)
    found = items(reglages.describe())
    assert found["fournisseur"]["pending"] and found["browser.extra_visible"]["pending"]
    assert found["brain_effort"]["applies"] == "hot"
    assert reglages.describe()["restart_needed"]
    reglages.mark_applied("brain")  # le cerveau a redémarré seul (panne, session neuve)
    assert not reglages.pending("fournisseur") and reglages.pending("browser.extra_visible")
    reglages.mark_applied("mcp")
    assert not reglages.restart_needed()
    reglages.set("fournisseur", "abonnement")
    reglages.set("fournisseur", "cle_claude")  # revenu à la valeur prise : plus en attente
    assert not reglages.pending("fournisseur")


# ------------------------------------------------------------------ lecteurs : cerveau, mcp.json, seuils

def test_reglages_du_cli_suivent_les_choix(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "s.json")
    conn = reglages.value("connectors")
    conn.update({"claude.ai Vercel": True, "claude.ai Gmail": False})
    reglages.set("connectors", conn)
    skills = reglages.value("skills")
    skills.update({"founder-cfo": "on", "run": "off"})
    reglages.set("skills", skills)
    reglages.set("plugins", {"caveman@synced": True})
    data = json.loads(brain.write_settings().read_text(encoding="utf-8"))
    denied = {d["serverName"] for d in data["deniedMcpServers"]}
    assert "claude.ai Gmail" in denied and "claude.ai Vercel" not in denied and "claude.ai Figma" in denied
    assert data["skillOverrides"]["run"] == "off" and "founder-cfo" not in data["skillOverrides"]
    assert data["enabledPlugins"] == {}


def test_effort_et_marque_au_demarrage_du_cerveau(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "s.json")
    monkeypatch.setattr(config, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(config, "MCP_CONFIG_FILE", tmp_path / "absent.json")
    launched = []

    class FakeProc:
        def __init__(self):
            self.pid, self.stdin, self.stdout, self.stderr = 0, None, io.StringIO(""), io.StringIO("")

        def poll(self):
            return 0

    monkeypatch.setattr(brain.subprocess, "Popen", lambda cmd, **k: launched.append(cmd) or FakeProc())
    reglages.set("brain_effort", "medium")
    b = brain.Brain()
    b.start()
    assert launched[-1][launched[-1].index("--effort") + 1] == "medium"
    reglages.set("brain_effort", "low")
    b.start()
    assert "low" in launched[-1] and not reglages.restart_needed()
    reglages.set("brain_effort", "")  # « réglage du CLI » : pas d'option
    b.start()
    assert "--effort" not in launched[-1]


def test_mcp_json_navigateurs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MCP_CONFIG_FILE", tmp_path / "mcp.json")
    cli = tmp_path / "cli.js"
    cli.write_text("")
    monkeypatch.setattr(mcp_config, "PLAYWRIGHT_CLI", cli)
    monkeypatch.setattr(mcp_config, "_node", lambda: "/usr/bin/node")
    monkeypatch.setattr(config, "BROWSER_ENABLED", True)
    monkeypatch.setattr(config, "BROWSER_HEADLESS", False)
    servers = mcp_config.write()["mcpServers"]
    assert "--headless" not in servers["playwright"]["args"]
    assert all("--headless" in servers[f"playwright{n}"]["args"] for n in range(2, 2 + config.BROWSER_EXTRA))
    reglages.set("browser.main_visible", False)
    reglages.set("browser.extra_visible", True)
    reglages.set("browser.extra_count", 1)
    assert reglages.restart_needed()
    servers = mcp_config.write()["mcpServers"]
    assert "--headless" in servers["playwright"]["args"]
    assert "--headless" not in servers["playwright2"]["args"] and "playwright3" not in servers
    assert not reglages.pending("browser.extra_count")  # mcp.json réécrit : pris


def test_seuils_a_chaud(astrel, monkeypatch):
    reglages.set("agent_alert_tokens", 1_000_000)
    reglages.set("agent_alert_s", 600)
    assert core._alert_limits() == (1_000_000, 600.0)
    reglages.set("quota_alert_pct", 70)
    assert core.Astrel._quota_alert() == 0.7
    reglages.set("context_relief_tokens", 0)
    j, _, _ = astrel
    j.brain.context_tokens = 900_000
    assert not relief.Relief(j).due()
    reglages.set("context_relief_tokens", 200_000)
    assert relief.Relief(j).due()


# ------------------------------------------------------------------ commandes du cœur

def test_commandes_get_et_set(astrel, published):
    j, _, _ = astrel
    res = j.on_command({"cmd": "settings_get", "_via": "hud", "_from_claude": False})
    assert res["ok"] and set(items(res)) == CONTRACT_KEYS
    assert of(published, "settings")[-1]["groups"] == res["groups"]
    res = j.on_command({"cmd": "settings_set", "key": "browser.extra_count", "value": 1, "_from_claude": False})
    assert res["ok"] and items(res)["browser.extra_count"]["value"] == 1
    assert items(of(published, "settings")[-1])["browser.extra_count"]["value"] == 1
    # Terminal : --send "settings_set brain_effort low"
    assert j.on_command({"cmd": "settings_set brain_effort low"})["ok"] and reglages.value("brain_effort") == "low"


def test_erreur_de_validation_en_notice(astrel, published):
    j, _, _ = astrel
    res = j.on_command({"cmd": "settings_set", "key": "quota_alert_pct", "value": 30, "_from_claude": False})
    assert res["ok"] is False and res["setting_error"]["key"] == "quota_alert_pct"
    note = of(published, "notice")[-1]
    assert note["setting_error"] == {"key": "quota_alert_pct", "message": res["setting_error"]["message"]}
    assert "au moins 50" in note["setting_error"]["message"]
    assert of(published, "settings") and reglages.value("quota_alert_pct") == 85


@pytest.mark.parametrize("cmd", [
    {"cmd": "settings_get"},
    {"cmd": "settings_set", "key": "connectors", "value": {"claude.ai Vercel": True}},
    {"cmd": "settings_apply"},
    {"cmd": "settings_action", "key": "compte", "action": "refresh"},
])
def test_refuse_a_claude(astrel, published, monkeypatch, cmd):
    j, _, _ = astrel
    applied = []
    monkeypatch.setattr(j, "_apply_settings", lambda *a: applied.append(a), raising=False)
    reglages.mark_applied("brain")
    reglages.set("fournisseur", "cle_claude")  # un réglage en attente : « apply » aurait de quoi faire
    res = j.on_command({**cmd, "_from_claude": True, "_via": "hud"})
    assert res["ok"] is False and "Claude" in res["error"]
    assert reglages.value("connectors")["claude.ai Vercel"] is False and applied == []
    assert not of(published, "settings")
    if cmd["cmd"] == "settings_get":
        # 21/10 (fiche P17-5) : la lecture de connexion (`askTheme` à chaque hello) d'un HUD
        # lancé par un agent ne prévient plus tous les HUD.
        assert not of(published, "notice")
    else:
        assert "Claude a tenté" in of(published, "notice")[-1]["text"]


def test_hud_de_claude_qui_se_reconnecte_sans_notice(astrel, published):
    j, _, _ = astrel
    for _ in range(3):  # trois hello d'un Chrome lancé par un agent
        assert j.on_command({"cmd": "settings_get", "_from_claude": True, "_via": "hud"})["ok"] is False
    assert not of(published, "notice") and not of(published, "settings")


def test_appliquer_relance_le_cerveau(astrel, published, monkeypatch, tmp_path):
    j, _, _ = astrel
    monkeypatch.setattr(config, "MCP_CONFIG_FILE", tmp_path / "mcp.json")
    monkeypatch.setattr(config, "BROWSER_ENABLED", True)
    monkeypatch.setattr(mcp_config, "_node", lambda: None)  # pas de navigateur déclaré : inutile ici
    res = j.on_command({"cmd": "settings_apply"})
    assert res == {"ok": True, "applied": False, **reglages.describe()}  # rien en attente : rien relancé
    restarts = []

    def restart(fresh=False):
        restarts.append(fresh)
        reglages.mark_applied("brain")

    j.brain.restart = restart
    j._turn_lock = threading.Lock()
    mcp_config.write()
    reglages.mark_applied("brain")
    reglages.set("browser.extra_count", 1)
    reglages.set("fournisseur", "cle_claude")
    j._tasks["t1"] = {"id": "t1"}
    started = []
    monkeypatch.setattr(j, "_apply_settings", lambda count=0: started.append(count), raising=False)
    assert j.on_command({"cmd": "settings_apply"}) == {"ok": True, "applied": True, "tasks": 1}
    deadline = time.time() + 2
    while not started and time.time() < deadline:
        time.sleep(0.01)
    assert started == [1]  # relance hors du fil de la commande
    core.Astrel._apply_settings(j, 1)
    assert restarts == [False]  # session reprise, pas repartie à neuf
    assert j._tasks == {} and of(published, "tasks")[-1]["items"] == []
    assert not reglages.restart_needed() and of(published, "settings")[-1]["restart_needed"] is False
    assert "1 tâche(s) de fond coupée(s)" in of(published, "notice")[-1]["text"]


# ------------------------------------------------------------------ garde-fou

def test_fichier_protege_contre_claude():
    path = str(config.ROOT / "runtime" / "settings.json")
    assert guard.assess("Write", {"file_path": path}, mode="autonome").needs_confirm
    v = guard.assess("Bash", {"command": "echo '{}' > runtime/settings.json"}, mode="autonome")
    assert v.needs_confirm and v.level == guard.CRITICAL
    assert not guard.assess("Bash", {"command": "cat runtime/settings.json"}, mode="prudent").needs_confirm
    # ~/.claude/settings.json ou .vscode/settings.json ne sont pas ce fichier.
    assert not guard.assess("Bash", {"command": "cat ~/.claude/settings.json"}, mode="prudent").needs_confirm


def test_tous_les_reglages_ont_une_aide():
    for key, item in ITEMS.items():
        assert item.label and item.help.endswith((".", ")")), key


# ------------------------------------------------------------------ nom affiché (23/10, plan P31)

def test_nom_affiche_reglage_et_source_unique(monkeypatch):
    """Réglage `assistant_name` : « Astrel » par défaut, une ligne courte sans chevrons, vide = défaut ;
    `i18n.assistant_name()` (bulle, textes du cœur) lit la MÊME valeur, à chaud ; ASTREL_ASSISTANT_NAME l'impose."""
    from astrel import i18n
    monkeypatch.delenv("ASTREL_ASSISTANT_NAME", raising=False)
    it = items(reglages.describe())["assistant_name"]
    assert it["kind"] == "text" and it["value"] == it["default"] == "Astrel" and it["max"] == 32
    assert it["applies"] == "hot" and i18n.assistant_name() == "Astrel"
    for bad, why in (("<b>Nova</b>", "chevrons"), ("No\nva", "une seule ligne"), ("x" * 33, "au plus 32"), (7, "texte")):
        with pytest.raises(SettingError, match=why):
            reglages.set("assistant_name", bad)
    assert reglages.set("assistant_name", "  Nova   Prime ") == "Nova Prime"
    assert i18n.assistant_name() == "Nova Prime"                       # même source, relue à chaud
    assert i18n.tr("réponse illisible d'Astrel : réponds dans le HUD", "fr") == "réponse illisible de Nova Prime : réponds dans le HUD"
    assert reglages.set("assistant_name", "") == "Astrel"               # vide = défaut, retiré du fichier
    assert "assistant_name" not in json.loads(config.ASTREL_SETTINGS_FILE.read_text(encoding="utf-8"))["values"]
    assert i18n.assistant_name() == "Astrel"
    assert i18n.tr("réponse illisible d'Astrel : réponds dans le HUD", "fr") == "réponse illisible d'Astrel : réponds dans le HUD"
    assert i18n.tr("réponse illisible d'Astrel : réponds dans le HUD", "en") == "unreadable reply from Astrel: reply in the HUD"
    monkeypatch.setenv("ASTREL_ASSISTANT_NAME", "Vega")
    assert items(reglages.describe())["assistant_name"]["locked"] and i18n.assistant_name() == "Vega"


def test_memoire_automatique_du_cli_coupee_pour_le_cerveau(monkeypatch, tmp_path):
    """04/11 (T49) : le MEMORY.md du CLI (sessions de l'utilisateur dans le même dossier, périmé) n'est pas injecté au
    cerveau d'Astrel : réglage `autoMemoryEnabled` ET variable d'environnement (interrupteur non documenté)."""
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "claude-settings.json")
    assert json.loads(brain.write_settings().read_text(encoding="utf-8"))["autoMemoryEnabled"] is False
    env = brain._sans_memoire_auto({"PATH": "/usr/bin"})
    assert env == {"PATH": "/usr/bin", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
    src = (config.ROOT / "astrel" / "brain.py").read_text(encoding="utf-8")
    assert src.count("env=_sans_memoire_auto(fournisseurs.env_cerveau(") == 2  # cerveau et passerelle
