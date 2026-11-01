// Images IA, lot A5 du plan P40 (28/10, demande de l'utilisateur : « finir tout le multicompte / images
// IA »). Trois choses, ZÉRO jeton (aucune ne passe par Claude) :
// 1. Panneau « Images » sous la barre de chat (#image-panel) : prompt, Auto/Manuel (= le réglage
//    `images_mode`), moteur et modèle (= `images_manuel`, mode manuel), pièces jointes, « Générer ».
//    Envoi : `image_generate {prompt, engine, model?, files, channel}` (astrel/core.py), comme l'outil
//    MCP `generate_image` ; la carte de l'image arrive par `image_job` (main.js).
//    Pièces : une par message, `image_piece {name, data}` → chemin dans runtime/attachments, filtré
//    par le cœur (PNG, JPEG, WebP, 5 Mo) ; une autre image est d'abord ré-encodée en JPEG ici.
// 2. Réglages › Images : comptes (ordre du repli, état par moteur, épuisés, plafonds), ajouter,
//    retirer, monter/descendre, connecter (fenêtre Chrome VISIBLE sur le profil, sans pilotage).
//    Commandes `image_compte_*`, réservées à l'utilisateur par le cœur.
// 3. Évènement sticky `ia_etat` (= `images_ia.Images.status()` sans `ok`) : état des comptes, des
//    moteurs (modèles PROPOSÉS, pièces), réglages, connexions ouvertes ; redemandé à chaque `hello`.
// 4. Lot B4 du plan P41 (28/10) : onglet « Vidéo » du même panneau (prompt, modèle et durée = le
//    réglage `video_choix` avec son coût en crédits, une image de départ facultative, « Générer » =
//    `video_generate`) et, dans Réglages › Images IA, les crédits vidéo par compte. Évènement sticky
//    `video_etat` (= `videos_ia.Videos.status()` sans `ok`), redemandé à chaque `hello`. Les comptes
//    sont ceux des images (un compte Google sert Gemini ET Flow).
// 5. 30/10 (demande de l'utilisateur) : onglet Vidéo, FORMAT 16:9 (défaut) ou 9:16 (vertical, Reels/TikTok) et
//    une image de FIN facultative : deux pièces au plus, la 1re = début, la 2e = fin (étiquetées,
//    inversables). `video_generate {…, image, image_fin, format}` ; le cœur revalide tout (une fin exige
//    un début, Dreamina ne prend pas d'image, format inconnu refusé). Le format n'est pas un réglage du
//    cœur : il reste dans ce navigateur (localStorage, facultatif).
// Tout texte venu du cœur passe par textContent (jamais de HTML) et tx() pour l'anglais.

import { command, on } from "./conn.js";
import { openModal } from "./modal.js";
import { icon, setLabel } from "./icons.js";
import { openImage } from "./viewer.js";
import { locale, t, tx } from "./i18n.js";
import { onGroupExtra, openSettings, rerenderSettings } from "./settings.js";

const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

const PIECE_MAX = 5 * 1024 * 1024; // = ia_web.PIECE_MAX_OCTETS
const PIECE_TYPES = /^image\/(png|jpeg|webp)$/;
const PIECE_SIDE = 2048; // ré-encodage d'une image d'un autre type (ou trop lourde) : côté long
const PREP_WAIT_MS = 30000;
const ALIAS = /^[a-z0-9][a-z0-9_-]{0,31}$/; // = images_ia.ALIAS (le cœur revalide)
const VIDEO_FORMATS = ["16:9", "9:16"]; // = videos_ia.FORMATS, défaut d'abord
const FORMAT_STORE = "astrel-video-format";
const VIDEO_PIECES_MAX = 2; // image de début, puis image de fin
// Sites vidéo qui prennent des images (catalogue config/ia-web.json, `pieces`) quand le cœur ne le dit pas
// dans ses choix (`pieces` d'un choix, s'il vient un jour, prime) : Google Flow seul (mode Frames).
const VIDEO_SITES_IMAGES = new Set(["flow"]);

let etat = null; // dernier `ia_etat` (ou réponse de `image_status`)
let toast = () => {};
let channel = () => "";
let reglageMode = ""; // valeur du réglage `images_mode` d'après le dernier `settings` reçu
let reglageManuel = ""; // `images_manuel` (« service/modèle »)
let reglageOrdre = ""; // `images_ordre` (« gemini,chatgpt,bing »)
let kind = "image"; // onglet du panneau : « image » ou « video »
let vetat = null; // dernier `video_etat` (ou réponse de `video_status`)
let reglageVideo = ""; // `video_choix` (« service/modèle/secondes »)
let videoFormat = readFormat(); // « 16:9 » ou « 9:16 »
const pieces = []; // { id, name, url (miniature), path, pending, ready }
let result = null; // dernier résultat d'une action sur les comptes {text, level}
const busy = new Set(); // actions sur les comptes en cours (« alias|action »)
// Réglage changé ici et pas encore confirmé : un `settings` plus ancien (réponse au changement précédent)
// ne doit pas le défaire à l'écran pendant l'aller-retour.
const inflight = new Set();
let panel = null; // éléments du panneau

// ------------------------------------------------------------------ état

/** Libellé d'un modèle (carte d'image) ; « » pour le défaut du site. */
export function modelLabel(service, id) {
  if (!id || id === "defaut") return "";
  const s = (etat?.services || []).find((x) => x.service === service);
  const m = (s?.modeles || []).find((x) => x.id === id);
  return m ? tx(String(m.libelle || id)) : String(id);
}

const services = () => (Array.isArray(etat?.services) ? etat.services : []).filter((s) => s && s.pret !== false);
const service = (id) => services().find((s) => s.service === id) || null;
const serviceName = (id) => tx(String(service(id)?.libelle || id || ""));

function mode() {
  const m = reglageMode || etat?.reglages?.mode;
  return m === "manuel" ? "manuel" : "auto";
}

function manual() {
  const [s, m] = String(reglageManuel || "").split("/");
  if (s && service(s)) return { service: s, modele: m || "defaut" };
  const r = etat?.reglages || {};
  return { service: service(r.service) ? r.service : services()[0]?.service || "", modele: r.modele || "defaut" };
}

function setEtat(ev) {
  if (!ev || typeof ev !== "object") return;
  const { ok, type, replay, ts, ...rest } = ev; // eslint-disable-line no-unused-vars
  etat = rest;
  paintPanel();
  rerenderSettings();
}

async function ask() {
  const r = await command("image_status");
  if (r && r.ok !== false && Array.isArray(r.services)) setEtat(r);
}

function setVideoEtat(ev) {
  if (!ev || typeof ev !== "object") return;
  const { ok, type, replay, ts, ...rest } = ev; // eslint-disable-line no-unused-vars
  vetat = rest;
  paintPanel();
  rerenderSettings();
}

async function askVideo() {
  const r = await command("video_status");
  if (r && r.ok !== false && Array.isArray(r.choix)) setVideoEtat(r);
}

const videoChoices = () => (Array.isArray(vetat?.choix) ? vetat.choix : []);
const videoChoice = () => {
  const v = reglageVideo || vetat?.reglage || "";
  return videoChoices().some((c) => c.valeur === v) ? v : videoChoices()[0]?.valeur || "";
};
const currentVideoChoice = () => videoChoices().find((c) => c.valeur === videoChoice()) || null;
// Le site du choix prend-il une image de début (et de fin) ? `pieces` du cœur s'il le donne, sinon le catalogue.
const takesImages = (c) => (typeof c?.pieces === "boolean" ? c.pieces : VIDEO_SITES_IMAGES.has(String(c?.service || "")));

// Format de la vidéo : gardé dans ce navigateur seulement (stockage absent ou refusé : 16:9).
function readFormat() {
  let v = "";
  try { v = globalThis.localStorage?.getItem(FORMAT_STORE) || ""; } catch { /* sans stockage */ }
  return VIDEO_FORMATS.includes(v) ? v : VIDEO_FORMATS[0];
}

function pickFormat(v) {
  if (!VIDEO_FORMATS.includes(v)) return;
  videoFormat = v;
  try { globalThis.localStorage?.setItem(FORMAT_STORE, v); } catch { /* sans stockage */ }
  paintPanel();
}

// ------------------------------------------------------------------ panneau « Images »

export function initImages(opts = {}) {
  toast = opts.toast || toast;
  channel = opts.channel || channel;
  on("ia_etat", setEtat);
  on("video_etat", setVideoEtat);
  on("hello", () => { ask(); askVideo(); });
  on("settings", (ev) => {
    for (const g of ev?.groups || []) {
      for (const it of g.items || []) {
        if (inflight.has(it.key)) continue;
        if (it.key === "images_mode") reglageMode = String(it.value || "");
        if (it.key === "images_manuel") reglageManuel = String(it.value || "");
        if (it.key === "images_ordre") reglageOrdre = String(it.value || "");
        if (it.key === "video_choix") reglageVideo = String(it.value || "");
      }
    }
    paintPanel();
  });
  onGroupExtra("images", settingsBlock);
  buildPanel();
  const btn = document.querySelector("#images-btn");
  if (btn) btn.onclick = () => toggle();
}

function toggle(show) {
  if (!panel) return;
  const open = show ?? panel.root.classList.contains("hidden");
  panel.root.classList.toggle("hidden", !open);
  const btn = document.querySelector("#images-btn");
  btn?.classList.toggle("on", open);
  btn?.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) {
    if (!etat) ask();
    if (!vetat) askVideo();
    paintPanel();
    panel.prompt.focus();
  }
}

function buildPanel() {
  const root = document.querySelector("#image-panel");
  if (!root) return;
  const head = el("div", "ip-head");
  const title = el("span", "ip-title");
  // Onglet Image / Vidéo (lot B4) : même panneau, même prompt, mêmes pièces jointes.
  const kinds = el("div", "ip-kind");
  kinds.setAttribute("role", "radiogroup");
  kinds.setAttribute("aria-label", t("videos.kind_aria"));
  const kindBtns = {};
  for (const k of ["image", "video"]) {
    const b = el("button", "", t(`videos.kind_${k}`));
    b.type = "button";
    b.setAttribute("role", "radio");
    b.onclick = () => setKind(k);
    b.onkeydown = (e) => {
      if (e.altKey || e.ctrlKey || e.metaKey || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) return;
      e.preventDefault();
      const next = k === "image" ? "video" : "image";
      setKind(next);
      kindBtns[next].focus();
    };
    kindBtns[k] = b;
    kinds.append(b);
  }
  const seg = el("div", "ip-mode");
  seg.setAttribute("role", "radiogroup");
  seg.setAttribute("aria-label", t("images.mode_aria"));
  const segs = {};
  for (const m of ["auto", "manuel"]) {
    const b = el("button", "", t(`images.mode_${m}`));
    b.type = "button";
    b.setAttribute("role", "radio");
    b.title = t(`images.mode_${m}_title`);
    b.onclick = () => setMode(m);
    b.onkeydown = (e) => { // radiogroup : les flèches passent d'un choix à l'autre
      // Alt, Ctrl ou Méta + flèche appartiennent au navigateur et au HUD (même garde que main.js, onglets)
      if (e.altKey || e.ctrlKey || e.metaKey || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) return;
      e.preventDefault();
      const next = m === "auto" ? "manuel" : "auto";
      setMode(next);
      segs[next].focus();
    };
    segs[m] = b;
    seg.append(b);
  }
  const gear = el("button", "close ip-settings");
  gear.type = "button";
  gear.append(icon("settings"));
  gear.title = t("images.accounts_title");
  gear.setAttribute("aria-label", t("images.accounts_title"));
  gear.onclick = () => openSettings(kind === "video" ? "video_choix" : "images_mode");
  const x = el("button", "close ip-close");
  x.type = "button";
  x.append(icon("x"));
  x.title = t("common.close");
  x.setAttribute("aria-label", t("common.close"));
  x.onclick = () => toggle(false);
  head.append(title, kinds, seg, gear, x);

  const prompt = el("textarea", "ip-prompt");
  prompt.rows = 2;
  prompt.maxLength = 2000; // = images_ia.PROMPT_MAX
  prompt.placeholder = t("images.prompt_placeholder");
  prompt.setAttribute("aria-label", t("images.prompt_aria"));
  prompt.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
    else if (e.key === "Escape") { e.preventDefault(); toggle(false); document.querySelector("#ask")?.focus(); }
  });

  const row = el("div", "ip-row");
  const engine = el("select", "ip-select ip-engine");
  engine.setAttribute("aria-label", t("images.engine"));
  engine.onchange = () => pickManual(engine.value, "");
  const model = el("select", "ip-select ip-model");
  model.setAttribute("aria-label", t("images.model"));
  model.onchange = () => pickManual(engine.value, model.value);
  // 29/10 (demande de l'utilisateur) : modèle puis durée, comme les images ; ensemble = le réglage `video_choix`
  const vchoice = el("select", "ip-select ip-video-choice");
  vchoice.setAttribute("aria-label", t("images.model"));
  vchoice.onchange = () => pickVideoModel(vchoice.value);
  const vlength = el("select", "ip-select ip-video-length");
  vlength.setAttribute("aria-label", t("videos.length"));
  vlength.onchange = () => pickVideo(vlength.value);
  // 30/10 : 16:9 par défaut, 9:16 pour les Reels et TikTok
  const vformat = el("select", "ip-select ip-video-format");
  vformat.setAttribute("aria-label", t("videos.format"));
  vformat.append(...VIDEO_FORMATS.map((f) => option(f, t(f === "9:16" ? "videos.format_portrait" : "videos.format_landscape"))));
  vformat.onchange = () => pickFormat(vformat.value);
  const attach = el("button", "round ip-attach");
  attach.type = "button";
  attach.append(icon("clip"));
  attach.title = t("images.attach_title");
  attach.setAttribute("aria-label", t("images.attach_title"));
  const input = el("input");
  input.type = "file";
  input.multiple = true;
  input.accept = "image/png,image/jpeg,image/webp,image/*";
  input.hidden = true;
  attach.onclick = () => input.click();
  input.onchange = () => { addPieces([...input.files]); input.value = ""; };
  const go = el("button", "btn ip-send");
  go.type = "button";
  setLabel(go, "sparkles", t("images.generate"), "lead");
  go.onclick = () => send();
  const fields = { engine: labelled(t("images.engine"), engine), model: labelled(t("images.model"), model),
    video: labelled(t("images.model"), vchoice), vlength: labelled(t("videos.length"), vlength),
    vformat: labelled(t("videos.format"), vformat) };
  row.append(fields.engine, fields.model, fields.video, fields.vlength, fields.vformat, attach, input, go);

  const list = el("div", "ip-pieces hidden");
  const note = el("div", "ip-note");
  note.setAttribute("role", "status");
  root.replaceChildren(head, prompt, row, list, note);
  // Glisser-déposer SUR le panneau : les images vont au panneau, pas à la barre de chat.
  root.addEventListener("dragover", (e) => { if ([...(e.dataTransfer?.types || [])].includes("Files")) e.preventDefault(); });
  root.addEventListener("drop", (e) => {
    if (!e.dataTransfer?.files?.length) return;
    e.preventDefault();
    e.stopPropagation();
    document.body.classList.remove("dropping");
    addPieces([...e.dataTransfer.files]);
  });
  prompt.addEventListener("paste", (e) => {
    const files = [...(e.clipboardData?.files || [])];
    if (!files.length) return;
    e.preventDefault();
    addPieces(files);
  });
  panel = { root, title, prompt, engine, model, vchoice, vlength, vformat, attach, go, list, note, seg, segs, kindBtns, fields };
  paintPanel();
}

function setKind(k) {
  if (k === kind) return;
  kind = k;
  if (k === "video" && !vetat) askVideo();
  paintPanel();
}

// Choix vidéo découpé (29/10) : « service/modèle » d'un choix, et ceux d'un même modèle (ses durées).
// Un cœur d'avant n'envoie que `valeur` : les parties en sont relues.
const videoModelKey = (c) => (c.service && c.modele ? `${c.service}/${c.modele}` : String(c.valeur || "").split("/").slice(0, 2).join("/"));
const videoLengths = (key) => videoChoices().filter((c) => videoModelKey(c) === key);

// Autre modèle : même durée s'il la propose, sinon sa première.
function pickVideoModel(key) {
  const cur = videoChoices().find((c) => c.valeur === videoChoice());
  const lengths = videoLengths(key);
  const same = lengths.find((c) => cur && Number(c.duree) === Number(cur.duree));
  if (lengths.length) pickVideo((same || lengths[0]).valeur);
}

// Modèle et durée de la vidéo : c'est le réglage `video_choix` (une seule vérité avec Réglages › Images IA
// et les demandes faites à la voix) ; seuls les choix PROPOSÉS par le cœur, coût en crédits compris.
async function pickVideo(value) {
  if (!videoChoices().some((c) => c.valeur === value) || value === videoChoice()) return;
  const before = reglageVideo;
  reglageVideo = value;
  paintPanel();
  inflight.add("video_choix");
  const r = await command("settings_set", { key: "video_choix", value });
  inflight.delete("video_choix");
  if (r?.ok === false) {
    reglageVideo = before;
    paintPanel();
    toast(t("images.mode_failed", { error: tx(r.error) || t("common.send_failed") }), "error");
  }
}

function labelled(text, control) {
  const lab = el("label", "ip-field");
  lab.append(el("span", "ip-label", text), control);
  return lab;
}

function option(value, text, disabled = false) {
  const o = el("option", "", text);
  o.value = value;
  o.disabled = disabled;
  return o;
}

async function setMode(m) {
  if (m === mode()) return;
  const before = reglageMode;
  reglageMode = m; // tout de suite à l'écran ; le cœur confirme par `settings`
  paintPanel();
  inflight.add("images_mode");
  const r = await command("settings_set", { key: "images_mode", value: m });
  inflight.delete("images_mode");
  if (r?.ok === false) {
    reglageMode = before;
    paintPanel();
    toast(t("images.mode_failed", { error: tx(r.error) || t("common.send_failed") }), "error");
  }
}

// Mode manuel : le moteur et le modèle choisis ici SONT le réglage `images_manuel` (une seule vérité
// avec Réglages › Images et les demandes faites à la voix). Seuls les modèles PROPOSÉS par le cœur.
async function pickManual(s, m) {
  const svc = service(s);
  if (!svc) return;
  const ids = (svc.modeles || []).map((x) => x.id);
  const modele = ids.includes(m) ? m : ids[0] || "defaut";
  const value = `${s}/${modele}`;
  if (value === reglageManuel) return;
  const before = reglageManuel;
  reglageManuel = value;
  paintPanel();
  inflight.add("images_manuel");
  const r = await command("settings_set", { key: "images_manuel", value });
  inflight.delete("images_manuel");
  if (r?.ok === false) {
    reglageManuel = before;
    paintPanel();
    toast(t("images.mode_failed", { error: tx(r.error) || t("common.send_failed") }), "error");
  }
}

function paintPanel() {
  if (!panel) return;
  const video = kind === "video";
  for (const [k, b] of Object.entries(panel.kindBtns)) {
    b.classList.toggle("on", k === kind);
    b.setAttribute("aria-checked", k === kind ? "true" : "false");
    b.tabIndex = k === kind ? 0 : -1;
  }
  setLabel(panel.title, video ? "play" : "image", t(video ? "videos.title" : "images.title"), "lead");
  panel.prompt.placeholder = t(video ? "videos.prompt_placeholder" : "images.prompt_placeholder");
  panel.seg.hidden = panel.fields.engine.hidden = panel.fields.model.hidden = video;
  panel.fields.video.hidden = panel.fields.vlength.hidden = panel.fields.vformat.hidden = !video;
  if (video) paintVideo();
  else paintImage();
  paintPieces();
}

// Onglet Vidéo : choix proposés par le cœur, format, images de début et de fin (deux au plus, sur un site
// qui en prend), crédits restants des comptes.
function paintVideo() {
  const { vchoice, vlength, vformat, attach, go, note } = panel;
  const choices = videoChoices();
  const cur = currentVideoChoice();
  // Modèles groupés par site (Flow, Dreamina) ; puis les durées du modèle choisi, coût compris.
  const groups = new Map();
  for (const c of choices) {
    const key = videoModelKey(c);
    const site = String(c.service_libelle || c.service || "");
    if (!groups.has(site)) groups.set(site, new Map());
    if (!groups.get(site).has(key)) groups.get(site).set(key, String(c.modele_libelle || c.libelle || key));
  }
  vchoice.replaceChildren(...[...groups].map(([site, models]) => {
    const g = el("optgroup");
    g.label = tx(site);
    g.append(...[...models].map(([key, label]) => option(key, tx(label))));
    return g;
  }));
  vchoice.value = cur ? videoModelKey(cur) : "";
  vchoice.disabled = !choices.length;
  const lengths = cur ? videoLengths(videoModelKey(cur)) : [];
  vlength.replaceChildren(...lengths.map((c) => option(c.valeur, tx(String(c.duree_libelle || c.libelle || c.valeur)))));
  vlength.value = cur ? cur.valeur : "";
  vlength.disabled = lengths.length < 2;
  vformat.value = videoFormat;
  // Images : site qui n'en prend pas (Dreamina) = trombone grisé, et Générer aussi s'il y en a déjà
  // (le cœur refuserait) ; plus de deux (ajoutées dans l'onglet Image) = Générer grisé.
  const noImages = !!cur && !takesImages(cur);
  const site = cur ? tx(String(cur.service_libelle || cur.service || "")) : "";
  const others = [...new Set(choices.filter(takesImages).map((c) => tx(String(c.service_libelle || c.service || ""))))];
  const noImagesText = !noImages ? "" : others.length ? t("videos.no_images_other", { site, sites: others.join(", ") })
    : t("videos.no_images", { site });
  attach.disabled = noImages || pieces.length >= VIDEO_PIECES_MAX;
  attach.title = noImages ? noImagesText : t("videos.attach_title");
  attach.setAttribute("aria-label", attach.title);
  go.disabled = !!panel.sending || pieces.length > VIDEO_PIECES_MAX || (noImages && pieces.length > 0) || !choices.length;
  go.setAttribute("aria-busy", panel.sending || pieces.some((p) => p.pending) ? "true" : "false");
  const msgs = [];
  let level = "info";
  if (vetat) {
    const pool = Array.isArray(vetat.comptes) ? vetat.comptes : [];
    const dispo = pool.filter((c) => c.disponible && c.connecte).length;
    msgs.push(t("images.available", { n: dispo, total: pool.length }));
    if (!dispo) level = "warn";
    const credits = pool.filter((c) => c.connecte).map((c) => `${c.compte} ${c.credits_restants}/${c.plafond}`);
    if (credits.length) msgs.push(t("videos.credits", { list: credits.join(", ") }));
    if (vetat.en_attente) msgs.push(t("images.queued", { n: vetat.en_attente }));
    if (vetat.pointe_jusqua) msgs.push(t("videos.peak", { time: when(vetat.pointe_jusqua) }));
  } else msgs.push(t("common.loading"));
  if (pieces.length > VIDEO_PIECES_MAX) { msgs.push(t("videos.two_images")); level = "warn"; }
  if (noImages && pieces.length) { msgs.push(noImagesText); level = "warn"; }
  note.textContent = msgs.join(" · ");
  note.className = `ip-note lv-${level}`;
}

function paintImage() {
  const { engine, model, attach, go, note, segs } = panel;
  const m = mode();
  for (const [k, b] of Object.entries(segs)) {
    b.classList.toggle("on", k === m);
    b.setAttribute("aria-checked", k === m ? "true" : "false");
    b.tabIndex = k === m ? 0 : -1;
  }
  const svcs = services();
  const max = Number(etat?.pieces_max) || 6;
  if (m === "auto") {
    const ordre = (reglageOrdre ? reglageOrdre.split(",") : etat?.reglages?.ordre || svcs.map((s) => s.service))
      .filter((s) => service(s));
    engine.replaceChildren(option("auto", t("images.engine_auto", { order: ordre.map(serviceName).join(" → ") || "…" })));
    model.replaceChildren(option("", t("images.model_auto")));
    engine.disabled = model.disabled = true;
  } else {
    const cur = manual();
    engine.replaceChildren(...svcs.map((s) => option(s.service, tx(String(s.libelle || s.service)))));
    engine.value = cur.service;
    const svc = service(cur.service);
    const models = svc?.modeles?.length ? svc.modeles : [{ id: "defaut", libelle: t("images.model_default") }];
    model.replaceChildren(...models.map((x) => option(x.id, tx(String(x.libelle || x.id)))));
    model.value = models.some((x) => x.id === cur.modele) ? cur.modele : models[0].id;
    engine.disabled = !svcs.length;
    model.disabled = models.length < 2;
  }
  // Pièces : moteur manuel qui n'en prend pas (Bing) = bouton grisé ; en auto, le cœur saute ces moteurs.
  const cur = m === "manuel" ? service(manual().service) : null;
  const noPieces = !!cur && cur.pieces === false;
  attach.disabled = noPieces || pieces.length >= max;
  attach.title = noPieces ? t("images.no_pieces", { engine: serviceName(cur.service) }) : t("images.attach_title");
  attach.setAttribute("aria-label", attach.title);
  const waiting = pieces.some((p) => p.pending);
  go.disabled = !!panel.sending || (noPieces && pieces.length > 0);
  go.setAttribute("aria-busy", panel.sending || waiting ? "true" : "false");
  // Ligne d'état : comptes disponibles (moteur choisi, ou tous en auto), file, liste illisible.
  const msgs = [];
  let level = "info";
  if (etat?.comptes_illisibles) { msgs.push(t("images.accounts_unreadable")); level = "error"; }
  if (etat) {
    const pool = (etat.comptes || []).filter((c) => (m === "auto" ? !!service(c.moteur) : c.moteur === manual().service));
    const dispo = pool.filter((c) => c.disponible && c.connecte).length;
    msgs.push(t("images.available", { n: dispo, total: pool.length }));
    if (!dispo) level = level === "error" ? level : "warn";
    if (etat.en_attente) msgs.push(t("images.queued", { n: etat.en_attente }));
  } else msgs.push(t("common.loading"));
  if (noPieces && pieces.length) { msgs.push(t("images.no_pieces", { engine: serviceName(cur.service) })); level = "warn"; }
  note.textContent = msgs.join(" · ");
  note.className = `ip-note lv-${level}`;
}

function paintPieces() {
  const { list } = panel;
  list.replaceChildren();
  list.classList.toggle("hidden", !pieces.length);
  // Onglet Vidéo : la 1re pièce est l'image de DÉBUT, la 2e celle de FIN (au-delà : rien, Générer est grisé).
  const roles = kind === "video" ? [t("videos.start"), t("videos.end")] : [];
  pieces.forEach((p, i) => {
    const role = roles[i] || "";
    const item = el("div", p.pending ? "att att-pending" : "att");
    if (p.pending) item.setAttribute("aria-busy", "true");
    if (role) item.dataset.role = i === 0 ? "debut" : "fin";
    const view = el("button", "att-view");
    view.type = "button";
    view.title = t("att.view");
    view.setAttribute("aria-label", role ? t("videos.piece_aria", { role, name: p.name }) : t("att.view_aria", { name: p.name }));
    const img = el("img");
    img.src = p.url;
    img.alt = p.name;
    view.append(img);
    view.onclick = () => openImage(p.url, p.name);
    const x = el("button", "close");
    x.type = "button";
    x.append(icon("x"));
    x.title = t("common.remove");
    x.setAttribute("aria-label", t("common.remove"));
    x.onclick = () => removePiece(p);
    if (role) item.append(el("span", "att-role", role));
    item.append(view, el("span", "att-name", p.pending ? `${p.name} · ${t("att.preparing")}` : p.name), x);
    list.append(item);
  });
  if (kind === "video" && pieces.length === VIDEO_PIECES_MAX) {
    const swap = el("button", "btn ip-swap");
    swap.type = "button";
    setLabel(swap, "refresh", t("videos.swap"), "lead");
    swap.title = t("videos.swap");
    swap.setAttribute("aria-label", t("videos.swap"));
    swap.onclick = () => {
      pieces.reverse();
      paintPanel();
      list.querySelector(".ip-swap")?.focus(); // le bouton est recréé : le focus le suit
    };
    list.append(swap);
  }
}

function removePiece(p) {
  const i = pieces.indexOf(p);
  if (i < 0) return;
  pieces.splice(i, 1);
  URL.revokeObjectURL(p.url);
  paintPanel();
}

const toBase64 = (blob) => new Promise((resolve, reject) => {
  const r = new FileReader();
  r.onload = () => resolve(String(r.result).split(",", 2)[1] || "");
  r.onerror = () => reject(r.error);
  r.readAsDataURL(blob);
});

// Image d'un autre type (GIF, BMP, HEIC lisible…) ou de plus de 5 Mo : JPEG, côté long ≤ 2048 px.
async function reencode(file) {
  let bmp;
  try {
    bmp = await createImageBitmap(file);
  } catch {
    throw new Error(t("images.unreadable")); // le message du navigateur est en anglais
  }
  try {
    return await drawJpeg(bmp);
  } finally {
    bmp.close(); // libère l'image décodée tout de suite (plusieurs dizaines de Mo pour une grande photo)
  }
}

async function drawJpeg(bmp) {
  let scale = Math.min(1, PIECE_SIDE / Math.max(bmp.width, bmp.height));
  const canvas = document.createElement("canvas");
  let blob = null;
  for (let i = 0; i < 5; i++) {
    canvas.width = Math.max(1, Math.round(bmp.width * scale));
    canvas.height = Math.max(1, Math.round(bmp.height * scale));
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
    blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.9));
    if (blob && blob.size <= PIECE_MAX) return blob;
    scale *= 0.7;
  }
  return blob;
}

function addPieces(files) {
  const video = kind === "video";
  const max = video ? VIDEO_PIECES_MAX : Number(etat?.pieces_max) || 6;
  const cur = video ? currentVideoChoice() : null;
  if (cur && !takesImages(cur) && files.length) { // glisser ou coller sur Dreamina : rien n'est joint
    toast(t("videos.no_images", { site: tx(String(cur.service_libelle || cur.service || "")) }), "warning");
    return;
  }
  for (const file of files) {
    if (pieces.length >= max) { toast(video ? t("videos.two_images") : t("images.pieces_max", { n: max }), "warning"); break; }
    if (!file.size) { toast(t("att.empty", { name: file.name || t("att.file") }), "warning"); continue; }
    const p = { id: Math.random().toString(16).slice(2, 12), name: file.name || "image.png", url: URL.createObjectURL(file),
      path: "", pending: true };
    pieces.push(p);
    p.ready = (async () => {
      let blob = file;
      let name = p.name;
      if (!PIECE_TYPES.test(file.type) || file.size > PIECE_MAX) {
        blob = await reencode(file); // lève si ce n'est pas une image
        name = name.replace(/\.\w+$/, "") + ".jpg";
      }
      if (!blob || blob.size > PIECE_MAX) throw new Error(t("images.too_big"));
      const data = await toBase64(blob);
      const r = await command("image_piece", { name, data }, 10000, 60000);
      if (!r?.ok || typeof r.path !== "string") throw new Error(tx(r?.error) || t("common.send_failed"));
      Object.assign(p, { path: r.path, name, pending: false });
    })().catch((err) => {
      toast(t("images.piece_failed", { name: p.name, error: err?.message || String(err) }), "error");
      removePiece(p);
    }).finally(() => { if (pieces.includes(p)) paintPanel(); });
  }
  paintPanel();
}

async function send() {
  // Entrée suit l'état du bouton : grisé (Bing avec pièces, envoi en cours) = rien ne part
  if (!panel || panel.sending || panel.go.disabled) return;
  const typed = panel.prompt.value;
  const prompt = typed.replace(/\s+/g, " ").trim();
  if (!prompt) { toast(t("images.prompt_empty"), "warning"); panel.prompt.focus(); return; }
  panel.sending = true;
  paintPanel();
  try {
    const waiting = pieces.filter((p) => p.pending);
    if (waiting.length) {
      await Promise.race([Promise.allSettled(waiting.map((p) => p.ready)), new Promise((r) => setTimeout(r, PREP_WAIT_MS))]);
      if (pieces.some((p) => p.pending)) { toast(t("images.pieces_waiting"), "warning"); return; }
    }
    const sent = pieces.filter((p) => p.path);
    let r;
    if (kind === "video") { // modèle et durée : le réglage `video_choix`, appliqué par le cœur
      // Une pièce partie en échec pendant l'attente : ce qui reste est relu dans l'ordre affiché
      // (1re = début, 2e = fin) ; jamais plus de deux, jamais une fin sans début.
      if (sent.length > VIDEO_PIECES_MAX) { toast(t("videos.two_images"), "warning"); return; }
      const cur = currentVideoChoice(); // modèle changé pendant la préparation des pièces
      if (sent.length && cur && !takesImages(cur)) {
        toast(t("videos.no_images", { site: tx(String(cur.service_libelle || cur.service || "")) }), "warning");
        return;
      }
      r = await command("video_generate", { prompt, channel: channel(), image: sent[0]?.path || "",
        image_fin: sent[1]?.path || "", format: videoFormat });
    } else {
      const args = { prompt, channel: channel(), files: sent.map((p) => p.path) };
      if (mode() === "manuel") {
        const cur = manual();
        Object.assign(args, { engine: cur.service, model: panel.model.value || cur.modele });
      } else args.engine = "auto";
      r = await command("image_generate", args);
    }
    if (r?.ok) {
      toast(kind === "video" ? t("videos.sent", { n: Number(r.credits_estimes) || 0 }) : t("images.sent"));
      // L'accusé peut arriver 25 s plus tard (reconnexion) : ce qui a été tapé ou ajouté entre-temps reste.
      if (panel.prompt.value === typed) panel.prompt.value = "";
      for (const p of sent) {
        const i = pieces.indexOf(p);
        if (i >= 0) pieces.splice(i, 1);
        URL.revokeObjectURL(p.url);
      }
    } else {
      toast(t("images.not_sent", { error: tx(r?.error) || t("common.send_failed") }), "error");
    }
  } finally {
    panel.sending = false;
    paintPanel();
  }
}

// ------------------------------------------------------------------ Réglages › Images

const PROVIDERS = { google: "Google", openai: "OpenAI" };

function when(ts) {
  const d = new Date(Number(ts) * 1000);
  if (!Number.isFinite(d.getTime())) return "";
  const same = d.toDateString() === new Date().toDateString();
  return same ? d.toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleString(locale(), { weekday: "short", hour: "2-digit", minute: "2-digit" });
}

/** État d'un (compte, moteur) en mots : { text, level }. */
export function compteEtat(c) {
  if (c.connexion_ouverte) return { text: t("images.st_open"), level: "info" };
  if (!c.connecte) return { text: t("images.st_not_connected"), level: "warn" };
  if (c.a_reconnecter) return { text: t("images.st_reconnect"), level: "warn" };
  if (!c.disponible && c.raison === "captcha") return { text: t("images.st_captcha"), level: "error" };
  if (!c.disponible && c.raison === "plafond") return { text: t("images.st_cap"), level: "warn" };
  if (!c.disponible) {
    const h = c.reprise_apres ? when(c.reprise_apres) : "";
    return { text: h ? t("images.st_used_up_until", { when: h }) : t("images.st_used_up"), level: "warn" };
  }
  return { text: t("images.st_ok"), level: "ok" };
}

function counter(c) {
  return t(c.fenetre === "24h" ? "images.count_24h" : "images.count_day", { n: c.images ?? 0, max: c.plafond ?? "?" });
}

async function compteAction(alias, action, args = {}) {
  const tag = `${alias}|${action}`;
  if (busy.has(tag)) return;
  busy.add(tag);
  result = null;
  rerenderSettings();
  const r = await command(`image_compte_${action}`, { alias, ...args });
  busy.delete(tag);
  result = r?.ok === false ? { text: tx(r.error) || t("settings.action_refused"), level: "error" }
    : r?.message ? { text: tx(r.message), level: "ok" } : null;
  rerenderSettings();
  return r;
}

async function addAccount(fournisseur) {
  await openModal({
    title: t("images.add_title", { provider: PROVIDERS[fournisseur] }),
    text: t("images.add_text"),
    ok: t("images.add_ok"),
    fields: [{ name: "alias", label: t("images.alias"), required: true, placeholder: t("images.alias_hint") }],
    check: async (v) => {
      const alias = String(v.alias || "").trim().toLowerCase();
      if (!ALIAS.test(alias)) return t("images.alias_invalid");
      const r = await command("image_compte_ajouter", { alias, fournisseur });
      if (r?.ok === false) return tx(r.error) || t("settings.action_refused");
      result = { text: t("images.added", { alias }), level: "ok" };
      rerenderSettings();
      return "";
    },
  });
}

// 30/10 (demande : nom mal tapé à l'ajout) : le profil Chrome, la connexion et les compteurs suivent.
async function renameAccount(alias) {
  await openModal({
    title: t("images.rename_title", { alias }),
    text: t("images.rename_text"),
    ok: t("images.rename"),
    fields: [{ name: "alias", label: t("images.alias"), required: true, value: alias, placeholder: t("images.alias_hint") }],
    check: async (v) => {
      const nouveau = String(v.alias || "").trim().toLowerCase();
      if (!ALIAS.test(nouveau)) return t("images.alias_invalid");
      if (nouveau === alias) return "";
      const r = await command("image_compte_renommer", { alias, nouveau });
      if (r?.ok === false) return tx(r.error) || t("settings.action_refused");
      result = { text: t("images.renamed", { alias, nouveau }), level: "ok" };
      rerenderSettings();
      return "";
    },
  });
}

async function removeAccount(alias) {
  const yes = await openModal({ title: t("images.remove_title", { alias }), text: t("images.remove_text"),
    ok: t("common.remove"), danger: true });
  if (yes) await compteAction(alias, "retirer");
}

function move(alias, step) {
  const order = (etat?.declares || []).map((c) => c.alias);
  const i = order.indexOf(alias);
  const j = i + step;
  if (i < 0 || j < 0 || j >= order.length) return;
  [order[i], order[j]] = [order[j], order[i]];
  compteAction(alias, "ordre", { ordre: order });
}

function button(text, fkey, onclick, opts = {}) {
  const b = el("button", `btn setting-action${opts.danger ? " danger" : ""}${opts.icon && !text ? " icon-only" : ""}`
    + (opts.cls ? ` ${opts.cls}` : ""));
  b.type = "button";
  if (opts.icon) b.append(icon(opts.icon));
  if (text) b.append(el("span", "", text));
  if (opts.aria) b.setAttribute("aria-label", opts.aria);
  if (opts.title) b.title = opts.title;
  b.dataset.fkey = fkey;
  b.disabled = !!opts.disabled;
  b.onclick = onclick;
  return b;
}

function row(key, ico, label, help) {
  const r = el("div", "setting wide with-ico");
  r.dataset.key = key;
  const head = el("div", "setting-head");
  head.append(icon(ico, "setting-ico"), el("span", "setting-label", label));
  r.append(head);
  if (help) r.append(el("div", "setting-help", help));
  return r;
}

function settingsBlock() {
  const out = [];
  // --- comptes
  const box = row("images.comptes", "user", t("images.accounts"), t("images.accounts_help"));
  if (!etat) {
    box.append(el("p", "setting-help", t("common.loading")));
    out.push(box);
    return out;
  }
  if (etat.comptes_illisibles) {
    const err = el("div", "setting-error");
    err.setAttribute("role", "alert");
    err.textContent = t("images.accounts_unreadable");
    box.append(err);
  }
  const list = el("ol", "img-accounts");
  const declares = Array.isArray(etat.declares) ? etat.declares : [];
  const comptes = Array.isArray(etat.comptes) ? etat.comptes : [];
  const open = new Set(Array.isArray(etat.connexions) ? etat.connexions : []);
  declares.forEach((d, i) => {
    const alias = String(d.alias || "");
    const li = el("li", "img-account");
    li.dataset.alias = alias;
    const top = el("div", "img-account-head");
    top.append(el("b", "img-alias", alias), el("span", "img-provider", PROVIDERS[d.fournisseur] || String(d.fournisseur || "")));
    li.append(top);
    const mine = comptes.filter((c) => c.compte === alias);
    const lines = el("dl", "info-lines");
    for (const c of mine) {
      const st = compteEtat(c);
      const line = el("div", `info-line lv-${st.level}`);
      line.append(el("dt", "", serviceName(c.moteur)), el("dd", "", `${st.text} · ${counter(c)}`));
      lines.append(line);
    }
    if (!mine.length) lines.append(el("div", "info-line lv-info", t("images.no_engine")));
    li.append(lines);
    const bar = el("div", "setting-actions");
    const connected = mine.some((c) => c.connecte);
    const opening = open.has(alias) || busy.has(`${alias}|connecter`);
    bar.append(
      button(connected ? t("images.reconnect") : t("images.connect"), `images|${alias}|connecter`,
        () => compteAction(alias, "connecter"), { icon: "user", disabled: opening,
          title: opening ? t("images.st_open") : t("images.connect_title") }),
      button(t("images.rename"), `images|${alias}|renommer`, () => renameAccount(alias),
        { icon: "pen", disabled: opening || busy.has(`${alias}|renommer`) }),
      button("", `images|${alias}|monter`, () => move(alias, -1), { icon: "chevron", cls: "img-up",
        aria: t("images.up", { alias }), title: t("images.up", { alias }), disabled: i === 0 || busy.has(`${alias}|ordre`) }),
      button("", `images|${alias}|descendre`, () => move(alias, 1), { icon: "chevron", cls: "img-down",
        aria: t("images.down", { alias }), title: t("images.down", { alias }),
        disabled: i === declares.length - 1 || busy.has(`${alias}|ordre`) }),
      button(t("common.remove"), `images|${alias}|retirer`, () => removeAccount(alias),
        { danger: true, disabled: busy.has(`${alias}|retirer`) }));
    li.append(bar);
    list.append(li);
  });
  // moteurs sans compte (Bing) : compteur seul, aucun bouton
  for (const c of comptes.filter((x) => x.sans_compte)) {
    const li = el("li", "img-account img-anon");
    const top = el("div", "img-account-head");
    top.append(el("b", "img-alias", serviceName(c.moteur)), el("span", "img-provider", t("images.no_account")));
    const st = compteEtat(c);
    const lines = el("dl", "info-lines");
    const line = el("div", `info-line lv-${st.level}`);
    line.append(el("dt", "", serviceName(c.moteur)), el("dd", "", `${st.text} · ${counter(c)}`));
    lines.append(line);
    li.append(top, lines);
    list.append(li);
  }
  if (!declares.length) box.append(el("p", "setting-help", t("images.none")));
  box.append(list);
  const add = el("div", "setting-actions");
  add.append(button(t("images.add_google"), "images|ajouter|google", () => addAccount("google"), { icon: "user" }),
    button(t("images.add_openai"), "images|ajouter|openai", () => addAccount("openai"), { icon: "user" }));
  box.append(add);
  if (result) {
    const res = el("div", `setting-result lv-${result.level}`);
    res.setAttribute("role", "status");
    res.append(el("div", "result-text", result.text));
    box.append(res);
  }
  out.push(box);
  // --- plafonds
  const caps = row("images.plafonds", "gauge", t("images.caps"), t("images.caps_help"));
  const dl = el("dl", "info-lines");
  for (const s of services()) {
    const line = el("div", "info-line lv-info");
    const txt = t(s.fenetre === "24h" ? "images.cap_24h" : "images.cap_day", { n: s.plafond ?? "?" });
    const extra = [s.fournisseur === "aucun" ? t("images.no_account") : "",
      s.pieces ? t("images.pieces_yes") : t("images.pieces_no")].filter(Boolean).join(" · ");
    line.append(el("dt", "", tx(String(s.libelle || s.service))), el("dd", "", `${txt} · ${extra}`));
    dl.append(line);
  }
  caps.append(dl);
  out.push(caps);
  out.push(videoCredits());
  return out;
}

// Crédits vidéo par compte (lot B4) : restants sur la fenêtre de 24 h, renouvellement prévu, état.
function videoCredits() {
  const box = row("videos.comptes", "play", t("videos.accounts"), t("videos.accounts_help"));
  if (!vetat) {
    box.append(el("p", "setting-help", t("common.loading")));
    return box;
  }
  const dl = el("dl", "info-lines");
  for (const c of Array.isArray(vetat.comptes) ? vetat.comptes : []) {
    const st = compteEtat(c);
    const parts = [st.text, t("videos.credits_left", { n: c.credits_restants ?? "?", max: c.plafond ?? "?" })];
    if (c.renouvellement) parts.push(t("videos.renew", { when: when(c.renouvellement) }));
    const line = el("div", `info-line lv-${st.level}`);
    line.append(el("dt", "", `${c.compte} · ${tx(String(c.service_libelle || c.service || ""))}`), el("dd", "", parts.join(" · ")));
    dl.append(line);
  }
  if (!dl.children.length) dl.append(el("div", "info-line lv-info", t("videos.none")));
  box.append(dl);
  return box;
}
