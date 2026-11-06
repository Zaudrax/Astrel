#!/usr/bin/env python3
"""Démo du HUD sans micro ni Claude : sert le HUD et rejoue une séquence
d'événements réalistes (états, voix, outils, quota, confirmation).

    .venv/bin/python tools/hud_demo.py            # puis ./hud.sh
    .venv/bin/python tools/hud_demo.py --once     # une séquence, puis attend
    Taper « démo markdown » dans la barre : réponse mise en forme écrite en flux.
    « démo tableau » (19/10) : tableau, schéma ASCII, liste et code.
    « démo liens » (04/11) : adresses nues cliquables, ponctuation, parenthèses, code, adresse longue.

15/10 (contrat des lots) : comptes rendus typés (`report_kind`, `report_files`),
question à choix (`choice` puis `choice_answered`), coût par agent (`tokens`,
`requests`, `elapsed_s`, `alert`) et prévision du quota (`forecast`, `windows`).
15/10 : page « Réglages » (`DemoSettings`, en mémoire, rien d'écrit sur le disque).
15/10 : émojis dans une réponse et une notice (le HUD les retire à l'affichage seulement),
commande `toggle` (micro coupé : `muted` dans l'état, comme le cœur).
15/10 : page « Plans » (`DemoPlans`, vrai module `astrel.plans`, fichiers dans le dossier
jetable) ; `demo_plan` (plan déposé en direct), `demo_plans_offline` (état d'erreur).
18/10 (plan P5) : appairage WhatsApp simulé (`whatsapp_link` : QR renouvelé, code,
compte détecté par `demo_wa_scan`, confirmé, délié) et guide de démarrage (vrai module
`astrel.guide` sur docs/GUIDE-DEMARRAGE.md, progression dans le dossier jetable).
26/10 (plan P38 lot 2) : vue « Tâches » (vrai module `astrel.todo`, todo.json et journal dans le
dossier jetable, statut des plans lu dans `DemoPlans`) ; l'agent d1 cite le plan P2 (`refs`).
02/11 (consigne de l'utilisateur) : données d'un assistant personnel (mails, agenda, recherches, rappels,
documents, déménagement), noms fictifs ; ni code, ni build, ni commit, ni agent qui code. Canaux de la
démo à elle (`CANAUX_DEMO`), jamais ceux de la machine.

Sert à régler l'interface et à la tester sans rien consommer sur l'abonnement.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys
import tempfile
import threading
import time
import types
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astrel import agents as agents_mod  # noqa: E402
from astrel import config  # noqa: E402
from astrel.brain import model_label  # noqa: E402
from astrel.channels import channels  # noqa: E402
from astrel.choices import choices  # noqa: E402
from astrel.events import bus  # noqa: E402
from astrel.graph import graph  # noqa: E402
from astrel import history  # noqa: E402  (04/11 : recherche dans tous les canaux, index jetable)
from astrel.icones import for_name as icon_for  # noqa: E402
from astrel.reports import reports  # noqa: E402
from astrel.sensors import sensors  # noqa: E402
from astrel.server import HudServer, login_url  # noqa: E402
from astrel import todo as todo_mod  # noqa: E402  (26/10, plan P38 lot 2)

# Canaux de la démo (02/11) : des sujets d'exemple, fictifs. Noms de canaux : jamais traduits.
CANAUX_DEMO = {"default": "general", "channels": [
    dict(id=cid, label=nom, color=couleur, **kw) for cid, nom, couleur, kw in (
        ("general", "Général", "#3ad6ff", {}),
        ("maison", "Maison", "#7ea8ff", {"keywords": ["courses", "ménage", "déménagement", "cartons"]}),
        ("voyages", "Voyages", "#34e3a4", {"keywords": ["train", "billet", "gîte", "vacances"]}),
        ("famille", "Famille", "#9d7bff", {"keywords": ["anniversaire", "Léa"]}),
        ("papiers", "Papiers", "#ff9d4d", {"keywords": ["mails", "factures", "banque", "assurance"]}),
        ("sport", "Sport", "#ffd54a", {"keywords": ["piscine", "course à pied"]}),
        ("lecture", "Lecture", "#c3f36b", {"keywords": ["livre", "bibliothèque"]}),
    )]}

# 05/11 (README anglais du dépôt public, tools/capture_readme.py) : CONTENU de la démo en anglais avec
# `--langue en` (ou ASTREL_DEMO_LANGUE=en) : conversation, questions à choix, agents, comptes rendus, canaux ;
# l'interface passe en anglais aussi (`ui_language`). Les textes du cœur (notices, garde-fou, états) restent
# écrits en français et traduits par le HUD, comme pour le vrai Astrel. Défaut : français, inchangé.
LANGUE = {"demo": "fr"}
VOIX = {"fr": "piper:fr_FR-upmc-medium", "en": "piper:en_US-joe-medium"}  # voix locale de la conversation
VOIX_NOMS = {"fr": "Piper UPMC", "en": "Piper Joe"}  # noms propres des voix (menu des voix)
CANAUX_EN = {"general": ("General", None), "maison": ("Home", ["groceries", "cleaning", "moving", "boxes"]),
             "voyages": ("Travel", ["train", "ticket", "cottage", "holiday"]),
             "famille": ("Family", ["birthday", "Emma"]), "papiers": ("Paperwork", ["inbox", "bills", "bank", "insurance"]),
             "sport": ("Sport", ["swimming", "running"]), "lecture": ("Reading", ["book", "library"])}
CONTENU_EN = {
    # séquence : question dite, outils, réponse, compte rendu d'un agent de fond, e-mail à confirmer
    "Qu'est-ce que j'ai dans mes mails ce matin ? Et regarde la météo à Nantes.":
        "What's in my inbox this morning? And check the weather in Bristol.",
    "mails de ce matin": "this morning's emails", "météo Nantes": "Bristol weather", "écran principal": "main screen",
    "Trois mails ce matin, dont la facture d'électricité à régler avant vendredi ✅. "
    "À Nantes, dix-sept degrés et un ciel dégagé ce soir 🌤️.":
        "Three emails this morning, including the electricity bill, due before Friday ✅. "
        "Bristol is at seventeen degrees, with clear skies tonight 🌤️.",
    "La recherche est finie : trois offres sérieuses, je te les détaille quand tu veux.":
        "The research is done: three solid offers. I can walk you through them whenever you like.",
    "À : agence@immo-exemple.fr · Objet : Préavis de départ du logement":
        "To: letting-agent@example.com · Subject: Notice to end my tenancy",
    # questions à choix (DEMO_CHOICES)
    "Le train de 8 h 12 est complet. Je fais quoi ?": "The 8:12 train is fully booked. What should I do?",
    "Prendre celui de 9 h 40": "Take the 9:40 instead", "Attendre ton retour": "Wait until I hear from you",
    "Laisser tomber ce trajet": "Skip this trip",
    "Quel nom pour le nouveau canal ?": "What should we call the new channel?",
    "Jardin": "Garden", "Potager": "Vegetable patch",
    "On réserve le restaurant pour samedi ?": "Shall I book the restaurant for Saturday?",
    "Oui, à 20 h": "Yes, at 8 pm", "Dimanche midi": "Sunday lunch instead", "Pas cette semaine": "Not this week",
    "Je regarde d'abord": "Let me check first",
    # agents en cours (mêmes mots que le catalogue anglais des descriptions de la démo)
    "Trier les mails de la semaine": "Sort this week's emails",
    "Lire le fil « Facture d'électricité »": "Read the \"Electricity bill\" thread",
    "Copier les photos sur le disque externe": "Copy the photos to the external drive",
    "Relire tous les contrats d'assurance": "Review all the insurance contracts",
    "Lire le contrat n° 7": "Read contract no. 7",
    # comptes rendus présents à l'ouverture
    "Deux déménageurs possibles : le moins cher le 21, ou le plus rapide le 14. Dis-moi lequel.":
        "Two movers are available: the cheaper one on the 21st, or the faster one on the 14th. Which do you prefer?",
    "/home/user/Documents/Déménagement/devis-comparés.pdf": "~/Documents/Moving/quotes-compared.pdf",
    "/tmp/astrel-demo/devis-demenageurs.txt": "/tmp/astrel-demo/mover-quotes.txt",
    "La réservation du gîte est bloquée : le site demande une pièce d'identité.":
        "The holiday cottage booking is stuck: the site wants a copy of your ID.",
    "Les photos sont copiées sur le disque externe et vérifiées.":
        "The photos have been copied to the external drive and checked.",
    "/media/disque-externe/Photos/2026-10-15": "/media/external-drive/Photos/2026-10-15",
}


def contenu(texte: str) -> str:
    """CONTENU de la démo dans sa langue (05/11) : anglais d'après `CONTENU_EN`, sinon tel quel."""
    return CONTENU_EN.get(texte, texte) if LANGUE["demo"] == "en" else texte


def canaux_demo() -> dict:
    """Canaux de la démo dans sa langue (05/11) : libellés et mots-clés anglais avec `--langue en`."""
    if LANGUE["demo"] != "en":
        return CANAUX_DEMO
    out = copy.deepcopy(CANAUX_DEMO)
    for c in out["channels"]:
        c["label"], mots = CANAUX_EN[c["id"]]
        if mots is not None:
            c["keywords"] = mots
    return out

INIT = {
    "model": "claude-sonnet-5",
    "tools": ["Task", "Bash", "Edit", "Read", "Write", "Grep", "Glob", "WebFetch", "WebSearch", "Skill",
              "TodoWrite", "mcp__astrel__screen_capture", "mcp__astrel__set_volume", "mcp__astrel__notify",
              "mcp__astrel__memory_search", "mcp__astrel__memory_write", "mcp__playwright__browser_navigate",
              "mcp__playwright__browser_snapshot", "mcp__playwright__browser_click"],
    "skills": ["deep-research", "dataviz", "anthropic-skills:pdf", "anthropic-skills:docx", "anthropic-skills:xlsx",
               "anthropic-skills:pptx"],
    "agents": ["Explore", "Plan", "general-purpose"],
    "mcp_servers": [{"name": "astrel", "status": "connected"}, {"name": "playwright", "status": "connected"}],
    "session_id": "demo",
}


def speak(text: str, seconds: float) -> None:
    bus.publish("state", state="speaking", text=text, muted=False, detail="")
    bus.publish("speech", text=text)
    t0 = time.time()
    while time.time() - t0 < seconds:
        t = time.time() - t0
        v = 0.45 + 0.35 * abs(math.sin(t * 7.3)) * abs(math.sin(t * 2.1 + 1))
        bus.publish("level", source="voice", value=round(v, 3))
        time.sleep(1 / 30)
    bus.publish("level", source="voice", value=0.0)


def sequence() -> None:
    bus.publish("state", state="listening", text="", muted=False, detail="")
    for i in range(40):
        bus.publish("level", source="mic", value=round(0.3 + 0.4 * abs(math.sin(i / 3)), 3))
        time.sleep(0.05)
    question = contenu("Qu'est-ce que j'ai dans mes mails ce matin ? Et regarde la météo à Nantes.")
    channel, _ = channels.route(question)
    mid = uuid.uuid4().hex[:12]
    bus.publish("transcript", who="user", text=question, source="voix", channel=channel, mid=mid)
    bus.publish("state", state="thinking", text=question, muted=False, detail="")
    time.sleep(1.2)
    for tool, desc in (("mcp__claude_ai_Gmail__search_threads", "mails de ce matin"), ("WebSearch", "météo Nantes"),
                       ("mcp__astrel__screen_capture", "écran principal")):
        desc = contenu(desc)
        tid = f"t{time.time()}"
        node = graph.record_use(tool)
        bus.publish("tool", tool=tool, id=tid, desc=desc, node=node)
        bus.publish("state", state="working", text="", muted=False, detail=f"{tool} · {desc}")
        time.sleep(1.6)
        bus.publish("tool_result", tool=tool, id=tid, ok=True, text="ok")
    graph.publish()
    # Émojis comme Claude en met parfois : affichés SANS par le HUD, gardés dans le texte brut (copie, lecture).
    reply = contenu("Trois mails ce matin, dont la facture d'électricité à régler avant vendredi ✅. "
                    "À Nantes, dix-sept degrés et un ciel dégagé ce soir 🌤️.")
    for word in reply.split(" "):
        bus.publish("reply_delta", text=word + " ", channel=channel, reply_to=mid)
        time.sleep(0.05)
    speak(reply, 4.5)
    bus.publish("reply", text=reply, usage={}, channel=channel, reply_to=mid, mid=uuid.uuid4().hex[:12])
    # Un agent de fond lancé depuis un autre canal finit pendant ce temps : son compte
    # rendu va dans ce canal (pastille de non-lus si un autre est ouvert),
    # en silence, avec un bouton « Lire » (mode « à la demande »).
    other = next((c.id for c in channels.items() if c.id not in (channel, channels.default)), channels.default)
    bus.publish("notice", level="info", channel=other, text="✅ Terminé : Comparatif des assurances habitation")
    report = contenu("La recherche est finie : trois offres sérieuses, je te les détaille quand tu veux.")
    rid = uuid.uuid4().hex[:12]
    pending = reports.on_demand
    reports.add(rid, report, other, pending)
    bus.publish("reply", text=report, usage={}, channel=other, reply_to="", mid=rid, report=True, pending_read=pending)
    if not pending:
        speak(report, 3.0)
    # Forme du cœur du 15/10 (`core.quota_windows`) : liste, `limit_type`, prévision vide si inconnue.
    bus.publish("quota", status="allowed", limit_type="five_hour", forecast="", windows=[
        {"name": "five_hour", "pct": 18, "utilization": 0.18, "resetsAt": time.time() + 7200},
        {"name": "seven_day", "pct": 7, "utilization": 0.07, "resetsAt": time.time() + 400000}])
    time.sleep(1)
    bus.publish("confirm", id="demo1", pending=True, tool="mcp__claude_ai_Gmail__send_message",
                desc="l'envoi d'un e-mail",
                cmd=contenu("À : agence@immo-exemple.fr · Objet : Préavis de départ du logement"),
                question="Attention, l'envoi d'un e-mail. Je continue ?")
    bus.publish("state", state="asking", text="", muted=False, detail="l'envoi d'un e-mail")


# Réponse de démonstration du rendu markdown du fil (15/10), avec des pièges :
# tout ce qui ressemble à du HTML ou à un lien non https doit rester du texte.
MARKDOWN_DEMO = """## Bilan de la journée
Trois points **importants**, puis le détail :

1. La facture d'*électricité* est réglée (`prélèvement du 15`, 82 €).
2. Le colis de la librairie est en route :
   - expédié à 16 h 08 ;
   - à suivre sur [La Poste](https://www.laposte.fr/outils/suivre-vos-envois).
3. Reste à relire `bail-appartement.pdf`.

### Liste de courses
```
pain, œufs, tomates, café
```

> Pièges (doivent rester du texte) : <script>alert(1)</script>, <img src=x onerror=alert(2)>,
> [clique ici](javascript:alert(3)) et ![pisteur](https://pisteur.example/p.gif)

---
Météo détaillée : https://meteofrance.com."""


# 19/10 (demande : « tableau, schéma et autre ? ») : tableau aligné, schéma ASCII dans un
# bloc ``` (espaces gardés), liste, code ; « démo tableau » dans la barre.
TABLE_DEMO = """### Comparatif des trajets
| Trajet | Train | État | Durée |
|:-------|:-----:|------|------:|
| Nantes → Bordeaux | n°8 | **complet** à 8 h 12 | 4 h 05 |
| Nantes → Bordeaux | n°9 | places libres | 4 h 20 |
| Nantes → Paris | — | `billet échangeable` | 2 h 10 |

Schéma du trajet du samedi :
```
 maison ──> gare ──> train ──> Bordeaux
                                 │
 hôtel <──── tram <──────────────┘
```

À faire ensuite :
- réserver le train **n°9** ;
- lire [la page de la ville](https://fr.wikipedia.org/wiki/Bordeaux) ;
- vérifier http://non-cliquable.example (http reste du texte).

```
Budget : 2 billets × 39 € = 78 €
```"""


# 04/11 (demande : « les liens ne sont pas ouvrables comme sur ChatGPT ») : adresses NUES dans une réponse,
# ponctuation finale, parenthèses, adresse longue, lien markdown, et ce qui ne doit JAMAIS devenir un lien
# (code en ligne, bloc ```, http://) ; « démo liens » dans la barre.
LINKS_DEMO = """Voici les liens :
- le dépôt : https://github.com/exemple/projet.
- la ville https://fr.wikipedia.org/wiki/Nantes_(France), puis la suite ;
- entre parenthèses (https://example.org/page) et fin.
- une adresse très longue : https://www.example.com/un/chemin/vraiment/tres/long/qui/n/en/finit/pas?avec=des&parametres=nombreux&encore=oui
- un lien écrit [en markdown](https://docs.example.org/guide) ;
- en code, jamais cliquable : `https://code.example/en-ligne`

```
https://bloc.example/jamais
```
Et http://pas-https.example reste du texte."""

# 04/11 (demande : chemins de fichiers cliquables) : « démo chemins » ; un clic COPIE, rien ne s'ouvre ; jamais
# dans un bloc ```, ni une date, une fraction, « et/ou » ou une adresse.
PATHS_DEMO = r"""Voici les fichiers :
- le script /home/user/Projets/exemple/run.sh, à relancer ;
- ta note ~/Documents/Notes/courses.md (et C:\Users\Lea\Bureau\devis.pdf sous Windows).
- en code : `/data/Mes documents/Note de frais.pdf` et `python3 /tmp/astrel-demo/a.py`
- pas des chemins : le 04/11/2026, et/ou, 1/2, https://example.org/a/b

```
cat /etc/hosts /data/jamais/dans-un-bloc.txt
```"""


def stream_markdown(channel: str, reply_to: str, text: str = MARKDOWN_DEMO) -> None:
    """Écrit le texte par petits morceaux, comme un vrai tour (reply_delta puis reply)."""
    for k in range(0, len(text), 7):
        bus.publish("reply_delta", text=text[k:k + 7], channel=channel, reply_to=reply_to)
        time.sleep(0.02)
    bus.publish("reply", text=text, usage={}, channel=channel, reply_to=reply_to,
                mid=uuid.uuid4().hex[:12])


# Questions à choix de la démo (15/10), posées par le VRAI module du cœur (`astrel.choices`,
# comme l'outil MCP ask_choice) : mêmes évènements `choice` / `choice_answered`, même
# épinglage dans le rejeu, et une nouvelle question dans un canal ANNULE celle qui y
# attendait encore (`choice_answered` avec `cancelled: true`).
DEMO_CHOICES = [
    ("Le train de 8 h 12 est complet. Je fais quoi ?",
     ["Prendre celui de 9 h 40", "Attendre ton retour", "Laisser tomber ce trajet"]),
    ("Quel nom pour le nouveau canal ?", ["Jardin", "Potager"]),
    ("On réserve le restaurant pour samedi ?", ["Oui, à 20 h", "Dimanche midi", "Pas cette semaine", "Je regarde d'abord"]),
]
choice_count = {"n": 0}


def publish_choice(channel: str) -> str:
    question, labels = DEMO_CHOICES[choice_count["n"] % len(DEMO_CHOICES)]
    choice_count["n"] += 1
    return str(choices.ask(contenu(question), [contenu(x) for x in labels], channel=channel).get("id") or "")


# 21/10 (plan P14) : images IA de la démo, par le VRAI module du cœur (`astrel.images_ia` : file,
# évènements `image_job`, copie pour /api/attachment) avec un FAUX worker : aucun navigateur,
# aucun site. `demo_image` {mode} : ok (une image), variantes (grille de 4, forme de Bing),
# refus (échec avec le texte de l'IA), lent (reste en chargement jusqu'à l'arrêt de la démo).
DEMO_IMAGE_PROMPTS = {
    "ok": "un phare breton au lever du soleil, style aquarelle",
    "variantes": "quatre variantes d'une carte d'anniversaire minimaliste",
    "refus": "une photo réaliste d'une personne célèbre",
    "lent": "une ville futuriste au coucher du soleil, vue du ciel",
}


def make_demo_images(root: Path):
    from PIL import Image, ImageDraw

    from astrel import images_ia

    config.IMAGES_RUNTIME = root / "imagegen"
    config.IMAGES_PROFILS = root / "imagegen" / "profils"
    config.IMAGES_ETAT_FILE = root / "imagegen" / "etat.json"
    config.IMAGES_DEBUG_DIR = root / "imagegen" / "debug"
    config.IMAGES_LOG = root / "images.log"
    config.IMAGES_SORTIE = root / "Images-IA"
    config.IMAGES_COMPTES = (("principal", "gemini"),)
    (config.IMAGES_PROFILS / "principal").mkdir(parents=True, exist_ok=True)
    config.IMAGES_SORTIE.mkdir(parents=True, exist_ok=True)
    modes: list[str] = []
    stop = threading.Event()

    def picture(path: Path, hue: int) -> None:
        w, h = 704, 384
        im = Image.new("RGB", (w, h))
        d = ImageDraw.Draw(im)
        for y in range(h):
            d.line([(0, y), (w, y)], fill=((40 + hue + y // 3) % 256, 90 + y // 4, 120 + (hue // 2)))
        d.ellipse([w // 2 - 110, h // 2 - 70, w // 2 + 110, h // 2 + 70], fill=(60, 170 + hue % 60, 90))
        d.ellipse([w // 2 + 40, h // 2 - 40, w // 2 + 80, h // 2], fill=(250, 160, 40))
        im.save(path, "PNG")

    def runner(entree: dict, ligne) -> dict:
        mode = modes.pop(0) if modes else "ok"
        for step, wait in (("ouverture", 0.8), ("saisie", 1.0)):
            ligne({"t": "etape", "etape": step})
            time.sleep(wait)
        ligne({"t": "envoye"})
        ligne({"t": "etape", "etape": "generation"})
        if mode == "lent":
            stop.wait(3600)
        time.sleep(2.0)
        if mode == "refus":  # texte écrit par l'IA : montré tel quel, jamais traduit
            said_by_ai = "I can't create realistic images of real people. Try describing a fictional character instead."
            return {"t": "echec", "code": "refus", "message": said_by_ai}
        ligne({"t": "etape", "etape": "telechargement"})
        time.sleep(0.5)
        files = []
        for i in range(4 if mode == "variantes" else 1):
            f = config.IMAGES_SORTIE / f"{time.strftime('%Y%m%d-%H%M%S')}-gemini-demo-{uuid.uuid4().hex[:6]}-{i}.png"
            picture(f, 40 * i + (90 if mode == "variantes" else 0))
            files.append({"fichier": str(f), "nom": f.name})
        return {"t": "fini", "fichier": files[0]["fichier"], "nom": files[0]["nom"], "fichiers": files}

    demo = images_ia.Images(runner=runner)

    # 28/10 (plan P40 lot A5) : comptes de la démo pour le panneau « Images » et Réglages › Images
    # (dossier jetable) : principal (Google, connecté, 3 images), famille (Google, épuisé, reprise dans 3 h),
    # perso (OpenAI, jamais connecté). La connexion n'ouvre AUCUN Chrome ici : fenêtre simulée.
    from astrel import ia_connexion, ia_web

    ia_web.ecrire_comptes([{"alias": "principal", "fournisseur": "google"}, {"alias": "famille", "fournisseur": "google"},
                           {"alias": "perso", "fournisseur": "openai"}])
    (config.IMAGES_PROFILS / "famille").mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        demo.etat.compter("principal", "gemini")
    demo.etat.epuise("famille", "You've reached your limit for today.", "+3h", False, moteur="gemini")
    ouvertes: set[str] = set()

    def ouvrir(alias: str, **_kw) -> dict:
        if alias not in {c["alias"] for c in ia_web.comptes()}:
            return {"ok": False, "code": "inconnu"}
        ouvertes.add(alias)
        (config.IMAGES_PROFILS / alias).mkdir(parents=True, exist_ok=True)
        demo.publier_etat()  # comme `ia_connexion._signaler` à l'ouverture de la vraie fenêtre
        return {"ok": True}

    ia_connexion.ouvrir = ouvrir
    ia_connexion.ouvertes = lambda: sorted(ouvertes)
    ia_connexion.ouverte = lambda alias: alias in ouvertes
    ia_connexion.on_change = demo.publier_etat

    def submit(mode: str, channel: str) -> dict:
        mode = mode if mode in DEMO_IMAGE_PROMPTS else "ok"
        modes.append(mode)
        return demo.submit(DEMO_IMAGE_PROMPTS[mode], "auto", [], channel)

    return demo, submit


def demo_reglages_images(settings) -> dict:
    """(démo) `images_ia.reglages_images` lu dans les Réglages de la démo, pas dans runtime/settings.json."""
    from astrel import ia_web

    val = {i["key"]: i["value"] for g in settings.groups for i in g["items"] if i["key"].startswith("images_")}
    services = ia_web.services("image")
    ordre = [s for s in str(val.get("images_ordre") or "").split(",") if s in services]
    service, _, modele = str(val.get("images_manuel") or "gemini/defaut").partition("/")
    return {"mode": "manuel" if val.get("images_mode") == "manuel" else "auto",
            "ordre": ordre + [s for s in services if s not in ordre], "service": service, "modele": modele or "defaut"}


# 22/10 (plan P26) : médias du fil par le VRAI module `astrel.medias` (vérification, type lu dans
# les octets, copies, /api/media) ; sources et copies dans le dossier jetable (sous /tmp, permis).
# `demo_media` {kind} : show (carte show_media : 2 images, PDF de 3 pages, son, vidéo), report
# (compte rendu avec fichiers), choice (question à choix illustrée), big (« Ouvrir » seul),
# secret (fichier de config/ d'Astrel : refusé, rien n'est affiché).
def make_demo_media(root: Path):
    import io
    import math as _math
    import shutil
    import struct
    import wave

    from PIL import Image, ImageDraw

    from astrel import medias

    medias.MEDIA_DIR = root / "medias"
    src = root / "medias-src"
    src.mkdir(exist_ok=True)

    def affiche(name: str, color: tuple, w: int = 960, h: int = 640) -> Path:
        im = Image.new("RGB", (w, h), (12, 18, 32))
        d = ImageDraw.Draw(im)
        d.ellipse([w // 2 - 200, h // 2 - 200, w // 2 + 200, h // 2 + 200], fill=color)
        d.rectangle([w // 2 - 40, h // 2 - 120, w // 2 + 40, h // 2 + 120], fill=(240, 240, 240))
        im.save(src / name, "PNG")
        return src / name

    files = {k: affiche(f"affiche-{k}.png", c) for k, c in
             (("bleue", (40, 120, 230)), ("orange", (240, 140, 30)), ("verte", (40, 180, 110)))}
    pages = []
    for i, c in enumerate(((235, 235, 240), (225, 235, 250), (250, 240, 225))):
        im = Image.new("RGB", (620, 877), c)
        d = ImageDraw.Draw(im)
        d.rectangle([60, 60, 560, 130], fill=(30, 60, 120))
        for y in range(180, 800, 34):
            d.rectangle([60, y, 60 + 400 + (y * 7 + i * 50) % 100, y + 12], fill=(120, 130, 150))
        pages.append(im)
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:])
    (src / "devis-traiteur.pdf").write_bytes(buf.getvalue())
    with wave.open(str(src / "note-vocale.wav"), "wb") as w:  # 2 s de La 440 Hz, doux
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(3000 * _math.sin(2 * _math.pi * 440 * n / 16000)))
                               for n in range(32000)))
    sample = next(iter(Path("/usr/share/help/C/gnome-help/figures").glob("*.webm")), None) \
        if Path("/usr/share/help/C/gnome-help/figures").is_dir() else None
    video = src / "demo-ecran.webm"
    if sample:
        shutil.copyfile(sample, video)
    else:  # pas de vidéo sur la machine : en-tête WebM seul (la carte dira « ne se lit pas ici »)
        video.write_bytes(b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\x82\x84webm" + b"\x00" * 64)

    def publish(kind: str, channel: str) -> dict:
        if kind == "show":
            ok, refused = medias.publish_many([str(files["bleue"]), str(files["orange"]), str(src / "devis-traiteur.pdf"),
                                               str(src / "note-vocale.wav"), str(video)])
            mid = uuid.uuid4().hex[:12]
            bus.publish("media", mid=mid, channel=channel, title="Affiches et devis pour la fête des voisins", items=ok)
            return {"ok": True, "mid": mid, **medias.summary(ok, refused)}
        if kind == "report":
            paths = [str(files["verte"]), str(src / "devis-traiteur.pdf")]
            ok, refused = medias.publish_many(paths)
            mid = uuid.uuid4().hex[:12]
            said = "Le devis est prêt, avec l'affiche retenue."  # écrit par « Claude » : jamais traduit
            reports.add(mid, said, channel, False, kind="resultat", files=paths)
            bus.publish("report", text=said, channel=channel, mid=mid, report=True, pending_read=False,
                        report_kind="resultat", report_files=paths, report_media=ok)
            return {"ok": True, "mid": mid}
        if kind == "choice":
            pics = [medias.publish(files[k]) for k in ("bleue", "orange", "verte")]
            return choices.ask("Quelle affiche pour la fête des voisins ?", ["Bleue", "Orange", "Verte"], channel,
                               images=pics)
        if kind == "big":
            old = medias.LIMITS["image"]
            medias.LIMITS["image"] = 1024
            try:
                ok, _ = medias.publish_many([str(files["bleue"])])
            finally:
                medias.LIMITS["image"] = old
            bus.publish("media", mid=uuid.uuid4().hex[:12], channel=channel, title="", items=ok)
            return {"ok": True}
        if kind == "broken":  # P26, fiche 2 du testeur : en-tête PNG, contenu abîmé → pas de miniature (404)
            bad = src / "photo-abimee.png"
            bad.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 300)
            ok, _ = medias.publish_many([str(bad)])
            bus.publish("media", mid=uuid.uuid4().hex[:12], channel=channel, title="", items=ok)
            return {"ok": bool(ok)}
        if kind == "secret":
            ok, refused = medias.publish_many([str(config.ROOT / "config" / "channels.json")])
            if ok:
                bus.publish("media", mid=uuid.uuid4().hex[:12], channel=channel, title="", items=ok)
            return {"ok": not ok, "refuses": refused}
        name = f"demo_media {kind}"
        return {"ok": False, "error": f"commande inconnue : {name}"}  # clé déjà au catalogue

    return publish


# 28/10 (plan P41 lot B3) : vidéos IA de la démo par le VRAI module (`astrel.videos_ia` : file, crédits,
# `video_job`, annulation) et la vraie publication dans le fil (`Astrel._video_finie`, médias), avec un
# FAUX worker : aucun navigateur, aucun site. Après `make_demo_images` (profils) et `make_demo_media`
# (dossier des médias, webm de démo). `demo_video` {mode} : ok (finie, lecteur dessous), lent (en cours
# jusqu'à « Annuler »).
def make_demo_videos(root: Path, settings):
    import shutil

    from astrel import videos_ia
    from astrel.core import Astrel

    videos_ia.SORTIE = root / "Videos-IA"
    videos_ia.LOG = root / "videos.log"
    videos_ia.SORTIE.mkdir(parents=True, exist_ok=True)
    # modèle et durée : le réglage de la démo (P41 B4), jamais les vrais Réglages d'Astrel
    videos_ia.reglage_choix = lambda: videos_ia.lire_choix(str(settings._item("video_choix").get("value") or ""))
    # 29/10 : jamais de créneau de pointe dans la démo (Flow 14-17 h UTC) : les contrôles ne dépendent pas de l'heure
    videos_ia.fin_pointe = lambda service, now: None
    modes: list[str] = []

    def runner(entree: dict, ligne) -> dict:
        mode = modes.pop(0) if modes else "ok"
        for step, wait in (("ouverture", 0.6), ("saisie", 0.6)):
            ligne({"t": "etape", "etape": step})
            time.sleep(wait)
        ligne({"t": "envoye"})
        ligne({"t": "etape", "etape": "generation"})
        fin = time.monotonic() + (3600 if mode == "lent" else 2.5)
        while time.monotonic() < fin and not (demo.en_cours and demo.en_cours.annule):
            time.sleep(0.1)
        f = videos_ia.SORTIE / f"{time.strftime('%Y%m%d-%H%M%S')}-flow-demo-{uuid.uuid4().hex[:6]}.webm"
        shutil.copyfile(root / "medias-src" / "demo-ecran.webm", f)
        return {"t": "fini", "fichier": str(f), "nom": f.name, "modele_utilise": entree["modele"]}

    demo = videos_ia.Videos(runner=runner)
    demo.on_fini = Astrel._video_finie

    def submit(mode: str, channel: str) -> dict:
        modes.append(mode if mode in ("ok", "lent") else "ok")
        return demo.submit("des vagues qui se brisent au pied d'un phare, plan rapproché", channel=channel)

    return demo, submit


def publish_demo_tasks(items: list[dict], done: list[dict] | None = None) -> None:
    """Comme le cœur ; sans `done` (forme d'avant P18) tant que `demo_tasks_done` n'a pas servi :
    le HUD doit accepter les deux."""
    if done is None:
        bus.publish("tasks", items=items)
    else:
        bus.publish("tasks", items=items, done=done)


# 21/10 (plan P18) : réponses de `task_detail` (forme de astrel/detail_agent.assemble), données
# seules. d1 porte des pièges (balise, U+202E, caractères de contrôle) : le HUD doit les montrer
# en TEXTE.
DEMO_TRAP = "<img src=x onerror=\"window.__pwned=1\">‮evil\x1b[31m rouge\x00 fin"


def demo_details(now: float) -> dict[str, dict]:
    def base(tid, kind, typ, desc, started, live=True, **extra):
        d = {"ok": True, "task_id": tid, "live": live, "state": "running" if live else "completed", "kind": kind,
             "type": typ, "desc": desc, "channel": "general", "background": True, "started": started,
             "ended": None, "elapsed_s": round(now - started, 1), "tokens": 0, "requests": 0, "tool_uses": 0,
             "step": "", "consigne": "", "todos": None, "doing": [], "actions": [], "errors": 0, "last_errors": [],
             "files": [], "summary": "", "passation": None, "passation_ambiguous": False, "truncated": False}
        d.update(extra)
        return d

    def act(tool, desc, ok, ago):
        return {"tool": tool, "desc": desc, "ok": ok, "at": round(now - ago, 1)}

    def todo(text, status):  # étape TodoWrite d'un agent : son CONTENU, pas un texte du HUD
        return {"text": text, "status": status}

    def doing(tool, desc):
        return {"tool": tool, "desc": desc}

    return {
        "d1": base("d1", "agent", "chercheur", contenu("Trier les mails de la semaine"), now - 142, effort="medium",
                   tokens=1_234_000, requests=12, tool_uses=7, step=contenu("Lire le fil « Facture d'électricité »"),
                   consigne="Trie les mails de la semaine par sujet.\nSignale les factures.\n" + DEMO_TRAP,
                   todos=[todo("Lister les mails de la semaine", "completed"),
                          todo("Ranger par sujet", "in_progress"),
                          todo("Signaler les factures " + DEMO_TRAP, "pending")],
                   doing=[doing("mcp__claude_ai_Gmail__get_thread", "Facture d'électricité")],
                   actions=[act("mcp__claude_ai_Gmail__search_threads", "newer_than:7d", True, 120),
                            act("Read", "/tmp/regles-tri.md", False, 90),
                            act("mcp__claude_ai_Gmail__get_thread", "Facture d'électricité", None, 4)],
                   errors=1, last_errors=["File does not exist: /tmp/regles-tri.md " + DEMO_TRAP]),
        "d2": base("d2", "local_bash", "commande", contenu("Copier les photos sur le disque externe"), now - 18,
                   consigne="rsync -a ~/Images/Vacances/ /media/disque-externe/Photos/"),
        "d3": base("d3", "agent", "auditeur", contenu("Relire tous les contrats d'assurance"), now - 2400, effort="high",
                   tokens=1_400_000, requests=162, tool_uses=162, step=contenu("Lire le contrat n° 7"),
                   consigne="Relis tous les contrats d'assurance et repère les doublons.",
                   actions=[act("Read", f"~/Documents/Assurances/contrat-{n}.pdf", True, 30 - n) for n in range(12)],
                   truncated=True),
        "d4": base("d4", "agent", "chercheur", "Comparer les billets de train", now - 900, live=False, state="failed",
                   effort="medium", ended=now - 300, elapsed_s=600.0, tokens=480_000, requests=31, tool_uses=24,
                   consigne="Compare les billets Nantes-Bordeaux pour samedi (plan P2).",
                   todos=[todo("Lire le plan P2", "completed"),
                          todo("Chercher les horaires", "in_progress"),
                          todo("Comparer les prix", "pending")],
                   actions=[act("WebFetch", "https://www.example-trains.fr/horaires", True, 420),
                            act("WebSearch", "train Nantes Bordeaux samedi", False, 360),
                            act("WebFetch", "https://www.example-trains.fr/tarifs", None, 310)],
                   errors=2, last_errors=["Site indisponible (503)", "Délai dépassé : 30 s sans réponse"],
                   files=["~/Documents/Voyages/horaires-samedi.md"], summary="Arrêté : le site des horaires ne répond plus.",
                   passation_ambiguous=True),
        "d5": base("d5", "agent", "planificateur", "Liste des cartons du déménagement", now - 700, live=False,
                   ended=now - 120, effort="high", elapsed_s=580.0, tokens=900_000, requests=48, tool_uses=61,
                   consigne="Prépare la liste des cartons, pièce par pièce (plan P1).",
                   actions=[act("Read", "~/Documents/Déménagement/inventaire.pdf", True, 400),
                            act("Write", "~/Documents/Déménagement/liste-cartons.md", True, 200)],
                   files=["~/Documents/Déménagement/liste-cartons.md"],
                   summary="Liste faite : 42 cartons, pièce par pièce.",
                   passation={"mission": "p1", "n": 1, "a": "chercheur", "at": now - 130, "fait": "Liste des cartons.",
                              "a_tester": "Dates à vérifier avec le déménageur.",
                              "risques": "Devis du déménageur pas encore signé.",
                              "preuves": "liste-cartons.md : 42 lignes"}),
    }


def demo_done_items(details: dict[str, dict]) -> list[dict]:
    keys = ("id", "kind", "desc", "background", "channel", "started", "ended", "elapsed_s", "tokens", "requests",
            "tool_uses", "status")
    out = []
    for d in details.values():
        if not d["live"]:
            item = {**d, "id": d["task_id"], "status": d["state"]}
            out.append({k: item.get(k) for k in keys})
    return sorted(out, key=lambda x: x["ended"])


def publish_demo_quota() -> None:
    """Quota sous la forme du cœur du 15/10 : `windows` en liste {name, pct, utilization,
    resetsAt}, `forecast`, et `limit_type` (plus `type`, qui écrasait le type d'évènement)."""
    now = time.time()
    bus.publish("quota", status="allowed", limit_type="five_hour", forecast="limite vers 17 h 40", windows=[
        {"name": "five_hour", "pct": 62, "utilization": 0.62, "resetsAt": now + 7200},
        {"name": "seven_day_opus", "pct": 35, "utilization": 0.35, "resetsAt": now + 400000},
        {"name": "seven_day_sonnet", "pct": 12, "utilization": 0.12, "resetsAt": now + 400000}])


# 21/10 (plan P20) : mémoire du PC, forme de `memoire.view` (évènement `memory`) et de `memoire.top`.
DEMO_MEMORY = {
    "ok": {"ram_used_go": 9.4, "ram_dispo_go": 5.9, "swap_pct": 62},
    "attention": {"ram_used_go": 13.6, "ram_dispo_go": 1.7, "swap_pct": 81},
    "blocage": {"ram_used_go": 14.6, "ram_dispo_go": 0.7, "swap_pct": 93},
}
DEMO_HOSTILE_LABEL = "<img src=x onerror=alert(1)>"  # hors inventaire i18n : ce n'est pas un texte d'écran
DEMO_MEMORY_TOP = [
    {"label": "LibreOffice", "n": 15, "go": 3.2, "swap_go": 0.4},
    {"label": "Navigateur Chrome", "n": 22, "go": 2.6, "swap_go": 0.9},
    {"label": "Lecteur de musique", "n": 10, "go": 1.1, "swap_go": 0.0},
    {"label": "Astrel", "n": 12, "go": 0.9, "swap_go": 0.6},
    {"label": "HUD d'Astrel", "n": 9, "go": 0.5, "swap_go": 0.2},
    {"label": "Visionneuse de photos", "n": 1, "go": 0.4, "swap_go": 0.0},
    # libellé hostile : affiché en texte seulement (jamais interprété)
    {"label": DEMO_HOSTILE_LABEL, "n": 1, "go": 0.1, "swap_go": 0.0},
]


# 26/10 (plan P33 lot 3) : forme de `courrier.etat` (évènement `courrier`) ; un extrait hostile,
# affiché en texte seulement.
def _demo_mission(etat: str) -> dict:
    """02/11 (plan P43) : mission dans un état précis (vérifiée, acceptée sans preuve, vérification
    échouée, en attente de décision), avec une déclaration HOSTILE affichée en texte."""
    now = time.time()
    racine = "/home/user/Projets/Demo"
    critere = {"version": 1, "figer": [f"{racine}/tests/test_calcul.py"], "controles": [
        {"type": "fichier_existe", "chemin": f"{racine}/docs/RAPPORT.md", "genre": "fichier"},
        {"type": "fichier_contient", "chemin": f"{racine}/README.md", "texte": "Tous les tests passent"},
        {"type": "git_head", "depot": racine, "branche": "main", "avance": True}]}
    ok = {"verifiee": True, "accepted": False, "failed": False, "decide": False}.get(etat, False)
    controles = [
        {"n": 1, "type": "fichier_existe", "cible": f"{racine}/docs/RAPPORT.md", "etat": "ok", "code": "existe",
         "infos": {"taille": 1204}},
        {"n": 2, "type": "fichier_contient", "cible": f"{racine}/README.md", "etat": "ok" if ok else "echec",
         "code": "texte_trouve" if ok else "texte_manquant", "infos": {}},
        {"n": 3, "type": "git_head", "cible": racine, "etat": "ok" if ok else "bloque" if etat == "decide" else "echec",
         "code": "head" if ok else "lien" if etat == "decide" else "pas_de_commit",
         "infos": {"head": "a1b2c3d4e5f6", "branche": "main"}},
        {"type": "figer", "cible": f"{racine}/tests/test_calcul.py", "etat": "ok", "code": "fige", "infos": {"sha": "9f8e7d6c5b4a"}}]
    statut = {"verifiee": "verifiee", "failed": "echec", "decide": "bloquee"}.get(etat)
    preuve = None if etat == "accepted" else {"verification_id": "m-demo:7", "at": now - 30, "statut": statut,
                                               "verifie": ok, "controles": controles}
    status = {"verifiee": "done", "accepted": "accepted_unverified", "failed": "verification_failed"}.get(etat, "active")
    # 04/11 : `id` d'une mission de démo, inconnu de la vraie Mission : son × essuie un refus (repli du HUD).
    return {"id": "m-demo", "status": status, "phase": "verification_blocked" if etat == "decide" else "travail",
            "objective": "Écrire le rapport de tests de Démo", "done_when": "le rapport existe et les tests passent",
            "critere": None if etat == "accepted" else critere, "origine": "claude" if etat == "decide" else "utilisateur",
            "approuve": False, "lancee_par": "astrel" if etat == "decide" else "utilisateur",
            "declaration": {"texte": "<img src=x onerror=alert(1)> tout est fait", "tour": 4, "at": now - 40},
            "preuve": preuve, "scope": [racine], "forbidden_paths": [], "turns": 4, "max_turns": 20,
            "started_at": now - 600, "ended_at": 0.0 if status == "active" else now - 20, "max_minutes": 60,
            "tokens": 420_000, "max_tokens": 3_000_000, "in_turn": False,
            "reason": {"accepted": "acceptée par l'utilisateur, sans preuve automatique",
                       "decide": "vérification bloquée : à toi de décider"}.get(etat, "")}


DEMO_COURRIER = [
    {"mission": "p2", "total": 4, "questions": 1, "dernier_at": time.time() - 60,
     "non_lus": [{"a": "planificateur", "n": 2}],
     "derniers": [
         {"n": 4, "de": "chercheur", "a": "planificateur", "kind": "info", "re": 0, "extrait": DEMO_HOSTILE_LABEL,
          "at": time.time() - 60},
         {"n": 3, "de": "chercheur", "a": "planificateur", "kind": "question", "re": 0,
          "extrait": "q?", "at": time.time() - 120},
         {"n": 2, "de": "planificateur", "a": "chercheur", "kind": "reponse", "re": 1, "extrait": "ok",
          "at": time.time() - 600}]},
    {"mission": "p1", "total": 1, "questions": 0, "dernier_at": time.time() - 86400 * 2, "non_lus": [],
     "derniers": [{"n": 1, "de": "auditeur", "a": "chercheur", "kind": "info", "re": 0, "extrait": "x",
                   "at": time.time() - 86400 * 2}]},
]


def publish_demo_memory(state: str = "ok", active: bool = False) -> dict:
    v = {"state": state, "ram_total_go": 15.3, "swap_total_go": 8.0, "active": active,
         **DEMO_MEMORY.get(state, DEMO_MEMORY["ok"])}
    bus.publish("memory", **v)
    return v


def _word_ok(cmd: dict, word: str) -> bool:
    """Mot de confirmation tapé : le français ou sa traduction (« lever » / « lift »), comme le cœur."""
    from astrel import i18n
    typed = str(cmd.get("confirm") or "").strip().lower()
    return typed in (word.lower(), i18n.tr(word, "en").lower())


# 23/10 (plan P28) : effort de réflexion. Niveaux et libellés de astrel/settings.py (`EFFORTS`) ; effort
# des agents par métier lu dans config/agents/*.md (lecture seule), choix de la démo en mémoire.
DEMO_EFFORTS = (("", "Réglage du CLI"), ("low", "Faible"), ("medium", "Moyen"), ("high", "Élevé"),
                ("xhigh", "Très élevé"), ("max", "Maximal"))
DEMO_EFFORT_LEVELS = [v for v, _ in DEMO_EFFORTS if v]
demo_agent_effort: dict[str, str] = {}  # choix faits dans la fiche d'agent de la démo


def publish_demo_effort(active: str, model: str) -> None:
    """Évènement `effort` sous la forme du cœur (`Astrel._publish_effort`)."""
    bus.publish("effort", active=active, levels=list(DEMO_EFFORT_LEVELS),
                reason="haiku" if "haiku" in model else "", locked=False, lock_reason="")


def demo_effort_info(kind: str, launched: str) -> dict:
    """Champs d'effort de `task_detail` (forme de `detail_agent._effort`)."""
    defs = agents_mod.load_agents(efforts=False)
    meta = defs.get(kind)
    if meta is None:
        return {"effort": "", "effort_next": "", "effort_default": "", "effort_settable": False, "applique": False,
                "effort_haiku": False, "effort_levels": list(DEMO_EFFORT_LEVELS)}
    default = str(meta.get("effort") or "")
    nxt = demo_agent_effort.get(kind, default)
    return {"effort": launched, "effort_next": nxt, "effort_default": default, "effort_settable": True,
            "applique": True, "effort_haiku": "haiku" in str(meta.get("model") or ""),
            "effort_levels": list(DEMO_EFFORT_LEVELS)}


class DemoSettings:
    """Page « Réglages » de la démo (15/10, docs/CONTRAT-REGLAGES-2026-10-15.md) : état
    EN MÉMOIRE seulement, mêmes évènements et commandes que le cœur (settings_get,
    settings_set, settings_apply ; erreur de validation → `notice` avec `setting_error`)."""

    def __init__(self) -> None:
        def it(key, label, help_, kind, value, applies="hot", **extra):
            return {"key": key, "label": label, "help": help_, "kind": kind, "value": value, "default": value,
                    "applies": applies, "pending": False, "locked": False, "lock_reason": "", **extra}

        connectors = {"google_calendar": False, "figma": False, "canva": False, "claude_docs": False,
                      "gmail": True, "google_drive": True, "dropbox": True}
        conn_labels = {"google_calendar": "Google Calendar", "figma": "Figma", "canva": "Canva",
                       "claude_docs": "Claude Docs", "gmail": "Gmail", "google_drive": "Google Drive", "dropbox": "Dropbox"}
        skills = {"deep-research": "on", "dataviz": "name-only", "anthropic-skills:docx": "on",
                  "anthropic-skills:xlsx": "name-only", "anthropic-skills:pdf": "on", "anthropic-skills:pptx": "off",
                  "founder-plan": "off"}
        quota = it("quota_alert_pct", "Alerte de quota", "Prévenir quand une fenêtre du quota dépasse ce pourcentage.",
                   "int", 80, min=10, max=100, step=5, unit="%")
        quota.update(locked=True, lock_reason="Imposé par la variable ASTREL_QUOTA_ALERT_PCT.")
        self.groups = [
            {"id": "navigateurs", "title": "Navigateurs", "items": [
                it("browser.main_visible", "Navigateur principal visible",
                   "Fenêtre Playwright à l'écran (sinon sans affichage).", "bool", True, "mcp"),
                it("browser.extra_visible", "Navigateurs des agents visibles",
                   "Les navigateurs 2, 3 et 4 ouvrent une fenêtre à l'écran.", "bool", False, "mcp"),
                it("browser.extra_count", "Navigateurs d'agents", "Nombre de navigateurs en plus du principal.",
                   "int", 3, "mcp", min=0, max=4, step=1, unit="")]},
            {"id": "connecteurs", "title": "Connecteurs claude.ai", "items": [
                it("connectors", "Connecteurs actifs", "Coché = utilisable ; décoché = bloqué.", "flags", connectors, "mcp",
                   labels=conn_labels,
                   # 19/10 (plan P10) : même champ que le cœur (icône locale ou pastille à lettre).
                   icons={k: icon_for(v) for k, v in conn_labels.items()})]},
            {"id": "skills", "title": "Skills", "items": [
                it("skills", "Skills", "Actif, nom seul (décrit en une ligne) ou coupé.", "tri", skills, "brain",
                   labels={k: k for k in skills})]},
            {"id": "plugins", "title": "Plugins", "items": [
                it("plugins", "Plugins", "Plugins de Claude Code chargés au démarrage du cerveau.", "flags",
                   {"anthropic-skills": True, "founder": False}, "brain",
                   labels={"anthropic-skills": "Anthropic Skills", "founder": "Founder"})]},
            {"id": "seuils", "title": "Seuils", "items": [
                it("agent_alert_tokens", "Alerte agent (tokens)", "Un agent de fond qui dépasse ce total est signalé.",
                   "int", 50_000_000, min=1_000_000, max=1_000_000_000, step=1_000_000, unit="tokens"),
                it("agent_alert_s", "Alerte agent (durée)", "Un agent de fond qui tourne plus longtemps est signalé.",
                   "int", 1800, min=60, max=86_400, step=60, unit="s"),
                it("session_idle_reset_s", "Nouvelle session après", "Silence au-delà duquel la conversation repart à zéro.",
                   "int", 3600, min=300, max=86_400, step=300, unit="s"),
                it("context_relief_tokens", "Allègement du contexte", "Taille du contexte qui déclenche un résumé.",
                   "int", 150_000, min=50_000, max=900_000, step=10_000, unit="tokens"),
                quota]},
            {"id": "cerveau", "title": "Cerveau", "items": [
                # 23/10 (plan P28) : appliqué à chaud, aussi par le sélecteur à côté du modèle (évènement `effort`)
                it("brain_effort", "Effort de réflexion", "Plus d'effort : réponses plus soignées, plus lentes.",
                   "choice", "high", choices=[{"value": v, "label": lbl} for v, lbl in DEMO_EFFORTS]),
                # 15/10 : mode plan (même réglage que astrel/settings.py ; la page « Plans » le bascule aussi)
                it("plan_mode", "Mode plan", "Manuel : un plan de chantier attend ta validation (page « Plans ») "
                   "avant tout codeur ; automatique : validé dès qu'il est déposé.", "choice", "manual",
                   choices=[{"value": "manual", "label": "Manuel"}, {"value": "auto", "label": "Automatique"}])]},
        ]
        # 17/10 (plan P4, lot 1) : genres `info`/`action`, groupe ouvert d'office, commande settings_action.
        self.logged_in = True
        self.groups.insert(0, {"id": "compte", "title": "Compte Claude", "open": True, "items": [
            it("compte", "Compte Claude", "L'abonnement Claude de cette machine ; Astrel ne voit ni ne garde aucun jeton.",
               "info", None)]})
        self.groups.append({"id": "diagnostic", "title": "Diagnostic", "items": [
            it("diagnostic", "Diagnostic", "Rapport sans donnée personnelle, à coller quand tu demandes de l'aide.",
               "action", None, actions=[{"id": "run", "label": "Lancer le diagnostic"}]),
            it("effacer", "Effacer l'historique", "Efface l'index des conversations d'Astrel.", "action", None,
               actions=[{"id": "historique", "label": "Effacer l'historique", "danger": True, "word": "EFFACER",
                         "confirm": "L'historique des conversations sera effacé."},
                        {"id": "grise", "label": "Mettre à jour", "disabled": True,
                         "reason": "dépôt de développement : mise à jour à la main"}])]})
        self._compte()
        # 18/10 (plan P5) : aide « ? » vers le guide, micro et essais, WhatsApp relié depuis le HUD.
        self._item("compte")["guide"] = "claude"
        self._item("plan_mode")["guide"] = "mode-plan"
        self.groups.insert(1, {"id": "voix", "title": "Voix, audio et langue", "items": [
            it("audio.mic", "Micro", "Micro qu'écoute Astrel.", "choice", "", guide="micro",
               choices=[{"value": "", "label": "Micro du système"}, {"value": "usb", "label": "Micro USB"}]),
            # 02/11 : assistant de calibrage (bouton exécuté par le HUD, séance simulée par `demo_calibrage`)
            it("audio.calibrage", "Calibrage du micro",
               "Mesure le bruit de fond puis ta voix (tu dis « Astrel » trois fois), et propose le gain et les seuils "
               "de ce micro, à côté des réglages actuels. Rien n'est changé sans « Enregistrer ».", "action",
               {"lines": [{"label": "Ce micro", "level": "warn",
                           "text": "pas encore calibré : réglages par défaut (gain ×2, VAD 0.35, réveil 0.50)"}]},
               guide="micro", actions=[{"id": "ouvrir", "label": "Calibrer ce micro", "hud": "calibrage"}]),
            it("audio.test", "Essais", "Fait parler Astrel, ou écoute le micro 3 secondes.", "action", None,
               guide="micro", actions=[{"id": "voice", "label": "Tester la voix"},
                                       {"id": "mic", "label": "Tester le micro (3 s)"}]),
            # 20/10 : langue de l'interface, même réglage que astrel/settings.py (le HUD se recharge)
            it("ui_language", "Langue de l'interface", "Langue des textes du HUD, de la bulle et du mini-chat. La voix "
               "d'Astrel et ses réponses n'en dépendent pas.", "choice", LANGUE["demo"],
               choices=[{"value": "fr", "label": "Français"}, {"value": "en", "label": "English"}])]})
        self.groups.insert(2, {"id": "whatsapp", "title": "WhatsApp", "items": [
            it("whatsapp", "WhatsApp", "Ton chat « message à soi-même » : Astrel y répond quand tu t'absentes.",
               "info", None, guide="whatsapp")]})
        self.wa = {"linked": False, "stage": "", "away": False, "gen": 0}
        self._whatsapp()
        # 18/10 (plan P7) : levée du garde-fou (même vue que `core._lift_view`, en mémoire).
        self.groups.append({"id": "garde_fou", "title": "Garde-fou", "items": [
            it("garde_fou_levee", "Levée du garde-fou pour travailler sur le code d'Astrel",
               "Levé : Claude modifie sans question le code du HUD, de la bulle, de WhatsApp et du garde-fou.",
               "action", None)]})
        self.lift = {"on": False, "since": 0.0}
        self._lift()
        # 20/10 (plan P11) : fournisseur du cerveau, clé collée en saisie MASQUÉE, jamais renvoyée.
        self.groups.insert(1, {"id": "fournisseur", "title": "Fournisseur du cerveau", "items": [
            it("fournisseur", "Fournisseur", "Qui fait tourner le cerveau : ton abonnement Claude, ta clé API Claude, "
               "ou un autre fournisseur par sa clé API, sans Claude.", "choice", "abonnement", applies="brain",
               choices=[{"value": "abonnement", "label": "Abonnement Claude (connexion du compte)"},
                        {"value": "cle_claude", "label": "Clé API Claude (facturée à l'usage)"},
                        {"value": "openai:gpt-5", "label": "OpenAI · GPT-5 (clé API, sans Claude)"}]),
            it("fournisseur.cles", "Clés et passerelle", "Clés API gardées sur cette machine seulement, jamais affichées.",
               "action", None)]})
        self.cle_openai = False
        self._cles()
        # 21/10 (plan P17) : apparence, mêmes réglages que astrel/settings.py (pastilles d'accent ;
        # le HUD l'applique sans rechargement). 25/10 (plan P35) : plus de réglage `theme`, Premium seul.
        from astrel.settings import ITEMS as _ITEMS

        def _theme_item(key):
            src = _ITEMS[key]
            return it(key, src.label, src.help, "choice", src.default(), choices=[
                {"value": v, "label": lbl} for v, lbl in src.choices], **src.extra)

        voix = next(i for i, g in enumerate(self.groups) if g["id"] == "voix")
        self.groups.insert(voix + 1, {"id": "apparence", "title": "Apparence", "items": [
            _theme_item("theme_mode"), _theme_item("theme_accent"),
            _theme_item("affichage"),  # 22/10 (plan P23) : affichage Simple / Avancé
            # 23/10 (plan P31) : nom affiché, même réglage que astrel/settings.py (validé par settings._text)
            it("assistant_name", _ITEMS["assistant_name"].label, _ITEMS["assistant_name"].help, "text",
               _ITEMS["assistant_name"].default(), max=_ITEMS["assistant_name"].max)]})
        # 25/10 (plan P36) : mot d'éveil, mêmes réglages que astrel/settings.py (ancien mot, variantes du nom)
        self.groups[voix]["items"][2:2] = [
            it("wake_legacy", _ITEMS["wake_legacy"].label, _ITEMS["wake_legacy"].help, "bool",
               bool(_ITEMS["wake_legacy"].default())),
            it("wake_names", _ITEMS["wake_names"].label, _ITEMS["wake_names"].help, "text",
               _ITEMS["wake_names"].default(), max=_ITEMS["wake_names"].max)]
        # 24/10 (plan P29 lot B) : cases de confirmation, mêmes réglages que astrel/settings.py (en mémoire).
        from astrel import limites as _lim

        self.cases = _lim.coches_defaut()
        gf = next(g for g in self.groups if g["id"] == "garde_fou")
        gf["items"][:0] = [it("confirmations", _ITEMS["confirmations"].label, _ITEMS["confirmations"].help,
                              "action", None)] + [
            it(f"confirmations.{gid}", title, _ITEMS[f"confirmations.{gid}"].help, "flags",
               {c.id: c.coche for c in _lim.cases_du_groupe(gid)}, labels={c.id: c.texte for c in _lim.cases_du_groupe(gid)})
            for gid, title in _lim.GROUPES_CASES]
        self._cases()
        # 28/10 (hud-boutons-t10) : accès retirés de la barre du HUD, mêmes lignes que astrel/settings.py.
        # Comptes rendus : le vrai module `reports` (fichier du dossier jetable) ; mission et extinction :
        # boutons exécutés par le HUD lui-même (`hud`), qui envoie `mission start` / `quit`.
        def _from_core(key, value=None, **extra):
            src = _ITEMS[key]
            if src.kind == "action":
                extra["actions"] = src.view()["actions"]
            return it(key, src.label, src.help, src.kind, value, **extra)

        # « demande » au départ (jamais le vrai runtime/reports.txt : le dossier jetable est posé plus tard)
        self.groups[voix]["items"].insert(-1, _from_core(
            "reports", "demande", choices=[{"value": v, "label": lbl} for v, lbl in _ITEMS["reports"].choices]))
        gf["items"].insert(len(gf["items"]) - 1, _from_core("mission"))
        # 04/11 (lot 2 du moteur local) : « Rien ne sort » ; la démo n'a pas de section Confidentialité
        gf["items"].append(_from_core("hors_ligne", False))
        app = next(i for i, g in enumerate(self.groups) if g["id"] == "apparence")
        self.groups.insert(app + 1, {"id": "systeme", "title": "Système", "items": [_from_core("systeme.eteindre")]})
        # 28/10 (plan P40 lot A5) : Réglages › Images, mêmes réglages et mêmes choix que astrel/settings.py
        # (comptes et plafonds : lignes du HUD d'après `ia_etat`, voir make_demo_images).
        from astrel.settings import _choices as _core_choices

        nav = next(i for i, g in enumerate(self.groups) if g["id"] == "navigateurs")
        self.groups.insert(nav + 1, {"id": "images", "title": "Images IA", "items": [
            it(k, _ITEMS[k].label, _ITEMS[k].help, "choice", _ITEMS[k].default(),
               choices=[{"value": v, "label": lbl} for v, lbl in _core_choices(_ITEMS[k])])
            for k in ("images_mode", "images_ordre", "images_manuel", "video_choix")]})
        # 05/11 (lot 6 du moteur local) : « Qui répond », même réglage que astrel/settings.py (sélecteur du HUD) ;
        # rangé dans « Cerveau » (la démo n'a pas de section Modèle local, comme pour « Rien ne sort »)
        next(g for g in self.groups if g["id"] == "cerveau")["items"].append(
            it("moteur_mode", _ITEMS["moteur_mode"].label, _ITEMS["moteur_mode"].help, "choice",
               _ITEMS["moteur_mode"].default(),
               choices=[{"value": v, "label": lbl} for v, lbl in _core_choices(_ITEMS["moteur_mode"])]))
        self.applied = {i["key"]: copy.deepcopy(i["value"]) for g in self.groups for i in g["items"]}

    def _cases(self) -> None:
        """(démo) Résumé et boutons des cases de confirmation (même vue que `core._cases_view`)."""
        from astrel import limites as _lim

        for gid, _t in _lim.GROUPES_CASES:
            item = self._item(f"confirmations.{gid}")
            if isinstance(item.get("value"), dict):
                self.cases.update(item["value"])
        item = self._item("confirmations")
        item["value"] = {"lines": [{"label": "État", "text": _lim.resume(self.cases),
                                   "level": "warn" if _lim.tout_decoche(self.cases) else "ok"}]}
        item["actions"] = [
            {"id": "tout_decocher", "label": "Tout décocher", "danger": True,
             "confirm": "Plus aucune carte de confirmation : Claude agira sans te demander, garde-fou compris. "
                        "« Stop tout » arrête toujours tout."},
            {"id": "tout_cocher", "label": "Tout cocher"}, {"id": "defauts", "label": "Valeurs par défaut"}]

    def cases_action(self, action: str) -> dict:
        from astrel import limites as _lim

        self.cases = {"tout_decocher": _lim.coches_toutes(False), "tout_cocher": _lim.coches_toutes(True),
                      "defauts": _lim.coches_defaut()}[action]
        for gid, _t in _lim.GROUPES_CASES:
            self._item(f"confirmations.{gid}")["value"] = {c.id: self.cases[c.id] for c in _lim.cases_du_groupe(gid)}
        self._cases()
        self.publish()
        return {"ok": True, "text": _lim.resume(self.cases)}

    def _lift(self) -> None:
        item = self._item("garde_fou_levee")
        if self.lift["on"]:
            since = time.strftime("%H:%M", time.localtime(self.lift["since"]))
            item["value"] = {"lines": [{"label": "État", "text": f"LEVÉ depuis {since}", "level": "warn"}]}
            item["actions"] = [{"id": "retablir", "label": "Rétablir le garde-fou"}]
        else:
            item["value"] = {"lines": [{"label": "État", "text": "en place", "level": "ok"}]}
            item["actions"] = [{"id": "lever", "label": "Lever le garde-fou", "danger": True, "word": "lever",
                                "confirm": "Claude pourra réécrire le HUD, la bulle, WhatsApp et le garde-fou."}]

    def lift_action(self, action: str, cmd: dict) -> dict:
        if action == "lever":
            if not _word_ok(cmd, "lever"):
                return {"ok": False, "error": "rien n'est fait : il faut taper « lever » pour confirmer."}
            self.lift.update(on=True, since=time.time())
        else:
            self.lift.update(on=False, since=0.0)
        bus.publish("levee", on=self.lift["on"], since=self.lift["since"],
                    changed=["hud/src/main.js", "astrel/guard.py"] if self.lift["on"] else [])
        self._lift()
        self.publish()
        return {"ok": True, "text": "Garde-fou levé." if self.lift["on"] else "Garde-fou rétabli."}

    def _whatsapp(self) -> None:
        """Même vue que `core._whatsapp_view` (états : non relié, appairage, relié)."""
        item, wa = self._item("whatsapp"), self.wa
        lines = [{"label": "Liaison", "text": "reliée à ton téléphone" if wa["linked"] else "non reliée",
                  "level": "ok" if wa["linked"] else "warn"}]
        item["value"] = {"lines": lines}
        if wa["stage"]:
            lines.append({"label": "Appairage", "level": "info", "text": {
                "qr": "scanne le QR ci-dessous", "code": "tape le code ci-dessous sur ton téléphone",
                "linked": "compte détecté : vérifie le numéro ci-dessous"}[wa["stage"]]})
            item["value"]["link_box"] = True
            item["actions"] = ([{"id": "link_confirm", "label": "Oui, c'est mon numéro"},
                                {"id": "link_cancel", "label": "Non, annuler", "danger": True}]
                               if wa["stage"] == "linked" else [{"id": "link_cancel", "label": "Annuler l'appairage"}])
        elif not wa["linked"]:
            item["actions"] = [{"id": "link", "label": "Relier avec un QR"},
                               {"id": "link_phone", "label": "Relier avec un code",
                                "input": {"label": "Ton numéro WhatsApp, indicatif compris (ex. 33612345678)",
                                          "type": "tel"}}]
        else:
            lines.append({"label": "Pont", "text": "connecté", "level": "ok"})
            item["actions"] = [{"id": "absent", "label": "Je m'absente"},
                               {"id": "unlink", "label": "Délier WhatsApp", "danger": True, "word": "délier",
                                "confirm": "L'appareil « Astrel » sera retiré de ton téléphone."}]

    def wa_qr(self, gen: int, n: int) -> None:
        """QR renouvelé toutes les 4 s tant que l'appairage attend (comme WhatsApp, ~20 s)."""
        if self.wa["gen"] != gen or self.wa["stage"] != "qr":
            return
        bus.publish("whatsapp_link", state="qr", n=n, qr=f"2@DEMO-QR-{gen}-{n}-" + "x" * 120)
        t = threading.Timer(4.0, self.wa_qr, (gen, n + 1))
        t.daemon = True
        t.start()

    def wa_scan(self) -> dict:
        if self.wa["stage"] not in ("qr", "code"):
            return {"ok": False, "error": "aucun appairage en cours"}
        self.wa["stage"] = "linked"
        bus.publish("whatsapp_link", state="linked", number="+33600000000", name="Ami")
        self._whatsapp()
        return self.publish()

    def wa_action(self, action: str, cmd: dict) -> dict:
        wa = self.wa
        if action in ("link", "link_phone"):
            wa["gen"] += 1
            if action == "link_phone":
                wa["stage"] = "code"
                bus.publish("whatsapp_link", state="code", code="DEMO1234")
            else:
                wa["stage"] = "qr"
                self.wa_qr(wa["gen"], 1)
            result = {"ok": True, "level": "info", "text": "(démo) Le QR arrive." if action == "link" else "(démo) Le code arrive."}
        elif action == "link_confirm":
            wa.update(stage="", linked=True)
            bus.publish("whatsapp_link", state="done", number="+33600000000")
            bus.clear_sticky("whatsapp_link")
            result = {"ok": True, "level": "ok", "text": "Numéro confirmé."}
        elif action == "link_cancel":
            wa["stage"] = ""
            bus.publish("whatsapp_link", state="failed", reason="appairage annulé")
            bus.clear_sticky("whatsapp_link")
            result = {"ok": True, "level": "info", "text": "Appairage annulé."}
        elif action == "unlink":
            if not _word_ok(cmd, "délier"):
                return {"ok": False, "error": "rien n'est fait : il faut taper « délier » pour confirmer."}
            wa.update(linked=False, stage="")
            result = {"ok": True, "level": "ok", "text": "(démo) WhatsApp délié."}
        elif action == "absent":
            result = {"ok": True, "level": "ok", "text": "(démo) Absent : la suite part sur WhatsApp."}
        else:
            return {"ok": False, "error": f"action inconnue : whatsapp/{action}"}
        self._whatsapp()
        self.publish()
        return result

    def _compte(self) -> None:
        item = self._item("compte")
        if self.logged_in:
            item["value"] = {"lines": [{"label": "État", "text": "connecté", "level": "ok"},
                                       {"label": "Abonnement", "text": "Max", "level": "info"},
                                       {"label": "E-mail", "text": "ami@exemple.fr <b>pas du html</b>", "level": "info"}]}
            item["actions"] = [{"id": "logout", "label": "Se déconnecter", "danger": True,
                                "confirm": "Astrel ne pourra plus répondre tant que tu ne te reconnectes pas."}]
        else:
            item["value"] = {"lines": [{"label": "État", "text": "non connecté", "level": "warn"}],
                             "link": {"label": "Page de connexion (si le navigateur ne s'ouvre pas)",
                                      "url": "https://claude.ai/oauth/authorize?demo=1"}}
            item["actions"] = [{"id": "login", "label": "Se connecter"}]

    def action(self, key: str, action: str, cmd: dict) -> dict:
        item = self._item(key)
        if item is None or action not in [a.get("id") for a in item.get("actions") or []]:
            return {"ok": False, "error": "action indisponible pour l'instant (la page s'est peut-être mise à jour)."}
        if key == "fournisseur.cles":  # (démo) la valeur n'est ni gardée ni renvoyée
            self.cle_openai = action == "enregistrer_openai" and bool(str(cmd.get("value") or "").strip())
            self._cles()
            self.publish()
            return {"ok": True, "level": "ok", "text": "(démo) Clé OpenAI enregistrée." if self.cle_openai
                    else "(démo) Clé OpenAI effacée."}
        if key == "whatsapp":
            return self.wa_action(action, cmd)
        if key == "garde_fou_levee":
            return self.lift_action(action, cmd)
        if key == "confirmations":  # 24/10 (plan P29 lot B)
            return self.cases_action(action)
        if key == "audio.test":
            return {"ok": True, "level": "ok", "text": "(démo) Le micro entend bien : du son est arrivé."
                    if action == "mic" else "(démo) Voici ma voix."}
        if key == "compte" and action in ("login", "logout"):
            self.logged_in = action == "login"
            self._compte()
            self.publish()
            return {"ok": True, "text": "(démo) Connecté." if self.logged_in else "(démo) Déconnecté."}
        if key == "diagnostic" and action == "run":
            return {"ok": True, "text": "(démo) Rapport prêt.", "level": "ok",
                    "block": "===== Rapport Astrel (à coller) =====\n[OK] Claude Code : 2.1.290\n"
                             "[ALERTE] micro : aucun micro vu <script>x</script>\n====="}
        if key == "effacer" and action == "historique":
            if not _word_ok(cmd, "EFFACER"):
                return {"ok": False, "error": "Mot de confirmation absent : rien n'est effacé."}
            return {"ok": True, "text": "(démo) Historique effacé (instantané pris avant).", "level": "ok"}
        return {"ok": False, "error": f"action inconnue : {key}/{action}"}

    def _cles(self) -> None:
        item = self._item("fournisseur.cles")
        has = self.cle_openai
        item["value"] = {"lines": [{"label": "Cerveau", "text": "Abonnement Claude", "level": "ok"},
                                   {"label": "Clé OpenAI", "text": "enregistrée sur cette machine" if has else "aucune",
                                    "level": "ok" if has else "info"}]}
        item["actions"] = [{"id": "enregistrer_openai", "label": f"{'Changer' if has else 'Enregistrer'} la clé OpenAI",
                            "input": {"label": "Clé API OpenAI", "secret": True},
                            "confirm": "Colle la clé : elle reste sur cette machine, jamais affichée."}]
        if has:
            item["actions"].append({"id": "effacer_openai", "label": "Effacer la clé OpenAI", "danger": True,
                                    "confirm": "La clé OpenAI sera effacée de cette machine."})

    def _item(self, key: str) -> dict | None:
        return next((i for g in self.groups for i in g["items"] if i["key"] == key), None)

    def publish(self, first_run: bool = False) -> dict:
        restart = any(i["pending"] for g in self.groups for i in g["items"])
        bus.publish("settings", restart_needed=restart, groups=copy.deepcopy(self.groups), first_run=first_run)
        return {"ok": True}

    @staticmethod
    def _invalid(item: dict, value) -> str:
        kind = item["kind"]
        if kind == "bool":
            return "" if isinstance(value, bool) else "oui ou non attendu"
        if kind in ("int", "float"):
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                    or (kind == "int" and value != int(value))):
                return "un nombre entier attendu" if kind == "int" else "un nombre attendu"
            if not item["min"] <= value <= item["max"]:
                return f"un nombre entre {item['min']} et {item['max']}"
            return ""
        if kind == "choice":
            return "" if value in [c["value"] for c in item["choices"]] else "choix inconnu"
        if kind == "text":  # 23/10 (plan P31) : même validation que le cœur
            from astrel.settings import ITEMS, SettingError, _text
            try:
                _text(ITEMS[item["key"]], value)
            except SettingError as exc:
                return exc.message.rstrip(".")
            return ""
        allowed = (True, False) if kind == "flags" else ("on", "name-only", "off")
        if not isinstance(value, dict) or set(value) != set(item["value"]) or any(
                v not in allowed or (kind == "flags" and not isinstance(v, bool)) for v in value.values()):
            return "valeur complète attendue"
        return ""

    def set(self, key: str, value) -> dict:
        item = self._item(key)
        message = ("réglage inconnu" if item is None
                   else "imposé par une variable d'environnement" if item["locked"]
                   else self._invalid(item, value))
        if message:
            text = f"Réglage refusé ({key}) : {message}."
            bus.publish("notice", level="warning", text=text, setting_error={"key": key, "message": text})
            self.publish()
            return {"ok": False, "error": text}
        if key == "hors_ligne" and value is True:
            # 04/11 : la démo a toujours un agent de fond qui tourne → refus avec « Tout arrêter » proposé.
            # 05/11 : la réponse du VRAI `_settings_command` (raison, notice `setting_error`, `stop_tout`, page),
            # par `refus_reglage` : avant, la démo donnait `stop_tout` et le cœur le perdait, le contrôle ne voyait rien.
            from astrel.coeur.reglages import refus_reglage
            from astrel.settings import SettingError

            res = self.hors_ligne_entrer()
            refus = refus_reglage(SettingError(key, str(res.get("error") or "refusé")), res)
            self.publish()  # la page suit par l'évènement `settings` (la démo ne la met pas dans la réponse)
            return refus
        if item["kind"] == "int":
            value = int(value)
        if item["kind"] == "text":
            from astrel.settings import ITEMS, _text
            value = _text(ITEMS[key], value)  # espaces resserrés, vide = défaut
        if key == "reports":  # 28/10 (hud-boutons-t10) : le vrai module, comme `settings._reports_put`
            reports.set_mode(str(value))
        item["value"] = value
        item["pending"] = item["applies"] != "hot" and value != self.applied[key]
        if item["applies"] == "hot":
            self.applied[key] = copy.deepcopy(value)
        if key.startswith("confirmations."):  # 24/10 (plan P29 lot B) : le résumé suit
            self._cases()
        return self.publish()

    @staticmethod
    def hors_ligne_entrer() -> dict:
        """05/11 : le VRAI `_hors_ligne_entrer` du cœur, qui refuse avant de rien toucher : la démo a toujours un
        agent de fond qui tourne (même raison que `_hors_ligne_occupe`)."""
        from astrel.coeur.hors_ligne import HorsLigneCoeur

        occupe = types.SimpleNamespace(_hors_ligne_occupe=lambda: "une tâche de fond de Claude tourne")
        return HorsLigneCoeur._hors_ligne_entrer(occupe, "HUD")

    def apply(self) -> dict:
        for g in self.groups:
            for i in g["items"]:
                if i["pending"]:
                    self.applied[i["key"]] = copy.deepcopy(i["value"])
                    i["pending"] = False
        bus.publish("notice", level="info", text="(démo) Réglages appliqués : le cerveau a redémarré.")
        return self.publish()


# Plans de la démo (15/10, docs/CONTRAT-PLANS-2026-10-15.md). Le VRAI module du cœur
# (`astrel.plans.Plans`) : mêmes évènements `plans`/`plan`, mêmes refus (version périmée,
# révision sans remarque…). Fichiers dans le dossier jetable de la démo, jamais runtime/plans.
DEMO_PLAN_BODY = """## Étapes
1. Demander trois devis de déménageurs et comparer les prix.
2. Prévenir la banque et l'assurance avec la lettre `changement-adresse.pdf`.
3. Réserver le monte-meubles pour le samedi 21, de 8 h à midi.

## Fichiers
- `devis-demenageurs.pdf`, `inventaire.pdf`
- `bail-nouvel-appartement.pdf`, `etat-des-lieux.pdf`

## Risques
- Un déménageur peut ne pas répondre avant la fin du mois :
  garder une location de camion en réserve.
- Textes du serveur affichés sans HTML : <img src=x onerror=alert(1)> reste du texte.

## Lots
- Lot unique : tout se fait dans la même semaine.

## Vérification
- **Liste des cartons** relue, puis `état des lieux` signé."""

DEMO_PLAN_REVISED = DEMO_PLAN_BODY.replace(
    "\n\n## Lots", "\n- Une version révisée renumérote les points : les remarques d'avant vont au fil général.\n\n## Lots")


class DemoPropositions:
    """04/11 (T49) : propositions de mémoire de la démo (`propositions_get`, `proposition_appliquer`,
    `proposition_refuser`), comme le cœur ; l'une cite une balise, qui doit rester du texte."""

    def __init__(self) -> None:
        self.items = [
            {"proposition_id": "2026-11-03-utilisateur-contraintes-nouvelle", "date": "2026-11-03 21:40",
             "type": "nouvelle", "statut": "en attente", "note": "utilisateur-contraintes-pour-choisir-un-projet.md",
             "renvois": [], "raison": "Contraintes durables dites en conversation, sans note dédiée.",
             "texte": "---\nname: utilisateur-contraintes\ndescription: critères de choix d'un projet\n---\n\n"
                      "# l'utilisateur contraintes pour choisir un projet\n\nUn projet doit tenir en 3 mois <img src=x>.",
             "empreinte": "a1b2c3d4e5f60718", "traitee": None},
            {"proposition_id": "2026-10-26-procedure-note-chantiers-consolidation", "date": "2026-10-26 18:02",
             "type": "consolidation", "statut": "en attente", "note": "procedures/procedure-note-chantiers.md",
             "renvois": [], "raison": "Deux ajouts empilés disent la même chose.",
             "texte": "# Procédure note chantiers\n\nQuand l'utiliser : avant de modifier la note CHANTIERS.",
             "empreinte": "0f1e2d3c4b5a6978", "traitee": None},
        ]

    def publish(self) -> None:
        bus.publish("propositions", en_attente=sum(it["statut"] == "en attente" for it in self.items))

    def command(self, name: str, cmd: dict) -> dict:
        if name == "propositions_get":
            toutes = cmd.get("toutes") is True
            return {"ok": True, "propositions": [it for it in self.items if toutes or it["statut"] == "en attente"]}
        it = next((x for x in self.items if x["proposition_id"] == cmd.get("proposition_id")), None)
        if not it:
            return {"ok": False, "error": "proposition inconnue"}
        if it["statut"] != "en attente":
            return {"ok": False, "error": f"proposition déjà traitée ({it['statut']})"}
        if name == "proposition_appliquer" and cmd.get("empreinte") != it["empreinte"]:
            return {"ok": False, "error": "la proposition a changé depuis son affichage : relis-la"}
        quand = time.strftime("%Y-%m-%d %H:%M")
        if name == "proposition_appliquer":
            it.update(statut="appliquée", traitee={"le": quand, "par": "utilisateur"})
        else:
            it.update(statut="refusée", traitee={"le": quand, "par": "utilisateur", "raison": str(cmd.get("raison") or "")})
        self.publish()
        return {"ok": True}


demo_propositions = DemoPropositions()


class DemoPropositionsLocal:
    """05/11 (lot 5 du moteur local) : propositions de modification de Qwen (`propositions_local_get`,
    `proposition_local_appliquer`, `proposition_local_refuser`) ; une applicable, une dont le fichier a changé ;
    le diff cite une balise, qui doit rester du texte."""

    def __init__(self) -> None:
        self.items = [
            {"proposition_id": "L20261105-101500-a1b2c3", "date": "2026-11-05 10:15", "statut": "en attente",
             "fichier": "/home/user/Projets/Demo/README.md", "canal": "general", "raison": "Faute de frappe dans le titre.",
             "nouveau": False, "etat_fichier": "identique", "plus": 1, "moins": 1, "empreinte": "9f8e7d6c5b4a3921",
             "diff": ["--- README.md", "+++ README.md", "@@ -1,3 +1,3 @@", "-# Projet de démo <img src=x>",
                      "+# Projet de démonstration <img src=x>", " ", " Une ligne inchangée."], "traitee": None},
            {"proposition_id": "L20261105-094000-d4e5f6", "date": "2026-11-05 09:40", "statut": "en attente",
             "fichier": "/home/user/Projets/Demo/config.toml", "canal": "general", "raison": "Délai plus long.",
             "nouveau": False, "etat_fichier": "modifié", "plus": 1, "moins": 1, "empreinte": "1122334455667788",
             "diff": ["--- config.toml", "+++ config.toml", "@@ -2 +2 @@", "-delai = 10", "+delai = 30"],
             "traitee": None},
        ]

    def publish(self) -> None:
        bus.publish("propositions_local", en_attente=sum(it["statut"] == "en attente" for it in self.items))

    def command(self, name: str, cmd: dict) -> dict:
        if name == "propositions_local_get":
            toutes = cmd.get("toutes") is True
            return {"ok": True, "propositions": [it for it in self.items if toutes or it["statut"] == "en attente"]}
        it = next((x for x in self.items if x["proposition_id"] == cmd.get("proposition_id")), None)
        if not it or it["statut"] != "en attente":
            return {"ok": False, "error": "proposition inconnue"}
        quand = time.strftime("%Y-%m-%d %H:%M")
        if name == "proposition_local_appliquer":
            if cmd.get("empreinte") != it["empreinte"]:
                return {"ok": False, "error": "la proposition a changé depuis son affichage : relis-la"}
            if it["etat_fichier"] != "identique":
                return {"ok": False, "error": "le fichier a changé depuis la proposition : elle n'est pas appliquée "
                                              "(demande-en une nouvelle)"}
            it.update(statut="appliquée", traitee={"le": quand, "par": "utilisateur", "instantane": True})
        else:
            it.update(statut="refusée", traitee={"le": quand, "par": "utilisateur", "raison": str(cmd.get("raison") or "")})
        self.publish()
        return {"ok": True, "fichier": it["fichier"]}


demo_propositions_local = DemoPropositionsLocal()


def demo_audit(cmd: dict) -> dict:
    """`audit_get` de la démo (plan P3, lot F) : 75 entrées fictives, filtrées et paginées
    comme le cœur (`astrel/audit.py`) ; l'une cite une balise, qui doit rester du texte."""
    now = time.time()
    kinds = ("confirmation", "refus", "whatsapp", "plan", "total", "agent")
    items = []
    for i in range(75):
        kind = kinds[i % len(kinds)]
        items.append({"ts": now - 60 * i, "kind": kind, "who": "claude" if kind != "plan" else "utilisateur",
                      "what": f"entrée {i} : " + ("Gmail : <img src=x onerror=alert(1)> | envoi d'un e-mail" if i == 0
                                                  else kind),
                      "auth": {"confirmation": "voix (oui pour 30 min)", "refus": "réservé à l'utilisateur"}.get(kind, ""),
                      **({"ok": kind != "refus"} if kind in ("confirmation", "refus", "total") else {})})
    want = cmd.get("kinds")
    if isinstance(want, list) and want:
        items = [e for e in items if e["kind"] in want]
    before = cmd.get("before")
    if isinstance(before, (int, float)) and not isinstance(before, bool):
        items = [e for e in items if e["ts"] < before]
    limit = cmd.get("limit") if isinstance(cmd.get("limit"), int) else 50
    return {"ok": True, "items": items[:limit], "kinds": list(kinds), "more": len(items) > limit}


def make_demo_plans(settings: DemoSettings):
    from astrel.plans import Plans

    class DemoPlans(Plans):
        """Plans de la démo : le mode vient des réglages de la démo, Astrel est simulé
        (réponse dans le fil, nouvelle version après une révision)."""

        offline = False  # tests du HUD : plans_get/plan_get refusés (état d'erreur de la page)

        def _mode(self) -> str:  # type: ignore[override]
            item = settings._item("plan_mode")
            return str(item["value"]) if item else "manual"

        def seed(self) -> None:
            p1 = self.submit("Organiser le déménagement", DEMO_PLAN_BODY,
                             "Aide-moi à organiser le déménagement, étape par étape.", channel=channels.default)["id"]
            self.comment(p1, "p2", "Pense aussi à prévenir la mutuelle.")
            # émojis comme Claude en met parfois : retirés à l'affichage par le HUD
            self.reply(p1, "J'ai tout mis dans **un seul lot** : la même semaine suffit ✅.")
            p2 = self.submit("Sauvegarde des photos de famille", "## Étapes\n1. Copier les photos sur le disque externe.\n"
                             "2. Garder une copie en ligne.\n\n## Vérification\n- Ouvrir une photo au hasard.",
                             channel=channels.default)["id"]
            self.validate(p2, 1, by="auto")
            p3 = self.submit("Refaire toute la cuisine 🚀", "## Étapes\n1. Changer tous les meubles.\n2. Repeindre les murs.\n\n"
                             "## Risques\n- Trois semaines de travaux au moins.", channel=channels.default)["id"]
            self.reject(p3, "Trop gros pour ce mois-ci : on garde la cuisine actuelle.")

        def new_plan(self) -> dict:
            r = self.submit("Anniversaire de Léa", "## Étapes\n1. Réserver la salle.\n2. Envoyer les invitations.\n\n"
                            "## Fichiers\n- `liste-invites.md`\n\n## Vérification\n- Confirmer le nombre d'invités.",
                            "Organise l'anniversaire de Léa, samedi en quinze.", channel=channels.default)
            if r.get("ok") and self._mode() == "auto":
                self.validate(r["id"], r["version"], by="auto")
            return r

        def command(self, name: str, cmd: dict) -> dict:
            if self.offline:
                return {"ok": False, "error": "(démo) cœur injoignable"}
            pid = str(cmd.get("plan_id") or "").strip()
            if name == "plans_get":
                return {"ok": True, **self.publish()}
            if name == "plan_get":
                plan = self.publish_plan(pid)
                return {"ok": True, "plan": plan} if plan else {"ok": False, "error": f"plan inconnu : {pid}"}
            if name == "plan_comment":
                point = str(cmd.get("point") or "")
                r = self.comment(pid, point, cmd.get("text"), ask=cmd.get("ask") is True)
                if r.get("ok") and r.get("message"):  # question : Astrel répond dans le fil
                    threading.Timer(1.0, self.reply, (pid, f"(démo) Oui, c'est prévu : le point {point or 'général'} "
                                                           "est traité dans le lot unique.", point)).start()
            elif name == "plan_revise":
                r = self.revise(pid)
                if r.get("ok"):
                    threading.Timer(0.8, self.reply, (pid, "(démo) Je reprends le plan avec tes remarques.")).start()
                    threading.Timer(2.0, self.submit, ("Organiser le déménagement", DEMO_PLAN_REVISED),
                                    {"plan_id": pid}).start()
            elif name == "plan_validate":
                r = self.validate(pid, cmd.get("version"), by="utilisateur")
                if r.get("ok"):
                    bus.publish("notice", level="info", text=r["shown"])
            else:
                r = self.reject(pid, cmd.get("reason") or "")
            return {k: v for k, v in r.items() if k not in ("message", "shown")}

    return DemoPlans()


def seed_demo_todo(scratch: Path, demo_plans) -> None:
    """26/10 (plan P38 lot 2) : vue « Tâches » par le VRAI module `astrel.todo`, fichiers dans le
    dossier jetable (jamais runtime/todo.json). Statut des plans lu dans les plans de la démo.
    Trois premiers projets du HUD : cartes dans les 4 colonnes, une abandonnée, une qui attend
    l'utilisateur, un titre hostile (texte brut attendu), un titre de 120 caractères."""
    todo_mod.TODO_FILE = scratch / "todo.json"
    todo_mod.JOURNAL_FILE = scratch / "todo-journal.jsonl"
    # Lot 3 (27/10) : note générée et fiches de passation dans le dossier jetable, jamais le vrai
    # coffre ni runtime/equipe. Une fiche de relecteur sans réserve sur la mission « p2 » (le cœur ne lit
    # « prêt à clore » que dans la fiche d'un relecteur ; jamais affichée en entier) : T1
    # (plan P2, en cours) porte le badge « prêt à clore ».
    todo_mod.NOTE_FILE = scratch / "vault" / "TODO-PAR-PROJET.md"
    todo_mod.EQUIPE_DIR = scratch / "equipe"
    (scratch / "equipe" / "p2").mkdir(parents=True, exist_ok=True)
    (scratch / "equipe" / "p2" / "fiches.jsonl").write_text(json.dumps(
        {"n": 3, "de": "relecteur", "a": "astrel", "fait": "Relecture : rien de bloquant, zéro remarque.",
         "preuves": "photos ouvertes au hasard", "at": time.time()}, ensure_ascii=False) + "\n", encoding="utf-8")
    todo_mod.Todo._plan_statuts = staticmethod(lambda: {i["id"]: i["status"] for i in demo_plans.describe()["items"]})
    todo_mod.todo.reset()
    canaux = [(c.id, c.label) for c in channels.items()]
    projets = [cid for cid, _ in canaux if cid != channels.default]
    if not projets:
        return
    p0, p1, p2 = (projets + projets * 2)[:3]
    add = lambda projet, titre, **kw: todo_mod.todo.add(projet, titre, canaux=canaux, force=True, **kw).get("id")
    add(p0, "Sauvegarde des photos de famille", etat="cours", plan="P2", par="claude",
        note="Copier les photos sur le disque externe.\nOuvrir une photo au hasard.")
    add(p0, "Relire le plan du déménagement", etat="lancer", plan="P1", attend="utilisateur")
    add(p0, "Résilier l'abonnement de la box", note="Avant le 30 du mois.")
    add(p0, "Renouveler le passeport", etat="fini", par="claude")
    gone = add(p0, "Refaire toute la cuisine")
    todo_mod.todo.move(gone, "abandonne", "Trop gros pour ce mois-ci : on garde la cuisine actuelle.")
    t6 = add(p1, "Réserver le gîte des vacances", note="Trois nuits, près de la mer.")
    todo_mod.todo.move(t6, "lancer", par="claude")
    add(p1, "Valider les billets de train", etat="cours", attend="utilisateur")
    add(p2, "<img src=x onerror=alert(1)>")
    add(p2, "Un titre très long pour voir comment la carte se comporte quand on dicte une phrase entière sans "
            "s'arrêter du tout, d'un bout à l'autre")
    todo_mod.todo.publish(canaux)


# Page « Tout ce qu'Astrel sait faire » (plan P22) : skills et connecteurs claude.ai FIXES (les
# contrôles du HUD ne dépendent pas des dossiers de la machine).
DEMO_SKILLS = {"deep-research": "on", "anthropic-skills:pdf": "on", "founder-cfo": "name-only", "docs": "off"}
DEMO_CLAUDEAI = {"claude.ai Gmail": True, "claude.ai Google Drive": True, "claude.ai Google Calendar": False}


class DemoCalibrage:
    """02/11 : séance de calibrage SIMULÉE (mêmes évènements `calibrage` que `astrel/calibrage.Seance`),
    accélérée : 1 s de silence, 2 s de voix, vumètre qui bouge. Rien n'est écrit (dossier jetable)."""

    def __init__(self) -> None:
        self.gen = 0
        self.etat = ""
        self.pause_at = ""  # tests du HUD : la séance s'arrête à cette étape (« parole »), pour la capture
        self.log: list[str] = []

    def command(self, name: str, cmd: dict) -> dict:
        self.log.append(name)
        if cmd.get("_from_claude") is True:
            return {"ok": False, "error": "calibrage du micro réservé à l'utilisateur (HUD), refusé à Claude"}
        if name == "calibrage_start":
            self.gen += 1
            threading.Thread(target=self._run, args=(self.gen,), daemon=True).start()
            return {"ok": True, "micro": "Micro USB", "canaux": 2}
        if name == "calibrage_cancel":
            self.gen += 1
            if self.etat in ("bruit", "parole", "analyse", "resultat"):
                self._pub("annule", raison="annulé par l'utilisateur")
            return {"ok": True, "cancelled": True}
        if self.etat != "resultat":
            return {"ok": False, "error": "aucun calibrage à enregistrer : lance d'abord les mesures"}
        self.gen += 1
        profil = "gain ×1.4, VAD 0.32, réveil 0.50, canaux 0+1"
        self._pub("enregistre", profil=profil, prioritaires=[])
        bus.publish("notice", level="info", text=f"Profil du micro enregistré : {profil}.")
        return {"ok": True, "profil": profil}

    def _pub(self, etat: str, **data) -> None:
        self.etat = etat
        bus.publish("calibrage", etat=etat, micro="Micro USB", **data)

    def _run(self, gen: int) -> None:
        for etat, etape, duree in (("bruit", 1, 1.0), ("parole", 2, 2.0)):
            n = int(duree / 0.08)
            for i in range(n):
                if gen != self.gen:
                    return
                niveau = 0.08 + 0.03 * math.sin(i) if etat == "bruit" else 0.35 + 0.3 * abs(math.sin(i / 2.5))
                self._pub(etat, etape=etape, duree_s=duree, reste_s=round((n - i - 1) * 0.08, 1),
                          niveau=round(niveau, 3), **({"parole": niveau > 0.45} if etat == "parole" else {}))
                if etat == self.pause_at and i == n // 2:
                    return  # figé au milieu de l'étape (capture du vumètre)
                time.sleep(0.08)
        if gen != self.gen:
            return
        self._pub("analyse")
        time.sleep(0.4)
        if gen != self.gen:
            return
        self._pub("resultat", ancien={"gain": 2.0, "vad": 0.35, "wake": 0.5, "canaux": [], "cle": ""},
                  propose={"gain": 1.4, "vad": 0.32, "wake": 0.5, "canaux": [0, 1]}, n_canaux=2,
                  parts=[0.82, 0.77], bruit=210, voix=1790, pic=9400, rapport=8.5, faible=False,
                  avertissement="", nom={"sur": 3, "propose": 3, "ancien": 2})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    # 05/11 : contenu (et interface) de la démo en anglais, pour la capture du README anglais
    ap.add_argument("--langue", choices=("fr", "en"), default="en" if os.environ.get("ASTREL_DEMO_LANGUE") == "en" else "fr")
    args = ap.parse_args()
    LANGUE["demo"] = args.langue
    CANAUX_DEMO.update(canaux_demo())  # libellés et mots-clés des canaux dans la langue de la démo
    # 15/10 : la demo ne doit JAMAIS ecraser le vrai jeton du HUD (runtime/hud-token) : le
    # vrai Astrel ne le relit pas, la bulle et ./hud.sh se retrouvaient avec un jeton faux.
    config.HUD_TOKEN_FILE = Path(tempfile.mkdtemp(prefix="astrel-demo-")) / "hud-token"
    # 18/10 (plan P5) : guide (progression, premier lancement) dans le dossier jetable ;
    # cochage d'office simulé (jamais `claude auth status` ni le contrôle de la machine).
    from astrel import guide as guide_mod

    config.GUIDE_STATE_FILE = config.HUD_TOKEN_FILE.parent / "guide.json"
    config.FIRST_RUN_FILE = config.HUD_TOKEN_FILE.parent / "first-run-done"
    config.FIRST_RUN_FILE.write_text("démo\n", encoding="utf-8")
    # 23/10 (relecture P26) : la commande `sensor` de la démo écrivait le VRAI runtime/sensors.json
    # (capteurs voulus du vrai Astrel) : l'état voulu va dans le dossier jetable.
    sys.modules["astrel.sensors"].STATE_FILE = config.HUD_TOKEN_FILE.parent / "sensors.json"
    srv = HudServer()
    answered = threading.Event()
    model = {"active": "sonnet"}
    settings = DemoSettings()
    guide_mod.AUTO.clear()
    guide_mod.AUTO.update({"claude": lambda: settings.logged_in, "whatsapp": lambda: settings.wa["linked"]})
    demo_plans = make_demo_plans(settings)
    demo_images, demo_image_submit = make_demo_images(config.HUD_TOKEN_FILE.parent)
    from astrel import images_ia as _images_ia
    from astrel.core import Astrel as _Astrel  # `_image_piece` : la VRAIE commande du cœur (dossier jetable)

    _images_ia.reglages_images = lambda: demo_reglages_images(settings)  # P40 A5 : réglages de la démo
    demo_images.publier_etat()  # évènement sticky `ia_etat` dès la connexion
    image_log: list[dict] = []  # commandes image_* reçues (contrôles du HUD)
    demo_media = make_demo_media(config.HUD_TOKEN_FILE.parent)
    demo_videos, demo_video_submit = make_demo_videos(config.HUD_TOKEN_FILE.parent, settings)
    demo_videos.publier_etat()  # évènement sticky `video_etat` dès la connexion (P41 B4)
    media_log: list[dict] = []  # boutons des cartes de médias (jamais d'application ouverte en démo)
    link_log: list[dict] = []  # 04/11 : liens du fil cliqués (jamais de navigateur ouvert en démo)
    link_fail = {"on": False}  # tests du HUD : le cœur refuse, le HUD se replie sur son propre onglet
    live: dict = {}  # 04/11 : réponse en cours d'écriture (`demo_answering`), close par `stop` (■, Échap)
    stop_log: list[dict] = []  # 04/11 : commandes `stop` reçues (contrôles du HUD : Échap = ■)

    def demo_recherche() -> dict:
        """04/11 : des messages « Sirius » dans deux canaux (affichés, indexés par `history.fil` comme ceux du
        cœur), un plus ancien qui n'est plus dans le fil du HUD (index seul), et les archives (transcriptions
        jetables d'avant le fil, écrites au démarrage)."""
        autre = next((c.id for c in channels.items() if c.id != channels.default), channels.default)
        for ch, ask, answer in ((channels.default, "Quand passe la comète Sirius ?", "La comète **Sirius** passe le 12."),
                                (autre, "Réserve le gîte près de l'observatoire Sirius", "Gîte réservé près de Sirius.")):
            mid = uuid.uuid4().hex[:12]
            bus.publish("transcript", who="user", text=ask, source="clavier", channel=ch, mid=mid)
            bus.publish("reply", text=answer, usage={}, channel=ch, reply_to=mid, mid=uuid.uuid4().hex[:12])
        # contenu d'une conversation (français voulu, pas un texte d'écran à traduire)
        history.fil_ecrire([dict(type="reply", text="Ancienne note sur Sirius, sortie du fil affiché.", channel=autre,
                                 mid="ancien-sirius", ts=time.time() - 86400)])
        return {"ok": True, "autre": autre}
    local_memo: dict[str, tuple[str, str]] = {}  # 05/11 (lot 6) : demandes locales « en mémoire » (mid → texte, canal)
    local_log: list[dict] = []  # commandes local_* reçues (contrôles du HUD)
    local_ancien = {"on": False}  # 05/11 : comme un cœur d'avant le lot 6 (HUD rouvert, cœur pas relancé)

    def demo_local(cmd: dict) -> dict:
        """05/11 (lot 6 du moteur local) : `demo_local {text, channel}` (tests du HUD) publie une demande et sa
        réponse locales (badge « Local », `vers_claude`) ; `local_demandes` / `local_vers_claude` comme le cœur
        (HUD seul ; demande sensible → carte « routage » ; oubliée → refus), sans aucun moteur."""
        from astrel import sensible
        from astrel.choices import choices as _choices
        from astrel.coeur import local_tour

        name = str(cmd["cmd"])
        if name == "demo_local" and "coeur_ancien" in cmd:  # 05/11 : `demo_local {coeur_ancien}` (tests du HUD)
            local_ancien["on"] = bool(cmd.get("coeur_ancien"))
            return {"ok": True}
        if name == "demo_local":
            ch, mid = str(cmd.get("channel") or channels.default), uuid.uuid4().hex[:12]
            texte = str(cmd.get("text") or "explique la photosynthèse")
            bus.publish("transcript", who="user", text=f"en local : {texte}", source="clavier", channel=ch, mid=mid,
                        engine="local")
            garde = not cmd.get("hors_ligne") and not cmd.get("oubliee") and not local_ancien["on"]  # `oubliee` : relance
            if garde:
                local_memo[mid] = (texte, ch)
            bus.publish("reply", text=str(cmd.get("reply") or "(démo) Réponse du modèle local."), channel=ch,
                        reply_to=mid, mid=uuid.uuid4().hex[:12], engine="local",
                        **({"vers_claude": True} if garde else {"hors_ligne": True} if cmd.get("hors_ligne") else {}))
            return {"ok": True, "mid": mid}
        local_log.append({"cmd": name, "mid": cmd.get("mid"), "via": cmd.get("_via")})
        if local_ancien["on"]:
            return {"ok": False, "error": f"commande inconnue : {name}"}
        if cmd.get("_from_claude") is True or cmd.get("_via") != "hud":
            return {"ok": False, "error": f"{name} est réservé au HUD de l'utilisateur"}
        if name == "local_demandes":
            return {"ok": True, "mids": list(local_memo), "hors_ligne": False}
        item = local_memo.pop(str(cmd.get("mid") or ""), None)
        if item is None:
            return {"ok": False, "error": local_tour.VC_OUBLIEE, "raison": "oubliee"}
        texte, ch = item
        if sensible.scan(texte, (), zones_conf=[]):
            _choices.ask("(démo) Ta demande contient des données sensibles. L'envoyer à Claude ?",
                         ["Envoyer à Claude", "Annuler"], channel=ch, kind="routage")
            return {"ok": True, "mid": cmd.get("mid"), "carte": True}
        bus.publish("notice", level="info", channel=ch, text=local_tour.VC_NOTICE)
        mid = uuid.uuid4().hex[:12]
        bus.publish("transcript", who="user", text=texte, source="clavier", channel=ch, mid=mid)
        bus.publish("reply", text="(démo) Réponse de Claude.", usage={}, channel=ch, reply_to=mid, mid=uuid.uuid4().hex[:12])
        return {"ok": True, "mid": cmd.get("mid"), "envoye": True}

    # 05/11 (soir, essai de l'utilisateur : 4 min de « Je réfléchis » seul) : réflexion du modèle local, détails du
    # VRAI cœur (`local_tour.detail_reflexion`). `demo_reflexion {etape, jetons?, depuis_s?}` (tests du HUD) : une
    # étape posée telle quelle (« fin » : repos) ; /reflechir les enchaîne, ■ ou Échap l'arrêtent.
    reflexion = {"since": 0.0, "tour": 0}

    def reflexion_etat(etape: str, jetons: int = 0, depuis_s: float | None = None) -> dict:
        from astrel.coeur import local_tour

        if etape == "fin":
            reflexion["since"] = 0.0
            bus.publish("state", state="sleeping", text="", muted=False, detail="", since=time.time())
            return {"ok": True}
        if depuis_s is not None or not reflexion["since"]:
            reflexion["since"] = time.time() - float(depuis_s or 0)
        detail = local_tour.detail_reflexion(etape, int(jetons or 0), 4)
        bus.publish("state", state="thinking", text="", muted=False, detail=detail, since=reflexion["since"])
        return {"ok": True, "detail": detail}

    def reflexion_simulee(ch: str, mid: str) -> None:
        reflexion["tour"] += 1
        tour = reflexion["tour"]
        reflexion["since"] = 0.0
        for etape, jetons in (("reflexion", 0), ("reflexion", 1300), ("budget", 0), ("relecture", 0)):
            if reflexion["tour"] != tour:
                return  # ■ : la démo s'arrête comme le tour du cœur
            reflexion_etat(etape, jetons)
            time.sleep(0.8)
        if reflexion["tour"] != tour:
            return
        bus.publish("reply", text="(démo) Réponse du modèle local, après réflexion.", channel=ch, reply_to=mid,
                    mid=uuid.uuid4().hex[:12], engine="local")
        reflexion_etat("fin")

    chat_log: list[dict] = []  # 05/11 : commandes `chat_commande` reçues (contrôles du HUD)
    chat_libre = {"on": False}  # 05/11 : la démo a des agents de fond : /clear refusée, sauf `demo_chat {libre}`

    def demo_chat(cmd: dict) -> dict:
        """05/11 : commandes « / » du chat. `chat_commande {nom, arg}` : la VRAIE analyse et les vrais refus du cœur
        (`commandes_chat`), effets simulés (réglages, mode d'usage, modèle, notice) ; `demo_chat {libre?}` (tests du
        HUD) : le journal des commandes reçues, et /clear permise ou non."""
        from astrel import commandes_chat
        from astrel.coeur import commandes_chat as cc

        if cmd["cmd"] == "demo_chat":
            if "libre" in cmd:
                chat_libre["on"] = bool(cmd["libre"])
            return {"ok": True, "items": list(chat_log)}
        chat_log.append({"nom": cmd.get("nom"), "arg": cmd.get("arg"), "channel": cmd.get("channel"),
                         "via": cmd.get("_via")})
        if cmd.get("_from_claude") is True or cmd.get("_via") != "hud":
            return {"ok": False, "error": cc.REFUS_HUD}
        nom, arg = str(cmd.get("nom") or "").strip(), str(cmd.get("arg") or "").strip()
        a = commandes_chat.analyser(f"/{nom} {arg}" if arg else f"/{nom}")
        if a is None or not a.ok:
            return {"ok": False, "error": a.erreur if a else commandes_chat.INCONNUE.format(nom=nom[:30])}
        if a.nom == "aide":
            return {"ok": True, "text": commandes_chat.aide()}
        if a.nom == "clear":
            if not chat_libre["on"]:
                return {"ok": False, "error": cc.CLEAR_TACHE}
            bus.publish("notice", level="info", text="Nouvelle conversation : contexte remis à zéro.")
            return {"ok": True, "vider": True}
        if a.nom == "mode":
            r = settings.set("moteur_mode", a.arg)
            return {"ok": r.get("ok") is not False, "mode": a.arg, **({"error": r["error"]} if r.get("ok") is False else {})}
        if a.nom in ("horsligne", "enligne"):
            # 05/11 : comme le cœur, /horsligne passe par l'interrupteur (pas de notice « Réglage refusé »)
            r = settings.hors_ligne_entrer() if a.nom == "horsligne" else settings.set("hors_ligne", False)
            return {"ok": r.get("ok") is not False, **{k: r[k] for k in ("error", "stop_tout") if k in r}}
        if a.nom == "modele":
            model["active"] = a.arg
            bus.publish("model", active=a.arg, label=model_label(a.arg), id="")
            bus.publish("notice", level="info", text=f"Modèle principal : {model_label(model['active'])}.")
            return {"ok": True, "model": a.arg}
        if a.nom in cc.MODES_USAGE:
            bus.publish("usage", mode=cc.MODES_USAGE[a.nom], followup=8.0)
            return {"ok": True, "usage": cc.MODES_USAGE[a.nom]}
        if a.nom == "tout-arreter":
            return {"ok": False, "error": cc.TOUT_ARRETER_HUD}
        if a.nom == "reflechir":  # 05/11 : comme le cœur (`_tour_local(..., reflexion=True)`), réponse simulée
            ch, mid = str(cmd.get("channel") or channels.default), uuid.uuid4().hex[:12]
            bus.publish("transcript", who="user", text=f"/reflechir {a.arg}", source="clavier", channel=ch, mid=mid,
                        engine="local")
            # 05/11 (soir) : la réflexion simulée, comme le cœur la montre (détail de l'état, temps écoulé du HUD)
            threading.Thread(target=reflexion_simulee, args=(ch, mid), name="demo-reflexion", daemon=True).start()
            return {"ok": True, "local": True}
        return {"ok": False, "error": commandes_chat.INCONNUE.format(nom=a.nom)}

    staged: list[dict] = []  # pièces jointes en attente déposées par le HUD (16/10)
    last_sent: list[str] = []  # id des pièces parties avec la dernière phrase dite (`demo_voice`)

    demo_calib = DemoCalibrage()

    def on_command(cmd: dict):
        if str(cmd.get("cmd") or "").startswith("calibrage_"):  # 02/11 : assistant de calibrage du micro
            return demo_calib.command(str(cmd["cmd"]), cmd)
        if cmd.get("cmd") == "demo_calibrage":  # tests du HUD : figer une étape, proposer l'assistant
            demo_calib.pause_at = str(cmd.get("pause_at") or "")
            if cmd.get("propose"):
                bus.publish("calibrage_propose", source="alsa_input.usb-demo", micro="Micro USB")
            return {"ok": True, "log": list(demo_calib.log), "etat": demo_calib.etat}
        if cmd.get("cmd") == "settings_get":
            return settings.publish()
        elif cmd.get("cmd") == "settings_set":
            r = settings.set(str(cmd.get("key") or ""), cmd.get("value"))
            if cmd.get("key") == "plan_mode":
                demo_plans.publish()  # le sélecteur Manuel/Auto de la page « Plans » suit (comme le cœur)
            if cmd.get("key") == "brain_effort" and r.get("ok", True) is not False:
                level = str(cmd.get("value") or "")
                publish_demo_effort(level, model["active"])
                label = dict(DEMO_EFFORTS).get(level, level)
                bus.publish("notice", level="info", text=f"Effort de réflexion : {label}. "
                                                         "Le prochain échange peut relire la conversation sans cache.")
            return r
        elif cmd.get("cmd") == "agent_effort":  # 23/10 (plan P28) : fiche d'agent, prochains agents du métier
            # mêmes textes que astrel/agents.py et core.py (catalogue anglais astrel/locales/en.json)
            name, effort = str(cmd.get("agent") or ""), str(cmd.get("effort") or "")
            if name not in agents_mod.load_agents(efforts=False):
                return {"ok": False, "error": f"agent inconnu : {name or '(vide)'}"}
            if effort and effort not in DEMO_EFFORT_LEVELS:
                levels = ", ".join(DEMO_EFFORT_LEVELS)
                return {"ok": False, "error": f"niveau d'effort inconnu : {effort} (au choix : {levels})"}
            demo_agent_effort[name] = effort
            agent, label = name, dict(DEMO_EFFORTS).get(effort, effort)
            bus.publish("notice", level="info", text=f"Effort de l'agent {agent} : {label}, pour ses prochains lancements.")
            return {"ok": True, "agent": name, "effort": effort, **demo_effort_info(name, "")}
        elif cmd.get("cmd") in ("plans_get", "plan_get", "plan_comment", "plan_revise", "plan_validate", "plan_reject"):
            return demo_plans.command(str(cmd["cmd"]), cmd)
        elif cmd.get("cmd") in todo_mod.COMMANDES:  # 26/10 (plan P38 lot 2) : vue « Tâches », comme le cœur
            # `id` venu du HUD = numéro de la requête (conn.js) : la carte est `todo_id` (core._on_command).
            return todo_mod.commande(str(cmd["cmd"]), {**cmd, "id": cmd.get("todo_id") or ""},
                                     [(c.id, c.label) for c in channels.items()])
        elif cmd.get("cmd") == "audit_get":  # page « Journal » (plan P3, lot F)
            return demo_audit(cmd)
        elif cmd.get("cmd") in ("propositions_get", "proposition_appliquer", "proposition_refuser"):  # 04/11 (T49)
            return demo_propositions.command(str(cmd["cmd"]), cmd)
        elif cmd.get("cmd") in ("propositions_local_get", "proposition_local_appliquer",
                                "proposition_local_refuser"):  # 05/11 (lot 5 du moteur local)
            return demo_propositions_local.command(str(cmd["cmd"]), cmd)
        elif cmd.get("cmd") == "demo_plan":  # tests du HUD : un nouveau plan déposé en direct
            return demo_plans.new_plan()
        elif cmd.get("cmd") == "demo_plans_offline":  # tests du HUD : cœur injoignable pour les plans
            demo_plans.offline = bool(cmd.get("on"))
            return {"ok": True, "offline": demo_plans.offline}
        elif cmd.get("cmd") == "demo_settings_forget":  # tests du HUD (relecture P17) : plus d'instantané
            bus.clear_sticky("settings")              # `settings` rejoué à la connexion (cœur relancé…)
            return {"ok": True}
        elif cmd.get("cmd") == "settings_apply":
            return settings.apply()
        elif cmd.get("cmd") == "settings_action":  # 17/10 (plan P4) : boutons des lignes info/action
            return settings.action(str(cmd.get("key") or ""), str(cmd.get("action") or ""), cmd)
        elif cmd.get("cmd") == "demo_first_run":  # tests du HUD : premier lancement, puis plus (rejeu sans effet)
            config.FIRST_RUN_FILE.unlink(missing_ok=True)
            settings.publish(first_run=True)
            config.FIRST_RUN_FILE.write_text("démo\n", encoding="utf-8")
            return settings.publish()
        elif cmd.get("cmd") in ("guide_get", "guide_mark"):  # 18/10 (plan P5) : vrai module du guide
            from astrel import guide as guide_mod

            r = guide_mod.command(str(cmd["cmd"]), cmd)
            if cmd.get("finish") is True:
                settings.publish()
            return r
        elif cmd.get("cmd") == "capacites_get":  # 23/10 (plan P22) : vrai catalogue, skills et connecteurs de démo
            from astrel import capacites

            groupe = cmd.get("groupe") or None
            with graph._lock:
                usage = dict(graph._usage)
            return capacites.describe(str(cmd.get("lang") or "") or None, groupe, capteurs=sensors.snapshot(),
                                      usage=usage, skills=DEMO_SKILLS, claudeai=DEMO_CLAUDEAI)
        elif cmd.get("cmd") == "demo_wa_scan":  # tests du HUD : le téléphone a scanné le QR
            return settings.wa_scan()
        elif cmd.get("cmd") == "demo_wa_old_qr":  # tests du HUD : QR de plus de 60 s, jamais dessiné
            bus.publish("whatsapp_link", state="qr", n=99, qr="2@DEMO-PERIME", ts=time.time() - 61)
            return {"ok": True}
        elif cmd.get("cmd") == "confirm":
            bus.publish("confirm", id=cmd.get("confirm_id") or cmd.get("id"), pending=False, allow=bool(cmd.get("allow")),
                        by="écran", desc="l'envoi d'un e-mail")
            bus.publish("state", state="sleeping", text="", muted=False, detail="")
            answered.set()
        elif cmd.get("cmd") == "pause":
            last = dict(bus.last("state") or {"state": "sleeping", "text": "", "muted": False, "detail": ""})
            last.pop("type", None)
            last.pop("ts", None)
            last["paused"] = bool(cmd.get("on"))
            bus.publish("state", **last)
            return {"ok": True, "paused": last["paused"]}
        elif cmd.get("cmd") == "toggle":  # micro coupé / rétabli : `muted` dans l'état, comme le cœur
            last = dict(bus.last("state") or {"state": "sleeping", "text": "", "muted": False, "detail": ""})
            last.pop("type", None)
            last.pop("ts", None)
            last["muted"] = not last.get("muted")
            bus.publish("state", **last)
            return {"ok": True, "muted": last["muted"]}
        elif cmd.get("cmd") in ("voice_mute", "mute_all"):  # 16/10 : coupures de la barre, comme le cœur
            on_ = bool(cmd.get("on"))
            if cmd["cmd"] == "mute_all":
                last = dict(bus.last("state") or {"state": "sleeping", "text": "", "muted": False, "detail": ""})
                last.pop("type", None)
                last.pop("ts", None)
                last["muted"] = on_
                bus.publish("state", **last)
            bus.publish("usage", mode="silencieux" if on_ else "conversation", followup=8.0)
            return {"ok": True, "voice_cut": on_}
        elif cmd.get("cmd") == "attach_stage":  # 16/10 : pièces en attente déposées par le HUD
            staged[:] = [i for i in (cmd.get("attachments") or []) if isinstance(i, dict) and i.get("id")]
            return {"ok": True, "staged": len(staged)}
        elif cmd.get("cmd") == "demo_voice":  # tests du HUD : une phrase DITE, qui emporte les pièces déposées
            from astrel import attachments
            items, staged[:] = list(staged), []
            joined = attachments.prepare([{k: i.get(k) for k in ("name", "type", "data")} for i in items])
            if items:
                last_sent[:] = [i["id"] for i in items]
                bus.publish("attachments_sent", ids=list(last_sent))
            channel = channels.active
            bus.publish("transcript", who="user", text=str(cmd.get("text") or "Regarde ça."), source="voix",
                        channel=channel, mid=uuid.uuid4().hex[:12], attachments=joined.shown)
            return {"ok": True, "attachments": joined.shown}
        elif cmd.get("cmd") == "demo_dictee":  # tests du HUD (16/10) : une phrase DICTÉE, jamais envoyée
            bus.publish("transcript", who="user", text=str(cmd.get("text") or "Phrase dictée."), source="dictée",
                        channel=channels.active, mid=uuid.uuid4().hex[:12])
            return {"ok": True}
        elif cmd.get("cmd") == "demo_whatsapp":  # tests du HUD (29/10) : message de l'utilisateur venu de WhatsApp
            bus.publish("transcript", who="user", text=str(cmd.get("text") or "Message WhatsApp."), source="whatsapp",
                        channel=channels.active, mid=uuid.uuid4().hex[:12])
            return {"ok": True}
        elif cmd.get("cmd") == "demo_unsent":  # tests du HUD (16/10) : message affiché que le cerveau n'a pas reçu
            # `source` (mission, réflexe… : pas de « Renvoyer ») ; `with_attachments` : les pièces
            # de la dernière phrase dite (`demo_voice`) n'étaient pas parties, comme dans le cœur.
            mid = uuid.uuid4().hex[:12]
            bus.publish("transcript", who="user", text=str(cmd.get("text") or "Message perdu."),
                        source=str(cmd.get("source") or "voix"), channel=channels.active, mid=mid)
            bus.update_history(lambda ev: ev.get("type") == "transcript" and ev.get("mid") == mid, unsent=True)
            lost = list(last_sent) if cmd.get("with_attachments") else []
            bus.publish("unsent", mid=mid, error="cerveau non démarré", **({"attachments": lost} if lost else {}))
            return {"ok": True}
        elif cmd.get("cmd") == "demo_att_gone":  # tests du HUD (19/10) : image partie puis effacée (7 jours passés)
            bus.publish("transcript", who="user", text="Image d'il y a longtemps.", source="clavier",
                        channel=channels.active, mid=uuid.uuid4().hex[:12],
                        attachments=[{"name": "ancienne.png", "image": True, "file": "20260101-000000-ancienne.png"}])
            return {"ok": True}
        elif cmd.get("cmd") in ("channel", "channels"):
            wanted = str(cmd.get("active") or "")
            return channels.select(wanted) if wanted else channels.describe()
        elif cmd.get("cmd") == "channel_move":
            return channels.move(str(cmd.get("mid") or ""), str(cmd.get("channel") or ""))
        elif cmd.get("cmd") in ("chat_commande", "demo_chat"):  # 05/11 : commandes « / » du chat, comme le cœur
            return demo_chat(cmd)
        elif cmd.get("cmd") == "say":
            from astrel import attachments
            try:
                joined = attachments.prepare(cmd.get("attachments"))  # enregistrées dans le dossier jetable
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            text = str(cmd.get("text") or "") or ("Regarde ce que je te joins." if joined.shown else "")
            if cmd.get("resend_of"):  # 16/10 : comme le cœur (`Astrel._mark_resent`) ; liste depuis le 17/10
                raw = cmd["resend_of"]
                for old in (str(x) for x in (raw if isinstance(raw, list) else [raw]) if x):
                    bus.update_history(lambda ev, old=old: ev.get("type") == "transcript" and ev.get("mid") == old,
                                       unsent=False, resent=True)
                    bus.publish("resent", mid=old)
            channel, _ = channels.route(text, requested=str(cmd.get("channel") or ""))
            mid = uuid.uuid4().hex[:12]
            bus.publish("transcript", who="user", text=text, source="clavier", channel=channel, mid=mid,
                        attachments=joined.shown, sizes=[len(i["data"]) * 3 // 4 for i in joined.images])
            if text.strip().lower() in ("démo markdown", "demo markdown"):
                threading.Thread(target=stream_markdown, args=(channel, mid), daemon=True).start()
                return {"ok": True}
            if text.strip().lower() in ("démo tableau", "demo tableau"):
                threading.Thread(target=stream_markdown, args=(channel, mid, TABLE_DEMO), daemon=True).start()
                return {"ok": True}
            if text.strip().lower() in ("démo liens", "demo liens"):
                threading.Thread(target=stream_markdown, args=(channel, mid, LINKS_DEMO), daemon=True).start()
                return {"ok": True}
            if text.strip().lower() in ("démo chemins", "demo chemins"):  # 04/11 : chemins copiés d'un clic
                threading.Thread(target=stream_markdown, args=(channel, mid, PATHS_DEMO), daemon=True).start()
                return {"ok": True}
            recu = f"(demo) Received in the {channel} channel." if LANGUE["demo"] == "en" else f"(démo) Reçu dans le canal {channel}."
            bus.publish("reply", text=recu, usage={}, channel=channel,
                        reply_to=mid, mid=uuid.uuid4().hex[:12])
            return {"ok": True}
        elif cmd.get("cmd") == "demo_confirm":  # tests du HUD : une question qui arrive pendant la frappe
            bus.publish("confirm", id="demo2", pending=True, tool="Bash", desc="une tâche planifiée",
                        cmd="rappel chaque lundi à 8 h : « Sortir les poubelles »",
                        question="Attention, une tâche planifiée. Je continue ?", grantable=True)
            return {"ok": True}
        elif cmd.get("cmd") == "demo_choice":  # tests du HUD : une question à choix qui arrive en direct
            return {"ok": True, "id": publish_choice(str(cmd.get("channel") or channels.default))}
        elif cmd.get("cmd") == "demo_report":  # 27/10 (plan P41) : compte rendu d'un projet, gardé en historique
            mid = uuid.uuid4().hex[:12]
            bus.publish("report", text=str(cmd.get("text") or "Compte rendu du projet."), mid=mid, report=True,
                        channel=str(cmd.get("channel") or channels.default), pending_read=False,
                        report_kind="info", report_files=[])
            return {"ok": True, "mid": mid}
        elif cmd.get("cmd") == "demo_image":  # 21/10 (plan P14) : carte d'image IA (chargement, image, échec)
            return demo_image_submit(str(cmd.get("mode") or "ok"), str(cmd.get("channel") or channels.default))
        elif cmd.get("cmd") == "demo_media":  # 22/10 (plan P26) : médias du fil
            return demo_media(str(cmd.get("kind") or "show"), str(cmd.get("channel") or channels.default))
        elif cmd.get("cmd") == "demo_video":  # 28/10 (plan P41 B3) : carte de vidéo IA (en cours, finie, annulée)
            return demo_video_submit(str(cmd.get("mode") or "ok"), str(cmd.get("channel") or channels.default))
        elif cmd.get("cmd") == "video_cancel":  # bouton « Annuler » d'une carte de vidéo
            return demo_videos.cancel(str(cmd.get("job_id") or ""))
        elif cmd.get("cmd") == "video_generate":  # onglet Vidéo du panneau (P41 B4) : vrai module, faux worker
            image_log.append({k: cmd.get(k) for k in ("cmd", "prompt", "engine", "model", "duree", "image", "image_fin",
                                                      "format", "channel")})
            # 30/10 : image de fin et format passés comme le cœur (astrel/coeur/medias.py)
            return demo_videos.submit(cmd.get("prompt"), cmd.get("engine") or "auto", cmd.get("model") or "",
                                      cmd.get("duree") or 0, cmd.get("image") or "",
                                      str(cmd.get("channel") or channels.default),
                                      image_fin=cmd.get("image_fin") or "", format=cmd.get("format") or "")
        elif cmd.get("cmd") == "video_status":
            demo_videos.publier_etat()
            return demo_videos.status()
        elif cmd.get("cmd") in ("media_open", "media_playing"):  # comme le cœur, sans rien ouvrir ni micro
            media_log.append({k: cmd.get(k) for k in ("cmd", "media_id", "playing")})
            if cmd.get("cmd") == "media_open":
                from astrel import medias

                return {"ok": medias.open_target(cmd.get("media_id")) is not None}
            return {"ok": True, "playing": bool(cmd.get("playing"))}
        elif cmd.get("cmd") == "open_link":  # 04/11 : lien du fil, même contrôle que le cœur, rien d'ouvert
            from astrel.coeur.medias import lien_ouvrable

            link_log.append({"url": cmd.get("url"), "via": cmd.get("_via")})
            if link_fail["on"]:  # comme un cœur d'avant le 04/11
                name = str(cmd["cmd"])
                return {"ok": False, "error": f"commande inconnue : {name}"}
            ok = cmd.get("_via") == "hud" and lien_ouvrable(cmd.get("url")) is not None
            return {"ok": True} if ok else {"ok": False, "error": "lien refusé : seules les adresses https:// s'ouvrent"}
        elif cmd.get("cmd") == "history_query":  # 04/11 : recherche (Ctrl+K), la VRAIE commande, index jetable
            return history.commande_hud(cmd)
        elif cmd.get("cmd") in ("local_vers_claude", "local_demandes", "demo_local"):  # 05/11 (lot 6 du moteur local)
            return demo_local(cmd)
        elif cmd.get("cmd") == "demo_recherche":  # tests du HUD : messages « Sirius » dans plusieurs canaux
            return demo_recherche()
        elif cmd.get("cmd") == "demo_answering":  # tests du HUD : une réponse en cours d'écriture (Échap = ■)
            live["channel"] = str(cmd.get("channel") or channels.default)
            bus.publish("state", state="thinking", text="", muted=False, detail="")
            bus.publish("reply_delta", text="Je cherche les horaires du train… ", channel=live["channel"], reply_to="")
            return {"ok": True}
        elif cmd.get("cmd") == "demo_reflexion":  # 05/11 (tests du HUD) : une étape de la réflexion locale
            return reflexion_etat(str(cmd.get("etape") or "reflexion"), int(cmd.get("jetons") or 0),
                                  cmd.get("depuis_s"))
        elif cmd.get("cmd") == "stop":  # ■ et Échap (04/11) : comme le cœur, la réponse en cours s'arrête
            stop_log.append({"via": cmd.get("_via"), "ts": time.time()})
            reflexion["tour"] += 1  # 05/11 : la réflexion simulée de /reflechir s'arrête aussi
            ch = live.pop("channel", None)
            if ch is not None:
                bus.publish("reply", text="Je cherche les horaires du train…", usage={}, channel=ch,
                            reply_to="", mid=uuid.uuid4().hex[:12])
            bus.publish("state", state="sleeping", text="", muted=False, detail="")
            return {"ok": True}
        elif cmd.get("cmd") == "demo_stop_log":
            return {"ok": True, "items": list(stop_log)}
        elif cmd.get("cmd") == "demo_link_log":  # tests du HUD : liens demandés, et refus simulé (`fail`)
            if "fail" in cmd:
                link_fail["on"] = bool(cmd.get("fail"))
            return {"ok": True, "items": list(link_log)}
        elif cmd.get("cmd") == "demo_media_log":
            return {"ok": True, "items": list(media_log)}
        elif cmd.get("cmd") == "image_retry":  # bouton « Réessayer » d'une carte en échec
            image_log.append({k: cmd.get(k) for k in ("cmd", "job_id", "engine")})
            return demo_images.retry(str(cmd.get("job_id") or ""), str(cmd.get("engine") or ""))
        # 28/10 (plan P40 lot A5) : panneau « Images » et Réglages › Images, par le VRAI module (faux worker,
        # aucune fenêtre Chrome) ; `demo_image_log` rend ce que le HUD a envoyé.
        elif cmd.get("cmd") == "image_generate":
            image_log.append({k: cmd.get(k) for k in ("cmd", "prompt", "engine", "model", "files", "channel")})
            return demo_images.submit(cmd.get("prompt"), cmd.get("engine") or "auto", cmd.get("files"),
                                      str(cmd.get("channel") or channels.default), cmd.get("model") or "")
        elif cmd.get("cmd") == "image_piece":
            image_log.append({"cmd": "image_piece", "name": cmd.get("name"), "size": len(str(cmd.get("data") or ""))})
            # le Chrome des contrôles descend de Claude : la démo ne juge pas l'origine (comme ses autres commandes)
            return _Astrel._image_piece({**cmd, "_from_claude": False})
        elif cmd.get("cmd") == "image_status":
            demo_images.publier_etat()
            return demo_images.status()
        elif cmd.get("cmd") in _images_ia.COMMANDES_COMPTES:
            image_log.append({k: cmd.get(k) for k in ("cmd", "alias", "fournisseur", "ordre")})
            return demo_images.compte_commande(str(cmd["cmd"]), cmd)
        elif cmd.get("cmd") == "demo_image_log":
            return {"ok": True, "items": list(image_log)}
        elif cmd.get("cmd") == "demo_quota":  # tests du HUD : fenêtres séparées en liste + prévision
            publish_demo_quota()
            return {"ok": True}
        elif cmd.get("cmd") == "quota_refresh":  # 22/10 : bouton ↻ du panneau Abonnement (relevé par /usage)
            time.sleep(0.4)  # le vrai relevé prend ≈ 1 s : le bouton doit montrer « en cours »
            now = time.time()
            bus.publish("quota", status="allowed", limit_type="five_hour", forecast="", limited=False, windows=[
                {"name": "five_hour", "pct": 8, "utilization": 0.08, "resetsAt": now + 7200},
                {"name": "seven_day", "pct": 92, "utilization": 0.92, "resetsAt": now + 400000}])
            return {"ok": True, "source": "usage", "tokens": 0}
        elif cmd.get("cmd") == "demo_memory":  # tests du HUD (plan P20) : état mémoire ok/attention/blocage
            state = str(cmd.get("state") or "ok")
            memory_now[:] = [publish_demo_memory(state if state in DEMO_MEMORY else "ok", bool(cmd.get("active")))]
            return {"ok": True}
        elif cmd.get("cmd") == "demo_courrier":  # tests du HUD (plan P33 lot 3) : volet « Courrier », vide si off
            bus.publish("courrier", missions=[] if cmd.get("off") else DEMO_COURRIER)
            return {"ok": True}
        elif cmd.get("cmd") == "memory_top":  # clic sur la ligne « Mémoire » (plan P20)
            return {"ok": True, "items": DEMO_MEMORY_TOP, "total_go": 9.2, "at": time.time(),
                    "memory": memory_now[0] if memory_now else None}
        elif cmd.get("cmd") == "choice_answer":
            # `choice_id` : « id » est celui de la requête WebSocket (voir hud/src/conn.js).
            # Le vrai module publie `choice_answered` et corrige l'historique (rejeu « répondu »).
            raw_id = cmd.get("id")
            return choices.answer(cmd.get("choice_id") or (raw_id if isinstance(raw_id, str) else ""),
                                  cmd.get("value"))
        elif cmd.get("cmd") == "stop_task":
            tid = str(cmd.get("task_id") or "")
            if not any(t["id"] == tid for t in demo_tasks):
                return {"ok": False, "error": "tâche inconnue"}
            demo_tasks[:] = [t for t in demo_tasks if t["id"] != tid]
            details.pop(tid, None)
            publish_demo_tasks(demo_tasks, demo_done_items(details) if done_shown else None)
            bus.publish("notice", level="info", text="Tâche arrêtée.")
            return {"ok": True}
        elif cmd.get("cmd") == "task_detail":  # 21/10 (plan P18) : clic sur une ligne du panneau « en cours »
            tid = str(cmd.get("task_id") or "")
            d = details.get(tid)
            if d is None:
                return {"ok": False, "error": "unknown_task", "task_id": tid[:120]}
            if d["live"]:
                d["elapsed_s"] = round(time.time() - d["started"], 1)
            if d["type"] != "commande":  # 23/10 (plan P28) : effort reçu au lancement, prochains du métier
                d.update(demo_effort_info(d["type"], d.get("effort") or ""))
            return d
        elif cmd.get("cmd") == "demo_tasks_done":  # tests du HUD : deux tâches finies (champ `done`)
            done_shown[:] = [True]
            publish_demo_tasks(demo_tasks, demo_done_items(details))
            return {"ok": True}
        elif cmd.get("cmd") == "demo_tasks_nodone":  # tests du HUD : évènement sans `done` (cœur d'avant P18)
            publish_demo_tasks(demo_tasks)
            return {"ok": True}
        elif cmd.get("cmd") == "demo_expire":  # tests du HUD : détail oublié par le cœur (10 min, relance)
            details.pop(str(cmd.get("task_id") or ""), None)
            return {"ok": True}
        elif cmd.get("cmd") == "usage":
            mode = str(cmd.get("mode") or "conversation")
            bus.publish("usage", mode=mode, followup=8.0)
            return {"ok": True, "usage": mode}
        elif cmd.get("cmd") == "autonomy":
            bus.publish("autonomy", mode=str(cmd.get("mode") or "autonome"))
            return {"ok": True}
        elif cmd.get("cmd") == "total":  # 15/10 : comme le cœur, pour voir le bandeau et le cadre rouges
            on_ = cmd.get("mode") == "on"
            bus.publish("total", on=on_, since=time.time() if on_ else 0.0, by="hud" if on_ else "")
            return {"ok": True}
        elif cmd.get("cmd") == "grants":
            bus.publish("grants", items=[])
            return {"ok": True, "grants": []}
        elif cmd.get("cmd") == "channel_create":
            return channels.create(str(cmd.get("label") or ""), workdir=str(cmd.get("workdir") or ""),
                                   keywords=cmd.get("keywords"))
        elif cmd.get("cmd") == "channel_edit":
            return channels.edit(str(cmd.get("channel") or ""), label=cmd.get("label"), workdir=cmd.get("workdir"),
                                 keywords=cmd.get("keywords"))
        elif cmd.get("cmd") == "channel_delete":
            return channels.delete(str(cmd.get("channel") or ""))
        elif cmd.get("cmd") == "channel_reorder":  # 19/10 : glisser-déposer des onglets
            return channels.reorder(cmd.get("order"))
        elif cmd.get("cmd") == "reports":
            word = str(cmd.get("mode") or "")
            return reports.set_mode(word) if word else reports.describe()
        elif cmd.get("cmd") == "quit":  # 28/10 (hud-boutons-t10) : Réglages › Système › Éteindre ; la démo reste
            return {"ok": True}
        elif cmd.get("cmd") == "read_report":
            mid = str(cmd.get("mid") or "")
            text = reports.text(mid) or str(cmd.get("fallback") or "")
            if not text:
                return {"ok": False, "error": "compte rendu introuvable"}
            reports.mark_read(mid)
            threading.Thread(target=speak, args=(text, 3.0), daemon=True).start()
            return {"ok": True, "paused": False}
        elif cmd.get("cmd") == "mission":  # 15/10 : la vraie Mission, fichier dans le dossier jetable
            from astrel import totalgate
            from astrel.mission import mission
            action = totalgate.mission_action(cmd) or "status"
            if action.startswith("start"):
                spec = dict(cmd.get("spec") or cmd)
                spec["base"] = str(config.WORKDIR)
                r = mission.start(spec)
                if r.get("ok"):
                    mission.take_prompt()  # démo : le premier tour part aussitôt
            elif action == "pause":
                r = mission.pause()
            elif action == "resume":
                r = mission.resume()
            elif action == "stop":
                r = mission.stop()
            elif action in ("accepter", "reverifier", "reprendre_travail", "approuver"):  # 02/11 (P43)
                r = {"accepter": mission.accept, "reverifier": mission.reverify,
                     "reprendre_travail": mission.back_to_work, "approuver": mission.approve}[action]()
            else:
                r = {"ok": True}
            bus.publish("mission", **mission.vue_publiee())  # 04/11 : close = publiée comme absente
            return r
        elif cmd.get("cmd") == "mission_close":  # 04/11 : × du panneau, la vraie Mission (dossier jetable)
            from astrel.mission import mission
            r = mission.close(str(cmd.get("mission_id") or "").strip())
            bus.publish("mission", **mission.vue_publiee())
            return r
        elif cmd.get("cmd") == "demo_mission":  # 02/11 (plan P43) : un état donné, pour la mise en page
            bus.publish("mission", **_demo_mission(str(cmd.get("etat") or "")))
            return {"ok": True}
        elif cmd.get("cmd") == "sensor":
            return sensors.set(cmd.get("name", ""), bool(cmd.get("on")))
        elif cmd.get("cmd") == "voices":
            voix = VOIX[LANGUE["demo"]]
            return {"ok": True, "voices": [{"spec": voix, "label": VOIX_NOMS[LANGUE["demo"]], "online": False},
                                           {"spec": "gemini:Charon", "label": "Gemini Charon", "online": True}],
                    "active": voix}
        elif cmd.get("cmd") == "models":
            return {"ok": True, "active": model["active"],
                    "models": [{"model": k, "label": label, "desc": desc}
                               for k, (label, desc) in config.CLAUDE_MODELS.items()]}
        elif cmd.get("cmd") == "model":
            model["active"] = str(cmd.get("model", ""))
            bus.publish("model", active=model["active"], label=model_label(model["active"]), id="")
            publish_demo_effort(str((settings._item("brain_effort") or {}).get("value") or ""), model["active"])  # Haiku : sans effort
            bus.publish("notice", level="info", text=f"Modèle principal : {model_label(model['active'])}.")
            return {"ok": True, "model": model["active"]}
        return {"ok": True}

    demo_tasks: list[dict] = []
    details: dict[str, dict] = {}  # plan P18 : réponses de `task_detail`
    memory_now: list[dict] = []  # plan P20 : dernier évènement `memory` de la démo
    done_shown: list[bool] = []    # `done` publié seulement après `demo_tasks_done`

    # Le canal ouvert et le réglage des comptes rendus de la démo ne doivent pas
    # écraser ceux du vrai Astrel.
    scratch = Path(tempfile.mkdtemp(prefix="astrel-demo-"))
    config.CHANNEL_FILE = scratch / "channel.txt"
    # Canaux de la démo : une liste d'exemple, jamais celle de la machine (« + » compris).
    (scratch / "channels.json").write_text(json.dumps(CANAUX_DEMO, ensure_ascii=False, indent=2), encoding="utf-8")
    config.WORKDIR = Path("/home/user/Projets")  # dossier des projets affiché par la démo (fictif)
    config.CHANNELS_FILE = scratch / "channels.json"
    from astrel import attachments
    attachments.ATTACH_DIR = scratch / "attachments"
    config.REPORTS_FILE = scratch / "reports.txt"
    config.MISSION_FILE = scratch / "mission.json"  # jamais runtime/mission.json (lu par le hook)
    config.PLANS_DIR = scratch / "plans"  # jamais runtime/plans (ce que lit le codeur)
    # 04/11 : recherche (Ctrl+K) sur un index JETABLE : jamais runtime/history.db ni les transcriptions de la
    # machine ; archives = une transcription d'exemple d'avant le fil.
    config.HISTORY_DB = scratch / "history.db"
    config.TRANSCRIPTS_DIR = scratch / "transcripts"
    config.TRANSCRIPTS_DIR.mkdir()
    (config.TRANSCRIPTS_DIR / "demo.jsonl").write_text(json.dumps(
        {"type": "user", "sessionId": "demo",  # deux jours avant le fil : gardé par `history_keep_days` (réglage de la machine)
         "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 2 * 86400)),
         "message": {"role": "user", "content": "On regardera Sirius au télescope cet été."}}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    history.fil.brancher(bus, exclus=channels.local_ids)
    bus.set_command_handler(on_command)
    srv.start()
    channels.publish()
    demo_plans.seed()  # P1 à relire (une remarque, une réponse), P2 validé (auto), P3 rejeté
    seed_demo_todo(scratch, demo_plans)  # 26/10 (plan P38 lot 2) : vue « Tâches »
    reports.publish()
    demo_propositions.publish()  # 04/11 (T49) : pastille « Mémoire »
    demo_propositions_local.publish()  # 05/11 (lot 5 du moteur local) : pastille « Qwen »
    bus.publish("autonomy", mode="autonome")
    bus.publish("usage", mode="conversation", followup=8.0)
    bus.publish("grants", items=[{"key": "bash:une tâche planifiée", "until": time.time() + 1500}])
    bus.publish("state", state="booting", text="", muted=False, detail="")
    graph.set_init(INIT)
    graph.set_voice(VOIX[LANGUE["demo"]])
    bus.publish("voice", engine=VOIX[LANGUE["demo"]], fallback="", active=VOIX[LANGUE["demo"]], degraded=False)
    bus.publish("session", id="demo", model=INIT["model"], version="demo")
    bus.publish("model", active="sonnet", label="Sonnet", id=INIT["model"])
    publish_demo_effort("high", "sonnet")  # 23/10 (plan P28) : sélecteur d'effort à côté du modèle
    sensors._publish()
    now = time.time()
    # 15/10 : coût par agent (contrat des lots, idée 10) ; d3 dépasse le seuil (alerte, sans voix).
    demo_tasks[:] = [
        {"id": "d1", "kind": "agent", "desc": contenu("Trier les mails de la semaine"), "background": True,
         "started": now - 142, "step": contenu("Lire le fil « Facture d'électricité »"), "tool_uses": 7, "channel": "general",
         "tokens": 1_234_000, "requests": 12, "elapsed_s": 142.0, "alert": False,
         "refs": ["p2"]},  # plan P38 lot 2 : sa consigne cite P2 (core._todo_refs) → badge « agent en cours »
        {"id": "d2", "kind": "local_bash", "desc": contenu("Copier les photos sur le disque externe"), "background": True,
         "started": now - 18, "step": "", "tool_uses": 0, "channel": "maison"},
        {"id": "d3", "kind": "agent", "desc": contenu("Relire tous les contrats d'assurance"), "background": True,
         "started": now - 2400, "step": contenu("Lire le contrat n° 7"), "tool_uses": 162, "channel": "general",
         # 04/11 (T50) : `tokens` = travail (hors cache relu), le total à part
         "tokens": 1_400_000, "tokens_total": 101_000_000, "cache_lu": 99_600_000,
         "requests": 162, "elapsed_s": 2400.0, "alert": True},
    ]
    details.update(demo_details(now))
    publish_demo_tasks(demo_tasks)
    # L'alerte du cœur (`_task_alerts`) : une `notice` sans voix qui porte `task_id` et `task_alert`.
    bus.publish("notice", level="warning", channel="general", task_id="d3", task_alert=True,
                text="Agent qui s'emballe ? « Relire tous les contrats d'assurance » : 1,4 M tokens de travail (hors cache "
                     "relu ; 99,6 M relus), 162 requêtes, 40 min. Rien n'est arrêté : bouton Stop du panneau « en cours » si besoin.")
    publish_demo_quota()
    if os.environ.get("ASTREL_DEMO_SANS_MEMOIRE") != "1":  # captures « avant » de hud_check_theme --compare
        memory_now[:] = [publish_demo_memory()]  # plan P20 : ligne « Mémoire » sous « Contexte »
    # 15/10 : comptes rendus typés (idée 3) et question à choix (idée 9), présents dès l'ouverture
    # de la page (rejeu de l'historique, comme après une reconnexion).
    other =next((c.id for c in channels.items() if c.id != channels.default), channels.default)
    rid = uuid.uuid4().hex[:12]
    decision = contenu("Deux déménageurs possibles : le moins cher le 21, ou le plus rapide le 14. Dis-moi lequel.")
    reports.add(rid, decision, other, True)
    bus.publish("reply", text=decision, usage={}, channel=other, reply_to="", mid=rid, report=True, pending_read=True,
                report_kind="decision",
                report_files=[contenu("/home/user/Documents/Déménagement/devis-comparés.pdf"),
                              contenu("/tmp/astrel-demo/devis-demenageurs.txt")])
    bus.publish("reply", text=contenu("La réservation du gîte est bloquée : le site demande une pièce d'identité."), usage={},
                channel=channels.default, reply_to="", mid=uuid.uuid4().hex[:12], report=True, pending_read=False,
                report_kind="blocage", report_files=[])
    bus.publish("reply", text=contenu("Les photos sont copiées sur le disque externe et vérifiées."), usage={},
                channel=channels.default, reply_to="", mid=uuid.uuid4().hex[:12], report=True, pending_read=False,
                report_kind="resultat", report_files=[contenu("/media/disque-externe/Photos/2026-10-15")])
    publish_choice(channels.default)
    time.sleep(1.5)
    bus.publish("state", state="sleeping", text="", muted=False, detail="")
    print("HUD (ticket valable 60 s, ou ./hud.sh) :", login_url(srv.issue_ticket()), flush=True)
    try:
        while True:
            time.sleep(4)
            sequence()
            if args.once:
                answered.wait()
                while True:
                    time.sleep(3600)
            answered.wait(30)
            answered.clear()
            time.sleep(8)
    except KeyboardInterrupt:
        srv.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
