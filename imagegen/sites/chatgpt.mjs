// ChatGPT (plan P40 lot A3) : même parcours « conversation » que Gemini (worker.mjs).
//
// ⚠️ Aucun sélecteur n'a été essayé sur le vrai site (consigne : aucun essai réel avant le pilote
// avec l'utilisateur) ; ils suivent la procédure du coffre et les libellés publics connus, par RÔLE et
// libellé accessible (FR + EN), plus deux repères stables : l'id du champ (#prompt-textarea) et
// l'attribut de rôle des messages (data-message-author-role). Une page changée = échec `page`
// avec capture. Compte OpenAI obligatoire (fournisseur `openai` du catalogue) : la page d'accueil
// d'un visiteur (bouton « Log in ») vaut `session`.
//
// Quota gratuit : « You've hit the free plan limit for image generation requests … resets in
// 23 hours and 59 minutes » → moteurs.classer : quota, reprise « +23h ». Fenêtre « 24h » du
// catalogue (glissante ou fixe : non établi, plan P40).
//
// Modèles : du CATALOGUE seulement (voir sites/gemini.mjs) ; `defaut` ne touche à rien.

import { choisirModeleMenu, joindreFichiers } from './commun.mjs'
import { promptImage } from './gemini.mjs'

const CHAMP = /message chatgpt|ask anything|poser une question|posez une question|demandez|envoyer un message|send a message/i
const DL = /^\s*(download( this image)?|télécharger( cette image| l.image)?)\s*$/i
const STOP = /stop (streaming|generating|response)|arrêter (la diffusion|la génération|la réponse)/i
const CONNEXION = /^\s*(log in|sign in|se connecter|connexion)\s*$/i
const JOINDRE = /add photos|attach files?|upload (files?|images?)|joindre des fichiers|ajouter des (photos|fichiers)|importer des fichiers/i
const RETIRER = /remove (file|image|attachment)|retirer (le fichier|l.image|la pièce)|supprimer (le fichier|l.image)/i

const ASSISTANT = '[data-message-author-role="assistant"]'

export const CHATGPT = {
  nom: 'chatgpt',
  libelle: 'ChatGPT',
  type: 'image',
  url: 'https://chatgpt.com/', // repli : l'adresse du catalogue config/ia-web.json prime
  // La conversation n'est pas la page du site : messages de l'utilisateur et de l'assistant.
  horsDefi: '[data-message-author-role]',
  hotesConnexion: /(^|\.)(auth\.openai\.com|auth0\.openai\.com|login\.openai\.com)$/i,
  prompt: promptImage,
  champ: (page) => page.getByRole('textbox', { name: CHAMP })
    .or(page.locator('#prompt-textarea')).filter({ visible: true }).first(),
  // Toute la page (comme Gemini) : le worker compte les boutons avant l'envoi et prend le dernier.
  telecharger: (page) => page.locator(ASSISTANT).getByRole('button', { name: DL })
    .or(page.locator(ASSISTANT).getByRole('link', { name: DL })),
  enCours: (page) => page.getByRole('button', { name: STOP }).or(page.locator('[data-testid="stop-button"]')),
  connexion: (page) => page.getByRole('link', { name: CONNEXION }).or(page.getByRole('button', { name: CONNEXION })),
  reponse: (page) => page.locator(ASSISTANT).last(),
  images: (page) => page.locator(ASSISTANT).last().locator('img'),

  choisirModele: (page, modele, outils) => choisirModeleMenu(page, modele, outils),

  joindre: (page, pieces) => joindreFichiers(page, pieces, {
    bouton: (p) => p.getByRole('button', { name: JOINDRE }),
    apercus: (p) => p.getByRole('button', { name: RETIRER })
  })
}
