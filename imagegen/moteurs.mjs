// Sites de génération d'IMAGES connus du worker (plan P14 ; P40 lot A3 : un module par site dans
// sites/). Sélecteurs PAR RÔLE et libellé accessible (FR + EN), jamais par classe CSS générée :
// une refonte du site casse quand même le worker (risque accepté, plan P14 étape 10), mais
// l'erreur dit « page changée » avec une capture dans runtime/imagegen/debug/.
//
// Chaque site suit l'INTERFACE décrite en tête de noyau.mjs, plus ce que lit le parcours des
// images (en tête de worker.mjs). Le défi anti-robot est cherché par le noyau (`captcha`).
//   - gemini  : compte Google ; choix du modèle (catalogue), pièces jointes ;
//   - chatgpt : compte OpenAI ; choix du modèle (catalogue), pièces jointes ;
//   - bing    : SANS compte, profil anonyme (alias réservé), 4 variantes, ni pièce ni modèle.
// Le cœur (astrel/images_ia.MOTEURS_PRETS) décide lesquels sont réellement proposés.

import { GEMINI } from './sites/gemini.mjs'
import { CHATGPT } from './sites/chatgpt.mjs'
import { BING } from './sites/bing.mjs'

export { classer } from './sites/commun.mjs'

export const MOTEURS = {
  gemini: GEMINI,
  chatgpt: CHATGPT,
  bing: BING
}
