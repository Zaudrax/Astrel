# Utiliser Astrel

Le mode d'emploi au quotidien : la voix, le HUD, les capteurs, les canaux, la
mémoire, la sécurité. Pour l'installer : [README](../README.md).

## Démarrer

L'installation est décrite dans le [README](../README.md) et dans
[INSTALLATION.md](INSTALLATION.md). Ensuite, sous Linux :

```bash
systemctl --user start astrel     # l'assistant (démarre ensuite avec la session)
./bubble.sh &                     # la bulle flottante
./hud.sh                          # le HUD (ou Ctrl+Alt+H)
```

Dire **« Astrel »** (le nom de l'assistant, ou « Hey Astrel »), attendre le
carillon, parler. Dire le nom SEUL : une question dite d'une traite après le nom
réveille, mais elle est perdue ; la redire après le carillon. L'ancien mot
d'éveil marche aussi tant que « Accepter aussi l'ancien mot d'éveil »
(Réglages › Voix, audio et langue, réglage `wake_legacy`) est sur Oui, le
défaut. La conversation continue sans répéter le mot de réveil ; après 8 s de
silence, Astrel repasse en veille.
« Au revoir », « merci c'est tout »… le remettent en veille tout de suite.

Raccourcis clavier (GNOME seulement pour l'instant) :

| Raccourci | Effet |
|---|---|
| `Ctrl+Alt+J` | parler maintenant |
| `Ctrl+Alt+M` | couper / réactiver le micro |
| `Ctrl+Alt+X` | interrompre Astrel |
| `Ctrl+Alt+P` | mettre la voix en pause / la reprendre |
| `Ctrl+Alt+H` | ouvrir le HUD |

## La chaîne

```
micro ─ parecord (Linux) ─ « Astrel » (faster-whisper local ; ancien mot d'éveil : openWakeWord)
      ─ fin de phrase (VAD Silero)
      ─ transcription (faster-whisper : carte graphique NVIDIA sous Linux, sinon processeur)
      ─ Claude Code (TON abonnement, JSON en flux, session persistante)
            ├─ outils natifs : Bash, fichiers, web, sous-agents, skills
            ├─ MCP « astrel » : écran, webcam, son, lumière, thème, médias,
            │                   presse-papiers, rappels, réflexes, MÉMOIRE
            ├─ MCP « playwright » : un Chrome à profil dédié, piloté
            ├─ MCP « connecteurs » : agenda CalDAV, trains, traduction DeepL
            ├─ MCP « homeassistant » : la domotique (serveur officiel, si configuré)
            └─ hook de sécurité : les actions sensibles attendent ton « oui »
      ─ la réponse est dite PHRASE PAR PHRASE pendant que Claude écrit
      ─ voix (Edge, Piper en secours) ─ paplay (Linux) / sounddevice (macOS, Windows)
```

Tout est local sauf le raisonnement (Claude) et, si tu la choisis, la voix
Gemini — qui ne reçoit que le TEXTE des réponses, jamais le micro.

## Les capteurs

Interrupteurs sur le HUD, ou à la voix (« allume le mode concentration »).

| Capteur | Ce qu'il fait | Coût sur l'abonnement |
|---|---|---|
| **EYES** | Détecte ta présence devant l'écran (visage, en local) ; te salue au retour | nul |
| **WATCH** | Joint une capture de l'écran à chaque demande (« c'est quoi cette erreur ? ») | ~1 500 jetons par demande |
| **HOLO** | Gestes : 👍 oui, 👎 non, ✋ stop, ☝ parler | nul |
| **FOCUS** | Ne pas déranger : bannières GNOME masquées, annonces non urgentes muettes | nul |
| **REFLEX** | Réflexes « quand… alors… » (`config/reflexes.json`) : batterie, disque, briefing du matin, pause écran | nul, sauf les réflexes qui interrogent Claude |

La webcam n'est ouverte que si EYES ou HOLO est allumé (voyant allumé = caméra
utilisée). La capture d'écran passe par le **partage d'écran GNOME**, autorisé
une fois ; supprimer `runtime/screencast-token` retire l'autorisation.

## Les connecteurs

Services extérieurs. Chacun reste
inactif tant que ses identifiants manquent (et le dit clairement) ; ils se
renseignent dans `config/secrets.json`. **Marche à suivre complète, clé par
clé : [`CONNECTEURS.md`](CONNECTEURS.md).**

| Connecteur | Exemple | Il te faut |
|---|---|---|
| Home Assistant | « allume la lumière du salon » | `HOMEASSISTANT_URL`, `HOMEASSISTANT_TOKEN` |
| Agenda CalDAV | « qu'est-ce que j'ai demain ? », « ajoute dentiste jeudi 10 h » | `CALDAV_URL`, `CALDAV_USERNAME`, `CALDAV_PASSWORD` |
| Trains (SNCF / Navitia) | « prochains trains depuis Toulouse » | `NAVITIA_API_KEY` |
| Traduction DeepL | « comment on dit merci en japonais ? » | `DEEPL_API_KEY` |

Withings (santé) et SOS ne sont pas codés : le pourquoi et ce qu'il faudrait
sont dans le même document.

## La mémoire

Un coffre de notes Markdown dans `vault/`, compatible Obsidian. Claude y
cherche avant de répondre sur toi ou tes projets, et y écrit ce qu'il apprend.
Les liens `[[Nom de note]]` relient les notes entre elles (graphe d'Obsidian si
tu ouvres le coffre avec ; le HUD n'affiche plus de graphe). Tu peux lire,
corriger et ranger ces notes toi-même : c'est voulu.

## La voix

`./run.sh --voices` fait parler toutes les voix disponibles ; le badge de voix
du HUD permet d'en changer à chaud.

La voix la plus naturelle est **Charon, la voix native de Gemini**.
On la retrouve par l'API TTS de Gemini : poser `GEMINI_API_KEY` dans
`config/secrets.json` (`{"GEMINI_API_KEY": "…"}`, clé gratuite sur
aistudio.google.com), puis choisir « Gemini Charon ». Si Gemini tombe (réseau,
quota), Astrel bascule seul sur la voix locale et le dit sur le HUD.

## Pause de la voix

Le bouton **⏸** du HUD (à côté de 🔇), le menu de la bulle, `Ctrl+Alt+P`, ou à
la voix (« mets ta voix en pause », « reprends ») : comme sur un lecteur vidéo,
le son se coupe **net**, même au milieu d'un mot, mais Astrel continue de
travailler, de faire tourner ses agents et d'écrire ses réponses dans le HUD.
**▶** reprend là où il s'était arrêté (au début du mot coupé), finit la phrase,
puis dit ce qui est arrivé entre-temps. Pendant la pause aucun son ne sort,
carillons compris ; les phrases qui n'ont de sens qu'à l'instant (question de
confirmation, « d'accord »…) sont jetées, et les confirmations se font à
l'écran. L'écoute est fermée aussi : la conversation en cours s'arrête (une
phrase en cours d'écoute est jetée), plus de bulle « j'écoute » après une
réponse, et le mot d'éveil (« Astrel » comme l'ancien) est ignoré. Pour reparler : **▶**, `Ctrl+Alt+P`,
ou un réveil exprès (`Ctrl+Alt+J`, double clic sur la bulle, « Parler
maintenant »), qui reprend la voix puis écoute. La pause survit à une relance
d'Astrel. Claude peut se mettre en pause, mais toi seul lui rends la
parole.

## Comptes rendus à la demande

Quand un agent de fond a fini, Astrel ne le dit plus d'office : le compte rendu
arrive **en silence** dans le canal du projet, avec un liseré ambre et un bouton
**🔊 Lire** à côté du message ; l'onglet du canal porte un 🔊 et un toast le
signale. Un clic sur 🔊 le fait lire à ce moment-là : le cœur dit le texte déjà
affiché, sans redemander quoi que ce soit à Claude. Le bouton devient
**🔊 Relire** (dans tous les HUD ouverts). Voix en pause : il sera lu à la reprise.

Réglage : Réglages › Voix, audio et langue › **Comptes rendus des tâches de
fond** (À la demande / Lus aussitôt ; le bouton de la barre du haut est retiré),
ou `./run.sh --send "reports direct"` (lus dès qu'ils arrivent, comme avant) /
`--send "reports demande"`. Retenu dans `runtime/reports.txt`,
`ASTREL_REPORTS=direct|demande` prioritaire. En mode FOCUS, un compte rendu
reste en attente quel que soit le réglage.

Claude peut aussi poser un compte rendu de lui-même avec l'outil MCP
`post_report` (par exemple quand une tâche finit pendant que tu lui parles
d'autre chose) : même affichage, même bouton.

## Canaux (un fil par projet)

Au-dessus de la conversation du HUD, des onglets : **Général**, puis un par
projet… Chacun est un fil à part ; les autres affichent une pastille de
non-lus quand Astrel y écrit. Clic sur un onglet, ou **Alt+1…9**, pour changer
de fil ; clic droit sur un message pour le **ranger ailleurs** (ton message
emmène la réponse d'Astrel). Dans le panneau « en cours », chaque
agent porte le nom du projet pour lequel il travaille, et le toast « Terminé »
mène à son canal d'un clic.

**Ajouter un projet** : une entrée dans `config/channels.json` (`id`, `label`,
`color` et `keywords` facultatifs), puis recharger le HUD. Ni code ni relance.

**Seul l'affichage est rangé.** Claude garde UNE conversation continue : il se
souvient de tout, quel que soit l'onglet, et le prompt qu'il reçoit est
inchangé. Les canaux ne sont qu'un champ `channel` sur les messages publiés
pour le HUD (`astrel/channels.py`).

Où va un message, et pourquoi ce choix :

1. **Le canal ouvert** au moment où tu parles ou tapes. C'est la règle de
   base parce qu'elle est prévisible : on voit l'onglet actif avant de parler,
   et une détection automatique qui se trompe range un message là où on ne le
   cherchera pas. Le canal ouvert est retenu d'une relance à l'autre
   (`runtime/channel.txt`) et partagé par tous les HUD ouverts.
2. **S'adresser à un canal** en commençant par son nom : « Mon projet, relance la
   recherche », « pour Mon projet lance les tests », « #general … ». Indispensable
   à la voix, où il n'y a pas d'onglet à cliquer. Le nom seul doit être suivi
   d'une ponctuation : « Mon projet avance bien ? » en parle sans changer de fil.
3. **Depuis Général seulement**, une demande qui nomme UN SEUL projet (son nom
   ou ses `keywords`) part dans son canal. Général est le fil « par défaut » :
   y détecter le projet évite de devoir changer d'onglet avant chaque question.
   Deux projets nommés, ou aucun : le message reste dans Général.

Dans les cas 2 et 3 l'onglet ouvert suit, et la suite de la conversation
vocale reste dans ce canal. Un réflexe (message que tu n'as pas dit) va
dans Général ou le canal qu'il nomme, sans changer l'onglet.

**Les réponses** : la réponse d'Astrel s'affiche dans le canal de ton DERNIER
message. Si tu écris dans un autre canal pendant qu'Astrel
répond (le CLI intègre alors son message au tour en cours), le début de la
réponse reste dans l'ancien canal et la suite va dans le nouveau. Quand un
**agent de fond** finit et qu'Astrel en rend compte de lui-même, le compte
rendu va dans le canal où l'agent a été lancé, même si tu as changé
d'onglet entre-temps : c'est tout l'intérêt quand plusieurs agents tournent
en parallèle sur des projets différents.

## Sécurité

Le hook `hooks/permission_hook.py` s'intercale avant CHAQUE action de Claude
(`astrel/guard.py` décide). **Une nouvelle installation démarre en mode PRUDENT** :
Astrel demande avant tout ce qui touche à tes comptes, à tes commits ou à ton
dossier personnel. En mode **autonome**, il fait seul tout ce qui se rattrape et
ne demande que pour l'irréversible.

| Action | Autonome | Prudent (défaut d'une nouvelle installation) |
|---|---|---|
| Lire, chercher, écrire dans tes projets | passe | passe |
| Commits, connecteurs (lire, étiqueter, archiver, brouillons, événements), écriture dans ton dossier personnel, tâches planifiées | passe | **confirmation** |
| `sudo`, disques, extinction, `git push`, `rm -rf` hors projets, **envoyer** un mail, **supprimer**, payer, déployer, partager | **confirmation** | **confirmation** |
| Toucher au garde-fou lui-même | **confirmation** | **confirmation** |

La confirmation se donne **à la voix, d'un clic sur le HUD, ou d'un pouce levé**
(HOLO). **« Oui pour 30 min »** (ou « oui à tout » à l'oral) autorise le même
type d'action pour la suite de la tâche. Pas de réponse claire, ou Astrel
injoignable : l'action est **refusée**.

Le mode se change par le bouton ⚡/🛡 du HUD, ou `./run.sh --send "autonomy autonome"`
(ou `prudent`).
Claude ne peut pas le changer lui-même.

Le HUD n'écoute que sur `127.0.0.1`, exige un jeton privé et refuse les
connexions venant d'autres sites.

## Outils

```bash
./run.sh --text           # mode clavier (même cerveau, même garde-fou)
./run.sh --voices         # écouter les voix
./run.sh --diag           # vumètre micro + VAD + mot de réveil en direct
./run.sh --listen         # tester micro + transcription
./run.sh --send CMD       # wake, toggle, hush (voix seule), stop (voix + travail), reset, status, quit
./run.sh --send "model opus"   # modèle principal (sonnet par défaut, opus, haiku), à chaud
./run.sh --send "pause toggle" # voix en pause / reprise (on, off, toggle, status)
./run.sh --send "canal general" # onglet ouvert du HUD (sans argument : la liste)
.venv/bin/python tools/hud_demo.py   # HUD en démo, sans micro ni Claude
.venv/bin/python -m pytest           # tests
cd hud && npm run build               # recompiler le HUD après modification
```

## Structure

```
astrel/core.py         chef d'orchestre : réveil → écoute → Claude → voix
astrel/brain.py        session Claude Code en JSON streaming, traduction du flux
astrel/voice/          texte pour l'oral, moteurs (Edge, Piper en secours, Gemini), orateur
astrel/listener.py     mot de réveil (nom + alias hey_jarvis), fin de phrase, transcription
astrel/eveil.py        repère le nom (ou une variante) dans le texte transcrit
astrel/audio.py        micro (surdité pendant la parole), lecteur, carillons
astrel/guard.py        ce qui exige une confirmation ; confirm.py : le canal
astrel/events.py       bus d'événements ; server.py : HUD local (WebSocket)
astrel/channels.py     canaux du HUD (un fil par projet, config/channels.json)
astrel/graph.py        le graphe neuronal (outils, skills, MCP, capteurs, notes, projets), plus affiché par le HUD
astrel/sensors.py      registre des capteurs ; senses/ : leurs moteurs
astrel/mcp_server.py   outils MCP « astrel » ; tools/ : leurs implémentations
astrel/mcp_connectors.py  outils MCP « connecteurs » (tools/agenda, transport, translate)
astrel/bubble.py       bulle flottante (GTK, XWayland ; Linux seulement)
astrel/platform/       ce qui dépend du système (bureau, capture, rappels, son)
hud/                   HUD web (orbe 2D, sans three.js ni graphe 3D) — src/ compilé dans dist/
hooks/                 hook PreToolUse de Claude Code
```
