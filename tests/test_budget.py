"""Budget du socle relu à CHAQUE requête (15/10, idée 4, docs/ECONOMIE-TOKENS.md § 9).

Le prompt système d'Astrel et le JSON `--agents` partent dans chaque contexte
(cerveau et sous-agents) et sont relus en cache à chaque requête. Ce test échoue
s'ils grossissent au-delà du budget (mesure du 15/10 + ~10 %), ou s'ils
contiennent une heure ou une date : une valeur qui change d'une session à
l'autre casse le cache du préfixe. Pour relever un budget : mesurer, noter la
raison dans docs/ECONOMIE-TOKENS.md, puis changer le chiffre ici.
"""
import chemins_machine  # chemins de la machine qui lance la suite
import json
import re

from astrel import agents, brain, config

# Mesuré le 15/10 (caractères) : prompt 9 779 (8 519 avant les idées 2, 3 et 8),
# agents 10 699. 17/10 (plan P3, lots B et D) : agents `bilan` et `archiviste`, 14 907 mesurés ; seule
# la description d'un agent part dans chaque contexte (« agents 572 » tokens au 15/10),
# sa consigne seulement quand il est lancé (docs/ECONOMIE-TOKENS.md § 9).
# 20/10 (plan P13, équipe d'agents) : 11 agents, 14 541 mesurés ; les consignes longues sont des
# fiches de métier (`config/metiers/`, lues par l'agent au lancement), d'où les plafonds PAR agent :
# description (part dans chaque contexte) et consigne d'un agent à fiche. Prompt : 10 719.
# 28/10 (carte T31) : la consigne « Todo » est dans les descriptions des outils todo_add et todo_move, pas
# dans le prompt : le plafond de l'utilisateur (10 800, aussi dans test_mode_leger et test_fournisseurs) tient.
PROMPT_MAX_CHARS = 10_800
AGENTS_MAX_CHARS = 16_800
DESCRIPTION_MAX_CHARS = 240
FICHE_AGENT_PROMPT_MAX_CHARS = 500

_MONTHS = "janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre"
_CLOCK_OR_DATE = re.compile(
    r"\b\d{1,2}\s?h\s?\d{2}\b"            # 18 h 51, 18h51
    r"|\b\d{1,2}:\d{2}\b"                 # 18:51
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"  # 26/09, 26/09/2026
    r"|\b(?:19|20)\d\d-\d\d-\d\d"         # 2026-09-26
    rf"|\b\d{{1,2}}(?:er)?\s+(?:{_MONTHS})\b",
    re.IGNORECASE,
)


def _agents_json() -> str:
    # Ce que `agents.write_agents` écrit et que le CLI reçoit par `--agents`.
    return json.dumps(agents.load_agents(), ensure_ascii=False, indent=2)


def test_prompt_systeme_dans_son_budget():
    size = chemins_machine.longueur_type(brain.VOICE_SYSTEM_PROMPT)
    assert size <= PROMPT_MAX_CHARS, f"prompt système : {size} caractères > {PROMPT_MAX_CHARS}"


def test_agents_dans_leur_budget():
    size = len(_agents_json())
    assert size <= AGENTS_MAX_CHARS, f"JSON --agents : {size} caractères > {AGENTS_MAX_CHARS}"


def test_descriptions_et_consignes_courtes():
    """Plan P13 : chaque description ≤ 240 caractères ; un agent qui a une fiche de métier garde
    une consigne ≤ 500 caractères (le reste est dans sa fiche), et cette fiche existe."""
    for name, a in agents.load_agents().items():
        assert len(a["description"]) <= DESCRIPTION_MAX_CHARS, f"{name} : description trop longue"
        assert not agents.missing_fiches(a["prompt"]), f"{name} : fiche citée absente"
        if a["prompt"].startswith("Première action, obligatoire : lis avec Read"):
            assert chemins_machine.longueur_type(a["prompt"]) <= FICHE_AGENT_PROMPT_MAX_CHARS, f"{name} : consigne trop longue"


def test_ni_heure_ni_date_dans_le_socle():
    for name, text in (("prompt système", brain.VOICE_SYSTEM_PROMPT), ("JSON --agents", _agents_json())):
        found = _CLOCK_OR_DATE.findall(text)
        assert not found, f"{name} : heure ou date {found} (casse le cache d'une session à l'autre)"


def test_le_detecteur_voit_les_heures_et_les_dates():
    for bad in ("il est 18 h 51", "à 9:30", "le 26/09", "2026-10-15", "le 1er octobre"):
        assert _CLOCK_OR_DATE.search(bad), bad
    for ok in ("La limite de session de 5 h", "garde-la sous 8 Ko", "moins de 300 mots", "1 à 3 phrases"):
        assert not _CLOCK_OR_DATE.search(ok), ok


def test_prompt_identique_d_un_demarrage_a_l_autre(tmp_path, monkeypatch):
    """Le CLI reçoit le texte constant, jamais une version formatée à la volée."""
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "s.json")
    monkeypatch.setattr(config, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(config, "MCP_CONFIG_FILE", tmp_path / "absent.json")
    cmds = []

    class FakePopen:
        def __init__(self, cmd, **kw):
            cmds.append(cmd)
            self.stdout, self.stderr, self.stdin = iter(()), iter(()), None

        def poll(self):
            return 0

    monkeypatch.setattr(brain.subprocess, "Popen", FakePopen)
    for _ in range(2):
        brain.Brain().start(fresh=True)
    prompts = [c[c.index("--append-system-prompt") + 1] for c in cmds]
    assert prompts[0] == prompts[1] == brain.VOICE_SYSTEM_PROMPT
    files = [c[c.index("--agents") + 1] for c in cmds]
    assert files[0] == files[1]
