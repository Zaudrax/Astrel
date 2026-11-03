"""Pression mémoire du PC (21/10, plan P20) : mesure, décision avec hystérésis, état lu par le
hook (fail-open), classement des gros consommateurs, refus d'un nouvel agent et du navigateur de
test en blocage ACTIVÉ, évènement `memory` et annonce par le cœur. Lecture seule : rien n'est tué."""
import json
import os
import subprocess
import sys
import time

import pytest

from astrel import agents, brain, config, memoire
from astrel.tools import system
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)

GO = memoire.GO


def s(dispo, swap, total=15.3):
    return {"ram_total": int(total * GO), "ram_dispo": int(dispo * GO), "swap_total": 8 * GO,
            "swap_used": int(8 * GO * swap / 100), "swap_pct": float(swap), "psi": None}


def suite(points, t0=1000.0, pas=5.0, st=None):
    """Applique `decide` à une suite d'échantillons (dispo, swap), un toutes les `pas` s."""
    st = st or memoire.Etat()
    out = []
    for i, (dispo, swap) in enumerate(points):
        st = memoire.decide(s(dispo, swap), st, t0 + i * pas)
        out.append(st.etat)
    return out, st


# ------------------------------------------------------------------ décision

def test_seuils_d_attention_et_de_blocage():
    assert suite([(6, 50)])[0] == ["ok"]
    assert suite([(6, 75)])[0] == ["attention"]          # swap ≥ 75 %
    assert suite([(1.9, 40)])[0] == ["attention"]        # disponible < 2 Go
    assert suite([(2.9, 90)])[0] == ["blocage"]          # swap ≥ 90 % ET disponible < 3 Go
    assert suite([(3.5, 95)])[0] == ["attention"]        # swap haut mais de la RAM : pas de blocage
    assert suite([(0.9, 10)])[0] == ["blocage"]          # disponible < 1 Go, quel que soit le swap


def test_attention_avec_hysteresis():
    etats, _ = suite([(6, 76), (6, 74), (6, 73), (6, 71.9), (6, 74)])
    assert etats == ["attention", "attention", "attention", "ok", "ok"]


def test_reprise_tenue_30_s_et_compteur_remis_a_zero():
    st = memoire.Etat()
    st = memoire.decide(s(2.5, 92), st, 0)
    assert st.etat == "blocage"
    # reprise possible (swap < 80 % ET disponible ≥ 2 Go) : il faut la tenir 30 s
    for t in (5, 10, 20, 30):
        st = memoire.decide(s(3, 70), st, t)
        assert st.etat == "blocage"
    st = memoire.decide(s(2.5, 92), st, 32)              # rechute : compteur à zéro
    assert st.etat == "blocage" and st.reprise_depuis == 0
    for t in (35, 50, 64):
        st = memoire.decide(s(3, 70), st, t)
        assert st.etat == "blocage"
    st = memoire.decide(s(3, 70), st, 65)
    assert st.etat == "ok" and st.depuis == 65


def test_le_swap_haut_seul_ne_retient_pas_le_blocage():
    """Le swap ne redescend pas seul quand la RAM se libère : disponible ≥ 4 Go suffit à reprendre."""
    st = memoire.decide(s(0.8, 92), memoire.Etat(), 0)
    for t in range(5, 40, 5):
        st = memoire.decide(s(5, 92), st, t)
    assert st.etat == "attention"  # sorti du blocage, swap toujours ≥ 75 %
    # swap haut ET peu de RAM : pas de reprise
    st = memoire.decide(s(0.8, 92), memoire.Etat(), 0)
    for t in range(5, 100, 5):
        st = memoire.decide(s(3.5, 85), st, t)
    assert st.etat == "blocage"


def test_vue_arrondie():
    v = memoire.view(s(5.53, 52.6), memoire.Etat("attention"))
    assert v == {"state": "attention", "ram_total_go": 15.3, "ram_used_go": 9.8, "ram_dispo_go": 5.5,
                 "swap_total_go": 8.0, "swap_pct": 53, "active": False}


# ------------------------------------------------------------------ mesure

def test_echantillon_reel_et_rapide():
    t0 = time.perf_counter()
    for _ in range(20):
        x = memoire.sample()
    # ≈ 0,15 ms mesuré ; large marge. 03/11 : sous Windows, psutil lit le swap par un compteur PDH (~10 ms
    # en CI) ; un échantillon toutes les 5 s (`PERIODE_S`) : 0,2 % de la boucle, budget à 50 ms.
    budget = 0.05 if os.name == "nt" else 0.005
    assert (time.perf_counter() - t0) / 20 < budget
    assert x["ram_total"] > 0 and 0 <= x["ram_dispo"] <= x["ram_total"] and 0 <= x["swap_pct"] <= 100


def test_pression_du_noyau(tmp_path):
    f = tmp_path / "memory"
    f.write_text("some avg10=1.50 avg60=0.20 avg300=0.00 total=1\nfull avg10=0.40 avg60=0.00 avg300=0.00 total=2\n")
    assert memoire._psi(f) == {"some": 1.5, "full": 0.4}
    assert memoire._psi(tmp_path / "absent") is None  # noyau sans PSI : ignoré


# ------------------------------------------------------------------ état lu par le hook

def ecrit(state="blocage", at=None, **kw):
    memoire.write_state({"state": state, "ram_dispo_go": 0.8, "swap_pct": 93, **kw}, at)


def test_fichier_absent_perime_ou_illisible_laisse_passer():
    assert memoire.pression() == "" and memoire.refus("nouvel agent") == ""
    memoire.SWITCH_FILE.write_text("on", encoding="utf-8")
    ecrit(at=time.time() - 31)                       # périmé
    assert memoire.pression() == "" and memoire.refus("nouvel agent") == ""
    memoire.STATE_FILE.write_text("{pas du json", encoding="utf-8")
    assert memoire.pression() == ""
    ecrit(state="inconnu")
    assert memoire.pression() == ""


def test_blocage_en_observation_tant_qu_il_n_est_pas_active(monkeypatch):
    ecrit()
    assert memoire.pression() == "attention" and memoire.refus("nouvel agent") == ""
    assert "observation" in memoire.resume_etat()
    memoire.SWITCH_FILE.write_text("on\n", encoding="utf-8")
    assert memoire.pression() == "blocage"
    why = memoire.refus("nouvel agent")
    assert "nouvel agent refusé" in why and "0,8 Go" in why and "93 %" in why and "Rien n'a été fermé" in why
    monkeypatch.setenv("ASTREL_MEMOIRE_BLOCAGE", "0")  # la variable prime sur le fichier
    assert memoire.refus("nouvel agent") == ""


def test_route_refuse_un_agent_en_blocage():
    decision, updated, why = agents.route({"subagent_type": "chercheur", "prompt": "p"}, defined={},
                                          pression="Mémoire du PC presque pleine")
    assert decision == "deny" and updated is None and why.startswith("Mémoire")
    assert agents.route({"subagent_type": "chercheur", "prompt": "p"}, defined={}, pression="") == ("", None, "")


def run_hook(payload, tmp_path, actif=True):
    env = {**os.environ, "ASTREL_ROUTE_LOG": str(tmp_path / "route.jsonl"),
           "ASTREL_MEMOIRE_FILE": str(memoire.STATE_FILE), "ASTREL_MEMOIRE_BLOCAGE": "1" if actif else "0"}
    proc = subprocess.run([sys.executable, str(config.ROUTE_HOOK_SCRIPT)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30, env=env)
    assert proc.returncode == 0
    return json.loads(proc.stdout)["hookSpecificOutput"] if proc.stdout.strip() else None


def test_hook_reel_refuse_agent_et_navigateur_de_test(tmp_path):
    if config.RUNTIME.joinpath("ESTOP").exists():
        pytest.skip("arrêt d'urgence posé sur la machine")
    nav = {"tool_name": "mcp__playwright2__browser_navigate", "tool_input": {"url": "http://127.0.0.1:4799"}}
    agent = {"tool_name": "Agent", "tool_input": {"subagent_type": "chercheur", "prompt": "p", "description": "d"}}
    assert run_hook(nav, tmp_path) is None and run_hook(agent, tmp_path) is None  # pas d'état : on passe
    ecrit()
    out = run_hook(nav, tmp_path)
    assert out["permissionDecision"] == "deny" and "navigateur de test (Chrome) refusé" in out["permissionDecisionReason"]
    assert run_hook({**nav, "tool_name": "mcp__playwright__browser_navigate"}, tmp_path)["permissionDecision"] == "deny"
    out = run_hook(agent, tmp_path)
    assert out["permissionDecision"] == "deny" and "nouvel agent refusé" in out["permissionDecisionReason"]
    # autres outils du navigateur (clic, capture) : jamais bloqués ; blocage non activé : rien
    assert run_hook({**nav, "tool_name": "mcp__playwright2__browser_click"}, tmp_path) is None
    assert run_hook(nav, tmp_path, actif=False) is None and run_hook(agent, tmp_path, actif=False) is None
    ecrit(state="attention")
    assert run_hook(nav, tmp_path) is None


def test_reglages_declarent_le_matcher_du_navigateur(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "s.json")
    hooks = json.loads(brain.write_settings().read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    nav = [h for h in hooks if h["matcher"] == "mcp__playwright.*__browser_navigate"]
    # chemins en « / » dans la ligne du hook (Git Bash sous Windows, 03/11) : identiques sous Unix
    assert len(nav) == 1 and config.ROUTE_HOOK_SCRIPT.as_posix() in nav[0]["hooks"][0]["command"]
    assert hooks[0]["matcher"] == "*"  # le garde-fou reste le premier


# ------------------------------------------------------------------ classement

def test_classement_par_application_sans_ligne_de_commande():
    hud = str(config.HUD_PROFILE_DIR)
    procs = {
        1: {"ppid": 0, "name": "systemd", "cmdline": ["/sbin/init"]},
        10: {"ppid": 1, "name": "chrome", "cmdline": ["/opt/chrome", f"--user-data-dir={hud}", "--app=x"]},
        11: {"ppid": 10, "name": "chrome", "cmdline": ["/opt/chrome", "--type=renderer"]},
        20: {"ppid": 1, "name": "chrome", "cmdline": ["/opt/chrome", "--headless=new", "--user-data-dir=/tmp/p"]},
        21: {"ppid": 20, "name": "chrome", "cmdline": ["/opt/chrome", "--type=gpu-process"]},
        30: {"ppid": 1, "name": "chrome", "cmdline": ["/opt/chrome"]},
        31: {"ppid": 30, "name": "chrome", "cmdline": ["/opt/chrome", "--type=renderer"]},
        40: {"ppid": 1, "name": "Isolated Web Co", "cmdline": []},
        41: {"ppid": 1, "name": "code", "cmdline": ["/usr/share/code/code"]},
        50: {"ppid": 1, "name": "python", "cmdline": [f"{config.ROOT}/.venv/bin/python", "-m", "astrel"]},
        51: {"ppid": 1, "name": "node", "cmdline": ["node", f"{config.ROOT}/mcp-servers/x.js"]},
        52: {"ppid": 1, "name": "python", "cmdline": [f"{config.ROOT}/.venv/bin/python", "-m", "pytest", "-q"]},
        60: {"ppid": 1, "name": "php", "cmdline": ["php", "-S", "secret=abc"]},
    }
    got = memoire.classify(procs)
    assert got[10] == got[11] == "HUD d'Astrel"
    assert got[20] == got[21] == "Chrome de test (headless)"
    assert got[30] == got[31] == "Navigateur Chrome"
    assert got[40] == "Firefox" and got[41] == "VS Code"
    assert got[50] == got[51] == "Astrel" and got[52] == "Tests (pytest)"
    assert got[60] == "php"  # le nom du processus, jamais ses arguments


def test_classement_reel_trie_et_en_cache(monkeypatch):
    r = memoire.top()
    assert r["items"] and r["total_go"] > 0
    for i in r["items"]:
        assert set(i) == {"label", "n", "go", "swap_go"} and i["n"] >= 1
    poids = [i["go"] + i["swap_go"] for i in r["items"]]
    assert poids == sorted(poids, reverse=True)
    monkeypatch.setattr(memoire, "_top", lambda limit: pytest.fail("recalculé malgré le cache"))
    assert memoire.top() is r


def test_system_status_donne_swap_et_classement(monkeypatch):
    monkeypatch.setattr(memoire, "top", lambda limit=8, force=False: {"items": [
        {"label": "VS Code", "n": 14, "go": 3.2, "swap_go": 0.4},
        {"label": "Astrel", "n": 3, "go": 0.5, "swap_go": 0.0}], "total_go": 3.7, "at": 0})
    monkeypatch.setattr(system, "_gpu", lambda: None)
    st = system.status()
    assert st["swap"].endswith("Mo)") and st["memoire_disponible"].endswith("Go")
    assert st["plus_gourmands"] == ["VS Code : 3,2 Go (+0,4 Go de swap), 14 processus", "Astrel : 0,5 Go, 3 processus"]
    assert st["pression_memoire"].startswith("inconnue")


def test_releve_des_seuils(tmp_path):
    for dispo, swap, etat, t in ((5, 50, "ok", 0), (2.5, 91, "blocage", 60), (1.5, 80, "attention", 120)):
        memoire.journal(s(dispo, swap), memoire.Etat(etat), t)
    out = memoire.releve()
    assert out.startswith("3 relevés") and "blocage 1" in out and "au-dessus du seuil de blocage : 1" in out
    assert "1.50 Go" in out and "91.0 %" in out


# ------------------------------------------------------------------ cœur

@pytest.fixture
def mesure(monkeypatch):
    box = {"s": s(6, 50)}
    monkeypatch.setattr(memoire, "sample", lambda: box["s"])
    return box


def test_coeur_publie_seulement_si_ca_change(astrel, published, mesure):
    j, _, _ = astrel
    j._memoire_tick(now=1000)
    j._memoire_tick(now=1003)  # moins de 5 s : rien
    j._memoire_tick(now=1005)  # même valeurs arrondies : fichier réécrit, pas d'évènement
    evs = of(published, "memory")
    assert len(evs) == 1 and evs[0]["state"] == "ok" and evs[0]["swap_pct"] == 50
    assert json.loads(memoire.STATE_FILE.read_text())["at"] == 1005
    mesure["s"] = s(6, 51)
    j._memoire_tick(now=1010)
    assert len(of(published, "memory")) == 2
    lignes = memoire.JOURNAL.read_text().splitlines()
    assert len(lignes) == 1  # une ligne par minute
    j._memoire_tick(now=1061)
    assert len(memoire.JOURNAL.read_text().splitlines()) == 2


def test_coeur_dit_le_blocage_une_fois_et_reprise_silencieuse(astrel, published, mesure, monkeypatch):
    j, _, _ = astrel
    memoire.SWITCH_FILE.write_text("on", encoding="utf-8")
    dits = []
    monkeypatch.setattr(j, "_announce", dits.append)
    j._memoire_tick(now=1000)
    mesure["s"] = s(0.8, 93)
    for t in (1005, 1010, 1015):
        j._memoire_tick(now=t)
    assert len(dits) == 1 and "nouvel agent" in dits[0] and "Rien n'a été fermé" in dits[0]
    assert json.loads(memoire.STATE_FILE.read_text())["state"] == "blocage" and memoire.pression(now=1016) == "blocage"
    notices = of(published, "notice")
    assert len(notices) == 1 and notices[0]["level"] == "warning" and "0,8 Go" in notices[0]["text"]
    mesure["s"] = s(5, 70)
    for t in range(1020, 1060, 5):
        j._memoire_tick(now=t)
    assert len(dits) == 1  # la reprise n'est pas dite
    fin = of(published, "notice")[-1]
    assert fin["level"] == "info" and fin["text"].startswith("Mémoire redescendue")


def test_coeur_en_observation_ne_dit_rien(astrel, published, mesure, monkeypatch):
    j, _, _ = astrel
    dits = []
    monkeypatch.setattr(j, "_announce", dits.append)
    mesure["s"] = s(0.8, 93)
    j._memoire_tick(now=1000)
    assert not dits and "observation" in of(published, "notice")[-1]["text"]
    assert of(published, "memory")[-1]["state"] == "blocage" and of(published, "memory")[-1]["active"] is False


def test_commande_memory_top_refusee_a_claude(astrel, monkeypatch):
    j, _, _ = astrel
    monkeypatch.setattr(memoire, "top", lambda limit=8, force=False: {"items": [], "total_go": 1.0, "at": 0})
    assert j.on_command({"cmd": "memory_top", "_from_claude": True}) == {"ok": False, "error": "hud_only"}
    r = j.on_command({"cmd": "memory_top", "_from_claude": False, "_via": "hud"})
    assert r["ok"] and r["items"] == [] and r["total_go"] == 1.0


def test_memory_est_rejoue_a_la_connexion():
    from astrel import events

    assert "memory" in events.STICKY and "memory" not in events.HISTORY
