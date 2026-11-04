"""Routage des demandes et barrière des données sensibles (04/11/2026, lot 3 du moteur local,
docs/chantiers/HYBRIDE-QWEN.md) : `routeur.decider`, `sensible.scan`, `coeur/routage.py`, et la zone
confidentielle du garde-fou (lot 3b)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from astrel import config, core, guard, routeur, sensible, state
from astrel.choices import choices
from astrel.events import bus

CORPUS = json.loads((Path(__file__).parent / "fixtures" / "routage.json").read_text(encoding="utf-8"))
SANS_ZONE = routeur.Etat(scan=lambda t, p: sensible.scan(t, p, zones_conf=[]))
CLE = "sk-ant-abcdefghijklmnopqrstuvwxyz0123456789"
IBAN = "FR76 3000 6000 0112 3456 7890 189"


def _etat(**kw):
    return routeur.Etat(scan=lambda t, p: sensible.scan(t, p, zones_conf=[]), **kw)


# ------------------------------------------------------------------ corpus

@pytest.mark.parametrize("cas", CORPUS, ids=[c["texte"][:40] for c in CORPUS])
def test_corpus_mode_automatique(cas):
    d = routeur.decider(cas["texte"], "", [], {}, SANS_ZONE)
    assert (d.moteur, d.carte) == (cas["moteur"], cas["carte"]), (d.categorie, d.raisons)


def test_corpus_couvre_les_deux_langues_et_les_faux_positifs():
    textes = " ".join(c["texte"] for c in CORPUS)
    assert len(CORPUS) >= 80 and "token" in textes and "mot de passe" in textes and "What" in textes


SENSIBLES = [f"Ma clé {CLE} ne marche plus", f"Vérifie mon IBAN {IBAN}", "Ma carte 4970 1012 3456 7893",
             "numéro de sécu 1 85 05 78 006 084 91", "password: Hunter2Hunter2Hunter22"]


@pytest.mark.parametrize("texte", SENSIBLES)
@pytest.mark.parametrize("mode", routeur.MODES)
@pytest.mark.parametrize("prefixe", ["", "avec Claude : ", "utilise Claude pour ceci : ", "with Claude: "])
@pytest.mark.parametrize("source", ["clavier", "voix", "whatsapp"])
def test_rien_de_sensible_ne_part_chez_claude_sans_carte(texte, mode, prefixe, source):
    """Propriété : quel que soit le réglage, le préfixe ou la source, une donnée sensible n'arrive jamais à
    Claude sans carte (le classifieur « factice » est ici le choix explicite de Claude)."""
    d = routeur.decider(prefixe + texte, "", [], {"moteur_mode": mode}, _etat(source=source))
    assert d.raisons
    assert not (d.moteur == routeur.CLAUDE and d.carte != routeur.CARTE_SENSIBLE), d


def test_hors_ligne_toujours_local_meme_claude_explicite():
    d = routeur.decider("avec Claude : explique DNS", "", [], {}, _etat(hors_ligne=True))
    assert d.moteur == routeur.LOCAL and d.categorie == "hors_ligne" and not d.carte


def test_modes_du_reglage():
    assert routeur.decider("explique DNS", "", [], {"moteur_mode": "local"}, SANS_ZONE).moteur == routeur.LOCAL
    assert routeur.decider("explique XSS", "", [], {"moteur_mode": "claude"}, SANS_ZONE).moteur == routeur.CLAUDE
    h = routeur.decider("explique DNS", "", [], {"moteur_mode": "hybride"}, SANS_ZONE)
    assert (h.moteur, h.carte) == (routeur.LOCAL, "")
    h = routeur.decider("corrige le bug de ce script python", "", [], {"moteur_mode": "hybride"}, SANS_ZONE)
    assert (h.moteur, h.carte, h.recommande) == (routeur.LOCAL, routeur.CARTE_CAPACITE, routeur.CLAUDE)
    assert routeur.mode_valide("n'importe") == "auto"


def test_capacite_manquante_carte_jamais_bascule_seule():
    d = routeur.decider("explique ce fichier", "", ["notes.txt"], {"moteur_mode": "local"}, SANS_ZONE)
    assert d.carte == routeur.CARTE_CAPACITE and d.moteur == routeur.LOCAL and "pièces jointes" in d.raisons
    d = routeur.decider("cherche sur internet la CVE-2024-1086", "", [], {}, SANS_ZONE)
    assert d.carte == routeur.CARTE_CAPACITE and d.recommande == routeur.LOCAL


def test_canal_relie_a_un_projet_va_a_claude_meme_en_securite():
    d = routeur.decider("explique cette vulnérabilité XSS", "astrel", [], {}, _etat(projet=True))
    assert d.moteur == routeur.CLAUDE and d.categorie == "projet"


def test_whatsapp_jamais_route_en_local_automatiquement():
    d = routeur.decider("explique la CVE-2024-3094", "", [], {}, _etat(source="whatsapp"))
    assert d.moteur == routeur.CLAUDE and not d.carte
    d = routeur.decider(f"vérifie {IBAN}", "", [], {}, _etat(source="whatsapp"))
    assert d.carte == routeur.CARTE_SENSIBLE and d.recommande == routeur.CLAUDE


# ------------------------------------------------------------------ sensible.scan

def test_scan_rend_des_raisons_jamais_la_valeur():
    raisons = sensible.scan(f"clé {CLE} et IBAN {IBAN}", zones_conf=[])
    assert "IBAN" in raisons and any("clé" in r for r in raisons)
    assert not any(CLE in r or "3000" in r for r in raisons)


@pytest.mark.parametrize("texte", [
    "IBAN FR76 3000 6000 0112 3456 7890 188",      # clé fausse
    "carte 4970 1012 3456 7890",                   # Luhn faux
    "commande 1111 1111 1111 1111",                # chiffres répétés
    "sécu 1 85 05 78 006 084 36",                  # clé fausse
    "le token : 150k et mon mot de passe : dans config/secrets.json",
    "le port 8080 et la version 3.13.5",
])
def test_scan_sans_faux_positif(texte):
    assert sensible.scan(texte, zones_conf=[]) == []


def test_scan_zone_et_secret_du_garde_fou(tmp_path):
    zone = tmp_path / "medical"
    zone.mkdir()
    assert sensible.scan(f"résume {zone}/bilan.pdf", zones_conf=[zone.resolve()]) == \
        ["chemin dans une zone confidentielle"]
    assert sensible.scan("lis le fichier", [str(zone / "x.txt")], zones_conf=[zone.resolve()]) == \
        ["pièce jointe : chemin dans une zone confidentielle"]
    secret = guard.SECRET_PATHS[0]
    assert "fichier secret d'Astrel" in sensible.scan(f"cat {secret}", zones_conf=[])


# ------------------------------------------------------------------ garde-fou : zone confidentielle (lot 3b)

@pytest.fixture
def zone(tmp_path, monkeypatch):
    z = tmp_path / "conf"
    (z / "sous").mkdir(parents=True)
    (tmp_path / "autre").mkdir()
    reglages = tmp_path / "settings-zones.json"
    reglages.write_text(json.dumps({"version": 1, "values": {"zones_confidentielles": f"{z} ; ~/rien-ici"}}),
                        encoding="utf-8")
    monkeypatch.setattr(config, "ASTREL_SETTINGS_FILE", reglages)
    return z


@pytest.mark.parametrize("outil,entree,demande", [
    ("Read", {"file_path": "{z}/a.txt"}, True),
    ("Read", {"file_path": "{t}/autre/a.txt"}, False),
    ("Grep", {"pattern": "x", "path": "{z}/sous"}, True),
    ("Grep", {"pattern": "x", "path": "{t}"}, True),          # recherche dans un dossier qui CONTIENT la zone
    ("Grep", {"pattern": "x", "path": "{t}/autre"}, False),
    ("Glob", {"pattern": "{z}/**/*.md"}, True),
    ("Bash", {"command": "cat {z}/a.txt"}, True),
    ("Bash", {"command": "grep -r motdepasse {t}"}, True),
    ("Bash", {"command": "ls {t}"}, False),                   # les noms seulement
    ("Bash", {"command": "ls -la"}, False),
])
def test_lecture_dans_une_zone_demandee(zone, outil, entree, demande):
    t = zone.parent
    # 04/11 (CI Windows) : dans une commande Bash, Git Bash mange les « \\ » non cités : chemins en « / »
    z_, t_ = (zone.as_posix(), t.as_posix()) if outil == "Bash" else (zone, t)
    entree = {k: v.format(z=z_, t=t_) for k, v in entree.items()}
    for mode in ("autonome", "total"):
        v = guard.assess(outil, entree, mode=mode, cwd=str(t / "autre"))
        assert v.needs_confirm is demande, (mode, v.desc)
        if demande:
            assert v.desc == guard.L_ZONE and v.level == guard.CRITICAL and guard.limites.PLAFOND in v.cats


@pytest.mark.skipif(os.name != "nt", reason="chemin Windows à antislash, cité entre guillemets")
def test_chemin_windows_cite_dans_une_zone(zone):
    v = guard.assess("Bash", {"command": f"cat '{zone}\\a.txt'"}, mode="autonome", cwd=str(zone.parent / "autre"))
    assert v.needs_confirm and v.desc == guard.L_ZONE


def test_bash_lance_dans_la_zone(zone):
    v = guard.assess("Bash", {"command": "cat *"}, mode="autonome", cwd=str(zone))
    assert v.needs_confirm and v.desc == guard.L_ZONE


def test_sans_zone_rien_ne_change(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ASTREL_SETTINGS_FILE", tmp_path / "absent.json")
    assert guard.zones_confidentielles() == []
    assert not guard.assess("Read", {"file_path": str(tmp_path / "a.txt")}, mode="autonome").needs_confirm


def test_reglage_zone_illisible_ou_relatif(tmp_path, monkeypatch):
    f = tmp_path / "s.json"
    f.write_text("{pas du json", encoding="utf-8")
    monkeypatch.setattr(config, "ASTREL_SETTINGS_FILE", f)
    assert guard.zones_confidentielles() == []
    f.write_text(json.dumps({"values": {"zones_confidentielles": "relatif/x;"}}), encoding="utf-8")
    assert guard.zones_confidentielles() == []


def test_reglage_zones_chemins_absolus_seulement():
    from astrel import settings
    with pytest.raises(settings.SettingError):
        settings.validate("zones_confidentielles", "relatif/x")
    assert settings.validate("moteur_mode", "hybride") == "hybride"


# ------------------------------------------------------------------ cœur : barrière avant brain.send

class FakeBrain:
    alive = True

    def __init__(self):
        self.sent = []

    def send(self, prompt, images=None):
        self.sent.append(prompt)


class FakeSpeaker:
    paused = busy = False

    def __init__(self):
        self.said = []

    def say(self, text, raw=False, tag="", local_only=False):
        self.said.append(text)

    def interrupt(self):
        pass


@pytest.fixture
def j(monkeypatch, tmp_path):
    f = tmp_path / "channels.json"
    f.write_text(json.dumps({"default": "general", "channels": [{"id": "general", "label": "Général"}]}),
                 encoding="utf-8")
    monkeypatch.setattr(config, "CHANNELS_FILE", f)
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")
    from astrel.channels import channels
    channels.refresh()
    j = object.__new__(core.Astrel)
    j.voice_out = True
    j.speaker, j.status, j.brain = FakeSpeaker(), state.StatePublisher(), FakeBrain()
    j._running, j._off, j._in_conversation, j._followup = True, False, False, False
    j._turn_active, j._usage, j._followup_setting = False, "conversation", 8.0
    j._released, j.locaux, j.envoyes = [], [], []
    j._release_attachments = lambda ids, items=None: j._released.extend(ids)
    j.attente_notes = []
    j._attente_noter = lambda *a, **k: j.attente_notes.append(a)
    monkeypatch.setattr(core.Astrel, "_tour_local", lambda self, *a, **k: self.locaux.append(a))
    # Juste après la décision (le chemin d'envoi à Claude) : on note ce qui part, sans le reste du tour.
    monkeypatch.setattr(core.Astrel, "_cycle_garder", lambda self, text, *a, **k: self.envoyes.append(text) or True)
    return j


@pytest.fixture
def events():
    got = []
    stop = bus.subscribe(got.append)
    yield got
    stop()


def _carte(events):
    return [e for e in events if e.get("type") == "choice" and e.get("choice_kind") == "routage"][-1]


def _attendre(cond, s=2.0):
    import time
    fin = time.time() + s
    while time.time() < fin and not cond():
        time.sleep(0.01)
    return cond()


def test_sensible_vers_claude_retenu_derriere_une_carte(j, events, monkeypatch):
    monkeypatch.setattr(core.reglages, "value", lambda k: "claude" if k == "moteur_mode" else None)
    j.handle(f"pourquoi ma clé {CLE} est refusée ?", source="clavier", channel="general")
    assert j.envoyes == [] and j.attente_notes == [] and j.locaux == []
    carte = _carte(events)
    assert CLE in carte["question"] and [o["label"] for o in carte["options"]][-1] == "Annuler"
    assert not [e for e in events if e.get("type") == "transcript"]  # rien d'affiché comme envoyé
    # Claude, la bulle (socket) : refusés ; le HUD de l'utilisateur : la demande part, telle quelle.
    assert not j._choice_answer({"choice_id": carte["id"], "value": "Envoyer à Claude", "_from_claude": True})["ok"]
    assert not j._choice_answer({"choice_id": carte["id"], "value": "Envoyer à Claude", "_via": "socket"})["ok"]
    assert j._choice_answer({"choice_id": carte["id"], "value": "Envoyer à Claude", "_via": "hud"})["ok"]
    assert _attendre(lambda: j.envoyes)
    assert j.envoyes == [f"pourquoi ma clé {CLE} est refusée ?"]


def test_rester_en_local_et_annuler(j, events, monkeypatch):
    monkeypatch.setattr(core.reglages, "value", lambda k: "claude" if k == "moteur_mode" else None)
    j.handle(f"vérifie mon IBAN {IBAN}", source="clavier", channel="general")
    r = j._choice_answer({"choice_id": _carte(events)["id"], "value": "Rester en local", "_via": "hud"})
    assert r["ok"] and r["routage"] == "local" and len(j.locaux) == 1 and j.envoyes == []
    j.handle(f"vérifie mon IBAN {IBAN}", source="clavier", channel="general")
    r = j._choice_answer({"choice_id": _carte(events)["id"], "value": "Annuler", "_via": "hud"})
    assert r["routage"] == "annuler" and j.envoyes == [] and len(j.locaux) == 1


def test_texte_libre_de_la_carte_ne_part_nulle_part(j, events, monkeypatch):
    monkeypatch.setattr(core.reglages, "value", lambda k: "claude" if k == "moteur_mode" else None)
    j.handle(f"vérifie {IBAN}", source="clavier", channel="general")
    r = j._choice_answer({"choice_id": _carte(events)["id"], "value": "envoie-le quand même", "_via": "hud"})
    assert r["routage"] == "annuler" and j.envoyes == [] and j.locaux == []


def test_automatique_cyber_au_local_avec_notice(j, events, monkeypatch):
    monkeypatch.setattr(core.reglages, "value", lambda k: "auto" if k == "moteur_mode" else None)
    j.handle("explique la CVE-2024-3094", source="clavier", channel="general")
    assert len(j.locaux) == 1 and j.envoyes == []
    assert any(e.get("type") == "notice" and "modèle local" in e.get("text", "") for e in events)
    j.handle("explique la photosynthèse", source="clavier", channel="general")
    assert j.envoyes == ["explique la photosynthèse"]


def test_voix_dit_pourquoi_sans_la_donnee(j, events, monkeypatch):
    monkeypatch.setattr(core.reglages, "value", lambda k: "claude" if k == "moteur_mode" else None)
    j.handle(f"ma clé {CLE}", source="voix", channel="general")
    assert j.speaker.said and not any(CLE in s for s in j.speaker.said)


def test_journal_du_routage_sans_le_texte(j, monkeypatch):
    from astrel.coeur import routage
    monkeypatch.setattr(core.reglages, "value", lambda k: "auto" if k == "moteur_mode" else None)
    j.handle(f"ma clé {CLE}", source="clavier", channel="general")
    lignes = routage.ROUTAGE_LOG.read_text(encoding="utf-8")
    assert CLE not in lignes and "sk-ant" not in lignes
    d = json.loads(lignes.splitlines()[-1])
    assert d["moteur"] == "local" and d["categorie"] == "sensible" and d["raisons"]


def test_carte_routage_refusee_hors_hud_meme_inconnue():
    """Une carte d'un autre genre n'est pas touchée par la règle du HUD seul."""
    from astrel.coeur.routage import RoutageCoeur
    res = choices.ask("Question ?", ["A", "B"], channel="general")
    assert RoutageCoeur._routage_refus({"_via": "socket"}, res["id"]) == ""
