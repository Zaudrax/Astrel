// Médias riches dans le fil du HUD (22/10, plan P26 lot 1b, demande de l'utilisateur : « dans le chat
// tu dois pouvoir afficher tout, pdf, image, vidéo… »). Le cœur publie un DESCRIPTEUR par fichier
// (astrel/medias.py) : {id, kind, name, mime, size, w, h, pages, open_only, path} ; le contenu ne
// vient que de /api/media/<id> (copie vérifiée, type lu dans les octets, CSP sandbox).
//
// Règles : nœuds DOM et textContent SEULEMENT (nom, légende, chemin = données, parfois venues d'un
// mail) ; id validé par MEDIA_ID avant toute adresse ; rien n'est chargé du dehors ; miniatures
// paresseuses ; audio sans préchargement ; vidéo créée AU CLIC sur « Lire », jamais d'autolecture ; UN
// seul lecteur vivant (lancer l'un arrête l'autre, la vidéo arrêtée redevient une carte) ; tant
// qu'un son ou une vidéo joue, le micro est retenu (commande `media_playing`, question 4 du plan).
// Les fonctions pures (adresses, nettoyage, tailles) sont testées par hud/tests/medias.test.mjs.

import { icon, setLabel } from "./icons.js";
import { locale, t } from "./i18n.js";
import { openImage } from "./viewer.js";

export const MEDIA_ID = /^[0-9a-f]{24}$/;
export const KINDS = ["image", "pdf", "audio", "video"];
export const MAX_ITEMS = 10;
const KIND_ICON = { image: "image", pdf: "plans", audio: "volume", video: "play" };

/** Adresse d'un média publié, "" si l'id n'est pas un id du cœur. `variant` : "" (le fichier),
 *  "thumb" (miniature d'image) ou "page" (page `n` d'un PDF, à partir de 1). */
export function mediaUrl(id, variant = "", n = 1) {
  if (typeof id !== "string" || !MEDIA_ID.test(id)) return "";
  const base = `/api/media/${id}`;
  if (variant === "thumb") return `${base}?v=thumb`;
  if (variant === "page") return Number.isInteger(n) && n >= 1 && n <= 2000 ? `${base}?v=page&n=${n}` : "";
  return base;
}

const num = (v) => (Number.isFinite(v) && v >= 0 ? Math.floor(v) : null);
const text = (v, max) => (typeof v === "string" ? v.replace(/[\u0000-\u001f\u007f‪-‮⁦-⁩]/g, " ").trim().slice(0, max) : "");

/** Descripteur reçu → objet sûr, ou null (id ou genre inconnus). */
export function cleanItem(d) {
  if (!d || typeof d !== "object" || !mediaUrl(d.id) || !KINDS.includes(d.kind)) return null;
  return {
    id: d.id, kind: d.kind, name: text(d.name, 160) || t("att.file"), size: num(d.size),
    w: num(d.w), h: num(d.h), pages: num(d.pages), openOnly: !!d.open_only, path: text(d.path, 400),
  };
}

/** Liste de descripteurs → objets sûrs, 10 au plus. */
export function cleanItems(list) {
  return (Array.isArray(list) ? list : []).map(cleanItem).filter(Boolean).slice(0, MAX_ITEMS);
}

/** Taille lisible : « 4,3 Mo » en français, « 4.3 MB » en anglais. */
export function formatSize(bytes, loc = "fr-FR") {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  const fr = !String(loc).startsWith("en");
  const units = fr ? ["o", "Ko", "Mo", "Go"] : ["B", "KB", "MB", "GB"];
  let v = bytes;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) { v /= 1024; u++; }
  const digits = u === 0 || v >= 10 ? 0 : 1;
  return `${new Intl.NumberFormat(loc, { maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(v)} ${units[u]}`;
}

// ------------------------------------------------------------------ liens avec main.js

let hooks = { command: async () => ({ ok: false }), toast: () => null, onLayout: () => {} };

/** main.js donne `command` (conn.js), `toast` et `onLayout` (recoller le fil en bas). */
export function initMedias(h) {
  hooks = { ...hooks, ...h };
}

const el = (tag, cls, txt) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  return e;
};

// ------------------------------------------------------------------ lecteur unique, micro retenu

let live = null;          // { media, stop } : le seul lecteur qui a le droit de jouer
const playing = new Set(); // éléments en lecture
let micHeld = false;

function syncMic() {
  const want = playing.size > 0;
  if (want === micHeld) return;
  micHeld = want;
  Promise.resolve(hooks.command("media_playing", { playing: want })).catch(() => {});
}

/** 23/10 (relecture P26) : le cœur relâche le micro retenu quand la connexion tombe ; après une
 *  reconnexion (`hello`), un son ou une vidéo qui joue encore le redemande. Rend vrai s'il l'a fait. */
export function resendMicHold() {
  if (!micHeld) return false;
  Promise.resolve(hooks.command("media_playing", { playing: true })).catch(() => {});
  return true;
}

/** Suit un lecteur (exporté pour les tests : un faux élément suffit). */
export function watch(media, stop) {
  media.addEventListener("play", () => {
    if (live && live.media !== media) live.stop();
    live = { media, stop };
    playing.add(media);
    syncMic();
  });
  for (const ev of ["pause", "ended", "emptied", "error"]) {
    media.addEventListener(ev, () => { playing.delete(media); syncMic(); });
  }
}

/** Le lecteur vivant n'est plus visible (autre canal ouvert, message retiré) : il s'arrête,
 *  sinon un son caché continuerait de jouer et de retenir le micro. */
export function stopHiddenMedia() {
  if (!live) return;
  const m = live.media;
  // message du fil caché (autre canal) ou retiré ; pas checkVisibility : faux pour un message
  // simplement hors de l'écran (content-visibility du fil)
  if (m.isConnected && !m.closest(".msg.hidden, .hidden")) return;
  live.stop();
  live = null;
}

async function openMedia(item) {
  const r = await hooks.command("media_open", { media_id: item.id });
  if (!r?.ok) hooks.toast(t("media.open_failed"), "warning");
}

function openBtn(item) {
  const b = el("button", "media-open", t("media.open"));
  b.type = "button";
  b.title = t("media.open_title", { name: item.name });
  b.onclick = (e) => { e.stopPropagation(); openMedia(item); };
  return b;
}

function head(item) {
  const h = el("div", "media-head");
  const name = el("span", "media-name", item.name);
  name.title = item.name;
  h.append(icon(KIND_ICON[item.kind], "lead"), name);
  // Avancé seulement (styles.css, bloc P26) : taille et chemin d'origine.
  const size = formatSize(item.size, locale());
  if (size) h.append(el("span", "media-size", size));
  if (item.path) {
    const p = el("span", "media-path", item.path);
    p.title = item.path;
    h.append(p);
  }
  return h;
}

// ------------------------------------------------------------------ cartes

function thumb(item, letter = "") {
  const b = el("button", "media-thumb");
  b.type = "button";
  b.title = t("media.view_title", { name: item.name });
  b.setAttribute("aria-label", t("media.view_aria", { name: item.name }));
  const img = el("img");
  img.alt = item.name;
  img.loading = "lazy";
  img.decoding = "async";
  img.draggable = false;
  if (item.w && item.h) { img.width = item.w; img.height = item.h; } // place gardée : le fil ne saute pas
  img.addEventListener("load", () => hooks.onLayout(), { once: true });
  // Pas de miniature (image abîmée, trop de pixels pour le cœur, copie purgée : 404) : carte
  // « pas d'aperçu » + Ouvrir, la lettre de la réponse gardée dans une galerie de question.
  img.addEventListener("error", () => { b.replaceWith(fileCard(item, t("media.no_preview"), letter)); hooks.onLayout(); },
    { once: true });
  img.src = mediaUrl(item.id, "thumb");
  b.append(img);
  if (letter) b.append(el("span", "media-letter", letter));
  b.onclick = (e) => { e.stopPropagation(); openImage(mediaUrl(item.id), item.name); };
  return b;
}

function fileCard(item, note, letter = "") {
  const c = el("div", `media-card media-${item.kind} media-only`);
  if (letter) c.append(el("span", "media-letter", letter));
  c.append(head(item));
  if (note) c.append(el("div", "media-note", note));
  c.append(openBtn(item));
  return c;
}

function pdfCard(item) {
  if (!item.pages) return fileCard(item, t("media.no_preview"));
  const c = el("div", "media-card media-pdf");
  // La page est un bouton : clic (ou Entrée) = cette page en grand dans la visionneuse (rendu
  // 1100 px du cœur), la vignette du fil restant compacte.
  const view = el("button", "media-page");
  view.type = "button";
  const img = el("img");
  img.alt = t("media.page_alt", { name: item.name, n: 1 });
  img.loading = "lazy";
  img.decoding = "async";
  img.draggable = false;
  const note = el("div", "media-note hidden", t("media.no_preview"));
  img.addEventListener("load", () => { note.classList.add("hidden"); img.classList.remove("hidden"); hooks.onLayout(); });
  img.addEventListener("error", () => { note.classList.remove("hidden"); img.classList.add("hidden"); });
  view.append(img, note);
  const nav = el("div", "media-nav");
  const prev = el("button", "media-step");
  const next = el("button", "media-step");
  prev.type = next.type = "button";
  prev.append(icon("chevron", "flip"));
  next.append(icon("chevron"));
  prev.setAttribute("aria-label", t("media.prev"));
  next.setAttribute("aria-label", t("media.next"));
  prev.title = t("media.prev");
  next.title = t("media.next");
  const at = el("span", "media-at");
  at.setAttribute("aria-live", "polite");
  let page = 1;
  const show = (n) => {
    page = Math.min(Math.max(1, n), item.pages);
    img.src = mediaUrl(item.id, "page", page);
    img.alt = t("media.page_alt", { name: item.name, n: page });
    view.title = t("media.view_title", { name: img.alt });
    view.setAttribute("aria-label", view.title);
    at.textContent = t("media.page_at", { n: page, total: item.pages });
    prev.disabled = page <= 1;
    next.disabled = page >= item.pages;
  };
  view.onclick = (e) => {
    e.stopPropagation();
    if (!img.classList.contains("hidden")) openImage(mediaUrl(item.id, "page", page), img.alt);
  };
  prev.onclick = (e) => { e.stopPropagation(); show(page - 1); };
  next.onclick = (e) => { e.stopPropagation(); show(page + 1); };
  nav.append(prev, at, next, openBtn(item));
  c.append(head(item), view, nav);
  show(1);
  return c;
}

function audioCard(item) {
  const c = el("div", "media-card media-audio");
  const a = el("audio");
  a.controls = true;
  a.preload = "none";
  a.src = mediaUrl(item.id);
  a.setAttribute("aria-label", t("media.audio_aria", { name: item.name }));
  watch(a, () => a.pause());
  c.append(head(item), a, openBtn(item));
  return c;
}

function videoCard(item) {
  const c = el("div", "media-card media-video");
  const box = el("div", "media-video-box");
  const play = el("button", "media-play");
  play.type = "button";
  setLabel(play, "play", t("media.play"), "lead");
  play.title = t("media.play_title", { name: item.name });
  const stopVideo = (v) => {
    v.pause();
    playing.delete(v);
    v.removeAttribute("src");
    v.load(); // libère le décodeur (mémoire)
    box.replaceChildren(play);
    syncMic();
    hooks.onLayout();
  };
  play.onclick = (e) => {
    e.stopPropagation();
    const v = el("video", "media-video-el");
    v.controls = true;
    v.preload = "metadata";
    v.playsInline = true;
    v.setAttribute("aria-label", t("media.video_aria", { name: item.name }));
    watch(v, () => stopVideo(v));
    // codec que ce Chrome ne lit pas (MKV, HEVC…) : la raison, et « Ouvrir » reste à côté
    v.addEventListener("error", () => box.replaceChildren(el("div", "media-note", t("media.cant_play")), play), { once: true });
    v.src = mediaUrl(item.id);
    box.replaceChildren(v);
    v.play().catch(() => {}); // clic de l'utilisateur : la lecture part de lui, jamais seule
    v.focus({ preventScroll: true });
    hooks.onLayout();
  };
  box.append(play);
  c.append(head(item), box, openBtn(item));
  return c;
}

/** Bloc de médias d'un message : galerie des images, puis une carte par PDF, son, vidéo. */
export function mediaList(list, title = "") {
  const items = cleanItems(list);
  if (!items.length) return null;
  const box = el("div", "media-list");
  const cap = text(title, 200);
  if (cap) box.append(el("div", "media-title", cap));
  const images = items.filter((i) => i.kind === "image" && !i.openOnly);
  if (images.length) {
    const grid = el("div", `media-gallery n${Math.min(images.length, 4)}`);
    for (const i of images) grid.append(thumb(i));
    box.append(grid);
  }
  for (const i of items) {
    if (i.kind === "image" && !i.openOnly) continue;
    if (i.openOnly) box.append(fileCard(i, t("media.too_big")));
    else if (i.kind === "pdf") box.append(pdfCard(i));
    else if (i.kind === "audio") box.append(audioCard(i));
    else box.append(videoCard(i));
  }
  return box;
}

/** Galerie d'une question à choix : une miniature par réponse illustrée, avec sa lettre. */
export function choiceGallery(entries, badgeOf) {
  const shown = entries.map((o, i) => [cleanItem(o.image), i]).filter(([d]) => d && d.kind === "image" && !d.openOnly);
  if (!shown.length) return null;
  const grid = el("div", `media-gallery choice-gallery n${Math.min(shown.length, 4)}`);
  for (const [d, i] of shown) grid.append(thumb(d, badgeOf(i)));
  return grid;
}
