# Connecteurs d'Astrel (et du futur bracelet Studio)

Ce qu'Astrel sait faire avec des services extérieurs, et **ce que tu dois
faire, toi, pour activer chacun**. Rien n'est actif tant que tu n'as pas mis
tes identifiants : un connecteur non configuré répond simplement « connecteur
non configuré, ajoute X dans config/secrets.json ».

| Connecteur | État du code | Ce qu'il te faut |
|---|---|---|
| Home Assistant (domotique) | prêt, serveur MCP **officiel** de Home Assistant | l'adresse de ton Home Assistant et un jeton longue durée |
| Agenda CalDAV (Nextcloud, Radicale, Baïkal) | prêt | l'adresse CalDAV, ton identifiant, un mot de passe (d'application) |
| Transports SNCF / Navitia | prêt | une clé gratuite de l'API SNCF (ou de navitia.io) |
| Traduction DeepL | prêt (texte ; la voix passe par Astrel) | une clé DeepL API **Free** |
| Withings (santé) | **pas codé**, volontairement | voir plus bas : inscription développeur et OAuth |
| SOS / urgence | **pas codé**, prématuré | voir plus bas : il manque le pont avec le téléphone |

## Où mettre les identifiants

Dans `config/secrets.json` (ignoré par Git ; le créer s'il n'existe pas) :

```json
{
  "HOMEASSISTANT_URL": "http://homeassistant.local:8123",
  "HOMEASSISTANT_TOKEN": "eyJhbGciOi…",

  "CALDAV_URL": "https://cloud.exemple.fr/remote.php/dav",
  "CALDAV_USERNAME": "utilisateur",
  "CALDAV_PASSWORD": "mot-de-passe-d-application",
  "CALDAV_CALENDAR": "Personnel",

  "NAVITIA_API_KEY": "ta-cle-sncf",

  "DEEPL_API_KEY": "ta-cle-deepl:fx"
}
```

Puis `chmod 600 config/secrets.json`. Ne mets que les lignes des connecteurs
que tu veux activer ; les autres restent simplement inactifs.

Autre possibilité : les mêmes noms en variables d'environnement (par exemple
`Environment=DEEPL_API_KEY=…` dans `systemd/astrel.service`). L'environnement
passe avant le fichier.

**Prise en compte :**
- agenda, transports, traduction : relus à chaque demande, **aucune relance** ;
- Home Assistant, ou toute clé posée en variable d'environnement : relancer
  Astrel (`./run.sh --send restart` ou `systemctl --user restart astrel`),
  car la liste des serveurs MCP (`runtime/mcp.json`) est écrite au démarrage.

Pour vérifier : « Astrel, quels connecteurs sont actifs ? » (outil
`connectors_status`, qui ne contacte aucun service).

---

## 1. Home Assistant (domotique)

**Choix technique.** Home Assistant fournit lui-même un serveur MCP officiel
(intégration « Model Context Protocol Server », adresse `/api/mcp`, protocole
Streamable HTTP). Astrel le branche tel quel au lieu de réécrire des outils :
Home Assistant expose ses propres commandes (allumer, éteindre, régler une
lumière ou un thermostat, état de la maison…), toujours à jour avec ta version.
Astrel ne peut agir que sur les entités que **tu** exposes à Assist.

**Ce que tu dois faire :**

1. Home Assistant récent (2025 ou plus). Si `/api/mcp` répond « 404 », mets-le
   à jour.
2. **Paramètres → Appareils et services → Ajouter une intégration →
   « Model Context Protocol Server »**. Choisis l'API « Assist ».
3. **Paramètres → Assistants vocaux → Exposer** : coche les appareils que
   Astrel peut voir et commander.
   ⚠️ En mode autonome, Astrel agit **sans demander** sur ce qui est exposé.
   N'expose **pas** serrures, porte de garage ni alarme, ou passe Astrel en
   mode prudent. (Une règle « toujours confirmer » pour ces appareils
   demanderait de modifier le garde-fou, fichier protégé : à décider par toi.)
4. **Ton profil (en bas à gauche) → onglet Sécurité → Jetons d'accès longue
   durée → Créer un jeton**. Nomme-le « Astrel », copie-le (il ne s'affiche
   qu'une fois).
5. Dans `config/secrets.json` : `HOMEASSISTANT_URL` (l'adresse que tu tapes
   dans ton navigateur, par ex. `http://192.168.1.20:8123`, sans `/api/mcp`)
   et `HOMEASSISTANT_TOKEN`.
6. Relance Astrel. Le serveur `homeassistant` est déclaré au cerveau (seulement s'il est configuré).
   Essai : « Astrel, allume la lumière du salon ».

Le jeton est recopié dans `runtime/mcp.json`, écrit en droits 0600 et hors Git.
Pour le révoquer : supprime le jeton dans ton profil Home Assistant.

## 2. Agenda CalDAV

**Outils :** `calendar_upcoming` (prochains événements, récurrences
comprises), `calendar_create_event` (ajout, avec alerte facultative),
`calendar_list_calendars`. Pas de suppression ni de modification pour
l'instant.

**Ce que tu dois faire**, selon ton serveur :

| Serveur | `CALDAV_URL` | Mot de passe |
|---|---|---|
| Nextcloud | `https://<ton-nextcloud>/remote.php/dav` | un **mot de passe d'application** : Paramètres personnels → Sécurité → « Créer un nouveau mot de passe d'application » (obligatoire si la double authentification est active) |
| Radicale | `http://<machine>:5232/` | celui de ton fichier d'utilisateurs Radicale |
| Baïkal | `https://<machine>/dav.php/` (ou `/baikal/html/dav.php/`) | celui de l'utilisateur créé dans l'admin Baïkal |

`CALDAV_URL` peut aussi être l'adresse directe d'un calendrier : Astrel
l'utilise alors sans chercher. `CALDAV_CALENDAR` (facultatif) choisit un
calendrier par son nom quand tu en as plusieurs ; sinon Astrel prend le
premier qui accepte des événements. « Astrel, liste mes calendriers » te donne
les noms exacts.

Essai : « Astrel, qu'est-ce que j'ai cette semaine ? », puis « ajoute
dentiste jeudi à 10 h ».

## 3. Transports SNCF / Navitia

**Outils :** `transport_departures` (prochains départs d'une gare, temps réel,
retards, perturbations), `transport_journey` (itinéraire entre deux lieux,
départ ou arrivée à une heure donnée), `transport_search_stop`.

**Ce que tu dois faire** (au choix) :

- **API SNCF (conseillé, par défaut)** : remplis le formulaire
  <https://numerique.sncf.com/startup/api/token-developpeur/>, la clé arrive
  par e-mail. Gratuite, 5 000 requêtes par jour. Mets-la dans
  `NAVITIA_API_KEY`, rien d'autre. Couvre les **trains** (TGV, TER,
  Intercités, Transilien), pas les bus et trams de ville.
- **navitia.io** (Hove), pour les réseaux urbains : crée un compte sur
  <https://navitia.io> si l'inscription gratuite est ouverte, puis ajoute
  `"NAVITIA_BASE_URL": "https://api.navitia.io/v1"` et
  `"NAVITIA_COVERAGE": "<région>"` (par ex. `fr-idf` ; la liste s'obtient sur
  `https://api.navitia.io/v1/coverage` avec ta clé).

Essai : « Astrel, prochains trains au départ de Toulouse Matabiau ».

## 4. Traduction DeepL

**Outils :** `translate_text` (langue dite en clair, « anglais », « allemand »,
ou code « EN-US » ; tutoiement ou vouvoiement si la langue le permet),
`translate_usage` (caractères consommés ce mois-ci).

**La voix :** Whisper transcrit ce que tu dis, DeepL traduit, Astrel prononce
la traduction (la voix Rémy est multilingue). « Astrel, comment on dit
“où est la gare” en espagnol ? » marche donc déjà à l'oral. L'API vocale
de DeepL (traduction de la parole en direct) est réservée aux abonnements
**payants** : pas branchée.

**Ce que tu dois faire :**

1. <https://www.deepl.com/pro-api> → offre **DeepL API Free** (500 000
   caractères par mois). DeepL demande une carte bancaire pour vérifier
   l'identité, mais l'offre Free ne facture rien : au-delà du quota, les
   traductions sont refusées jusqu'au mois suivant.
2. Compte → **Clés d'API** → copie la clé. Une clé Free **finit par `:fx`**.
3. `DEEPL_API_KEY` dans `config/secrets.json`.

⚠️ Une clé **Pro** (sans `:fx`) fonctionne aussi, mais elle est facturée au
caractère. Astrel plafonne chaque demande à 5 000 caractères.

---

## Withings (santé) — non implémenté, à faire par toi d'abord

**Pourquoi rien n'est codé :** l'API Withings n'a pas de simple clé. Elle
impose une application déclarée sur leur portail développeur et un flux
**OAuth 2** où tu te connectes toi-même dans un navigateur pour autoriser
l'accès. Écrire ce code sans application enregistrée donnerait une fausse
intégration impossible à tester.

**Les étapes, de ton côté :**

1. Crée un compte développeur sur <https://developer.withings.com> (Withings
   Partner Hub) et une application de type « Public API integration ».
2. Déclare une **adresse de rappel** (callback). Vérifie sur le portail si
   `http://localhost:<port>/…` est accepté ; sinon il faudra une petite page
   en HTTPS qui récupère le code.
3. Note le **Client ID** et le **Consumer Secret** (secret : jamais dans Git).
4. Choisis les autorisations (« scopes ») : `user.metrics` (poids, tension,
   fréquence cardiaque), `user.activity` (pas, sommeil), `user.info`.

**Ce qu'il faudrait coder ensuite** (une fois ces éléments en main) : une
commande `./run.sh --withings-login` qui ouvre la page d'autorisation
Withings, reçoit le code sur l'adresse de rappel et l'échange contre un jeton
d'accès (valable environ 3 heures) et un jeton de renouvellement. Withings
**remplace** le jeton de renouvellement à chaque usage : il faut enregistrer le
nouveau à chaque fois (fichier 0600 dans `runtime/`), sinon l'accès est perdu.
Puis des outils en lecture seule : dernières mesures, sommeil, activité.

**Raccourci à considérer :** Home Assistant a déjà une intégration Withings
officielle. Si tu la configures dans Home Assistant et exposes ses capteurs
(poids, pas, sommeil) à Assist, Astrel pourra les lire via le connecteur Home
Assistant, sans une ligne de code de plus.

Les données de santé sont sensibles : les garder sur ta machine, et ne jamais
les écrire dans la mémoire d'Astrel (`vault/`) sans ton accord.

## SOS / urgence — non implémenté, prématuré

**Pourquoi rien n'est codé :** une alerte d'urgence a besoin de ta **position
GPS** et d'**envoyer un SMS** (ou d'appeler), donc d'un pont avec ton
téléphone. Ce pont n'existe pas : Astrel tourne aujourd'hui sur l'ordinateur de
bureau, sans lien avec le mobile. Un bouton SOS qui ne dépendrait que du PC ne
marcherait justement pas quand tu en as besoin (dehors, PC éteint, loin du
bureau). Une fausse sécurité est pire que pas de sécurité.

**Ce qu'il faudra avant de s'y mettre :**

1. **Le pont mobile** : l'application Studio sur le téléphone, reliée au
   bracelet (Bluetooth) et capable de recevoir un ordre d'alerte.
2. **Un chemin d'alerte déterministe, sans Claude** : l'appui long sur le
   bracelet (ou une chute détectée) déclenche l'alerte directement dans
   l'application, même sans réseau vers Claude, sans modèle de langue qui
   pourrait mal comprendre ou tarder.
3. **Position et SMS depuis le téléphone** : autorisation de localisation,
   envoi de SMS (sur Android, permission `SEND_SMS`, que le Play Store
   restreint ; sur iPhone, une application ne peut pas envoyer un SMS sans
   que l'utilisateur appuie sur « Envoyer »). Prévoir un repli (notification
   push à un proche, appel).
4. **Une liste de contacts d'urgence** choisie par toi, et une annulation
   possible pendant quelques secondes (fausse alerte).
5. **Une décision sur le garde-fou** : aujourd'hui « envoyer » est une action
   CRITIQUE, toujours confirmée. Une alerte SOS ne doit pas attendre de
   confirmation : c'est une exception à décider par toi, et donc à ne pas
   faire passer par Claude.
6. Rappeler dans l'application que Studio n'est pas un dispositif d'urgence
   certifié et ne remplace pas le 112.
