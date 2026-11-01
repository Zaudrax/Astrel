"""22/10 (demande de l'utilisateur : « le compteur Account Usage ne se met pas à jour dans Astrel quand
je ne l'utilise pas sur Astrel ») : relevé du quota hors des tours par le CLI lui-même
(`claude -p "/usage"`, 0 jeton ; repli : sonde haiku et son `rate_limit_event`)."""
import json
import subprocess
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from astrel import config, core, quota_releve
from astrel.brain import Brain, BrainEvent
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)

PARIS = ZoneInfo("Europe/Paris")
# Sortie réelle de `claude -p "/usage"` (CLI 2.1.285, 22/10/2026), abrégée après les fenêtres.
USAGE = """You are currently using your subscription to power your Claude Code usage

Current session: 3% used · resets Oct 22, 8:19pm (Europe/Paris)
Current week (all models): 63% used · resets Oct 24, 10:59am (Europe/Paris)
Current week (Fable): 0% used · resets Oct 24, 11am (Europe/Paris)

What's contributing to your limits usage?
Last 24h · 3281 requests · 16 sessions
"""
NOW = datetime(2026, 10, 22, 15, 0, tzinfo=PARIS).timestamp()
FIVE = 1792693200   # 22/10 20:20 Paris, tel que le flux le donne
WEEK = 1792832400   # 24/10 11:00 Paris


def paris(*a):
    return datetime(*a, tzinfo=PARIS).timestamp()


# ------------------------------------------------------------------ lecture du texte de /usage

def test_usage_lu_deux_fenetres_et_heures():
    info = quota_releve.lire_usage(USAGE, NOW)
    w = info["unifiedWindows"]
    assert set(w) == {"five_hour", "seven_day"}  # semaine par modèle (Fable) : absente du flux, ignorée
    assert w["five_hour"]["utilization"] == 0.03 and w["seven_day"]["utilization"] == 0.63
    assert w["five_hour"]["resetsAt"] == paris(2026, 10, 22, 20, 19)
    assert w["seven_day"]["resetsAt"] == paris(2026, 10, 24, 10, 59)
    assert info["status"] == "allowed"


def test_heure_recalee_sur_celle_du_flux():
    """« 10:59am » du texte = 11:00 du flux : sinon `QuotaTrend` et la clé « une fois par fenêtre »
    croiraient à une nouvelle fenêtre à chaque changement de source."""
    connu = {"five_hour": {"utilization": 0.02, "resetsAt": FIVE}, "seven_day": {"utilization": 0.62, "resetsAt": WEEK}}
    w = quota_releve.lire_usage(USAGE, NOW, connu)["unifiedWindows"]
    assert w["five_hour"]["resetsAt"] == FIVE and w["seven_day"]["resetsAt"] == WEEK
    loin = {"seven_day": {"resetsAt": WEEK - 3600}}  # une heure d'écart : autre fenêtre, pas recalée
    assert quota_releve.lire_usage(USAGE, NOW, loin)["unifiedWindows"]["seven_day"]["resetsAt"] == paris(2026, 10, 24, 10, 59)


def test_usage_variantes_et_illisible():
    t = "Current session: 100% used · resets 9pm (Europe/Paris)\nCurrent week (all models): 41.5% used"
    info = quota_releve.lire_usage(t, NOW)
    assert info["unifiedWindows"]["five_hour"] == {"utilization": 1.0, "resetsAt": paris(2026, 10, 22, 21, 0)}
    assert info["unifiedWindows"]["seven_day"] == {"utilization": 0.415}
    # Pleine (correction n°2, relecture du 22/10) : « rejected » + la fenêtre pleine, comme le flux ;
    # sinon `_guard_quota` lançait au repos un tour « point de reprise » voué à l'échec.
    assert info["status"] == "rejected" and info["rateLimitType"] == "five_hour"
    # année : la date la plus proche (fin décembre vue début janvier)
    jan = paris(2027, 1, 2, 10, 0)
    w = quota_releve.lire_usage("Current week (all models): 5% used · resets Dec 31, 11pm (Europe/Paris)", jan)
    assert w["unifiedWindows"]["seven_day"]["resetsAt"] == paris(2026, 12, 31, 23, 0)
    for bad in ("", "Contexte : 3 %", "/usage isn't available in this environment.",
                "Last 24h · 3281 requests · 16 sessions", "Subagent-heavy sessions: 74%",
                "Top subagents: codeur 55%"):
        assert quota_releve.lire_usage(bad, NOW) is None


def test_deux_fenetres_pleines_la_plus_longue_limite():
    t = ("Current session: 100% used · resets Oct 22, 8:19pm (Europe/Paris)\n"
         "Current week (all models): 100% used · resets Oct 24, 10:59am (Europe/Paris)")
    info = quota_releve.lire_usage(t, NOW)
    assert info["status"] == "rejected" and info["rateLimitType"] == "seven_day"
    q = Brain().apply_rate_limit(info)
    assert q["limit_kind"] == "hebdomadaire" and q["reset_at"] == paris(2026, 10, 24, 10, 59)
    assert "rateLimitType" not in quota_releve.lire_usage(USAGE, NOW)  # non pleine : la dernière connue reste


# Même relevé si le CLI affiche /usage en français (hypothèse : aucune sortie française observée au
# 22/10, LANG=fr_FR la laisse en anglais) : sans ça, repli sur la sonde haiku à chaque cycle.
USAGE_FR = """Vous utilisez votre abonnement pour Claude Code

Session en cours (fenêtre de 5 h) : 3 % utilisés · se réinitialise le 22 oct. à 20:19 (Europe/Paris)
Semaine en cours (tous les modèles) : 63,5 % utilisés · se réinitialise le 24 oct., 10 h 59 (Europe/Paris)
Semaine en cours (Fable) : 0 % utilisé · se réinitialise le 24 oct. à 11 h (Europe/Paris)
"""


def test_usage_en_francais():
    info = quota_releve.lire_usage(USAGE_FR, NOW)
    w = info["unifiedWindows"]
    assert set(w) == {"five_hour", "seven_day"}
    assert w["five_hour"] == {"utilization": 0.03, "resetsAt": paris(2026, 10, 22, 20, 19)}
    assert w["seven_day"] == {"utilization": 0.635, "resetsAt": paris(2026, 10, 24, 10, 59)}
    assert info["status"] == "allowed"
    autre = quota_releve.lire_usage("Limite de 5 heures : 12 % · réinitialisation à 21 h (Europe/Paris)\n"
                                    "Hebdomadaire : 100 % · réinitialisation le 1er nov. à 9:00 (Europe/Paris)", NOW)
    assert autre["unifiedWindows"]["five_hour"] == {"utilization": 0.12, "resetsAt": paris(2026, 10, 22, 21, 0)}
    assert autre["unifiedWindows"]["seven_day"] == {"utilization": 1.0, "resetsAt": paris(2026, 11, 1, 9, 0)}
    assert autre["status"] == "rejected" and autre["rateLimitType"] == "seven_day"
    lanceur = Lanceur(json.dumps({"type": "result", "result": USAGE_FR}))
    r = quota_releve.relever(lanceur=lanceur, now=NOW)
    assert r["ok"] and r["source"] == "usage" and len(lanceur.appels) == 1  # pas de sonde haiku


# ------------------------------------------------------------------ relevé : commandes, repli, erreurs

def test_commandes_sans_outils_ni_mcp_ni_session():
    u, s = quota_releve.commande_usage(), quota_releve.commande_sonde()
    for cmd in (u, s):
        assert cmd[0] == config.CLAUDE_BIN and "-p" in cmd and "--no-session-persistence" in cmd
        assert cmd[cmd.index("--tools") + 1] == "" and "--strict-mcp-config" in cmd
        assert cmd[cmd.index("--setting-sources") + 1] == ""
        assert "--dangerously-skip-permissions" not in cmd and "--resume" not in cmd
    assert u[u.index("-p") + 1] == "/usage" and "--model" not in u
    assert s[s.index("--model") + 1] == "haiku" and "--disable-slash-commands" in s


def test_environnement_sans_cle_ni_reflexion(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-faux")
    env = quota_releve.environnement()
    assert "ANTHROPIC_API_KEY" not in env and env["MAX_THINKING_TOKENS"] == "0"


class Lanceur:
    def __init__(self, *sorties):
        self.sorties, self.appels = list(sorties), []

    def __call__(self, cmd, **kw):
        self.appels.append((cmd, kw))
        out = self.sorties.pop(0)
        if isinstance(out, BaseException):
            raise out
        return subprocess.CompletedProcess(cmd, 0, out, "")


def test_relever_par_usage_zero_jeton():
    lanceur = Lanceur(json.dumps({"type": "result", "result": USAGE, "local_command": "usage"}))
    r = quota_releve.relever(lanceur=lanceur, now=NOW)
    assert r["ok"] and r["source"] == "usage" and r["jetons"] == 0
    assert len(lanceur.appels) == 1  # aucune sonde
    cmd, kw = lanceur.appels[0]
    assert kw["cwd"] != str(config.WORKDIR) and kw["timeout"] == quota_releve.DELAI_S
    assert kw["stdin"] == subprocess.DEVNULL


def test_repli_sonde_si_usage_illisible():
    flux = "\n".join(json.dumps(e) for e in (
        {"type": "system", "subtype": "init"},
        {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "resetsAt": FIVE, "rateLimitType": "five_hour",
         "unifiedWindows": {"five_hour": {"utilization": 0.08, "resetsAt": FIVE}, "seven_day": {"utilization": 0.62, "resetsAt": WEEK}}}},
        {"type": "result", "usage": {"input_tokens": 345, "output_tokens": 84, "cache_read_input_tokens": 0}}))
    lanceur = Lanceur(json.dumps({"result": "Usage: (format inconnu)"}), flux)
    r = quota_releve.relever(lanceur=lanceur, now=NOW)
    assert r["ok"] and r["source"] == "sonde" and r["jetons"] == 429
    assert r["info"]["unifiedWindows"]["seven_day"]["utilization"] == 0.62
    assert "haiku" in lanceur.appels[1][0]


def test_sonde_sans_pourcentage_et_pannes():
    statut_seul = json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}})
    r = quota_releve.relever(lanceur=Lanceur("rien", statut_seul), now=NOW)
    assert r == {"ok": False, "error": "relevé du quota : aucune fenêtre d'utilisation dans la réponse du CLI"}
    r = quota_releve.relever(lanceur=Lanceur(subprocess.TimeoutExpired("claude", 30)), now=NOW)
    assert r == {"ok": False, "error": "relevé du quota : le CLI ne répond pas"}
    r = quota_releve.relever(lanceur=Lanceur(FileNotFoundError("claude")), now=NOW)
    assert not r["ok"] and r["error"].startswith("relevé du quota impossible")

    def echec(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "erreur")
    assert not quota_releve.relever(lanceur=echec, now=NOW)["ok"]


# ------------------------------------------------------------------ cerveau : même conversion que le flux

def test_brain_apply_rate_limit_garde_la_fenetre_qui_limite():
    b = Brain()
    ev = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "resetsAt": FIVE, "rateLimitType": "five_hour",
          "unifiedWindows": {"five_hour": {"utilization": 0.02, "resetsAt": FIVE}}}}
    (out,) = list(b._translate(ev, {}, False))
    assert out.kind == "quota" and b.quota["resets_at"] == FIVE and b.quota["limit_kind"] == "cinq_heures"
    assert time.time() - b.quota_at < 5
    q = b.apply_rate_limit(quota_releve.lire_usage(USAGE, NOW, b.quota["windows"]))
    assert q["type"] == "five_hour" and q["resets_at"] == FIVE and q["reset_at"] == float(FIVE)
    assert q["windows"]["seven_day"]["utilization"] == 0.63
    neuf = Brain().apply_rate_limit(quota_releve.lire_usage(USAGE, NOW))  # rien de connu : fenêtre de 5 h
    assert neuf["type"] == "five_hour" and neuf["resets_at"] == paris(2026, 10, 22, 20, 19)


def test_tendance_ignore_un_point_d_arrondi():
    trend = core.QuotaTrend()
    for t, u in ((0, 0.62), (300, 0.63), (600, 0.62), (1300, 0.66)):
        trend.observe({"windows": {"seven_day": {"utilization": u, "resetsAt": WEEK}}}, now=1000 + t)
    assert [s[1] for s in trend._samples["seven_day"]] == [0.62, 0.63, 0.66]
    trend.observe({"windows": {"seven_day": {"utilization": 0.40, "resetsAt": WEEK}}}, now=3000)
    assert trend._samples["seven_day"] == [(3000, 0.40)]  # vraie baisse : on repart de zéro


# ------------------------------------------------------------------ cœur : quand relever, et publication

@pytest.fixture
def releves(astrel, monkeypatch):
    """Relevés lancés par `_quota_tick` (le fil n'est pas démarré : l'appel est noté)."""
    j, _, _ = astrel
    out = []

    class FauxFil:
        def __init__(self, target, name="", daemon=True):
            self.target = target

        def start(self):
            out.append(self.target)
    monkeypatch.setattr(core.threading, "Thread", FauxFil)
    return out


def test_tick_attend_dix_minutes_et_le_repos(astrel, releves, monkeypatch):
    j, _, _ = astrel
    t0 = 10_000_000.0
    j._quota_tick(t0)
    assert releves == []  # démarrage : une minute de grâce
    j._quota_tick(t0 + 61)
    assert len(releves) == 1  # rien relevé depuis le démarrage : on relève
    j._quota_tick(t0 + 61 + 599)
    assert len(releves) == 1  # au plus une fois toutes les 10 min
    j.brain.quota_at = t0 + 61 + 500  # un rate_limit_event du flux entre-temps
    j._quota_tick(t0 + 61 + 601)
    assert len(releves) == 1  # dernier relevé de moins de 10 min : rien
    later = t0 + 61 + 500 + 601
    j._turn_active = True
    j._quota_tick(later)
    j._turn_active = False
    j._tasks["a1"] = {"id": "a1"}  # un agent tourne
    j._quota_tick(later)
    j._tasks.clear()
    with monkeypatch.context() as m:
        m.setattr(core.reglages, "value", lambda k, _v=core.reglages.value: False if k == "quota_refresh" else _v(k))
        j._quota_tick(later)
    assert len(releves) == 1  # tour, agent, réglage coupé : jamais
    j._quota_tick(later)
    assert len(releves) == 2


def test_tick_ni_sous_estop_ni_hors_abonnement(astrel, releves, monkeypatch, tmp_path):
    j, _, _ = astrel
    t = 10_000_000.0
    j._quota_refresh_last = 0.0
    monkeypatch.setattr(j.brain, "fournisseur", core.fournisseurs.Effectif("cle_claude", "cle_claude"))
    j._quota_tick(t)
    assert releves == []
    monkeypatch.setattr(j.brain, "fournisseur", core.fournisseurs.Effectif("abonnement", core.fournisseurs.DEFAUT))
    estop = tmp_path / "ESTOP"
    estop.write_text("")
    monkeypatch.setattr(config, "ESTOP_FILE", estop)
    j._quota_tick(t)
    assert releves == []
    assert j._quota_refresh_command() == {"ok": False, "error": "relevé du quota : arrêt d'urgence en cours"}


def test_releve_publie_comme_le_flux_sans_relancer_le_point_de_reprise(astrel, published, monkeypatch):
    j, _, _ = astrel
    handled = []
    monkeypatch.setattr(j, "handle", lambda text, source="voix", **k: handled.append(source))
    ev = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "resetsAt": FIVE, "rateLimitType": "five_hour",
          "unifiedWindows": {"five_hour": {"utilization": 0.90, "resetsAt": FIVE}, "seven_day": {"utilization": 0.62, "resetsAt": WEEK}}}}
    for out in j.brain._translate(ev, {}, False):
        j._on_brain_event(out)
    assert handled == ["réflexe"]  # 90 % : point de reprise demandé, une fois pour cette fenêtre
    texte = USAGE.replace("Current session: 3%", "Current session: 92%")
    monkeypatch.setattr(quota_releve, "relever", lambda connu=None, **k: {
        "ok": True, "source": "usage", "jetons": 0, "info": quota_releve.lire_usage(texte, NOW, connu)})
    r = j._quota_refresh_command()
    assert r["ok"] and r["tokens"] == 0 and {w["name"]: w["pct"] for w in r["windows"]} == {"five_hour": 92, "seven_day": 63}
    q = of(published, "quota")[-1]
    assert {w["name"]: w["pct"] for w in q["windows"]} == {"five_hour": 92, "seven_day": 63}
    assert q["limit_type"] == "five_hour" and q["limited"] is False
    assert j.brain.quota["resets_at"] == FIVE
    assert handled == ["réflexe"]  # même fenêtre (heure recalée) : pas de second point de reprise
    assert j._quota_refresh_command() == {"ok": False, "error": "quota relevé il y a moins de 30 s"}


def test_releve_en_echec_ne_touche_a_rien(astrel, published, monkeypatch):
    j, _, _ = astrel
    monkeypatch.setattr(quota_releve, "relever", lambda **k: {"ok": False, "error": "relevé du quota : le CLI ne répond pas"})
    assert j._quota_refresh() == {"ok": False, "error": "relevé du quota : le CLI ne répond pas"}
    assert of(published, "quota") == [] and j.brain.quota == {}
    assert j._quota_refresh_lock.acquire(blocking=False)  # verrou rendu


def test_commande_quota_refresh_routee(astrel, monkeypatch):
    j, _, _ = astrel
    monkeypatch.setattr(j, "_quota_refresh_command", lambda: {"ok": True, "vu": True})
    assert j.on_command({"cmd": "quota_refresh"}) == {"ok": True, "vu": True}


def test_reglage_actif_par_defaut():
    from astrel.settings import reglages
    assert reglages.value("quota_refresh") is True
    assert BrainEvent  # import utilisé par les autres tests du module
