# Astrel

[English version](README.en.md)

**Parle à ton ordinateur. Claude Code fait le travail, et te demande avant tout ce qui ne se rattrape pas.**

Astrel est un assistant vocal qui tourne sur l'ordinateur et confie le raisonnement à Claude Code,
le CLI d'Anthropic, sur l'abonnement de l'utilisateur. On dit « Astrel », on parle, il agit
(terminal, fichiers, navigateur, bureau) et répond à voix haute. Un tableau de bord local, le HUD,
montre ce qu'il fait pendant qu'il le fait.

![Le HUD : fil de conversation, question à choix, agents en cours](docs/images/hud.png)

![La bulle : repos, écoute, réflexion, parole, question, micro coupé](docs/images/bulle.gif)

## Ce qui le distingue

**Un garde-fou avant chaque action.** Claude tourne sans demander de permission au CLI, mais un hook
`PreToolUse` (`hooks/permission_hook.py`, logique dans `astrel/guard.py`) juge chaque appel d'outil.
Les commandes Bash sont analysées de façon structurée : suppressions récursives, écritures vers des
fichiers sensibles, commandes déguisées. Ce qui est grave (supprimer, envoyer, payer, partager,
`sudo`…) attend une réponse à la voix, d'un clic dans le HUD ou d'un geste devant la webcam.
Un hook qui plante refuse. Au troisième refus d'affilée dans un même message, un disjoncteur dit à
Claude d'arrêter de contourner et de demander. Avant chaque écriture de Claude, le hook garde la
version d'avant : « annule ce que tu viens de faire » rétablit le dernier lot de fichiers. Claude ne peut ni répondre « oui » à sa propre demande ni desserrer le
garde-fou : sous Linux, le cœur reconnaît ce qui vient de lui par la descendance des processus et
le cgroup du service.

**La voix phrase par phrase.** La réponse est lue au fil de l'eau, une phrase dès qu'elle est
complète, jamais au milieu d'un bloc de code. Le réveil et la transcription se font sur la machine
(faster-whisper, détection de fin de phrase par Silero VAD) ; seule la voix Edge reçoit le texte
des réponses, avec Piper en secours local. Le micro est coupé pendant qu'Astrel parle, et rien de
non demandé n'est dit tant que l'utilisateur a la parole.

**En français ou en anglais.** La conversation se règle dans Réglages › Voix, audio et langue ›
« Langue de la conversation ». En anglais, Whisper transcrit en anglais, Astrel répond avec une
voix anglaise et des consignes anglaises, et les commandes vocales ont leur version anglaise. Le
mot d'éveil reste « Astrel ». Dans la même page, un assistant (« Calibrer ce micro ») mesure le
bruit de fond puis la voix, et propose un gain et des seuils pour ce micro à côté des réglages
actuels ; rien ne change sans « Enregistrer ».

**Un HUD qui reste sur la machine.** Page web servie sur 127.0.0.1 seulement, avec un jeton qui ne
passe jamais par la ligne de commande et un cookie signé. Le markdown des réponses est rendu en
nœuds DOM d'une liste fermée, sans `innerHTML`. Une bulle flottante (GTK) donne l'état d'un coup
d'œil et reçoit les questions de confirmation.

**Des agents spécialisés, lancés en fond pour les tâches longues.** Une recherche sur plusieurs
sites, une semaine de mails à trier : Astrel confie ce qui prend du temps à des sous-agents définis
dans `config/agents/`, chacun avec sa fiche de métier, et la conversation continue pendant ce temps.
Le HUD montre ce que fait chaque agent et ce qu'il a coûté. Il prévient quand l'un d'eux s'emballe.
Les gros chantiers passent par un mode plan : un plan écrit, relu dans le HUD, puis exécuté.

**De la mémoire et des jetons comptés.** Notes en Markdown dans un coffre local, rappelées avant
chaque message ; historique des conversations indexé en SQLite FTS5 (`history_search`). Quand le
contexte dépasse 150 000 jetons, la session est relayée par une neuve avec un résumé. Les
vérifications simples (un fichier a changé, une commande répond) tournent sans modèle.

**Claude par défaut, d'autres fournisseurs possibles.** Le cerveau est Claude Code sur
l'abonnement Claude. Réglages › Fournisseur propose aussi Claude par clé d'API, puis OpenAI, Gemini,
OpenRouter et Grok par clé d'API, à travers une passerelle LiteLLM locale (127.0.0.1, installée à la
demande). Les clés restent dans un fichier en 0600 et ne passent ni par l'environnement ni par la
ligne de commande. Hors Claude, Astrel converse sans outils par défaut : les outils ne s'ouvrent
qu'après un banc d'essai réussi, en mode prudent, et jamais en mode « Sans confirmation » ni garde-fou levé.
Les CLI Codex et Grok Build, avec l'abonnement de ces outils, sont branchés mais refusés tant
qu'un essai réel n'a pas montré que le garde-fou les tient.

Le reste : canaux par projet dans le HUD, liste de tâches par projet, WhatsApp limité au chat
« message à soi-même », connecteurs (agenda CalDAV, Home Assistant…), génération d'images et de
vidéos par le navigateur sur les comptes de l'utilisateur (arrêt net devant un défi anti-robot),
interface en français et en anglais. Côté fiabilité, les processus enfants meurent avec le cœur,
un message reçu pendant un redémarrage est retrouvé au démarrage suivant, et un chien de garde
signale une boucle bloquée sans rien arrêter de lui-même.

## En chiffres

Mesuré sur ce dépôt le 01/11/2026 :

| | |
|---|---|
| Python (cœur `astrel/` et hooks) | 56 613 lignes, commentaires compris |
| JavaScript du HUD (`hud/src/`) | 11 673 lignes |
| Tests Python (`tests/`) | 3 956 tests (`pytest --collect-only`), 36 347 lignes |
| Outils MCP exposés à Claude | 68 (59 dans le serveur d'Astrel, 9 dans celui des connecteurs) |

## Comment ça marche

```mermaid
flowchart LR
    Micro((Micro)) --> Reveil["« Astrel »<br/>faster-whisper, local"]
    Reveil --> Fin["Fin de phrase<br/>Silero VAD, local"]
    Fin --> STT["Transcription<br/>faster-whisper, local"]
    STT --> Coeur["Cœur d'Astrel<br/>astrel/core.py"]
    Coeur <-->|"flux JSON, --resume"| Claude["Claude Code<br/>(CLI, abonnement)"]
    Claude --> Hook{"Hook PreToolUse<br/>garde-fou"}
    Hook -->|autorisé| Outils["Terminal, fichiers,<br/>MCP Astrel, navigateur"]
    Hook -->|grave| Accord(["Accord de l'utilisateur :<br/>voix, HUD, bulle, geste"])
    Claude --> Agents["Agents spécialisés<br/>tâches longues, en fond"]
    Coeur --> Voix["Voix phrase par phrase<br/>Edge, Piper en secours"] --> HP((Haut-parleur))
    Coeur <--> HUD["HUD web<br/>127.0.0.1 seulement"]
    Coeur <--> Bulle["Bulle GTK"]
    Coeur <--> Memoire["Coffre Markdown<br/>historique SQLite FTS5"]
```

Tout se passe sur la machine, sauf le raisonnement (Claude) et la voix Edge, qui ne reçoit que le
texte des réponses, jamais le son du micro.

## Où ça marche

| Système | État |
|---|---|
| **Debian 13**, GNOME, Wayland | Utilisé tous les jours : c'est la machine de référence. |
| Ubuntu 24.04, Debian 12 | Mêmes paquets ; pas encore essayé sur une machine neuve. |
| Windows 10 ou 11 + WSL2 | **La voie conseillée sous Windows** : le code Linux, dans Ubuntu. Premier essai en cours. |
| macOS | Porté (couche `astrel/platform/`) et vérifié par l'intégration continue : installation, démarrage, un tour de conversation, tests. **Jamais lancé sur une vraie machine.** |
| Windows natif | Porté et vérifié par l'intégration continue : installation, démarrage, un tour de conversation, tests. **Jamais essayé sur un vrai PC.** |

Hors Linux, la bulle, les raccourcis globaux et le démarrage automatique sont portés mais n'ont
jamais tourné sur un vrai bureau. Sous Windows, le garde-fou reconnaît un programme lancé par Claude
grâce à un Job Object, l'équivalent du cgroup de Linux ; sous macOS, il n'a ni l'un ni l'autre et le
reconnaît moins bien.

## Installation

### Debian ou Ubuntu

Il faut Claude Code installé et connecté à un abonnement Claude (Pro ou Max), Python 3.11 ou plus,
et environ 6 Go de disque. Une carte NVIDIA accélère la transcription ; sans elle, le processeur
s'en charge, plus lentement.

```bash
sudo apt install git python3 python3-venv python3-gi gir1.2-gtk-3.0 \
  gstreamer1.0-pipewire gir1.2-gstreamer-1.0 pulseaudio-utils libnotify-bin \
  xclip wireplumber nodejs npm curl poppler-utils
git clone https://github.com/Zaudrax/Astrel.git ~/Astrel && cd ~/Astrel
python3 tools/check_install.py        # contrôle de la machine, ne modifie rien
./install.sh --projets ~/Projets      # sans sudo ; --projets : le dossier où Claude travaille
systemctl --user start astrel         # l'assistant
./hud.sh                              # le HUD
```

Le pas à pas complet est dans [docs/INSTALLATION.md](docs/INSTALLATION.md). Au premier lancement,
le HUD ouvre un guide de démarrage ([docs/GUIDE-DEMARRAGE.md](docs/GUIDE-DEMARRAGE.md)) ; le reste
du mode d'emploi est dans [docs/UTILISATION.md](docs/UTILISATION.md) et
[docs/DEPANNAGE.md](docs/DEPANNAGE.md).

Une nouvelle installation démarre en mode prudent : il demande aussi pour les comptes connectés et
l'écriture dans le dossier personnel. `./run.sh doctor` vérifie que le garde-fou est en
place et n'a pas changé.

Pour essayer le HUD sans micro ni abonnement : `.venv/bin/python tools/hud_demo.py` puis `./hud.sh`.

### macOS (expérimental : jamais lancé sur une vraie machine)

L'installateur est commun (`install.py`) et le contrôle de la machine connaît macOS, mais personne
n'a encore lancé Astrel sur un Mac. Il faut Claude Code installé et connecté (`claude`), puis :

```bash
xcode-select --install                    # git
brew install python@3.12 node             # ou Python depuis python.org
git clone https://github.com/Zaudrax/Astrel.git ~/Astrel && cd ~/Astrel
python3.12 tools/check_install.py
python3.12 install.py                     # installateur commun (aucun sudo)
.venv/bin/python -m astrel                # l'assistant
.venv/bin/python -m astrel --hud          # le HUD, dans un autre terminal
```

Le son passe par sounddevice, installé avec Astrel ; macOS demande l'accès au micro au premier
lancement. Le dossier des projets est `~/Projets`, ou celui de la variable `ASTREL_WORKDIR`.

### Windows : par WSL2

Astrel tourne alors dans un Ubuntu intégré à Windows, avec le même code que sur la machine de
référence. Il faut Windows 11, ou Windows 10 à jour (version 22H2), pour que WSLg transmette le son,
le micro et les fenêtres. Cette voie est en cours de premier essai : signale tout ce qui coince.

1. Dans PowerShell **lancé en administrateur** :
   ```powershell
   wsl --install -d Ubuntu-24.04
   ```
   Redémarre, ouvre « Ubuntu » depuis le menu Démarrer et crée l'utilisateur demandé.
2. Vérifie que systemd tourne dans Ubuntu : `systemctl --user status` doit répondre. Sinon :
   ```bash
   printf '[boot]\nsystemd=true\n' | sudo tee /etc/wsl.conf
   ```
   puis `wsl --shutdown` dans PowerShell, et rouvre Ubuntu.
3. Installe Claude Code **dans Ubuntu** et connecte-le à ton abonnement Claude (Pro ou Max) :
   ```bash
   curl -fsSL https://claude.ai/install.sh | bash
   claude          # suis le lien de connexion, puis /exit
   ```
4. Installe Google Chrome dans Ubuntu : le HUD s'ouvre dans une fenêtre de Chrome, affichée sur le
   bureau Windows par WSLg.
   ```bash
   wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
   sudo apt install ./google-chrome-stable_current_amd64.deb
   ```
5. Suis la partie **Debian ou Ubuntu** ci-dessus, dans la fenêtre Ubuntu. Clone Astrel dans le
   dossier Linux (`~/Astrel`), pas sous `/mnt/c/…`, beaucoup plus lent.
6. Vérifie le micro avec `./run.sh --listen`. Windows doit autoriser le micro aux applications de
   bureau (Paramètres › Confidentialité et sécurité › Microphone).

Une carte NVIDIA sert aussi sous WSL2 si son pilote Windows est à jour ; sinon le processeur
transcrit, plus lentement. La bulle et les raccourcis globaux ne sont pas garantis sous WSLg.

### Windows natif (expérimental : jamais lancé sur un vrai PC)

L'intégration continue installe Astrel sous Windows, le démarre et lui fait tenir une conversation à
chaque envoi, mais personne ne l'a encore lancé sur un vrai PC. Il faut Python 3.11 à 3.13
(python.org), Node.js, Git et Claude Code installé et connecté (`claude`), puis, dans PowerShell :

```powershell
git clone https://github.com/Zaudrax/Astrel.git $HOME\Astrel ; cd $HOME\Astrel
py -3 tools\check_install.py
powershell -ExecutionPolicy Bypass -File .\install.ps1   # installateur commun (install.py)
.venv\Scripts\python -m astrel            # l'assistant
.venv\Scripts\python -m astrel --hud      # le HUD, dans un autre terminal
```

La transcription sur carte graphique n'est réglée que sous Linux : ailleurs, Astrel transcrit sur le
processeur, plus lentement (`ASTREL_STT_DEVICE=cuda` si CUDA et cuDNN sont installés à la main).

## Limites connues

- macOS et Windows n'ont jamais tourné sur une vraie machine.
- L'assistant de calibrage du HUD n'a pas encore été essayé avec un vrai micro.
- WhatsApp a été essayé sur un vrai compte ; les connecteurs agenda et domotique ne l'ont pas
  encore été.
- La génération de vidéos dépend de sites qui changent souvent ; elle a réussi en réel sur deux
  sites, sans essai en multicompte.
- Aucun fournisseur à clé, Claude compris, n'a encore été essayé avec une vraie clé ; Codex et
  Grok Build n'ont jamais tourné.

## Contribuer

Comment proposer un correctif, et les contrôles à passer avant : [CONTRIBUTING.md](CONTRIBUTING.md). Une faille
de sécurité se signale en privé, jamais dans une issue : [SECURITY.md](SECURITY.md).

## Licence

Astrel est publié sous licence [MIT](LICENSE) : tu peux l'utiliser, le modifier et le redistribuer,
y compris dans un cadre commercial. Attention : certains composants installés à part gardent leur
propre licence, et deux sont réservés à un usage non commercial (les modèles du mot d'éveil et les
voix Piper dérivées de Lessac). La liste complète est dans [NOTICE](NOTICE).

Auteur : Baptiste Renaud ([Zaudrax](https://github.com/Zaudrax)).
