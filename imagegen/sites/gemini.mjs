// Gemini (plan P14 ; P40 lot A3 : module à part, choix du modèle, pièces jointes).
//
// Méthode validée à la main le 21/10 : champ « Enter a prompt for Gemini », bouton
// « Download full size image » sous l'image (procedures/procedure-images-gemini-navigateur.md).
// Choix du modèle et pièces jointes : JAMAIS essayés sur le vrai site (aucun relevé, question Q5
// du plan P40). Les modèles viennent du CATALOGUE seulement (config/ia-web.json : `modeles`, et
// au besoin `motif` par modèle et `menu_modeles` pour le service, relevés sur le site) ; tant que
// `modeles_releves` est faux, seul `defaut` existe et la page n'est pas touchée.
//
// Interface : celle d'un site du noyau (noyau.mjs, en-tête) + ce que lit le parcours des images
// (worker.mjs, en-tête).

import { choisirModeleMenu, joindreFichiers } from './commun.mjs'
import { note } from '../noyau.mjs'
import { PAUSE_COURTE_MS, TEST, cliquer, dormir, pause } from '../humain.mjs'

const CHAMP = /enter a prompt|prompt for gemini|ask gemini|demander à gemini|saisissez une (requête|instruction)|saisir une requête/i
const DL = /download full.size image|télécharger l.image en taille réelle|télécharger.{0,20}taille (réelle|originale)/i
const STOP = /stop (response|generating)|arrêter (la réponse|la génération)/i
const CONNEXION = /^\s*(sign in|se connecter|connexion)\s*$/i
// Bouton des pièces jointes (menu « Upload files » ensuite) et aperçu d'une pièce déposée
// 30/10 (1er essai réel) : bouton « + » = « Importation et outils » / « Upload & tools » (menu ensuite)
const JOINDRE = /open upload file menu|upload (&|and) tools|importation et outils|upload (files?|images?)|add (files?|images?|photos?)|ouvrir le menu d.importation|importer des fichiers|ajouter des (fichiers|images|photos)/i
// 30/10 (1er essai réel) : au 1er fichier d'un compte, fenêtre « Creating content from images and files »
// (avoir les droits sur les fichiers envoyés), boutons Cancel / Agree. Décision de l'utilisateur (30/10) :
// le pilote clique « Agree », comme le « Confirmer » de Dreamina, et le note au journal du worker.
const ATTESTATION = /creating content from images and files|créer du contenu à partir d.images et de fichiers|necessary rights to any images or files|droits nécessaires (sur|pour) (les|toutes les) images/i
// Nom réel (relevé du 30/10) : « Agree (Closes dialog box and gives disclaimer) » : précision entre parenthèses permise
const ACCEPTER = /^\s*(agree|i agree|accepter|j.accepte|d.accord)\s*(\(.*\))?\s*$/i
// 30/10 (1er essai réel) : fenêtres de nouveautés au hasard (« Let Gemini personalize using memory »,
// No thanks / Allow) qui couvrent la page. Règle : REFUSER ou fermer (choix le plus discret), jamais
// accepter ; une fenêtre sans bouton de refus reste, et le parcours échoue avec sa capture.
const REFUSER = /^\s*(no,? thanks|not now|maybe later|non,? merci|pas maintenant|plus tard|later|dismiss|skip|ignorer|close|fermer|got it|j.ai compris|compris)\s*(\(.*\))?\s*$/i
// 30/10 (relevé du vrai site) : aperçu d'une pièce = bouton « close attachment »
const RETIRER = /remove (file|image|attachment)|close attachment|retirer (le fichier|l.image|la pièce)|supprimer (le fichier|l.image|la pièce jointe)|fermer (la )?pièce jointe/i

export const VERBE_IMAGE = /^\s*(génère|genere|générer|crée|cree|créer|dessine|fais une image|generate|create|draw|make an image)\b/i

/** « Génère une image : … » en tête, sauf si la demande commence déjà par un verbe de création. */
export const promptImage = (texte) => (VERBE_IMAGE.test(texte) ? texte : `Génère une image : ${texte}`)

export const GEMINI = {
  nom: 'gemini',
  libelle: 'Gemini',
  type: 'image',
  url: 'https://gemini.google.com/app', // repli : l'adresse du catalogue config/ia-web.json prime
  // La conversation n'est pas la page du site : bulle du prompt, réponse du modèle.
  horsDefi: 'model-response, user-query',
  hotesConnexion: /(^|\.)accounts\.google\.com$/i,
  prompt: promptImage,
  champ: (page) => page.getByRole('textbox', { name: CHAMP })
    .or(page.locator('rich-textarea [contenteditable="true"]')).first(),
  telecharger: (page) => page.getByRole('button', { name: DL }),
  enCours: (page) => page.getByRole('button', { name: STOP }),
  connexion: (page) => page.getByRole('link', { name: CONNEXION }).or(page.getByRole('button', { name: CONNEXION })),
  reponse: (page) => page.locator('model-response').last(),
  images: (page) => page.locator('model-response').last().locator('img'),

  /** Modèle du catalogue choisi dans le menu des modèles de Gemini (voir commun.mjs). */
  choisirModele: (page, modele, outils) => choisirModeleMenu(page, modele, outils),

  /** Pièces jointes (images vérifiées par pieces.mjs) déposées avant la saisie du prompt. */
  joindre: (page, pieces) => joindreFichiers(page, pieces, {
    bouton: (p) => p.getByRole('button', { name: JOINDRE }),
    // 30/10 (vrai site) : le bouton de l'aperçu n'apparaît qu'au survol : compté même masqué
    apercus: (p) => p.getByRole('button', { name: RETIRER, includeHidden: true }),
    attestation: async (p) => (await accepterAttestation(p) ? 'attestation' : await fermerFenetres(p) > 0 ? 'fenetre' : false),
    unParUn: true
  }),

  /** Avant la saisie : fenêtres de nouveautés de Gemini refusées (voir REFUSER). */
  async preparer (page) {
    for (let i = 0; i < 3; i++) {
      await dormir(TEST ? 50 : 900)
      if (!await fermerFenetres(page) && i) break
    }
  }
}

/** Bouton visible de nom `re` posé dans une couche AU-DESSUS de la page (fenêtre, panneau fixe) dont
 * le texte vérifie `texte(t)` ; null sinon. Le balisage des fenêtres de Gemini n'est pas fiable
 * (30/10 : l'attestation n'a pas le rôle « dialog ») : on part du bouton et on remonte. */
async function boutonDeFenetre (page, re, texte) {
  const boutons = page.getByRole('button', { name: re })
  const n = Math.min(await boutons.count().catch(() => 0), 8)
  for (let i = 0; i < n; i++) {
    const b = boutons.nth(i)
    if (!await b.isVisible().catch(() => false)) continue
    // Le conteneur le PLUS HAUT qui ressemble à une fenêtre (30/10 : le premier trouvé était la rangée
    // de boutons « mat-dialog-actions », sans le titre de la fenêtre)
    const fenetre = await b.evaluate((el) => {
      let texte = null
      for (let p = el.parentElement, k = 0; p && p !== document.body && k < 15; p = p.parentElement, k++) {
        const role = p.getAttribute('role') || ''
        const classe = typeof p.className === 'string' ? p.className : ''
        if (/^(dialog|alertdialog)$/.test(role) || p.tagName === 'MAT-DIALOG-CONTAINER' || /dialog|overlay|popup|modal/i.test(classe) ||
            getComputedStyle(p).position === 'fixed') {
          texte = (p.innerText || '').slice(0, 1500)
        }
      }
      return texte
    }).catch(() => null)
    if (fenetre !== null && texte(fenetre)) return { bouton: b, texte: fenetre }
  }
  return null
}

/** Fenêtre d'attestation des fichiers (voir ATTESTATION) : « Agree » cliqué ; rend vrai si elle était là. */
export async function accepterAttestation (page) {
  const vu = await boutonDeFenetre(page, ACCEPTER, (t) => ATTESTATION.test(t))
  if (!vu) return false
  await cliquer(page, vu.bouton, { timeout: 5000 })
  note('Gemini : attestation « Creating content from images and files » acceptée (décision du 30/10)')
  await pause(PAUSE_COURTE_MS)
  return true
}

/** Fenêtres de Gemini qui couvrent la page (sauf l'attestation des fichiers) : refusées ou fermées.
 * Rend le nombre de fenêtres fermées. */
export async function fermerFenetres (page) {
  let n = 0
  for (let k = 0; k < 3; k++) {
    const vu = await boutonDeFenetre(page, REFUSER, (t) => !ATTESTATION.test(t))
    if (!vu) break
    const nomBouton = (await vu.bouton.innerText().catch(() => '')).trim() || await vu.bouton.getAttribute('aria-label').catch(() => '')
    await cliquer(page, vu.bouton, { timeout: 5000 })
    note(`Gemini : fenêtre « ${vu.texte.trim().split('\n')[0].slice(0, 80)} » refusée (« ${nomBouton} »)`)
    await pause(PAUSE_COURTE_MS)
    n++
  }
  return n
}
