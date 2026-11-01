// Visionneuse d'image du HUD (19/10, demande : « une fois l'image collée je ne peux pas
// l'ouvrir en grand quand je clique dessus »). Repose sur openPanel (modal.js) : rôle
// dialog, Échap, croix, clic à côté, focus piégé puis rendu à l'élément d'avant. Fond plus
// sombre, image centrée, réduite à la fenêtre (jamais agrandie au-delà de sa taille).
// Source acceptée : URL de données d'image ou blob:/chemin de l'ORIGINE du HUD ; rien d'autre.

import { openPanel } from "./modal.js";
import { t } from "./i18n.js";

/** L'adresse si elle reste dans le HUD, sinon "" (rien n'est ouvert). */
export function viewerSrc(src) {
  const s = String(src || "");
  if (/^data:image\/(png|jpeg|gif|webp);base64,[a-z0-9+/=]+$/i.test(s)) return s;
  try {
    const u = new URL(s, location.href);
    // l'origine d'une URL blob: est celle de la page qui l'a créée
    if (["blob:", "http:", "https:"].includes(u.protocol) && u.origin === location.origin) return u.href;
  } catch { /* adresse illisible */ }
  return "";
}

/**
 * Ouvre l'image en grand ; rend { close, closed } de openPanel, ou null si la source
 * n'est pas acceptée. name : nom affiché (titre, texte alternatif).
 */
export function openImage(src, name = "image") {
  const safe = viewerSrc(src);
  if (!safe) return null;
  const v = openPanel({ title: name, icon: "image", cls: "viewer" });
  v.box.removeAttribute("aria-labelledby");
  v.box.setAttribute("aria-label", t("viewer.aria", { name }));
  const img = document.createElement("img");
  img.className = "viewer-img";
  img.alt = name;
  img.src = safe;
  img.draggable = false;
  v.body.append(img);
  // un clic dans la fenêtre mais à côté de l'image ferme aussi
  v.body.addEventListener("click", (e) => { if (e.target === v.body) v.close(); });
  // clic sur le fond : openPanel ferme au pointerdown (fond déjà retiré) et rend le focus ;
  // sans ceci, le mousedown qui suit le reprendrait (focus perdu par la barre de saisie)
  const back = v.box.parentElement;
  back.addEventListener("pointerdown", (e) => {
    if (e.target !== back || e.pointerType !== "mouse") return;
    document.addEventListener("mousedown", (m) => m.preventDefault(), { capture: true, once: true });
  });
  return v;
}
