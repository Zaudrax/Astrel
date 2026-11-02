# Guide de démarrage d'Astrel

Bienvenue. Astrel est un assistant qui vit sur ton ordinateur : tu lui parles,
il réfléchit avec **Claude** (ton propre abonnement), agit sur ta machine
(terminal, fichiers, web, navigateur, bureau) et te répond à voix haute. Tout ce
qu'il fait s'affiche dans le **HUD**, la fenêtre où tu lis ce guide.

Ce guide a deux parties :

- **Installation** : les réglages à faire une fois, dans l'ordre. Chaque étape a
  ses boutons (« Tester », réglage sur place) et se coche quand c'est fait.
  Certaines se cochent toutes seules quand Astrel peut le vérifier.
- **Comment ça marche** : tout ce qu'Astrel sait faire, et comment le garder
  sous contrôle.

Compte une vingtaine de minutes pour l'installation. Tu peux fermer le guide à
tout moment et le rouvrir avec la pastille **Guide**, en haut à droite. Le
bouton **Terminé** l'empêche de s'ouvrir tout seul au démarrage.

Ce document est aussi lisible tel quel dans le dossier `docs/` d'Astrel.

## Vérifier la machine
<!-- id: controle | etape | test: demarrage/check | reglage: demarrage -->
Avant tout, Astrel contrôle ce dont il a besoin : Python, Claude Code, le son,
Node.js, l'espace disque. Le résultat s'affiche juste en dessous, une ligne par
élément :

- **vert** : prêt ;
- **rouge** : bloquant, à corriger d'abord (la flèche → dit quoi faire) ;
- **orange** : Astrel marchera, mais sans cette fonction (par exemple pas de
  capture d'écran) ;
- **sans couleur** : pour information.

Clique **Tester** (ou « Revérifier ») après chaque correction. Le même contrôle
existe dans un terminal, sans rien modifier :

```bash
python3 tools/check_install.py
```

Pour demander de l'aide, la page **Réglages › Premier lancement et diagnostic**
prépare un **rapport à coller** sans donnée personnelle (ni e-mail, ni nom
d'utilisateur, ni chemin de ton dossier).

## Connecter ton compte Claude
<!-- id: claude | etape | test: compte/refresh | reglage: compte -->
Astrel n'a pas de cerveau à lui : il passe par **Claude Code**, connecté à **ton**
compte Claude (abonnement Pro ou Max). Aucune clé API, rien n'est partagé avec
la personne qui t'a donné Astrel, et Astrel ne voit ni ne garde aucun jeton.

Si la ligne ci-dessous dit « connecté », cette étape est faite : elle se coche
toute seule. Sinon, clique **Se connecter** : une page de connexion de Claude
s'ouvre dans ton navigateur. Si rien ne s'ouvre, un lien s'affiche ici à la
place. Tu peux aussi le faire dans un terminal :

```bash
claude
```

puis `/exit` une fois connecté, et **Tester** pour revérifier.

Bon à savoir : chaque échange consomme ton abonnement, comme si tu écrivais à
Claude toi-même. Les tâches longues (agents, missions) consomment plus. Le
badge du modèle, en haut du HUD, montre lequel répond : Sonnet par défaut,
Opus pour les questions difficiles, Haiku pour aller vite.

## Choisir et tester le micro
<!-- id: micro | etape | test: audio.test/mic | reglage: audio.mic -->
Choisis le micro qu'Astrel doit écouter (« Micro du système » suit le réglage
de son de ton bureau), puis clique **Tester** : Astrel écoute 3 secondes et dit
si le son arrive. Parle normalement pendant l'essai.

L'étape se coche toute seule quand le contrôle de la machine voit un micro ;
coche-la toi-même une fois l'essai réussi.

Si le mot de réveil passe mal ensuite, quelques réglages :

- **Variantes du nom qui réveillent** (Réglages › Voix, audio et langue) : mots
  acceptés comme le nom au début d'une phrase, séparés par des virgules (la
  reconnaissance vocale écrit souvent « astral » pour « Astrel »).
- **Sensibilité de l'ancien mot d'éveil** (même section) : seuil de « Hey
  Astrel » seulement, sans effet sur le nom ; plus bas, il se réveille plus
  facilement, avec plus de faux réveils.
- **La calibration**, qui mesure TON micro (4 secondes de silence, puis 10
  secondes de voix) et retient ses réglages :

```bash
./run.sh --calibrate
```

Autres outils utiles : `./run.sh --mics` (le micro est-il vu ?),
`./run.sh --listen` (ta phrase doit s'afficher), `./run.sh --diag` (vumètre et
score du mot de réveil en direct).

## Choisir la voix
<!-- id: voix | etape | test: audio.test/voice | reglage: audio.voice -->
Choisis la voix d'Astrel, puis **Tester** : il dit une phrase d'essai. Si tu
ne l'entends pas, vérifie le **haut-parleur** choisi (Réglages › Voix, audio et
langue) et le volume de ton système.

Les voix proposées sont des voix en ligne gratuites. Elles ne reçoivent que le
**texte** des réponses, jamais ce que capte ton micro. Sans Internet, ou si le
service tombe, Astrel passe tout seul sur une voix locale, moins naturelle, et
reprend la voix choisie quand le service revient. La voix ne se tait jamais
pour une panne.

Pendant qu'Astrel parle, son micro est sourd : il ne s'entend pas lui-même.
Pour lui couper la parole, voir « Parler à Astrel ».

## La langue que tu parles
<!-- id: langue | etape | reglage: stt_language -->
Choisis la langue dans laquelle tu parles à Astrel : c'est elle que la
reconnaissance vocale attend. Français par défaut ; anglais, espagnol, allemand
et italien sont possibles.

Les réponses d'Astrel restent en français, quelle que soit la langue choisie
ici. Les voix proposées suivent ce choix : seules celles qui parlent cette
langue apparaissent dans la liste.

Pour une conversation TOUT en anglais (Astrel t'écoute, te répond et parle en
anglais, avec une voix anglaise), choisis **Langue de la conversation ›
Anglais** : une session neuve démarre à ton message suivant. Le mot d'éveil
reste « Astrel ».

La langue des textes du HUD, de la bulle et du mini-chat se choisit à part :
**Réglages › Voix, audio et langue › Langue de l'interface** (français ou
anglais), appliquée tout de suite, sans relancer Astrel.

Coche l'étape quand c'est réglé, même si tu gardes le français.

## Le dossier de tes projets
<!-- id: projets | etape -->
Claude travaille dans **un dossier de projets** : c'est là qu'il lit, écrit et
lance ses commandes sans te demander la permission. Hors de ce dossier, écrire
dans ton dossier personnel demande ton accord en mode prudent.

Ce dossier se choisit à l'installation :

```bash
mkdir -p ~/Projets
./install.sh --projets ~/Projets
```

Une réinstallation garde le dossier déjà choisi. Si Astrel ne démarre pas tout
seul (pas de service, par exemple sous WSL2), indique le dossier en le lançant :

```bash
ASTREL_WORKDIR=~/Projets ./run.sh
```

Chaque sous-dossier de premier niveau devient un **projet** qu'Astrel
reconnaît à la voix. Pour ranger la conversation par projet, voir « Les
canaux ». Coche l'étape quand ton dossier existe et contient tes projets.

## Relier WhatsApp (facultatif)
<!-- id: whatsapp | etape | reglage: whatsapp -->
Avec WhatsApp, tu parles à Astrel depuis ton téléphone quand tu n'es pas devant
l'ordinateur. Astrel ne lit qu'**un seul chat** : ton chat « message à
soi-même » (le chat avec ton propre numéro). Il ne voit ni tes contacts, ni tes
groupes, ni tes autres conversations, et n'écrit qu'à toi.

Il faut que l'ordinateur soit allumé et Astrel lancé : WhatsApp ne le réveille
pas. Pour relier, ici même :

1. **Préparer WhatsApp**, si le bouton apparaît : télécharge une fois le pont
   WhatsApp (une à deux minutes, Node.js et Internet nécessaires).
2. **Relier avec un QR** : un QR s'affiche. Sur ton téléphone : WhatsApp ›
   Réglages › **Appareils connectés** › **Connecter un appareil**, puis vise le
   QR. Il se renouvelle tout seul toutes les vingt secondes environ.
3. Si l'appareil photo n'arrive pas à le lire : **Relier avec un code**, tape
   ton numéro avec l'indicatif (par exemple 33 suivi de ton numéro sans le
   premier zéro), puis sur le téléphone « Connecter avec un numéro de
   téléphone » et tape le code de 8 caractères affiché ici.
4. Astrel affiche le numéro détecté : **Oui, c'est mon numéro** s'il est bien à
   toi. Sinon, **Non, annuler** : l'appareil est aussitôt délié.

Le QR et le code donnent accès à ton compte : ne les montre à personne. Ils ne
sont jamais enregistrés, et un appairage qui n'aboutit pas en 3 minutes
s'arrête tout seul.

Une fois relié, écris-toi « Bonjour » dans ton chat à toi-même : la réponse
arrive, marquée au début d'un petit robot (c'est ainsi qu'Astrel reconnaît
ses propres messages). Voir « Le mode Absent » pour la suite.

**Délier WhatsApp** (en tapant le mot « délier ») retire l'appareil « Astrel »
de ton téléphone et efface les identifiants de cet ordinateur. Si le téléphone
ne peut pas être prévenu, Astrel le dit : retire alors « Astrel » toi-même dans
Appareils connectés. Pour l'arrêter sans délier : **Couper WhatsApp**.

Ce qu'il faut savoir : WhatsApp passe par une bibliothèque non officielle.
Astrel écrit peu et lentement pour ne pas ressembler à un robot, mais un
blocage du numéro par WhatsApp ne peut pas être exclu. WhatsApp délie aussi
tout appareil quand le téléphone reste 14 jours sans connexion. Si tu ne veux
pas de WhatsApp, coche simplement cette étape.

## Démarrer avec ta session
<!-- id: demarrage-auto | etape | reglage: systeme.autostart -->
Sous Linux avec systemd, Astrel peut démarrer tout seul à l'ouverture de ta
session : active **Démarrer avec la session**. Rien n'est lancé ni arrêté sur
le moment, cela vaut pour la prochaine ouverture. Dans le même groupe :

- **Bulle au démarrage** : la petite fenêtre flottante s'ouvre aussi (voir « La
  bulle ») ;
- **Raccourcis clavier** (bureau GNOME seulement) : Ctrl+Alt+J pour parler,
  Ctrl+Alt+M pour le micro, Ctrl+Alt+X pour interrompre, Ctrl+Alt+P pour la
  pause de la voix, Ctrl+Alt+H pour le HUD.

Pour démarrer ou relancer à la main :

```bash
systemctl --user start astrel      # ou restart
./hud.sh                           # rouvrir le HUD
journalctl --user -u astrel -f     # le journal en direct
```

Si ces réglages sont grisés, c'est que ton système ne les permet pas (par
exemple WSL2 sans systemd) : lance Astrel dans un terminal que tu laisses
ouvert, avec `./run.sh`, puis le HUD avec `./hud.sh` dans un autre terminal.
Coche l'étape une fois ton choix fait.

## Parler à Astrel
<!-- id: parler -->
Dis **« Astrel »** (le nom de l'assistant, ou « Hey Astrel »), attends le petit
carillon, puis parle naturellement. Dis le nom SEUL : une phrase dite d'une
traite après le nom (« Astrel, quelle heure est-il ») réveille, mais la question
est perdue ; il faut la redire après le carillon. Astrel repère tout seul la fin
de ta phrase. Il répond phrase par phrase, au fur et à mesure que Claude écrit.

- Le mot d'éveil suit le **Nom de l'assistant** (Réglages › Apparence). Pendant
  la transition, l'ancien mot d'éveil marche aussi, tant que
  « Accepter aussi l'ancien mot d'éveil » (Réglages › Voix, audio et langue)
  est sur Oui (c'est le cas par défaut).
- Après sa réponse, tu as **8 secondes** pour enchaîner sans redire le nom :
  la bulle affiche « tu peux enchaîner ».
- « Au revoir », « merci c'est tout » le remettent en veille tout de suite.
- Pour le couper : le bouton **Arrêter** du HUD (ou Ctrl+Alt+X) coupe la voix
  et la réponse en cours ; les tâches de fond continuent.

Sans parler : la barre de saisie en bas du HUD. **Entrée** envoie, **Maj+Entrée**
passe à la ligne. Tu peux joindre un fichier ou une image (trombone, Ctrl+V ou
glisser-déposer). Le mode clavier existe aussi dans un terminal :
`./run.sh --text`.

Trois modes, en bas du HUD :

- **Conversation** : il t'écoute et répond à voix haute ;
- **Silencieux** : il t'écoute et répond à l'écran, sans rien dire ;
- **Dictée** : ce que tu dis est copié dans le presse-papiers, rien ne part à
  Claude (« fin de dictée » pour sortir).

Trois boutons de coupure : le **micro** seul, la **voix** seule, ou **les
deux**. Un micro coupé le reste après une relance : toi seul le rallumes.

La **pause** (bouton ⏸, Ctrl+Alt+P, ou « mets ta voix en pause ») coupe le son
net, comme un lecteur vidéo : Astrel continue de travailler et reprend là où il
s'était arrêté.

## Le HUD
<!-- id: hud -->
Le HUD est l'écran d'Astrel, dans ton navigateur. Il n'écoute que ton
ordinateur (adresse 127.0.0.1) et exige un accès privé : personne d'autre sur
le réseau ne peut l'ouvrir. Pour l'ouvrir : `./hud.sh`, ou Ctrl+Alt+H sous
GNOME.

Ce que tu y vois :

- **L'orbe**, qui vit avec la voix d'Astrel, et sous elle ce qu'il fait
  (il écoute, réfléchit, parle) ;
- **La conversation**, rangée en **canaux** (voir « Les canaux ») ; sa hauteur se
  règle avec la poignée au-dessus (double-clic : taille d'origine) ;
- **En cours** : les tâches et agents qui travaillent, avec leur coût ;
- **Les badges** : le modèle de Claude, la voix, le micro (un clic pour changer) ;
- **Les capteurs** : EYES (ta présence devant l'écran, en local), WATCH (une
  capture d'écran jointe à chaque demande, plus coûteuse), HOLO (gestes : pouce
  levé pour oui, pouce baissé pour non), FOCUS (ne pas déranger), REFLEX (les
  réflexes).

Les pastilles du haut : **Plans**, **Journal** (qui a autorisé quoi),
**Réglages**, **Guide**, et les commandes de sécurité (voir « Le garde-fou »).
Le bouton marche/arrêt éteint Astrel pour libérer la mémoire.

Si le HUD reste vide : Astrel doit tourner avant `./hud.sh`. Si la page refuse
la connexion, ferme-la et relance `./hud.sh`.

## Les canaux
<!-- id: canaux -->
Au-dessus de la conversation, des onglets : **Général**, puis un par projet.
Chaque canal est un fil à part, pour ne pas mélanger les sujets quand plusieurs
agents travaillent. Clic sur un onglet, ou **Alt+1 à 9**, pour changer de fil.

Seul l'affichage est rangé : Claude garde **une seule conversation** et se
souvient de tout, quel que soit l'onglet.

Où va un message :

1. dans **le canal ouvert** quand tu parles ou écris ;
2. dans le canal que tu **nommes en tête de phrase** : « Mon projet, relance les
   tests » (utile à la voix) ;
3. depuis Général seulement, une demande qui nomme **un seul** projet part dans
   son canal.

La réponse suit ton dernier message. Le compte rendu d'un agent de fond va dans
le canal où il a été lancé, même si tu as changé d'onglet. Clic droit sur un
message pour le ranger ailleurs.

Pour ajouter un projet : une entrée dans `config/channels.json` (un `id`, un
`label`, et des `keywords` facultatifs), puis recharger le HUD. Tu peux aussi
demander à Astrel de créer le canal.

## La mémoire
<!-- id: memoire -->
Astrel a une mémoire durable : le **coffre**, un dossier de notes Markdown
(`vault/`), lisible aussi avec Obsidian. Il y cherche avant de répondre sur toi
ou tes projets, et y écrit ce qu'il apprend : tes préférences, l'état d'un
chantier, une décision. Chaque écriture garde l'ancienne version.

Tu peux lire, corriger et ranger ces notes toi-même : c'est voulu. Demande-lui
aussi « souviens-toi que… » ou « qu'est-ce que tu sais sur… ».

Réglages utiles (Réglages › Cerveau, et Confidentialité et données) :

- **Rappel de la mémoire** : à chaque message, Astrel joint les notes qui en
  parlent (noms et extraits, noms seuls, ou rien) ;
- **Garder l'historique** : combien de jours garder l'index des conversations ;
- **Exporter la mémoire** (un zip) ou **effacer** l'historique ou la mémoire, en
  tapant un mot de confirmation ;
- **Ce qui part de cette machine** : la liste de ce qui est envoyé, et à qui.

Sur la durée, la conversation est résumée et reprise à neuf quand elle devient
trop longue (la « relève ») : les chantiers en cours sont notés dans le coffre
avant, rien d'important ne se perd. Après un long silence (2 heures par défaut),
la conversation repart aussi à neuf.

## Les agents et les tâches de fond
<!-- id: agents -->
Pour un travail long, Astrel lance des **agents** qui travaillent en parallèle
pendant que tu continues à lui parler : un chercheur (recherche en lecture
seule), un relecteur, un auditeur, un planificateur, un codeur (toujours sur le
modèle le plus fort) et d'autres. Tu les vois dans **En cours**, chacun avec le
projet pour lequel il travaille, sa durée et sa consommation.

Quand un agent finit, Astrel ne te coupe pas : le **compte rendu** arrive en
silence dans le canal du projet, avec un bouton **Lire** pour l'écouter quand tu
veux. Pour qu'ils soient lus dès qu'ils arrivent : Réglages › Voix, audio et langue ›
**Comptes rendus des tâches de fond**. Astrel ne prend jamais la parole pendant que tu parles : ce qui
arrive est mis en attente et dit quand tu as fini.

Un agent qui dépasse un seuil de durée ou de consommation déclenche une
**alerte**, jamais un arrêt (Réglages › Seuils d'alerte). Pour tout arrêter
d'un coup : **Stop tout** (voir « Le garde-fou »).

Les agents qui naviguent sur le web ont chacun leur navigateur ; Réglages ›
Navigateurs dit s'ils s'affichent à l'écran ou travaillent sans fenêtre.

## Le garde-fou et les confirmations
<!-- id: garde-fou -->
Avant **chaque** action de Claude, un garde-fou décide : elle passe, ou elle
attend ton accord. Sans réponse claire, ou si Astrel ne peut pas te joindre,
l'action est **refusée**.

Deux modes, sur l'interrupteur **Autonome** du haut :

- **Prudent** (celui d'une nouvelle installation) : il demande pour les commits,
  tes comptes connectés, l'écriture dans ton dossier personnel, les tâches
  planifiées, et toujours pour l'irréversible ;
- **Autonome** : il ne demande plus que pour l'**irréversible** : `sudo`,
  disques, extinction, `git push`, supprimer hors de tes projets, **envoyer** un
  mail, **supprimer**, payer, déployer, partager.

Pour répondre : **à la voix** (« oui », « non »), **d'un clic** sur la carte du
HUD ou de la bulle, d'un **pouce levé** devant la webcam (capteur HOLO), ou sur
WhatsApp quand tu es absent. « **Oui pour 30 min** » autorise le même type
d'action pour la suite de la tâche.

Toucher au garde-fou lui-même demande toujours ton accord, quel que soit le
mode. Sur WhatsApp, une telle question ne se valide qu'en écrivant exactement
« oui garde-fou ».

Les autres commandes de sécurité, en haut du HUD :

- **Autonome TOTAL** : plus aucune question (sauf sur le garde-fou), retombe à
  NON à chaque relance ;
- **Mission** : Astrel enchaîne seul vers un objectif, dans un dossier et un
  budget que tu fixes (Réglages › Garde-fou › **Lancer une mission**) ; un bandeau
  violet la montre sous la barre, avec Pause et Arrêter ;
- **Stop tout** : arrêt d'urgence ; voix coupée, travail et agents arrêtés, plus
  rien de nouveau jusqu'à « Reprendre ».

Claude ne peut changer **aucun** de ces réglages lui-même : seulement toi, par
le HUD ou ton terminal. Le **Journal** garde la trace de qui a autorisé quoi, et
`./run.sh doctor` vérifie que le garde-fou est en place et n'a pas changé.

## Les réflexes
<!-- id: reflexes -->
Un **réflexe** est une règle « quand… alors… » qu'Astrel applique tout seul :

- **quand** : à une heure donnée (par exemple 8 h 30 en semaine), à un moment
  précis, quand la batterie ou le disque passe sous un seuil, après un long
  moment devant l'écran, ou après une vérification régulière ;
- **alors** : dire une phrase, ou poser une question à Claude (un briefing du
  matin, par exemple).

Quelques exemples sont fournis, certains éteints : batterie faible, disque
presque plein, briefing du matin, pause écran. Le plus simple pour en créer un
est de le demander : « tous les jours de semaine à 8 h 30, fais-moi un briefing
de mes projets ». Astrel le crée, l'active ou le supprime sur demande. Ils sont
rangés dans `config/reflexes.json`.

Les réflexes qui ne font que vérifier quelque chose ne consomment rien ; seuls
ceux qui interrogent Claude utilisent ton abonnement. S'il n'a rien à dire,
Astrel reste muet. Un réflexe ne parle jamais par-dessus toi, et **Stop tout**
les suspend.

## Le mode plan
<!-- id: mode-plan -->
Pour un gros chantier de code (plusieurs fichiers, plusieurs agents), Astrel
fait d'abord écrire un **plan** par son planificateur : les fichiers touchés,
les risques, les étapes, et parfois des questions pour toi. Le plan apparaît
dans la page **Plans** du HUD.

Tu le relis point par point, tu peux commenter un point, demander une révision,
le rejeter ou le **valider**. En mode **Manuel** (par défaut), aucun codeur ne
démarre avant ta validation. En mode **Automatique**, un plan est validé dès
qu'il est déposé. Le choix se fait dans la page Plans ou dans Réglages ›
Cerveau.

Seul toi peux valider un plan, depuis le HUD : Claude ne valide jamais le sien.

## Le mode Absent
<!-- id: absent -->
Quand tu quittes l'ordinateur, dis ou écris « **je m'absente** » (ou bouton
« Je m'absente » dans Réglages › WhatsApp). Il faut WhatsApp relié.

- Astrel se tait sur l'ordinateur : rien ne parle dans une pièce vide ;
- il t'envoie sur WhatsApp un récapitulatif (le dernier échange, les tâches en
  cours) ;
- puis tout ce qui arrive sans que tu l'aies demandé part sur WhatsApp : fins de
  tâche, comptes rendus, erreurs, rappels, et ses **questions de
  confirmation**, auxquelles tu réponds « oui » ou « non ».

« **Je suis de retour** », ou n'importe quel vrai message à la voix ou au
clavier, te ramène en mode présent.

Même sans être absent, un message WhatsApp suffit pour parler à Astrel : sa
réponse revient sur WhatsApp, dans la même conversation qu'au HUD. Depuis
WhatsApp, « **stop** » arrête la réponse en cours et « **stop tout** » pose
l'arrêt d'urgence (qui ne se lève que sur l'ordinateur). Tes notes vocales sont
transcrites sur l'ordinateur ; les images et documents reçus ne sont pas encore
lus. Les messages écrits pendant qu'Astrel était éteint sont ignorés : renvoie
ce qui compte encore.

Astrel peut aussi t'envoyer un fichier sur WhatsApp, seulement depuis les
dossiers permis (variable `ASTREL_WHATSAPP_FILE_ROOTS`), jamais ses propres
réglages, secrets ou mémoire.

## La bulle
<!-- id: bulle -->
La **bulle** est une petite fenêtre flottante, toujours visible, qui montre si
Astrel écoute, réfléchit ou parle. Elle ne prend jamais le clavier.

- **Un clic** : un petit champ pour écrire à Astrel ;
- **double-clic** : il t'écoute tout de suite ;
- les **questions de confirmation** s'affichent en carte à côté d'elle.

Elle se lance avec `./bubble.sh &`, ou toute seule à l'ouverture de ta session
(Réglages › Système › Bulle au démarrage). Elle n'existe que sous Linux pour
l'instant, et peut se déplacer à la souris ; sa place est retenue.

## Les rappels
<!-- id: rappels -->
Demande simplement : « rappelle-moi dans 20 minutes de sortir le linge », ou
« rappelle-moi demain à 9 h d'appeler le garage ». À l'heure dite, une
notification s'affiche et Astrel le dit à voix haute, sauf si tu parles à ce
moment-là : il attend que tu aies fini. En mode Absent, le rappel part sur
WhatsApp.

« Quels sont mes rappels ? » les liste, « supprime le rappel du garage »
l'efface.

Un rappel survit à une relance d'Astrel, mais pas à un redémarrage de
l'ordinateur avant l'heure. Sous Linux avec systemd, il est confié au système :
si Astrel est arrêté à l'heure dite, la notification s'affiche quand même.

## Mails, agenda et autres services (facultatif)
<!-- id: connecteurs -->
Rien de tout ça n'est nécessaire pour commencer. Deux façons de brancher des
services :

**Les connecteurs de ton compte Claude** (Gmail, Google Agenda, Google Drive…)
s'ajoutent sur le site de Claude, dans les réglages de ton compte, rubrique
connecteurs. Claude Code s'en sert ensuite tout seul. Dans Réglages ›
Connecteurs claude.ai, tu peux en **bloquer** pour Astrel seul : leurs outils ne
pèsent alors plus sur chaque échange. Lire, étiqueter ou ranger passe librement
en mode autonome ; **envoyer** un mail ou **supprimer** demande toujours ton
accord.

**Les connecteurs d'Astrel**, à renseigner dans `config/secrets.json` :

- un **agenda CalDAV** (« qu'est-ce que j'ai demain ? ») ;
- les **horaires de trains** et transports en commun ;
- la **traduction** DeepL ;
- la **domotique** Home Assistant (« allume la lumière du salon »).

Chacun reste inactif tant que ses identifiants manquent, et Astrel le dit
clairement si tu le demandes. La marche à suivre, clé par clé, est dans
`docs/CONNECTEURS.md`. Après une modification, les réglages en attente
s'appliquent avec **Appliquer maintenant** dans les Réglages.

## Dépannage
<!-- id: depannage -->
Commence toujours par ces deux contrôles, qui ne modifient rien :

```bash
python3 tools/check_install.py     # la machine
./run.sh doctor                    # Astrel : garde-fou, outils, droits des fichiers
```

Les cas courants :

- **Il n'entend pas son nom** : le micro est-il coupé dans le HUD ? Une notice
  « Je n'entends plus le mot d'éveil » dit que la reconnaissance vocale locale
  ne se charge pas et que l'ancien mot est coupé. Puis `./run.sh --mics`,
  `./run.sh --listen`, et `./run.sh --calibrate`.
- **Il entend mais ne parle pas** : la voix est-elle coupée ou en pause dans le
  HUD, ou le mode Silencieux actif ? Puis `./run.sh --say "bonjour"`, et le
  haut-parleur choisi dans les Réglages.
- **Il ne démarre pas** : `journalctl --user -u astrel -n 50`. « Non connecté » :
  reconnecte ton compte Claude (première étape), puis
  `systemctl --user restart astrel`.
- **Le HUD est vide** : Astrel doit tourner avant `./hud.sh`.
- **Il demande tout le temps la permission** : c'est le mode prudent ; passe en
  autonome si tu le souhaites (voir « Le garde-fou »).
- **WhatsApp ne répond plus** : regarde Réglages › WhatsApp. « Session perdue » :
  délie, puis relie.

Pour demander de l'aide : Réglages › Premier lancement et diagnostic ›
**Préparer le rapport**, puis Copier. Le rapport ne contient ni e-mail, ni nom
d'utilisateur, ni chemin personnel. Le détail est dans `docs/DEPANNAGE.md`.

Pour mettre Astrel à jour : `./update.sh` (Réglages › Mises à jour le propose
aussi). Tes réglages, ta mémoire et tes journaux ne sont jamais touchés.

## Limites connues, selon ton système
<!-- id: limites -->
Dit franchement, pour que tu ne cherches pas un bug là où il n'y en a pas :

- **Linux (Debian ou Ubuntu, bureau GNOME)** : c'est là que tout marche. Sur un
  autre bureau, les raccourcis, le thème et la capture d'écran peuvent manquer.
- **Windows 11 par WSL2** : la voie conseillée sous Windows, mais **pas encore
  essayée de bout en bout**. Le son passe par WSLg : le micro est à vérifier
  avec `./run.sh --listen` (et Windows doit autoriser le micro : Paramètres ›
  Confidentialité › Microphone). Souvent sans systemd : pas de démarrage
  automatique, lance `ASTREL_WORKDIR=~/Projets ./run.sh` dans un terminal ouvert.
  Pas de bulle, pas de raccourcis, pas de capture du bureau Windows. L'ouverture
  du HUD depuis le navigateur de Windows n'a pas encore été vérifiée : si
  `./hud.sh` n'ouvre rien, installe Chromium dans Ubuntu.
- **macOS et Windows sans WSL** : **pas encore prêts**. Le code est en partie
  porté mais n'a jamais tourné sur une vraie machine ; Astrel ne démarrera
  probablement pas.
- **Langue** : l'interface, la voix et les consignes sont en français.
- **WhatsApp** : fonction récente, par une bibliothèque non officielle (voir
  l'étape WhatsApp). Signale tout comportement étrange.
- **Licence** : PolyForm Noncommercial 1.0.0 : usage
  **non commercial** seulement.

Si quelque chose ne marche pas sur ton système, le rapport à coller (voir
« Dépannage ») est le meilleur moyen de le faire corriger.
