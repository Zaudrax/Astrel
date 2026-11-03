import atexit
import os
import shutil
import tempfile
from collections import OrderedDict

# Dossier des projets JETABLE, le même pour toute la suite : les tests ne dépendent ni de
# ~/Projets, ni de config/profile.json, ni d'un ASTREL_WORKDIR de la machine. Dans le dossier
# personnel comme ~/Projets (pas dans /tmp, que le garde-fou traite à part), retiré à la fin.
_PROJETS = tempfile.mkdtemp(prefix="astrel-projets-",
                            dir=os.environ.get("ASTREL_TEST_PROJETS_DIR") or os.path.expanduser("~"))
os.environ["ASTREL_WORKDIR"] = _PROJETS
atexit.register(shutil.rmtree, _PROJETS, True)

import pytest  # noqa: E402
from pathlib import Path

from astrel import config
from astrel.floor import backlog, floor
from astrel.reports import reports

# Connecteurs claude.ai d'exemple, triés comme la sortie de `claude mcp list`
# (tests/test_claudeai.py) : ceux que les tests des réglages s'attendent à trouver.
CONNECTEURS_DE_TEST = (
    "claude.ai Canva", "claude.ai Claude Docs", "claude.ai Dropbox", "claude.ai Figma", "claude.ai Gmail",
    "claude.ai Google Calendar", "claude.ai Google Drive", "claude.ai harvest", "claude.ai Mailroom",
    "claude.ai Monday.com", "claude.ai Vercel",
)


@pytest.fixture(autouse=True)
def _canal_de_controle_isole(tmp_path, monkeypatch):
    """19/10 : `cli.open_hud` interroge le cœur (`hud_pages`, `hud_replace`). Sans ceci,
    un test qui l'appelle parlerait au VRAI Astrel et fermerait le HUD de l'utilisateur.
    Un test qui monte son propre canal remplace ce chemin."""
    monkeypatch.setattr(config, "CONTROL_SOCKET", tmp_path / "control-isole.sock")


@pytest.fixture(scope="session", autouse=True)
def _filet_de_session_images_videos(tmp_path_factory):
    """30/10 : filet pour TOUTE la session. Un fil de génération (images, vidéos) qui finit APRÈS son
    test écrivait dans les vrais logs/images.log, logs/videos.log, compteurs et dossiers de sortie :
    le `monkeypatch` du test avait déjà remis les chemins d'origine. Ici, l'« origine » devient un
    dossier temporaire de la session ; les fixtures `lieux` des tests restent la vraie isolation.
    Les objets globaux (`images_ia.images`, `videos_ia.videos`) gardaient en plus le chemin RÉEL de
    l'état des quotas, pris à l'import : leur `Etat` est redirigé aussi."""
    from astrel import attachments, graph, images_ia, medias, videos_ia

    base = tmp_path_factory.mktemp("filet-ia-web")
    mp = pytest.MonkeyPatch()
    mp.setattr(config, "IMAGES_RUNTIME", base / "imagegen")
    mp.setattr(config, "IMAGES_PROFILS", base / "imagegen" / "profils")
    mp.setattr(config, "IMAGES_ETAT_FILE", base / "imagegen" / "etat.json")
    mp.setattr(config, "IMAGES_DEBUG_DIR", base / "imagegen" / "debug")
    mp.setattr(config, "IMAGES_LOG", base / "images.log")
    mp.setattr(config, "IMAGES_SORTIE", base / "Images-IA")
    mp.setattr(videos_ia, "SORTIE", base / "Videos-IA")
    mp.setattr(videos_ia, "LOG", base / "videos.log")
    mp.setattr(images_ia.images.etat, "path", base / "imagegen" / "etat.json")
    mp.setattr(videos_ia.videos.etat, "path", base / "imagegen" / "etat.json")
    mp.setattr(attachments, "ATTACH_DIR", base / "attachments")
    mp.setattr(medias, "MEDIA_DIR", base / "medias")
    mp.setattr(graph, "USAGE_FILE", base / "graph-usage.json")
    mp.setattr(config, "PROMPT_FILE", base / "prompt-systeme.txt")  # 03/11 : jamais le vrai runtime/
    yield
    mp.undo()


@pytest.fixture(autouse=True)
def _compteurs_du_graphe_isoles(tmp_path, monkeypatch):
    """30/10 : chaque appel d'outil simulé passe par `graph.record_use`, qui réécrit le fichier des
    compteurs : sans ceci, test_relief, test_detail_agent, test_equipe… gonflaient le VRAI
    runtime/graph-usage.json (centaines d'écritures par suite)."""
    from astrel import graph

    monkeypatch.setattr(graph, "USAGE_FILE", tmp_path / "graph-usage.json")


@pytest.fixture(autouse=True)
def _comptes_rendus_isoles(tmp_path, monkeypatch):
    """Le réglage des comptes rendus de la machine (runtime/reports.txt,
    ASTREL_REPORTS) ne doit changer aucun test : chacun part du défaut, « demande »."""
    monkeypatch.setattr(config, "REPORTS_FILE", tmp_path / "reports.txt")
    monkeypatch.delenv("ASTREL_REPORTS", raising=False)
    monkeypatch.setattr(reports, "_mode", None)
    monkeypatch.setattr(reports, "_items", OrderedDict())


@pytest.fixture(autouse=True)
def _hors_du_cerveau(monkeypatch):
    """Plan P6 : lancés par le Bash d'Astrel, les tests hériteraient du marqueur
    du cerveau (ASTREL_BRAIN) et passeraient tous pour Claude. Le canal local est
    celui du système (pas de ASTREL_IPC imposé de dehors)."""
    monkeypatch.delenv("ASTREL_BRAIN", raising=False)
    monkeypatch.delenv("ASTREL_IPC", raising=False)


@pytest.fixture(autouse=True)
def _moteur_audio_fixe(monkeypatch):
    """Les tests simulent `paplay`/`pactl` (moteur « pulse ») : même chemin de code
    sur macOS et Windows en CI (P2, lot 3), où le défaut serait sounddevice. Sous
    Linux, rien ne change (pulse est déjà le défaut)."""
    monkeypatch.setenv("ASTREL_AUDIO_ENGINE", "pulse")


@pytest.fixture(autouse=True)
def _parole_isolee(monkeypatch):
    """Personne n'a la parole au début d'un test, la file est vide, délai de grâce par défaut."""
    monkeypatch.setattr(config, "SPEECH_GRACE_S", 3.0)
    floor.reset()
    backlog.clear()
    yield
    floor.reset()
    backlog.clear()


@pytest.fixture(autouse=True)
def _p12_isole(tmp_path, monkeypatch):
    """20/10 (plan P12) : ni la politique d'outils de la machine (hooks/tool-policy.json), ni
    ses interrupteurs rtk/context7 ; un test qui en veut un le pose lui-même."""
    from astrel import guard

    monkeypatch.setattr(guard, "TOOL_POLICY_FILE", tmp_path / "tool-policy.json")
    monkeypatch.setattr(config, "RTK_SWITCH_FILE", tmp_path / "rtk.txt")
    monkeypatch.setattr(config, "CONTEXT7_SWITCH_FILE", tmp_path / "context7.txt")


@pytest.fixture(autouse=True)
def _suivi_isole(tmp_path, monkeypatch):
    """01/11 : instantanés avant écriture et série de refus du disjoncteur : jamais ceux de la vraie
    machine (un test du hook de permission qui refuse écrirait sinon dans runtime/garde-refus/)."""
    from astrel import guard, instantanes

    monkeypatch.setattr(guard, "REFUS_DIR", tmp_path / "garde-refus")
    monkeypatch.setattr(instantanes, "DIR", tmp_path / "instantanes")
    monkeypatch.setattr(instantanes, "INDEX", tmp_path / "instantanes" / "index.json")
    monkeypatch.setattr(instantanes, "VERROU", tmp_path / "instantanes" / ".verrou")


@pytest.fixture(autouse=True)
def _economie_isolee(tmp_path, monkeypatch):
    """Arrêt d'urgence, amorce de relève, agents écrits, journal de routage et index
    de l'historique : jamais ceux de la vraie machine (un ESTOP posé par l'utilisateur
    ne doit changer aucun test, et un test ne doit rien écrire dans runtime/)."""
    from astrel import agents

    monkeypatch.setattr(config, "ESTOP_FILE", tmp_path / "ESTOP")
    monkeypatch.setattr(config, "RELIEF_AMORCE_FILE", tmp_path / "relief-amorce")
    monkeypatch.setattr(config, "RELIEF_REPRISE_FILE", tmp_path / "relief-reprise.json")
    monkeypatch.setattr(config, "BOOT_FRESH_FILE", tmp_path / "boot-fresh.json")
    monkeypatch.setattr(config, "HISTORY_DB", tmp_path / "history.db")
    monkeypatch.setattr(agents, "AGENTS_FILE", tmp_path / "agents.json")
    monkeypatch.setattr(agents, "ROUTE_LOG", tmp_path / "route.jsonl")
    # Plan P28 : effort par métier choisi par l'utilisateur, et ce que le CLI en marche a reçu.
    monkeypatch.setattr(agents, "EFFORT_FILE", tmp_path / "agent-effort.json")
    monkeypatch.setattr(agents, "_written", None)
    monkeypatch.setattr(agents, "_loaded", None)
    # Reprise après coupure (15/10) : ni l'état ni les réflexes de la vraie machine.
    from astrel.senses import reflex

    monkeypatch.setattr(config, "REPRISE_FILE", tmp_path / "reprise.json")
    monkeypatch.setattr(reflex, "RULES_FILE", tmp_path / "reflexes.json")
    # Plan P34 : arrêt des agents sur quota, ni l'état ni l'ordre de la vraie machine.
    monkeypatch.setattr(config, "QUOTA_ARRET_FILE", tmp_path / "quota-arret.json")
    monkeypatch.setattr(config, "QUOTA_ORDRE_FILE", tmp_path / "quota-ordre.json")
    monkeypatch.setattr(config, "QUOTA_ORDRE_DIR", tmp_path / "quota-ordre.d")


@pytest.fixture(autouse=True)
def _memoire_isolee(tmp_path, monkeypatch):
    """21/10 (plan P20) : ni l'état mémoire de la machine (runtime/memoire.json, qui pourrait
    dire « blocage »), ni son interrupteur, ni son journal ; un test qui en veut un le pose."""
    from astrel import memoire

    monkeypatch.setattr(memoire, "STATE_FILE", tmp_path / "memoire.json")
    monkeypatch.setattr(memoire, "SWITCH_FILE", tmp_path / "memoire-blocage.txt")
    monkeypatch.setattr(memoire, "JOURNAL", tmp_path / "memoire.jsonl")
    monkeypatch.setattr(memoire, "_top_cache", None)
    monkeypatch.delenv("ASTREL_MEMOIRE_BLOCAGE", raising=False)
    monkeypatch.delenv("ASTREL_MEMOIRE_FILE", raising=False)


@pytest.fixture(autouse=True)
def _index_memoire_isole(tmp_path, monkeypatch):
    """Index FTS5 du coffre et notes déjà rappelées (15/10) : jamais runtime/memoire.db
    ni runtime/rappel-vus.json de la machine."""
    from astrel import rappel
    from astrel.tools import memory

    monkeypatch.setattr(memory, "INDEX_DB", tmp_path / "memoire.db")
    monkeypatch.setattr(rappel, "SEEN_FILE", tmp_path / "rappel-vus.json")


@pytest.fixture(autouse=True)
def _canal_ouvert_isole(tmp_path, monkeypatch):
    """Canal ouvert du HUD (19/10) : jamais le runtime/channel.txt de la machine. Un canal avec
    dossier de projet ajoute « [Canal x : …] » aux messages : deux tests de la relève, qui
    comparent le texte envoyé, échouaient dès que l'utilisateur ouvrait le canal « Astrel »."""
    monkeypatch.setattr(config, "CHANNEL_FILE", tmp_path / "channel.txt")


@pytest.fixture(autouse=True)
def _reglages_isoles(tmp_path, monkeypatch):
    """Page « Réglages » (15/10) : jamais le runtime/settings.json de la machine, ni
    une variable ASTREL_* qui imposerait une valeur ; état en mémoire oublié."""
    from astrel.settings import ITEMS, reglages

    monkeypatch.setattr(config, "ASTREL_SETTINGS_FILE", tmp_path / "settings.json")
    # Skills découvertes (19/10) : jamais le vrai ~/.claude/skills ni celui du dossier de travail
    # (chemin absolu : `config.WORKDIR / PROJECT_SKILLS` le garde tel quel).
    from astrel import settings as _settings

    monkeypatch.setattr(_settings, "USER_SKILLS", tmp_path / "skills-absent")
    monkeypatch.setattr(_settings, "PROJECT_SKILLS", tmp_path / "skills-projet-absent")
    # Connecteurs claude.ai (19/10, plan P10) : jamais le vrai `claude mcp list` ni
    # runtime/connecteurs-vus.json ; liste figée (celle du compte au 19/10), déjà « fraîche ».
    from astrel import claudeai

    monkeypatch.setattr(claudeai, "SEEN_FILE", tmp_path / "connecteurs-vus.json")
    monkeypatch.setattr(claudeai, "discover", lambda: list(CONNECTEURS_DE_TEST))
    found = claudeai.Connecteurs()
    found._names, found._at = list(CONNECTEURS_DE_TEST), float("inf")
    monkeypatch.setattr(claudeai, "connecteurs", found)
    # Icônes récupérées d'elles-mêmes (19/10) : AUCUN appel réseau réel, jamais le vrai
    # hud/assets/icons/auto ni runtime/icones-* (tests/test_icones_auto.py rebranche un faux réseau).
    from astrel import icones

    monkeypatch.setattr(icones, "_download", lambda url, max_bytes: None)
    monkeypatch.setattr(icones, "AUTO_DIR", tmp_path / "icones-auto")
    monkeypatch.setattr(icones, "STATE_FILE", tmp_path / "icones-auto.json")
    monkeypatch.setattr(icones, "DATA_FILE", tmp_path / "icones-cache" / "simple-icons.json")
    monkeypatch.setattr(icones, "_table_cache", None)
    for item in ITEMS.values():
        if item.env:
            monkeypatch.delenv(item.env, raising=False)
    reglages.reset()
    # 17/10 (plan P4) : sortie du son jamais lue par le vrai pactl (test_reglages_voix la débranche).
    reglages.bind("audio.output", get=lambda: "", put=lambda v: {"ok": False, "error": "test"}, options=lambda: [])
    # Groupe « Compte » : jamais le vrai `claude auth status` (fil de
    # fond, compte de l'utilisateur) ; état connecté figé, aucune relecture.
    from astrel import compte

    fixed = compte.Compte()
    fixed._state = {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max", "email": "",
                    "orgName": ""}
    fixed._at = float("inf")
    monkeypatch.setattr(compte, "compte", fixed)
    # Premier lancement et diagnostic : jamais tools/check_install.py en fond ; pas un
    # premier lancement (marqueur posé dans le dossier du test).
    from astrel import demarrage

    done = tmp_path / "first-run-done"
    done.write_text("test")
    monkeypatch.setattr(config, "FIRST_RUN_FILE", done)
    checked = demarrage.Demarrage()
    checked._result = {"checks": [], "bloquant": 0, "rapport": "(test)"}
    monkeypatch.setattr(demarrage, "demarrage", checked)
    # « Effacer la mémoire » / « Exporter » : jamais le vrai coffre ni ~/Astrel-export.
    from astrel import confidentialite

    monkeypatch.setattr(confidentialite, "_vault", lambda: tmp_path / "coffre-de-test")
    monkeypatch.setattr(config, "EXPORT_DIR", tmp_path / "Astrel-export")
    # Mises à jour : jamais git (ni fetch, ni update.py) ; dépôt figé « propre ».
    from astrel import maj

    frozen = maj.MiseAJour()
    frozen._local = {"git": True, "commit": "abc1234", "origin": "https://exemple.fr/astrel.git", "changed": 0}
    frozen._local_at = float("inf")
    monkeypatch.setattr(maj, "maj", frozen)
    # Système (démarrage, raccourcis) : ni systemctl ni gsettings ; grisé « indisponible ».
    from astrel import settings as settings_mod
    from astrel.platform import Indisponible

    def _no_system(name):
        raise Indisponible("indisponible pendant les tests.")

    monkeypatch.setattr(settings_mod, "_sys_state", _no_system)
    yield
    reglages.reset()


@pytest.fixture(autouse=True)
def _modele_local_isole(tmp_path, monkeypatch):
    """Plan P8 (19/10) : ni le vrai Ollama, ni le registre, ni le matériel de la machine, ni
    runtime/local-model.json. Ollama « injoignable » (port 9 fermé), profil factice rtx12."""
    from astrel import modeles_locaux
    from astrel.platform import materiel

    monkeypatch.setattr(config, "LOCAL_MODEL_FILE", tmp_path / "local-model.json")
    monkeypatch.setattr(config, "OLLAMA_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(config, "OLLAMA_REGISTRY", "")
    monkeypatch.setenv("ASTREL_MATERIEL_FAKE", "rtx12")
    materiel.forget()
    monkeypatch.setattr(modeles_locaux, "local", modeles_locaux.ModelesLocaux())
    yield
    materiel.forget()


@pytest.fixture(autouse=True)
def _modes_isoles(tmp_path, monkeypatch):
    """Mission, mode d'usage, fenêtre de suite (14/10) : jamais ceux de la machine."""
    monkeypatch.setattr(config, "MISSION_FILE", tmp_path / "mission.json")
    monkeypatch.setattr(config, "USAGE_FILE", tmp_path / "usage-mode.txt")
    monkeypatch.setattr(config, "FOLLOWUP_FILE", tmp_path / "followup.txt")
    monkeypatch.setattr(config, "FOLLOWUP_LAST_FILE", tmp_path / "followup-last.txt")  # 20/10, Réglages
    monkeypatch.setattr(config, "TOTAL_LOG", tmp_path / "autonome-total.log")
    monkeypatch.setattr(config, "LIFT_LOG", tmp_path / "garde-fou-leve.log")  # levée du garde-fou (P7)
    monkeypatch.delenv("ASTREL_USAGE_MODE", raising=False)
    # Micro coupé retenu (15/10) : un test qui coupe le micro ne touche pas au vrai.
    from astrel import core

    monkeypatch.setattr(core, "MUTE_FILE", tmp_path / "mic-muted")


@pytest.fixture(autouse=True)
def _p3_isole(tmp_path, monkeypatch):
    """Plan P3 (17/10) : bilan, banc d'essai, ménage de nuit du coffre : jamais le
    runtime/ de la machine."""
    from astrel import audit, banc, bilan
    from astrel.tools import vault_review

    monkeypatch.setattr(audit, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(bilan, "BILAN_DIR", tmp_path / "bilan")
    monkeypatch.setattr(banc, "BANC_DIR", tmp_path / "banc")
    monkeypatch.setattr(vault_review, "MENAGE_FILE", tmp_path / "vault-menage.json")
    monkeypatch.setattr(vault_review, "PROPOSALS_DIR", tmp_path / "vault-propositions")


@pytest.fixture(autouse=True)
def _plans_isoles(tmp_path, monkeypatch):
    """Mode plan (15/10) : jamais le runtime/plans/ de la machine ; état en mémoire oublié."""
    from astrel.plans import plans

    monkeypatch.setattr(config, "PLANS_DIR", tmp_path / "plans")
    monkeypatch.setattr(config, "PLAN_MODE", "manual")
    plans.reset()
    yield
    plans.reset()


@pytest.fixture(autouse=True)
def _todo_isolee(tmp_path, monkeypatch):
    """Plan P38 lot 3 (27/10) : un plan validé ou rejeté dans un test fait suivre sa carte, et chaque
    `publish` réécrit la note du coffre : jamais le runtime/todo.json, le coffre ni les fiches de la machine."""
    from astrel import todo as todo_mod

    monkeypatch.setattr(todo_mod, "TODO_FILE", tmp_path / "todo.json")
    monkeypatch.setattr(todo_mod, "JOURNAL_FILE", tmp_path / "todo-journal.jsonl")
    monkeypatch.setattr(todo_mod, "NOTE_FILE", tmp_path / "coffre-todo" / "TODO-PAR-PROJET.md")
    monkeypatch.setattr(todo_mod, "EQUIPE_DIR", tmp_path / "equipe-todo")
    todo_mod.todo.reset()
    yield
    todo_mod.todo.reset()


@pytest.fixture(autouse=True)
def _fournisseur_isole(tmp_path, monkeypatch):
    """20/10 (plan P11) : jamais le vrai config/secrets.json, ni le jeton de la passerelle de l'Astrel
    en marche, ni sa dépense du jour, ni la venv de la passerelle ; aucune passerelle ne survit."""
    from astrel import fournisseurs, passerelle

    for name in ("ASTREL_FOURNISSEUR", "ASTREL_PASSERELLE_OUTILS", "ASTREL_DEPENSE_ALERTE_USD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "SECRETS_FILE", tmp_path / "secrets.json")
    monkeypatch.setattr(config, "PASSERELLE_JETON_FILE", tmp_path / "passerelle-jeton")
    monkeypatch.setattr(config, "DEPENSE_FILE", tmp_path / "depense.json")
    monkeypatch.setattr(config, "PASSERELLE_VENV", tmp_path / "venv-passerelle-absente")
    monkeypatch.setattr(config, "PASSERELLE_OUTILS", False)
    monkeypatch.setattr(config, "FOURNISSEUR", "abonnement")
    monkeypatch.setattr(fournisseurs, "etat", fournisseurs.Etat())
    monkeypatch.setattr(fournisseurs, "depense", fournisseurs.Depense())
    gw = passerelle.Passerelle()
    monkeypatch.setattr(passerelle, "passerelle", gw)
    # 21/10 (plan P16) : ni l'état ni le verrou de la vraie connexion xAI ; un gardien neuf.
    from astrel import xai_oauth

    monkeypatch.setattr(xai_oauth, "ETAT_FILE", tmp_path / "xai-oauth-etat.json")
    monkeypatch.setattr(xai_oauth, "LOCK_FILE", tmp_path / "xai-oauth.lock")
    garde = xai_oauth.Gardien()
    monkeypatch.setattr(xai_oauth, "gardien", garde)
    monkeypatch.setattr(xai_oauth, "ouvrir", lambda url: None)  # aucune fenêtre
    # Jamais xAI : hors de x.ai, `_permise` arrête tout AVANT le réseau (les tests visent un faux serveur).
    for nom in ("ISSUER", "DEVICE_URL", "TOKEN_URL", "REVOKE_URL", "API_URL"):
        monkeypatch.setattr(xai_oauth, nom, "https://xai-interdit-en-test.invalid/")
    yield
    garde.arreter()
    gw.stop()


@pytest.fixture(autouse=True)
def _cases_confirmation_isolees(tmp_path, monkeypatch):
    """24/10 (plan P29 lot B) : jamais le vrai runtime/confirmations.json, sa clé ni son journal ;
    cases relues (défauts : aucun fichier dans le dossier du test)."""
    from astrel import guard
    from astrel.cases_confirmation import cases

    monkeypatch.setattr(guard, "CONFIRM_CASES_FILE", tmp_path / "confirmations.json")
    monkeypatch.setattr(guard, "CONFIRM_KEY_FILE", tmp_path / "confirmations-cle")
    monkeypatch.setattr(guard, "CONFIRM_CASES_LOG", tmp_path / "confirmations.log")
    cases.reset()
    yield
    cases.reset()


def remplacer_coeur(monkeypatch, nom, valeur):
    """`core.<nom>` remplacé, ET la même liaison dans chaque mixin du cœur qui l'importe (`astrel/coeur/`,
    découpe du 29/10/2026) : sinon une méthode déplacée garderait l'objet d'origine (canaux, questions…)."""
    import importlib
    import pkgutil

    from astrel import coeur, core

    monkeypatch.setattr(core, nom, valeur)
    for m in pkgutil.iter_modules(coeur.__path__):
        mod = importlib.import_module(f"astrel.coeur.{m.name}")
        if hasattr(mod, nom):
            monkeypatch.setattr(mod, nom, valeur)


@pytest.fixture(autouse=True)
def _fiabilite_isolee(tmp_path, monkeypatch):
    """01/11 (fiabilité) : ni le vrai runtime/messages-en-attente.json (chaque `handle` d'un test y
    noterait son message), ni la clé du HUD de la machine pour les signer, ni le chien de garde des
    boucles, ni PR_SET_PDEATHSIG (les faux Popen des tests n'ont pas `preexec_fn`) ; un test qui en
    veut un le rallume."""
    from astrel import attente as attente_mod
    from astrel import gel

    monkeypatch.setattr(config, "PDEATHSIG", False)
    monkeypatch.setattr(config, "ATTENTE_FILE", tmp_path / "messages-en-attente.json")
    monkeypatch.setattr(attente_mod, "_cle_hud", lambda: b"cle-de-test-" * 4)
    monkeypatch.setattr(attente_mod, "_cle_cache", None)
    monkeypatch.setattr(attente_mod.attente, "_items", None)
    monkeypatch.setattr(config, "GEL_ON", False)
    monkeypatch.setattr(config, "GEL_LOG", tmp_path / "gel.log")
    monkeypatch.setattr(gel.chien, "_boucles", {})


def mode_prive(chemin, attendu: int = 0o600) -> bool:
    """03/11 (CI Windows) : droits Unix du fichier (0600 par défaut). Windows n'a pas ces bits (os.chmod n'y règle
    que la lecture seule) : la protection y vient du profil de l'utilisateur, la vérification est sautée."""
    import os
    from pathlib import Path
    return os.name == "nt" or (Path(chemin).stat().st_mode & 0o777) == attendu


def prompt_de(cmd: list) -> str:
    """Prompt système d'une ligne de commande du CLI : en texte (`--append-system-prompt`) ou, depuis le 03/11,
    par un fichier (`--append-system-prompt-file`, cmd.exe de Windows limitant une ligne à 8 191 caractères)."""
    from pathlib import Path
    if "--append-system-prompt-file" in cmd:
        return Path(cmd[cmd.index("--append-system-prompt-file") + 1]).read_text(encoding="utf-8")
    return cmd[cmd.index("--append-system-prompt") + 1]


class CheminBash(type(Path())):
    """Chemin écrit en « / » (f-string, str) : il entre dans des commandes Bash, où Git Bash (Windows) mangerait
    les « \\ ». Identique sous Linux ; Windows comprend aussi « C:/… ». 03/11, CI Windows."""

    def __str__(self) -> str:
        return super().__str__().replace("\\", "/")

    def __format__(self, spec: str) -> str:
        return format(str(self), spec)
