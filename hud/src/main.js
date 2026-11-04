// HUD d'Astrel : assemble l'orbe, la conversation et les commandes.
// 23/10 (plan P28, demande de l'utilisateur) : plus de graphe 3D en fond, ni Recadrer ni Orbite.

import { command, connect, on } from "./conn.js";
import { PremiumOrb } from "./orb_premium.js";
import { GROUP, STATE, premiumGlass, premiumStateColors } from "./theme.js";
import { affichageNow, applyAffichage, applyTheme, onAffichage, onTheme } from "./apparence.js";
import { linkifyText, renderMarkdown, safeHref } from "./markdown.js";
import { missionPathsOutside } from "./mission-chemins.js";
import { badge, deplacer, entrees, indexChiffre, indexLettre, toucheAutre } from "./choix.js";
import { openModal, openPanel } from "./modal.js";
import { openImage } from "./viewer.js";
import { choiceGallery, initMedias, mediaList, resendMicHold, stopHiddenMedia } from "./medias.js";
import { initSettings, loadSettings, onHudAction, openSettings } from "./settings.js";
import { initCalibrage } from "./calibrage.js";
import { initImages, modelLabel } from "./images.js";
import { initGuide, openGuide } from "./guide.js";
import { initCapacites } from "./capacites.js";
import { initPlans } from "./plans.js";
import { initTodo } from "./todo.js";
import { initAudit } from "./audit.js";
import { initPropositions } from "./propositions.js";
import { hydrateIcons, icon, setIcon, setLabel, stripEmoji } from "./icons.js";
import { applyI18n, assistantName, locale, t, tx } from "./i18n.js";
import { msgTime } from "./heure.js";

// 15/10 : plus d'émojis, des icônes vectorielles (icons.js) ; les marques
// <i data-icon> de index.html deviennent des <svg> avant tout le reste.
hydrateIcons();
// 20/10 : langue de l'interface (i18n.js) ; le HTML statique d'abord, avant tout rendu.
applyI18n();

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

// ------------------------------------------------------------------ mise en page
// 15/10 (demande : « tout est collé, le bandeau chevauche ») : la tête (barre du haut
// + bandeaux Total et mission) réserve sa hauteur. Son bas est mesuré à chaque
// changement de taille (barre passée sur deux lignes, bandeau affiché ou retiré) ;
// panneaux, fil et notifications se placent sous --head-h (styles.css).
const head = $("#head");
const placeHead = () => document.documentElement.style.setProperty(
  "--head-h", `${Math.ceil(head.getBoundingClientRect().bottom)}px`);
new ResizeObserver(placeHead).observe(head);
addEventListener("resize", placeHead); // la marge du haut (--edge) change avec la largeur
placeHead();

// ------------------------------------------------------------------ orbe, graphe

// 21/10 (plan P17) : orbe Premium en 2D (orb_premium.js ; 28/10 : dessin « Constellation » de la
// bulle, sans disque de verre). 25/10 (plan P35) : seule
// orbe du HUD (l'orbe WebGL du thème Classique, orb.js, est retirée avec ce thème) ; sa toile
// #orb-premium est dans index.html. L'accent ne suit plus l'état : c'est celui du thème (styles.css).
const orbPremium = new PremiumOrb($("#orb-premium"));
let currentState = "booting";

// Mode et accent (Réglages › Apparence, apparence.js) : couleurs, encre et liseré de l'orbe.
onTheme((th) => {
  orbPremium.setPalette(premiumStateColors(th.mode, th.accent), premiumGlass(th.mode));
  orbPremium.setActive(true);
});
applyTheme();

// ------------------------------------------------------------------ connexion

on("connection", ({ online, reason }) => {
  // 19/10 : #conn est la pastille du logo ; « en ligne / hors ligne » au survol.
  const c = $("#conn");
  c.classList.toggle("on", online);
  c.classList.toggle("off", !online);
  c.title = t(online ? "conn.title_on" : "conn.title_off");
  c.setAttribute("aria-label", t(online ? "conn.aria_on" : "conn.aria_off"));
  if (!online && reason) toast(reason, "warning");
});

on("state", (ev) => {
  currentState = ev.state;
  lastStateEv = ev;
  renderStateText();
  orbPremium.setState(ev.state);
  // Comme le bouton « muet » d'un appel : micro barré en rouge tant qu'il est coupé.
  micMuted = !!ev.muted;
  const mic = $("#mic-btn");
  mic.classList.toggle("muted", !!ev.muted);
  setIcon(mic, ev.muted ? "mic-off" : "mic");
  mic.setAttribute("aria-pressed", ev.muted ? "true" : "false");
  mic.setAttribute("aria-label", t(ev.muted ? "mic.uncut" : "mic.cut"));
  mic.title = t(ev.muted ? "mic.off_title" : "mic.on_title");
  const listening = ev.state === "listening";
  $("#wake-btn").classList.toggle("on", listening);
  $("#wake-btn").title = t(listening ? "wake.close_title" : "wake.now_title");
  setPaused(!!ev.paused);
  if (ev.usage) setUsage(ev.usage);
  renderCuts();
});

// 22/10 (plan P23, proposition 4) : en affichage Simple, l'orbe réduite dit « Je travaille » (réflexion
// et action) ou « Prêt » (veille), et le détail ne nomme pas l'outil (« Bash : npm test » en Avancé).
let lastStateEv = { state: "booting", detail: "" };
const SIMPLE_STATE = { thinking: "state.simple_working", working: "state.simple_working", sleeping: "state.simple_ready" };

function renderStateText() {
  const ev = lastStateEv;
  const key = STATE[ev.state] ? ev.state : "sleeping";
  const simple = simpleView();
  $("#state-label").textContent = t(simple && SIMPLE_STATE[key] ? SIMPLE_STATE[key] : `state.${key}`);
  $("#state-detail").textContent = simple && key === "working" ? "" : stripEmoji(tx(ev.detail || ""));
}

// Pause de la voix, comme un lecteur vidéo : le son se coupe net, le travail
// continue, et « lecture » reprend là où Astrel s'était arrêté. Le texte reste ici.
let voicePaused = false;

function setPaused(paused) {
  voicePaused = paused;
  const b = $("#pause-btn");
  b.classList.toggle("paused", paused);
  setIcon(b, paused ? "play" : "pause");
  b.setAttribute("aria-label", t(paused ? "pause.resume" : "pause.pause"));
  b.title = t(paused ? "pause.resume_title" : "pause.pause_title");
  $("#paused-note").classList.toggle("hidden", !paused);
}

async function togglePause() {
  const r = await command("pause", { on: !voicePaused });
  if (!r.ok) toast(r.error || t("pause.failed"), "error");
}

on("level", (ev) => {
  // La voix d'Astrel fait vibrer l'orbe ; le micro seulement quand il écoute.
  if (ev.source === "voice" || ["listening", "asking"].includes(currentState)) orbPremium.setLevel(ev.value);
});

// 23/10 (plan P28) : l'évènement `graph` du cœur n'est plus affiché (ni graphe ni compteurs
// « N nœuds · liens · notes ») ; le cœur le garde pour la page des capacités (usage).

on("model", (ev) => {
  setLabel($("#model-badge"), "brain", tx(ev.label), "lead");
  // 20/10 (plan P11) : autre fournisseur que Claude (`ext`) ou clé API facturée (`cle`) : badge orange.
  $("#model-badge").classList.toggle("ext", !!ev.ext || !!ev.cle);
  const name = `${tx(ev.label)}${ev.id ? ` (${ev.id})` : ""}`;
  $("#model-badge").title = ev.ext ? t("model.ext_title", { name })
    : t(ev.cle ? "model.key_title" : "model.title", { name });
});
on("voice", (ev) => {
  setLabel($("#voice-badge"), "waves", `${ev.active}${ev.degraded ? t("voice.fallback_suffix") : ""}`, "lead");
  $("#voice-badge").title = ev.degraded ? t("voice.degraded_title", { engine: ev.engine }) : t("voice.change");
});

// ------------------------------------------------------------------ conversation

// Chaque message porte son canal (voir « canaux » plus bas) : tous restent dans
// la page, seuls ceux du canal ouvert sont visibles.
const messages = [];        // { node, body, raw, who, channel, mid, replyTo }
const byMid = new Map();    // un HUD qui se reconnecte reçoit l'historique : pas de doublon
const liveReplies = new Map(); // canal → réponse en cours d'écriture
const PER_CHANNEL = 60;

on("transcript", (ev) => {
  const channel = ev.channel || defaultChannel;
  closeLive(channel);
  // 29/10 : un message venu de WhatsApp portait l'icône du micro (comme s'il avait été dit)
  const src = { clavier: "keyboard", "dictée": "pen", whatsapp: "smartphone" }[ev.source] || "mic";
  const m = addMsg("user", ev.text, { src, channel, mid: ev.mid, replay: ev.replay, ts: ev.ts });
  if (ev.attachments?.length && !m.node.querySelector(".att-list")) {
    const list = el("div", "att-list");
    for (const a of ev.attachments) list.append(sentAttachment(a));
    m.node.append(list);
  }
  m.source = ev.source;
  // 16/10 (bug) : une phrase DICTÉE ne part jamais chez Astrel ; elle s'affichait comme un
  // message envoyé. Un message que le cœur n'a pas pu transmettre (`unsent`) non plus.
  // `resent` : déjà renvoyé (historique d'un HUD rechargé), pas de second « Renvoyer ».
  if (ev.source === "voix" && !ev.replay && !ev.unsent && !ev.resent) warnVoiceWithoutPending();
  if (ev.resent) markResent(m);
  else if (ev.source === "dictée" && !ev.replay && !m.unsentBox) dictationToBar(m);
  else if (ev.source === "dictée") markUnsent(m, "dictee");
  else if (ev.unsent) markUnsent(m, "failed");
});

// 19/10 (demande : voir l'image dans le fil après l'envoi) : une image partie porte `file`
// (nom dans runtime/attachments, jamais un chemin, astrel/attachments.py) ; le fil en montre
// une vignette servie par le cœur (/api/attachment/<file>, cookie du HUD), qui ouvre la
// visionneuse. Fichier effacé (gardé 7 jours) ou illisible : la pastille du nom, comme avant.
const ATT_FILE = /^[\p{L}\p{M}\p{N}_.\- ]{1,120}$/u;

function sentAttachment(a) {
  const name = String(a?.name || t("att.file"));
  const chip = el("span", "att-chip");
  setLabel(chip, a?.image ? "image" : "clip", name, "lead");
  const file = typeof a?.file === "string" ? a.file : "";
  if (!a?.image || !ATT_FILE.test(file) || file.startsWith(".")) return chip;
  const url = `/api/attachment/${encodeURIComponent(file)}`;
  const view = el("button", "att-view att-thumb");
  view.type = "button";
  view.title = t("att.view_named", { name });
  view.setAttribute("aria-label", t("att.view_aria", { name }));
  const img = el("img");
  img.alt = name;
  img.draggable = false;
  img.addEventListener("error", () => view.replaceWith(chip), { once: true });
  img.src = url;
  view.append(img);
  view.onclick = () => openImage(url, name);
  return view;
}

// 17/10 (demande : « j'avais collé un lien, ma dictée ne s'est pas envoyée avec ») : une
// phrase dictée s'écrit à la SUITE de ce qui est dans la barre (lien collé…), sans partir ;
// Entrée envoie le tout en UN message. Avant, elle n'allait que dans le fil (« pas
// envoyée ») et Entrée envoyait le lien seul. Une dictée rejouée (HUD rechargé) ne revient
// pas dans la barre : elle garde « Envoyer » / « Dans la barre ».
const dictInBar = new Set(); // dictées versées dans la barre, pas encore parties

function appendToAsk(text) {
  const a = $("#ask");
  const cur = a.value.replace(/[ \t]+$/, "");
  const sep = !cur || cur.endsWith("\n") || text.startsWith("\n") ? "" : " ";
  a.value = cur + sep + text;
  fitAsk();
}

function dictationToBar(m) {
  appendToAsk(m.raw);
  m.node.classList.add("unsent");
  m.unsentBox = el("div", "unsent-box");
  m.unsentBox.append(el("span", "unsent-note", t("unsent.dictation_in_bar")));
  m.node.append(m.unsentBox);
  dictInBar.add(m);
}

// Barre vidée à la main : ces dictées ne partiront pas avec le prochain message, elles
// redeviennent « pas envoyées » (Envoyer / Dans la barre).
function releaseDictations() {
  for (const m of dictInBar) {
    m.unsentBox.remove();
    m.unsentBox = null;
    markUnsent(m, "dictee");
  }
  dictInBar.clear();
}

// Le cœur n'a pas pu transmettre ce message au cerveau (moteur arrêté, relance…).
// Ses pièces jointes (`attachments` : leurs id) reviennent dans la barre.
on("unsent", (ev) => {
  const m = ev.mid && byMid.get(ev.mid);
  if (m) markUnsent(m, "failed", ev.error);
  if (Array.isArray(ev.attachments) && ev.attachments.length) restoreAttachments(ev.attachments);
});
on("resent", (ev) => {
  const m = ev.mid && byMid.get(ev.mid);
  if (m) {
    dictInBar.delete(m);
    markResent(m);
  }
});

// Parti (plus bas) : la note remplace les boutons, ici ou dans un HUD rechargé.
function markResent(m) {
  // 20/10 (demande : « ça me l'écrit à la fois dans l'autre chat et dans un truc à part ») :
  // une dictée partie est déjà dans le message envoyé ; sa bulle d'origine disparaît du fil au
  // lieu de rester en double. Gardée dans `messages`/`byMid` (pas de retour au rechargement).
  if (m.source === "dictée") {
    dictInBar.delete(m);
    m.node.remove();
    return;
  }
  m.node.classList.remove("unsent");
  m.node.classList.add("resent");
  const note = el("span", "unsent-note", t(m.source === "dictée" ? "unsent.sent_below" : "unsent.resent_below"));
  if (!m.unsentBox) {
    m.unsentBox = el("div", "unsent-box");
    m.node.append(m.unsentBox);
  }
  m.unsentBox.replaceChildren(note);
}

// « Non envoyé » visible, avec de quoi le faire partir : Renvoyer (ou Envoyer pour une
// dictée) et, pour une dictée, « Dans la barre » pour la compléter (lien, texte collé).
// « Renvoyer » pour la voix et le clavier seulement : un message de mission, de réflexe
// ou de plan n'affiche qu'un libellé, le renvoyer le ferait passer pour l'utilisateur.
function markUnsent(m, kind, error = "") {
  if (m.unsentBox) return;
  const dictee = kind === "dictee";
  m.node.classList.add("unsent");
  const box = el("div", "unsent-box");
  box.append(el("span", "unsent-note", t(dictee ? "unsent.dictation" : "unsent.failed")));
  if (error) box.title = tx(error);
  m.node.append(box);
  m.unsentBox = box;
  if (!dictee && !["voix", "clavier"].includes(m.source)) return;
  const send = el("button", "read-btn pending");
  setLabel(send, dictee ? "message" : "refresh", t(dictee ? "unsent.send" : "unsent.resend"), "lead");
  send.title = t(dictee ? "unsent.send_title" : "unsent.resend_title");
  send.onclick = async (e) => {
    e.stopPropagation();
    if (send.disabled) return;
    send.disabled = true;
    // `resend_of` : le cœur efface « non envoyé » de l'historique (pas de doublon après rechargement).
    const r = await command("say", { text: m.raw, channel: m.channel, resend_of: m.mid });
    send.disabled = false;
    if (!r.ok) { toast(t("unsent.still", { error: tx(r.error) || t("common.send_failed") }), "error"); return; }
    markResent(m);
  };
  box.append(send);
  if (dictee) {
    const bar = el("button", "read-btn");
    setLabel(bar, "keyboard", t("unsent.to_bar"), "lead");
    bar.title = t("unsent.to_bar_title");
    bar.onclick = (e) => {
      e.stopPropagation();
      const a = $("#ask");
      appendToAsk(m.raw);
      a.focus();
      a.setSelectionRange(a.value.length, a.value.length);
    };
    box.append(bar);
  }
}

on("reply_delta", (ev) => {
  const channel = ev.channel || defaultChannel;
  let m = liveReplies.get(channel);
  if (!m) {
    m = addMsg("astrel", "", { channel, replyTo: ev.reply_to, live: true, ts: ev.ts });
    liveReplies.set(channel, m);
  }
  m.raw += ev.text;
  scheduleRender(m);
});

// Réponse finie, ou morceau de réponse clos parce que l'utilisateur a écrit
// ailleurs en plein tour (`partial`) : la suite va dans le canal de son message.
on("reply", (ev) => {
  if (ev.mid && byMid.has(ev.mid)) {
    if (ev.report) markReport(byMid.get(ev.mid), ev); // HUD reconnecté : « lu » a pu changer
    return;
  }
  const channel = ev.channel || defaultChannel;
  let m = ev.replay ? null : liveReplies.get(channel);
  if (m) {
    closeLive(channel, ev.text);
    setMid(m, ev.mid);
  } else if (ev.text) {
    m = addMsg("astrel", ev.text, { channel, mid: ev.mid, replyTo: ev.reply_to, replay: ev.replay, ts: ev.ts });
  }
  // P41 : réponse finie sous les yeux de l'utilisateur : vue jusqu'à son heure de FIN (sinon, rejouée
  // après un rechargement, elle compterait comme non lue).
  if (!ev.replay && viewing && shownIn(channel) === activeChannel) markSeen(activeChannel, Number(ev.ts) || undefined);
  if (m && ev.report) reportArrived(m, ev);
});

// ------------------------------------------------------------------ comptes rendus

// Compte rendu d'une tâche de fond (ou posé par l'outil post_report) : en mode
// « à la demande », Astrel ne le dit pas, il attend le clic sur « Lire ». Le cœur
// dit alors le texte affiché, sans repasser par Claude.
on("report", (ev) => {
  if (ev.mid && byMid.has(ev.mid)) { markReport(byMid.get(ev.mid), ev); return; }
  // P41 : un compte rendu sans canal (cœur d'avant P41) va dans le canal OUVERT, pas dans Général.
  const m = addMsg("astrel", ev.text, { channel: ev.channel || activeChannel, mid: ev.mid, replay: ev.replay, ts: ev.ts });
  reportArrived(m, ev);
});

on("report_read", (ev) => {
  const m = byMid.get(ev.mid);
  if (m) markReport(m, { pending_read: false });
});

function reportArrived(m, ev) {
  // Pas encore vu : arrivé dans un autre canal que celui ouvert (point du canal, voir renderChannels),
  // ou rejoué mais plus récent que la dernière visite de ce canal (P41).
  if (shownIn(m.channel) !== activeChannel && (!ev.replay || (Number(ev.ts) || 0) > seenAt(shownIn(m.channel)))) {
    m.unseen = true;
  }
  markReport(m, ev);
  if (ev.replay || !ev.pending_read) return;
  const c = channelList.length > 1 ? chanShown(shownIn(m.channel)) : null;
  const what = t(m.reportKind === "decision" ? "report.decision" : "report.ready");
  const n = toast(`${c ? `${chanLabel(c)} · ` : ""}${t("report.toast", { what })}`,
    m.reportKind === "decision" ? "warning" : "info", "volume");
  toastLeadsTo(n, c, m);
}

// 15/10 (idée 3) : compte rendu typé par post_report(kind=…). `report_kind` :
// decision (l'utilisateur doit trancher), blocage, resultat ; info ou inconnu = pas de
// badge. `report_files` : chemins produits, en texte, copiés d'un clic.
const REPORT_KINDS = {
  decision: { label: "report.decision", icon: "alert", title: "report.decision_title" },
  blocage: { label: "report.blocked", icon: "no-entry", title: "report.blocked_title" },
  resultat: { label: "report.result", icon: "check", title: "report.result_title" },
};
const MAX_REPORT_FILES = 10;

function markKind(m, ev) {
  if (typeof ev.report_kind === "string") {
    const kind = REPORT_KINDS[ev.report_kind] ? ev.report_kind : "";
    if (kind !== (m.reportKind || "")) {
      for (const k of Object.keys(REPORT_KINDS)) m.node.classList.toggle(`report-${k}`, k === kind);
      m.badge?.remove();
      m.badge = null;
      if (kind) {
        const meta = REPORT_KINDS[kind];
        const b = el("span", `report-badge ${kind}`);
        setLabel(b, meta.icon, t(meta.label), "lead");
        b.title = t(meta.title);
        m.node.insertBefore(b, m.body);
        m.badge = b;
      }
      m.reportKind = kind;
    }
  }
  if (Array.isArray(ev.report_files)) {
    const files = ev.report_files.filter((f) => typeof f === "string" && f.trim()).slice(0, MAX_REPORT_FILES);
    m.filesBox?.remove();
    m.filesBox = null;
    if (files.length) {
      const box = el("div", "report-files");
      box.append(el("span", "report-files-head", t("report.files", { n: files.length })));
      for (const f of files) {
        const b = el("button", "report-file", f);
        b.title = t("report.copy_path");
        b.onclick = (e) => { e.stopPropagation(); copyText(f, t("report.path_copied", { path: f })); };
        box.append(b);
      }
      m.body.after(box);
      m.filesBox = box;
    }
  }
  // 22/10 (plan P26) : fichiers MONTRÉS (image, PDF, son, vidéo ; medias.js), sous la liste des
  // chemins. Bâtis une seule fois : « lu » et le rejeu ne recréent pas un lecteur en cours.
  if (Array.isArray(ev.report_media) && !m.mediaBox) {
    const box = mediaList(ev.report_media);
    if (box) {
      (m.filesBox || m.body).after(box);
      m.mediaBox = box;
    }
  }
}

// 22/10 (plan P26) : carte de médias posée par l'outil `show_media` (jamais dite). Le texte gardé
// (`raw`) est la légende ; les fichiers viennent de /api/media/<id> seulement (medias.js).
initMedias({ command, toast: (...a) => toast(...a), onLayout: () => scrollTranscript() });

on("media", (ev) => {
  if (!ev.mid || byMid.has(ev.mid)) return;
  const box = mediaList(ev.items, String(ev.title || ""));
  if (!box) return;
  const channel = ev.channel || defaultChannel;
  const node = el("div", "msg media-msg");
  node.setAttribute("role", "group");
  node.setAttribute("aria-label", t("media.aria"));
  node.append(box, timeEl(ev.ts));
  const m = { node, body: box, raw: String(ev.title || ""), who: "media", channel, mid: "", replyTo: "", isMedia: true };
  setMid(m, ev.mid);
  messages.push(m);
  transcript.append(node);
  placeMsg(m);
  pruneChannel(channel);
  if (arrivesUnseen(channel, ev.replay, ev.ts)) {
    bumpUnread(channel);
    renderChannels();
  }
  scrollTranscript();
  noteArrival(node, ev.replay);
});

function markReport(m, ev) {
  markKind(m, ev);
  if (!m.isReport) { m.isReport = true; placeMsg(m); } // Simple : visible même d'un canal de projet
  const pending = !!ev.pending_read;
  if (!m.readBtn) {
    const b = el("button", "read-btn");
    b.onclick = (e) => { e.stopPropagation(); readReport(m); };
    m.node.append(b);
    m.readBtn = b;
  }
  m.pendingRead = pending;
  m.node.classList.toggle("to-read", pending);
  m.readBtn.classList.toggle("pending", pending);
  setLabel(m.readBtn, "volume", t(pending ? "report.read" : "report.reread"), "lead");
  m.readBtn.title = t(pending ? "report.read_title" : "report.reread_title");
  renderChannels();
}

async function readReport(m) {
  // Le texte affiché part aussi : après une relance, le cœur ne l'a plus en mémoire.
  const r = await command("read_report", { mid: m.mid, fallback: m.raw });
  if (!r.ok) toast(r.error || t("report.read_failed"), "error");
  else if (r.paused) toast(t("report.paused"), "warning", "pause");
}

// Interrupteur à état VISIBLE (14/10, l'utilisateur ne voyait pas s'il était actif) :
// pastille verte « OUI » ou grise « NON », et aria-checked.
function setToggle(chip, on, value = t(on ? "common.yes_caps" : "common.no_caps")) {
  chip.classList.toggle("on", on);
  chip.setAttribute("aria-checked", on ? "true" : "false");
  chip.querySelector(".val").textContent = value;
}

// 28/10 (T10, décision de l'utilisateur) : interrupteur « Lire aussitôt » retiré de la barre ; le mode des
// comptes rendus (`demande|direct`) se règle dans Réglages › Voix, audio et langue (réglage `reports`).

// ------------------------------------------------------------------ questions à choix (15/10, idée 9)

// L'outil MCP ask_choice pose une question à 2-4 réponses (la recommandée en premier)
// et rend aussitôt la main ; la carte attend ici le clic de l'utilisateur. Elle vit dans le
// fil de son canal, rejouée à la reconnexion (`choice` puis `choice_answered`).
// Une seule carte ACTIVE par canal : la plus ancienne sans réponse, les suivantes
// attendent leur tour (une question à la fois).
// 21/10 (plan P21, « comme Claude Code ») : entrées a) b) c), « Autre » en dernière lettre
// (déplie un champ multiligne), groupe de boutons radio (flèches, Entrée/Espace, lettre quand
// la carte a le focus ; chiffres 1…5 globaux si la barre est vide). Fonctions pures : choix.js.
// Le focus n'est JAMAIS pris à l'arrivée d'une carte (l'utilisateur tape peut-être ailleurs).
const choices = new Map();       // id → message du fil portant `m.choice`
const choiceAnswers = new Map(); // id → réponse arrivée avant sa question (rejeu dans le désordre)

on("choice", (ev) => {
  const id = String(ev.id || "");
  if (!id) return;
  const m = choices.get(id) || addChoice(ev, id);
  // Rejeu : le cœur corrige l'historique (answered, value, cancelled) ; sinon la réponse
  // a pu arriver avant sa question (choiceAnswers).
  if (ev.answered) answerChoice(m, ev.value, !!ev.cancelled);
  else if (choiceAnswers.has(id)) answerChoice(m, ...choiceAnswers.get(id));
});

on("choice_answered", (ev) => {
  const id = String(ev.id || "");
  const m = choices.get(id);
  // `cancelled` : question remplacée par une nouvelle (le cœur n'en garde qu'une en attente par canal).
  if (m) answerChoice(m, ev.value, !!ev.cancelled);
  else if (id) choiceAnswers.set(id, [ev.value, !!ev.cancelled]);
});

function addChoice(ev, id) {
  const channel = ev.channel || activeChannel; // P41 : carte sans canal (offre d'un cœur d'avant P41) : canal ouvert
  const question = String(ev.question || "");
  const items = entrees(ev.options, ev.allow_other !== false);
  const node = el("div", "msg choice");
  node.setAttribute("role", "group");
  node.setAttribute("aria-label", t("choice.aria"));
  // 20/10 : une question composée par le cœur (offre de modèle) se traduit ; celle de Claude, inconnue
  // du catalogue, reste telle quelle. La réponse envoyée reste le libellé d'origine (o.label).
  const body = el("div", "choice-question", stripEmoji(tx(question)));
  body.id = `choice-q-${id}`;
  const m = { node, body, raw: question, who: "choice", channel, mid: "", replyTo: "",
    choice: { id, items, options: items.filter((o) => !o.autre), answered: false, value: "", sending: false,
      active: false, buttons: [], at: 0, otherOpen: false } };
  const row = el("div", "choice-options");
  row.setAttribute("role", "radiogroup");
  row.setAttribute("aria-labelledby", body.id);
  items.forEach((o, i) => {
    const b = el("button", `choice-btn${o.recommended ? " recommended" : ""}${o.autre ? " other" : ""}`);
    b.type = "button";
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", "false");
    b.tabIndex = i === 0 ? 0 : -1;
    b.append(el("kbd", "choice-key", badge(i)),
      el("span", "choice-label", o.autre ? t("choice.other") : stripEmoji(tx(o.label))));
    if (o.recommended) b.append(el("span", "choice-rec", t("choice.recommended")));
    b.title = o.autre ? t("choice.other_title") : t("choice.answer_title", { label: tx(o.label) });
    b.onclick = (e) => { e.stopPropagation(); pickChoice(m, i); };
    m.choice.buttons.push(b);
    row.append(b);
  });
  row.addEventListener("keydown", (e) => choiceKey(m, e));
  const head = el("div", "choice-head");
  setLabel(head, "help", t("choice.head"), "lead");
  node.append(head, body);
  // 22/10 (plan P26) : une image par réponse (`ask_choice(images)`) = galerie à choisir, chaque
  // miniature marquée de sa lettre et agrandie au clic ; on répond par les boutons dessous.
  const gallery = choiceGallery(items, badge);
  if (gallery) node.append(gallery);
  node.append(row);
  if (items.some((o) => o.autre)) {
    const other = el("textarea", "choice-other hidden");
    other.rows = 2;
    other.maxLength = 500;
    other.placeholder = t("choice.other_placeholder");
    other.setAttribute("aria-label", t("choice.other_aria"));
    other.addEventListener("keydown", (e) => {
      const action = toucheAutre(e);
      if (!action || action === "ligne") return; // Maj+Entrée : retour à la ligne normal du champ
      e.preventDefault();
      e.stopPropagation(); // ni la carte de confirmation ni le panneau d'activité ne la voient
      if (action === "retour") { closeOther(m); return; }
      const v = other.value.trim();
      if (v) sendChoice(m, v);
    });
    m.choice.other = other;
    node.append(other);
  }
  m.choice.status = el("div", "choice-status");
  m.choice.status.setAttribute("role", "status"); // annoncé poliment, sans prendre le focus
  node.append(m.choice.status, timeEl(ev.ts));
  choices.set(id, m);
  messages.push(m);
  transcript.append(node);
  placeMsg(m);
  pruneChannel(channel);
  if (!ev.answered && arrivesUnseen(channel, ev.replay, ev.ts)) {
    bumpUnread(channel);
    if (!ev.replay) {
      const c = chanShown(shownIn(channel));
      const n = toast(`${c ? `${chanLabel(c)} · ` : ""}${t("choice.waiting_toast", { question: tx(question) })}`, "warning");
      toastLeadsTo(n, c, m);
    }
  }
  refreshChoices();
  renderChannels();
  scrollTranscript();
  noteArrival(node, ev.replay);
  return m;
}

function renderChoice(m) {
  const c = m.choice;
  const waiting = !c.answered && !c.active;
  m.node.classList.toggle("answered", c.answered);
  m.node.classList.toggle("cancelled", !!c.cancelled);
  m.node.classList.toggle("active", c.active && !c.answered);
  m.node.classList.toggle("waiting", waiting);
  c.buttons.forEach((b, i) => {
    b.disabled = c.answered || !c.active || c.sending;
    b.classList.toggle("hidden", c.answered);
    b.tabIndex = i === c.at ? 0 : -1; // un seul arrêt de tabulation dans le groupe
  });
  if (c.other) {
    c.other.disabled = c.answered || !c.active || c.sending;
    c.other.classList.toggle("hidden", c.answered || !c.otherOpen);
  }
  const n = c.items.length;
  c.status.className = `choice-status${c.answered && !c.cancelled ? " done" : " small muted"}`;
  const said = c.cancelled ? t("choice.cancelled")
    : c.answered ? t("choice.answered", { value: tx(c.value) || "—" })
    : c.sending ? t("choice.sending")
      : waiting ? t("choice.waiting")
        : t(n ? "choice.click_or_type" : "choice.click", { n });
  // Zone `status` (annoncée) : réécrite seulement si le texte change, sinon chaque flèche la redirait.
  if (c.status.textContent !== said) c.status.textContent = said;
}

function refreshChoices() {
  const first = new Set(); // canaux affichés qui ont déjà leur carte active
  for (const m of messages) {
    if (!m.choice) continue;
    const ch = shownIn(m.channel);
    m.choice.active = !m.choice.answered && !first.has(ch);
    if (m.choice.active) first.add(ch);
    renderChoice(m);
  }
}

function answerChoice(m, value, cancelled = false) {
  const c = m.choice;
  if (c.cancelled) return; // annulée par le cœur : elle le reste
  c.answered = true;
  c.cancelled = cancelled;
  c.sending = false;
  c.otherOpen = false;
  if (value != null && String(value) !== "") c.value = String(value);
  choiceAnswers.delete(c.id);
  refreshChoices();
  renderChannels();
}

async function sendChoice(m, value) {
  const c = m.choice;
  if (c.answered || c.sending || !c.active) return;
  c.sending = true;
  renderChoice(m);
  // `choice_id` et pas `id` : `id` est l'identifiant de la requête WebSocket (conn.js),
  // il écraserait celui de la question (même piège que `confirm_id`).
  const r = await command("choice_answer", { choice_id: c.id, value });
  if (r.ok) { answerChoice(m, value); return; } // `choice_answered` du cœur confirmera
  c.sending = false;
  renderChoice(m);
  toast(t("choice.not_sent", { error: tx(r.error) || t("common.send_failed") }), "error");
}

// Coche l'entrée i (aria-checked) et y met l'arrêt de tabulation ; `focus` : y aller aussi.
function checkChoice(m, i, focus = false) {
  const c = m.choice;
  if (!c.buttons[i]) return;
  c.at = i;
  c.buttons.forEach((b, j) => b.setAttribute("aria-checked", String(j === i)));
  renderChoice(m);
  if (focus) c.buttons[i].focus();
}

// Une entrée choisie (clic, Entrée, Espace, lettre, chiffre) : une réponse part ; « Autre »
// déplie son champ et y met le focus (geste exprès de l'utilisateur, pas une arrivée de carte).
function pickChoice(m, i) {
  const c = m.choice;
  const o = c.items[i];
  if (!o || c.answered || c.sending || !c.active) return;
  checkChoice(m, i);
  if (!o.autre) { sendChoice(m, o.label); return; }
  c.otherOpen = true;
  renderChoice(m);
  c.other.focus();
}

// Échap dans « Autre » : le champ se replie (son texte est gardé), retour sur l'entrée « Autre ».
function closeOther(m) {
  const c = m.choice;
  c.otherOpen = false;
  renderChoice(m);
  const i = c.items.findIndex((o) => o.autre);
  if (i >= 0 && !c.buttons[i].disabled) c.buttons[i].focus();
}

// Clavier DANS le groupe (un bouton radio a le focus). Les touches traitées ne remontent pas :
// la fenêtre répond sinon à une carte de confirmation sur Entrée ou Échap.
function choiceKey(m, e) {
  const c = m.choice;
  if (e.target?.getAttribute?.("role") !== "radio") return;
  if (e.key === "Enter" || e.key === "Escape") e.stopPropagation();
  if (!c.active || c.answered || c.sending) return;
  const n = c.buttons.length;
  const cur = Math.max(0, c.buttons.indexOf(e.target));
  const plain = !e.ctrlKey && !e.altKey && !e.metaKey;
  const to = plain ? deplacer(e.key, cur, n) : -1;
  if (to >= 0) { e.preventDefault(); e.stopPropagation(); checkChoice(m, to, true); return; }
  if (plain && !e.shiftKey && (e.key === "Enter" || e.key === " ")) {
    e.preventDefault(); // sinon le bouton reçoit aussi un clic
    e.stopPropagation();
    pickChoice(m, cur);
    return;
  }
  const i = indexLettre(e, n);
  if (i >= 0) { e.preventDefault(); e.stopPropagation(); pickChoice(m, i); }
}

// Raccourcis 1…5 : seulement si la carte active est le DERNIER message visible du fil
// et que l'utilisateur ne tape rien (barre vide, aucun autre champ, aucune boîte ouverte).
function lastChoice() {
  let last = transcript.lastElementChild;
  while (last && last.classList.contains("hidden")) last = last.previousElementSibling;
  const m = last && messages.find((x) => x.node === last);
  return m?.choice?.active && !m.choice.answered ? m : null;
}

addEventListener("keydown", (e) => {
  if (indexChiffre(e, 9) < 0) return;
  if (document.querySelector(".modal-back")) return;
  const ask = $("#ask");
  if (typingIn() && !(document.activeElement === ask && !ask.value)) return;
  const m = lastChoice();
  const i = m ? indexChiffre(e, m.choice.items.length) : -1;
  if (i < 0) return;
  e.preventDefault();
  pickChoice(m, i);
});

// 21/10 (plan P14, demande de l'utilisateur) : carte d'une IMAGE IA dans le fil, retrouvée par `job`
// (astrel/images_ia.py, évènement `image_job` ; au rejeu de l'historique, seul le dernier état
// compte). Tant que l'image n'est pas finie : animation de chargement (transform seul, coupée
// par prefers-reduced-motion) et l'étape en cours. Finie : l'image (ou la grille des variantes,
// la principale en tête) servie par /api/attachment/<file>, visionneuse au clic, et « Généré
// avec Gemini · compte amw ». Échec : la raison, le texte de l'IA tel quel, « Réessayer ».
const imageCards = new Map(); // job → message du fil
const IMAGE_STATES = ["file", "en_cours", "fini", "echec"];
const IMAGE_STEPS = ["ouverture", "saisie", "generation", "telechargement", "bascule"];
const imageDone = (m) => m.image && (m.image.etat === "fini" || m.image.etat === "echec");

on("image_job", (ev) => {
  const id = String(ev.job || "");
  if (!/^[\w-]{1,40}$/.test(id)) return;
  const m = imageCards.get(id) || addJobCard(ev, imageCards, t("image.aria"), { imageJob: id, image: ev });
  m.image = ev;
  renderImageCard(m);
  scrollTranscript();
});

// Carte d'un job IA (image ou vidéo) : `fields` porte son id et son dernier évènement.
function addJobCard(ev, cards, aria, fields) {
  const channel = ev.channel || defaultChannel;
  const node = el("div", "msg image-job");
  node.setAttribute("role", "group");
  node.setAttribute("aria-label", aria);
  const m = { node, body: node, raw: String(ev.prompt || ""), who: "image", channel, mid: "", replyTo: "", ...fields };
  cards.set(String(ev.job), m);
  messages.push(m);
  transcript.append(node);
  placeMsg(m);
  pruneChannel(channel);
  if (arrivesUnseen(channel, ev.replay, ev.ts)) {
    bumpUnread(channel);
    renderChannels();
  }
  return m;
}

function imageEngine(ev) {
  return String(ev.moteur_libelle || "") || t("image.the_ai");
}

function imageStep(ev) {
  if (ev.etat === "file") return ev.position > 1 ? t("image.queued_n", { n: ev.position }) : t("image.queued");
  const step = IMAGE_STEPS.includes(ev.etape) ? ev.etape : "ouverture";
  return t(`image.step_${step}`, { engine: imageEngine(ev) });
}

function imageThumb(item) {
  const file = typeof item?.file === "string" ? item.file : "";
  const name = String(item?.nom || file || t("att.file"));
  const chip = el("span", "att-chip");
  setLabel(chip, "image", name, "lead");
  if (!ATT_FILE.test(file) || file.startsWith(".")) return chip;
  const url = `/api/attachment/${encodeURIComponent(file)}`;
  const view = el("button", "image-view");
  view.type = "button";
  view.title = t("image.saved_title", { name });
  view.setAttribute("aria-label", t("image.view"));
  const img = el("img");
  img.alt = name;
  img.draggable = false;
  img.addEventListener("error", () => view.replaceWith(chip), { once: true });
  img.addEventListener("load", () => scrollTranscript(), { once: true });
  img.src = url;
  view.append(img);
  view.onclick = () => openImage(url, name);
  return view;
}

function renderImageCard(m) {
  const ev = m.image;
  const state = IMAGE_STATES.includes(ev.etat) ? ev.etat : "en_cours";
  for (const s of IMAGE_STATES) m.node.classList.toggle(`image-${s}`, s === state);
  const head = el("div", "image-head");
  setLabel(head, "image", t("image.head"), "lead");
  const prompt = el("div", "image-prompt", stripEmoji(String(ev.prompt || "")));
  const parts = [head, prompt];
  if (state === "file" || state === "en_cours") {
    const load = el("div", "image-loading");
    load.setAttribute("role", "status");
    load.setAttribute("aria-label", t("image.loading_aria"));
    const anim = el("div", "image-shimmer");
    anim.setAttribute("aria-hidden", "true");
    load.append(anim, el("div", "image-step", imageStep(ev)));
    parts.push(load);
  } else if (state === "fini") {
    const items = (Array.isArray(ev.images) && ev.images.length ? ev.images : [{ file: ev.file, nom: ev.nom }]).slice(0, 4);
    const grid = el("div", `image-grid n${items.length}`);
    items.forEach((it) => grid.append(imageThumb(it)));
    // 28/10 (plan P40 lot A5) : modèle nommé s'il n'est pas celui par défaut du site
    const model = modelLabel(String(ev.moteur || ""), String(ev.modele || ""));
    const engine = model ? `${imageEngine(ev)} (${model})` : imageEngine(ev);
    const credit = el("div", "image-credit", ev.compte
      ? t("image.made_with", { engine, account: String(ev.compte) }) : t("image.made_with_short", { engine }));
    if (ev.nom) credit.title = t("image.saved_title", { name: String(ev.nom) });
    parts.push(grid, credit);
  } else {
    parts.push(el("div", "image-error", tx(String(ev.erreur || "")) || t("image.failed")));
    if (ev.texte_ia) {
      parts.push(el("div", "image-ai-label", t("image.ai_said", { engine: imageEngine(ev) })),
        el("blockquote", "image-ai-text", String(ev.texte_ia)));
    }
    if (ev.code !== "estop") {
      const retry = el("button", "image-retry");
      retry.type = "button";
      setLabel(retry, "refresh", t("common.retry"), "lead");
      retry.onclick = async (e) => {
        e.stopPropagation();
        retry.disabled = true;
        // `job_id` et pas `id` : `id` est l'identifiant de la requête WebSocket (conn.js).
        const r = await command("image_retry", { job_id: m.imageJob });
        if (r.ok) toast(t("image.retry_sent"));
        else { retry.disabled = false; toast(t("image.retry_failed", { error: tx(r.error) || t("common.send_failed") }), "error"); }
      };
      parts.push(retry);
      // 28/10 (plan P40 étape 5, lot A5) : manuel en échec, un autre moteur est libre. Jamais sans le
      // clic de l'utilisateur (le cœur refuse ce bouton à Claude) : la qualité change d'un moteur à l'autre.
      const other = String(ev.proposition || "");
      if (/^[a-z0-9_-]{1,20}$/.test(other)) {
        const label = String(ev.proposition_libelle || other);
        const alt = el("button", "image-retry image-other");
        alt.type = "button";
        setLabel(alt, "sparkles", t("image.try_with", { engine: label }), "lead");
        alt.onclick = async (e) => {
          e.stopPropagation();
          alt.disabled = retry.disabled = true;
          const r = await command("image_retry", { job_id: m.imageJob, engine: other });
          if (r.ok) toast(t("image.retry_sent"));
          else {
            alt.disabled = retry.disabled = false;
            toast(t("image.retry_failed", { error: tx(r.error) || t("common.send_failed") }), "error");
          }
        };
        parts.push(alt);
      }
    }
  }
  if (Number(ev.pieces) > 0) parts.splice(2, 0, el("div", "image-pieces", t("image.with_pieces", { n: Number(ev.pieces) })));
  m.node.replaceChildren(...parts);
}

// 28/10 (plan P41 lot B3) : carte d'une VIDÉO IA (astrel/videos_ia.py, évènement `video_job`), aux
// styles de la carte image. Toujours : modèle, durée et crédits estimés. En file ou en cours : l'étape,
// le temps écoulé (ou l'heure de reprise d'un report) et « Annuler ». Finie : la vidéo arrive juste
// dessous en carte de médias (`media`, lecteur au clic). Échec : la raison et le texte du site tel quel.
const videoCards = new Map(); // job → message du fil
const VIDEO_STEPS = ["ouverture", "saisie", "generation", "telechargement", "bascule"];
const videoDone = (m) => m.video && (m.video.etat === "fini" || m.video.etat === "echec");

on("video_job", (ev) => {
  const id = String(ev.job || "");
  if (!/^[\w-]{1,40}$/.test(id)) return;
  let m = videoCards.get(id);
  if (!m) {
    m = addJobCard(ev, videoCards, t("video.aria"), { videoJob: id, video: ev });
    m.node.classList.add("video-job");
  }
  m.video = ev;
  renderVideoCard(m);
  scrollTranscript();
});

const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

function videoStep(ev) {
  if (ev.etat === "file") {
    const until = Number(ev.reporte_jusqua) || 0;
    if (until > Date.now() / 1000) {
      const time = new Date(until * 1000).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
      return t(ev.etape === "pointe" ? "video.deferred_peak" : "video.deferred", { time });
    }
    return ev.position > 1 ? t("image.queued_n", { n: ev.position }) : t("image.queued");
  }
  const step = VIDEO_STEPS.includes(ev.etape) ? ev.etape : "ouverture";
  const engine = String(ev.service_libelle || "") || t("image.the_ai");
  const since = Number(ev.debut) > 0 ? Math.max(0, Date.now() / 1000 - Number(ev.debut)) : 0;
  return `${t(`video.step_${step}`, { engine })} · ${clock(since)}`;
}

function renderVideoCard(m) {
  const ev = m.video;
  const state = IMAGE_STATES.includes(ev.etat) ? ev.etat : "en_cours";
  for (const s of IMAGE_STATES) m.node.classList.toggle(`image-${s}`, s === state);
  m.step = null;
  const head = el("div", "image-head");
  setLabel(head, "play", t("video.head"), "lead");
  const parts = [head, el("div", "image-prompt", stripEmoji(String(ev.prompt || "")))];
  if (ev.modele_libelle && Number(ev.cout) > 0) {
    const info = [t("video.info", { model: String(ev.modele_libelle), s: Number(ev.duree), n: Number(ev.cout) })];
    if (ev.compte) info.push(t("video.account", { account: String(ev.compte) }));
    parts.push(el("div", "image-credit", info.join(" · ")));
  }
  if (state === "file" || state === "en_cours") {
    const load = el("div", "image-loading");
    load.setAttribute("role", "status");
    load.setAttribute("aria-label", t("video.loading_aria"));
    const anim = el("div", "image-shimmer");
    anim.setAttribute("aria-hidden", "true");
    m.step = el("div", "image-step", videoStep(ev));
    load.append(anim, m.step);
    const cancel = el("button", "image-retry");
    cancel.type = "button";
    setLabel(cancel, "x", t("common.cancel"), "lead");
    cancel.onclick = async (e) => {
      e.stopPropagation();
      cancel.disabled = true;
      // `job_id` et pas `id` : `id` est l'identifiant de la requête WebSocket (conn.js).
      const r = await command("video_cancel", { job_id: m.videoJob });
      if (!r.ok) { cancel.disabled = false; toast(tx(r.error) || t("common.send_failed"), "error"); }
    };
    parts.push(load, cancel);
  } else if (state === "fini") {
    parts.push(el("div", "image-credit", t("video.ready")));
  } else {
    parts.push(el("div", "image-error", tx(String(ev.erreur || "")) || t("video.failed")));
    if (ev.texte_ia) {
      parts.push(el("div", "image-ai-label", t("image.ai_said", { engine: String(ev.service_libelle || t("image.the_ai")) })),
        el("blockquote", "image-ai-text", String(ev.texte_ia)));
    }
  }
  m.node.replaceChildren(...parts);
}

// Temps écoulé des vidéos en cours : seule la ligne d'étape est réécrite.
setInterval(() => {
  for (const m of videoCards.values()) if (m.step && !videoDone(m)) m.step.textContent = videoStep(m.video);
}, 1000);

function closeLive(channel, text) {
  const m = liveReplies.get(channel);
  if (!m) return;
  liveReplies.delete(channel);
  m.node.classList.remove("live");
  const changed = !!text && text !== m.raw;
  if (changed) m.raw = text;
  if (changed || m.dirty || m.pending) renderBody(m, true);
}

// 15/10 : les réponses d'Astrel sont rendues en markdown SÛR (markdown.js :
// listes, intertitres, gras, code… jamais de HTML venu du texte). Le texte brut
// reste dans `m.raw` : c'est lui que dit « Lire » et que copie le bouton Copier
// (émojis compris : ils ne sont retirés qu'à l'affichage, voir stripEmoji). Pendant
// l'écriture, le message entier est re-rendu au plus une fois par image (les
// morceaux arrivés entre-temps sont regroupés), et moins souvent si le rendu
// devient coûteux (très longue réponse) ; jamais pendant que l'utilisateur y
// sélectionne du texte, pour ne pas lui retirer la sélection sous la souris.
function scheduleRender(m) {
  if (m.pending) return;
  m.pending = true;
  const run = () => {
    if (!m.pending) return; // déjà rendu (réponse close entre-temps)
    m.pending = false;
    renderBody(m);
    scrollTranscript();
  };
  const wait = Math.min(500, (m.cost || 0) * 5);
  if (wait < 16) requestAnimationFrame(run);
  else setTimeout(run, wait);
}

function renderBody(m, force = false) {
  // 04/11 : message de l'utilisateur en texte brut, seules ses adresses https:// deviennent des liens (markdown.js)
  if (m.who !== "astrel") { m.body.replaceChildren(linkifyText(m.raw)); return; }
  const s = getSelection();
  if (!force && s && !s.isCollapsed && (m.body.contains(s.anchorNode) || m.body.contains(s.focusNode))) {
    m.dirty = true;
    return;
  }
  m.dirty = false;
  m.pending = false;
  const t0 = performance.now();
  m.body.replaceChildren(renderMarkdown(m.raw)); // émojis retirés du texte rendu (markdown.js)
  m.cost = performance.now() - t0;
}

async function copyText(text, done) {
  try {
    await navigator.clipboard.writeText(text);
    toast(done);
  } catch {
    toast(t("common.copy_failed"), "warning");
  }
}

const copyRaw = (m) => copyText(m.raw, t("msg.copied"));

// 04/11 (demande : « les liens ne sont pas ouvrables comme sur ChatGPT, on est obligé de copier-coller ») :
// un lien du fil (markdown.js : https seulement, nœuds DOM, classe md-link) s'ouvre dans le NAVIGATEUR de
// l'utilisateur, par le cœur (`open_link`, réservé au HUD : xdg-open). Dans la fenêtre --app du HUD, target=_blank
// ouvrirait un onglet du profil DÉDIÉ du HUD (ni ses sessions ni ses mots de passe, à côté du cookie du HUD).
// Repli (cœur injoignable, plus ancien, refus) : cet onglet-là, sans opener ni referrer, aussitôt (moins de
// 3 s : le clic autorise encore la fenêtre). Clic du milieu et Ctrl+clic : pareil. Le texte DIT ne change pas.
async function openLink(a) {
  const href = safeHref(a.getAttribute("href"));
  if (!href) return;
  const r = await command("open_link", { url: href }, 0, 3000);
  if (r.ok) { toast(t("link.opened"), "info", "external-link"); return; }
  window.open(href, "_blank", "noopener,noreferrer");
}
function linkClick(e) {
  const a = e.target?.closest?.("a.md-link");
  if (!a || e.button !== (e.type === "auxclick" ? 1 : 0)) return;
  e.preventDefault();
  // Clic à la souris : le lien ne garde pas le focus, l'Entrée suivante reste à la carte de confirmation
  // (au clavier, `detail` vaut 0 : le focus reste sur le lien).
  if (e.detail > 0) a.blur();
  openLink(a);
}
document.addEventListener("click", linkClick);
document.addEventListener("auxclick", linkClick);

function setMid(m, mid) {
  if (!mid) return;
  m.mid = mid;
  byMid.set(mid, m);
}

// 23/10 (demande de l'utilisateur) : heure du message, discrète (heure.js) ; `ts` du cœur, rejoué compris.
function timeEl(ts) {
  const when = msgTime(ts, Date.now() / 1000, locale());
  const time = el("time", "msg-time", when.text);
  time.dateTime = when.iso;
  time.title = when.title;
  return time;
}

function addMsg(who, text, { src = null, channel = defaultChannel, mid = "", replyTo = "", live = false, replay = false, ts = null } = {}) {
  if (mid && byMid.has(mid)) return byMid.get(mid);
  const node = el("div", `msg ${who}${live ? " live" : ""}`);
  if (src) { const s = el("span", "src"); s.append(icon(src)); node.append(s); }
  const body = who === "astrel" ? el("div", "md") : el("span", "");
  node.append(body);
  node.append(timeEl(ts));
  const m = { node, body, raw: text || "", who, channel, mid: "", replyTo: replyTo || "" };
  renderBody(m, true);
  // 04/11 (comme ChatGPT) : « Copier » aussi sur les messages de l'utilisateur, toujours le texte brut (m.raw)
  if (who === "astrel" || who === "user") {
    const copy = el("button", "copy-btn");
    copy.type = "button";
    copy.append(icon("copy"));
    copy.title = t(who === "user" ? "msg.copy_user_title" : "msg.copy_title");
    copy.setAttribute("aria-label", t("common.copy"));
    copy.onclick = (e) => { e.stopPropagation(); copyRaw(m); };
    node.append(copy);
  }
  setMid(m, mid);
  node.addEventListener("contextmenu", (e) => moveMenu(m, e));
  messages.push(m);
  $("#transcript").append(node);
  placeMsg(m);
  pruneChannel(channel);
  if (who === "astrel" && arrivesUnseen(channel, replay, ts)) {
    bumpUnread(channel);
    renderChannels();
  }
  scrollTranscript(who === "user" && !replay); // ce que l'utilisateur vient de dire ramène en bas
  noteArrival(node, replay);
  return m;
}

// 22/10 (relecture P23, lot A) : en Simple, sans onglets, une question à choix ou un compte rendu
// d'un canal de projet était caché et inatteignable ; il s'affiche dans le fil, sans nom de projet.
// Le reste de la conversation d'un projet reste caché (démo).
const simpleShows = (m) => simpleView() && (!!m.choice || !!m.isReport || !!m.isMedia); // P26 : médias aussi
function placeMsg(m) { m.node.classList.toggle("hidden", shownIn(m.channel) !== activeChannel && !simpleShows(m)); }

/** Toast d'une question ou d'un compte rendu arrivé hors du canal ouvert : en Avancé, il mène au
 *  canal du projet ; en Simple (pas d'onglets), au message lui-même, affiché dans le fil. */
function toastLeadsTo(n, c, m) {
  if (!n) return;
  if (c) { n.classList.add("clickable"); n.onclick = () => showChannel(c.id); }
  else if (m && simpleShows(m)) {
    n.classList.add("clickable");
    n.onclick = () => { if (m.node.isConnected) m.node.scrollIntoView({ block: "nearest", behavior: "smooth" }); };
  }
}

function pruneChannel(channel) {
  // Une question à choix sans réponse reste, même ancienne : Astrel l'attend encore.
  // Une image ou une vidéo IA en cours aussi (21/10, 28/10) : sa carte attend le résultat.
  const inChannel = messages.filter((x) => x.channel === channel && !(x.choice && !x.choice.answered)
    && !(x.imageJob && !imageDone(x)) && !(x.videoJob && !videoDone(x)));
  for (const old of inChannel.slice(0, Math.max(0, inChannel.length - PER_CHANNEL))) {
    old.node.remove();
    messages.splice(messages.indexOf(old), 1);
    if (old.mid) byMid.delete(old.mid);
    if (old.choice) choices.delete(old.choice.id);
    if (old.imageJob) imageCards.delete(old.imageJob);
    if (old.videoJob) videoCards.delete(old.videoJob);
  }
}

// Défilement du fil : il ne recolle en bas que si l'utilisateur y était déjà, et
// jamais pendant une sélection (signalé le 08/10 : le fil remontait ou
// bougeait sous la souris). Sinon, le bouton « Aller en bas » y ramène.
const transcript = $("#transcript");
let stickToBottom = true;
let dragLock = null;   // scrollTop gardé tant qu'une sélection à la souris est en cours
let wheelUntil = 0;    // la molette reste permise pendant cette sélection

const atBottom = () => transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 40;
function selectingText() {
  const s = getSelection();
  return dragLock !== null || (s && !s.isCollapsed
    && (transcript.contains(s.anchorNode) || transcript.contains(s.focusNode)));
}
function updateJump() {
  const bottom = atBottom();
  $("#jump-btn").classList.toggle("hidden", bottom);
  if (bottom) setJumpNew(false);
  // Fondu du haut seulement s'il y a des messages cachés au-dessus (16/10 : sinon
  // il rognait le premier message d'un fil qui tient sans défiler).
  transcript.classList.toggle("fade", transcript.scrollTop > 0);
}

// 04/11 (comme ChatGPT) : un message arrivé pendant que l'utilisateur lit plus haut met un point sur le bouton ↓
// (nom accessible « nouveaux messages ») ; retiré dès qu'il est revenu en bas. Rien ne défile pour autant.
let jumpNew = false;
function setJumpNew(on) {
  if (on === jumpNew) return;
  jumpNew = on;
  const j = $("#jump-btn");
  j.classList.toggle("has-new", on);
  j.setAttribute("aria-label", t(on ? "chat.jump_new" : "chat.jump"));
  j.title = t(on ? "chat.jump_new" : "chat.jump_title");
}
function noteArrival(node, replay) {
  if (!replay && !node.classList.contains("hidden") && !atBottom()) setJumpNew(true);
}

function scrollTranscript(force = false) {
  if (force) stickToBottom = true;
  if (stickToBottom && (force ? dragLock === null : !selectingText())) transcript.scrollTop = transcript.scrollHeight;
  updateJump();
}

transcript.addEventListener("scroll", () => {
  // Chrome fait défiler le fil dès qu'une sélection déborde un peu : on l'en empêche.
  if (dragLock !== null && performance.now() > wheelUntil) { transcript.scrollTop = dragLock; return; }
  if (dragLock !== null) dragLock = transcript.scrollTop;
  stickToBottom = atBottom();
  updateJump();
});
transcript.addEventListener("pointerdown", (e) => {
  // pas sur la barre de défilement : la tirer doit rester possible
  if (e.button !== 0 || e.clientX - transcript.getBoundingClientRect().left >= transcript.clientWidth) return;
  dragLock = transcript.scrollTop;
  $("#dialog").classList.add("selecting"); // onglets et poignée ne captent plus la sélection
});
transcript.addEventListener("wheel", () => { wheelUntil = performance.now() + 400; }, { passive: true });
const endDrag = () => { dragLock = null; $("#dialog").classList.remove("selecting"); };
addEventListener("pointerup", endDrag);
addEventListener("pointercancel", endDrag);
addEventListener("blur", endDrag);
addEventListener("pointermove", (e) => { if (dragLock !== null && !(e.buttons & 1)) endDrag(); });
$("#jump-btn").onclick = () => { getSelection().removeAllRanges(); scrollTranscript(true); };
// 19/10 : la barre de saisie grandit avec le texte (carte sur deux étages) ; le bouton ↓
// reste juste au-dessus du bas du fil, et un fil collé en bas le reste quand il rétrécit.
const belowChat = new ResizeObserver(() => {
  const dlg = $("#dialog");
  const gap = dlg.getBoundingClientRect().bottom - transcript.getBoundingClientRect().bottom;
  dlg.style.setProperty("--jump-b", `${Math.round(gap + 14)}px`);
  if (stickToBottom) scrollTranscript();
});
for (const s of ["#transcript", "#confirm", "#dictation", "#attachments", "#composer", "#image-panel"]) belowChat.observe($(s));

// Hauteur du fil : poignée au-dessus, retenue d'une ouverture à l'autre.
const savedChatH = localStorageGet("astrel-chat-h");
if (savedChatH) $("#dialog").style.setProperty("--chat-h", `${savedChatH}px`);
$("#chat-grip").addEventListener("pointerdown", (e) => {
  e.preventDefault();
  const grip = e.currentTarget;
  grip.setPointerCapture(e.pointerId);
  grip.classList.add("dragging");
  const startY = e.clientY;
  const startH = transcript.clientHeight;
  const others = $("#dialog").offsetHeight - transcript.offsetHeight;
  const wasBottom = atBottom();
  const move = (ev) => {
    const h = Math.round(Math.max(80, Math.min(innerHeight - 80 - others, startH + startY - ev.clientY)));
    $("#dialog").style.setProperty("--chat-h", `${h}px`);
    if (wasBottom) transcript.scrollTop = transcript.scrollHeight;
  };
  const up = () => {
    grip.classList.remove("dragging");
    grip.removeEventListener("pointermove", move);
    const h = parseInt($("#dialog").style.getPropertyValue("--chat-h"), 10);
    if (h) localStorageSet("astrel-chat-h", String(h));
  };
  grip.addEventListener("pointermove", move);
  grip.addEventListener("pointerup", up, { once: true });
  grip.addEventListener("pointercancel", up, { once: true });
});
$("#chat-grip").ondblclick = () => {
  $("#dialog").style.removeProperty("--chat-h");
  try { localStorage.removeItem("astrel-chat-h"); } catch { /* sans stockage */ }
  scrollTranscript();
};

// ------------------------------------------------------------------ canaux

// Un fil par projet (liste dans config/channels.json, relue à chaud). La
// session Claude reste UNE seule conversation : seul l'affichage est rangé.
// Le canal ouvert est aussi celui où part ce que l'utilisateur dit ou tape ; le
// cœur peut le faire basculer (« Compta, … ») et le HUD suit.
let channelList = [{ id: "general", label: "Général", color: "#3ad6ff" }];
let defaultChannel = "general";
let activeChannel = "general";
const unread = new Map();
// 27/10 (plan P41, demande de l'utilisateur : « des messages se perdent dans d'autres canaux ») : la
// pastille des non-lus SURVIT au rechargement du HUD. Par canal, l'heure (secondes, horloge du cœur)
// jusqu'à laquelle l'utilisateur l'a vu, dans localStorage ; un message REJOUÉ plus récent, hors du canal
// ouvert, compte comme non lu. Premier lancement (rien de retenu) : rien d'ancien n'est compté.
// Astrel relancé : l'historique repart vide, la pastille aussi (elle ne mène jamais à un fil vide).
const SEEN_KEY = "astrel-chan-seen";
const seen = (() => {
  try {
    const v = JSON.parse(localStorageGet(SEEN_KEY) || "null");
    if (v && Number.isFinite(v.since) && v.ch && typeof v.ch === "object" && !Array.isArray(v.ch)) return v;
  } catch { /* abîmé : on repart de maintenant */ }
  return { since: Date.now() / 1000, ch: {} };
})();
let viewing = false; // vrai après la première ouverture d'un canal (pas le « Général » d'avant la connexion)
const seenAt = (id) => (Number.isFinite(Number(seen.ch[id])) ? Number(seen.ch[id]) : seen.since);
function markSeen(id, ts = Date.now() / 1000) {
  if (!id || ts <= seenAt(id)) return;
  seen.ch[id] = ts;
  localStorageSet(SEEN_KEY, JSON.stringify(seen));
}
/** Un message arrive dans `channel` : non lu s'il tombe hors du canal ouvert (en direct, ou rejoué
 *  mais plus récent que la dernière visite de ce canal). Dans le canal ouvert, il est vu. */
function arrivesUnseen(channel, replay, ts) {
  const id = shownIn(channel);
  const at = Number(ts) || Date.now() / 1000;
  if (id === activeChannel) {
    if (!replay && viewing) markSeen(id, at);
    return false;
  }
  return !replay || at > seenAt(id);
}
function bumpUnread(channel) {
  const id = shownIn(channel);
  unread.set(id, (unread.get(id) || 0) + 1);
}
addEventListener("pagehide", () => { if (viewing) markSeen(activeChannel); });
let dragChan = ""; // onglet en cours de glisser-déposer (id), vide sinon
let orderSending = false; // un channel_reorder en vol
let orderWanted = null; // dernier ordre voulu, pas encore confirmé par le cœur

const channelOf = (id) => channelList.find((c) => c.id === id);
// 20/10 : le canal par défaut s'appelle « Général » dans config/channels.json ; affiché dans la
// langue de l'interface. Les canaux de projet gardent le nom donné par l'utilisateur.
const chanLabel = (c) => (c.id === "general" && c.label === "Général" ? t("channels.general") : c.label);
// Canal retiré de la config : ses messages s'affichent dans le canal par défaut plutôt que de disparaître.
const shownIn = (id) => (channelOf(id) ? id : defaultChannel);
// 22/10 (plan P23, proposition 9) : en affichage Simple, ni onglets ni noms de projets (démo) ;
// les notifications ne disent plus le projet et n'y mènent plus.
const simpleView = () => affichageNow() === "simple";
const chanShown = (id) => (simpleView() ? null : channelOf(id));

function applyChannels(ev) {
  const items = ev.items || ev.channels;
  if (Array.isArray(items) && items.length) channelList = inWantedOrder(items);
  defaultChannel = channelOf(ev.default) ? ev.default : channelList[0].id;
  showChannel(ev.active || activeChannel, false);
}

on("channels", applyChannels);
// Projet ajouté dans config/channels.json : il apparaît dès que le HUD se (re)connecte.
on("hello", async () => { const r = await command("channels"); if (r.ok) applyChannels(r); });

function showChannel(id, tell = true) {
  // P23 : en Simple, sans onglets, le fil reste sur le canal par défaut (même si le cœur
  // rejoue à la connexion un canal de projet resté ouvert)
  const left = activeChannel;
  activeChannel = simpleView() ? defaultChannel : shownIn(id);
  unread.delete(activeChannel);
  // P41 : canal quitté vu jusqu'ici (pas le « Général » d'avant la connexion), canal ouvert vu.
  if (viewing && left !== activeChannel) markSeen(left);
  markSeen(activeChannel);
  viewing = true;
  for (const m of messages) {
    if (shownIn(m.channel) === activeChannel) m.unseen = false;
    placeMsg(m);
  }
  stopHiddenMedia(); // P26 : un son ou une vidéo du canal quitté s'arrête (et rend le micro)
  refreshChoices();
  renderChannels();
  scrollTranscript(true);
  if (tell) {
    command("channel", { active: activeChannel }).then((r) => {
      if (!r.ok) toast(r.error || t("channels.not_changed"), "warning");
    });
  }
}

function renderChannels() {
  const box = $("#channels");
  box.classList.remove("hidden"); // toujours visible : le « + » crée le premier canal de projet
  // Onglet qui avait le focus (Alt+← → enchaînés, non-lu qui redessine) : il le garde.
  const focused = box.contains(document.activeElement) ? document.activeElement.dataset.chan : "";
  box.replaceChildren();
  channelList.forEach((c, i) => {
    const tab = el("button", `channel-tab${c.id === activeChannel ? " on" : ""}`);
    tab.style.setProperty("--chan", c.color);
    tab.title = `${chanLabel(c)}${i < 9 ? ` (Alt+${i + 1})` : ""} — ${t("channels.tab_title")}`;
    tab.dataset.chan = c.id;
    tab.draggable = true;
    if (c.id === dragChan) tab.classList.add("dragging");
    tab.addEventListener("dragstart", (e) => {
      dragChan = c.id;
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", c.label);
      tab.classList.add("dragging");
    });
    tab.addEventListener("dragend", endChannelDrag);
    tab.addEventListener("dragover", (e) => {
      if (!dragChan || dragChan === c.id) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      const after = dropsAfter(tab, e);
      tab.classList.toggle("drop-before", !after);
      tab.classList.toggle("drop-after", after);
    });
    tab.addEventListener("dragleave", () => tab.classList.remove("drop-before", "drop-after"));
    tab.addEventListener("drop", (e) => {
      e.preventDefault();
      const moved = dragChan;
      endChannelDrag();
      if (moved && moved !== c.id) placeChannel(moved, c.id, dropsAfter(tab, e));
    });
    tab.addEventListener("keydown", (e) => {
      // Alt+← / Alt+→ sur l'onglet qui a le focus : le décaler d'un cran.
      if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || !/^Arrow(Left|Right)$/.test(e.key)) return;
      e.preventDefault();
      if (e.repeat) return; // touche maintenue : un cran par appui, pas une rafale d'écritures
      const ids = channelList.map((x) => x.id);
      const at = ids.indexOf(c.id);
      const to = at + (e.key === "ArrowLeft" ? -1 : 1);
      if (at < 0 || to < 0 || to >= ids.length) return;
      ids.splice(at, 1);
      ids.splice(to, 0, c.id);
      reorderChannels(ids);
    });
    tab.append(el("span", "dot"), el("span", "", chanLabel(c)));
    // P41 : nom accessible de l'onglet = nom du canal + ce que disent la pastille et les marques
    // (le nombre seul, ou une couleur, ne dit rien à un lecteur d'écran).
    const said = [chanLabel(c)];
    const n = unread.get(c.id);
    if (n) {
      const pill = el("span", "unread", n > 99 ? "99+" : String(n));
      pill.title = t("channels.unread", { n });
      pill.setAttribute("aria-hidden", "true");
      tab.append(pill);
      said.push(t("channels.unread", { n }));
    }
    // Un compte rendu pas encore écouté dans ce canal.
    if (messages.some((x) => x.pendingRead && shownIn(x.channel) === c.id)) {
      const mark = el("span", "to-read-mark");
      mark.append(icon("volume"));
      mark.title = t("channels.to_read");
      tab.append(mark);
      said.push(t("channels.to_read"));
    }
    // 15/10 : une décision attend l'utilisateur dans ce canal (compte rendu « décision » non lu
    // ou pas encore vu, question à choix sans réponse) : point ambre, visible d'un coup d'œil.
    if (messages.some((x) => shownIn(x.channel) === c.id && ((x.reportKind === "decision" && (x.pendingRead || x.unseen))
      || (x.choice && !x.choice.answered)))) {
      const mark = el("span", "decision-mark");
      mark.title = t("channels.decision");
      tab.append(mark);
      said.push(t("channels.decision"));
    }
    tab.setAttribute("aria-label", said.join(", "));
    if (c.id === activeChannel) tab.setAttribute("aria-current", "true");
    tab.onclick = () => showChannel(c.id);
    tab.addEventListener("contextmenu", (e) => channelMenu(c, e));
    box.append(tab);
    if (focused && c.id === focused) tab.focus();
  });
  const add = el("button", "channel-tab add", "+");
  add.title = t("channels.add_title");
  add.onclick = createChannel;
  box.append(add);
}

// 19/10 (demande de l'utilisateur) : classer les onglets en les faisant glisser. L'ordre
// est celui du tableau « channels » de config/channels.json : le cœur l'écrit et
// republie « channels », les autres HUD suivent, et il revient au rechargement.

function endChannelDrag() {
  dragChan = "";
  for (const t of $("#channels").querySelectorAll(".channel-tab")) t.classList.remove("dragging", "drop-before", "drop-after");
}
// Onglet redessiné pendant le glisser (non-lu arrivé) puis lâché ailleurs : son « dragend »
// ne vient pas. Chrome ne donne aucun mousemove pendant un glisser natif, le premier après le clôt.
addEventListener("mousemove", () => { if (dragChan) endChannelDrag(); });

// Lâché sur la moitié droite d'un onglet : après lui, sinon avant.
function dropsAfter(tab, e) {
  const r = tab.getBoundingClientRect();
  return e.clientX > r.left + r.width / 2;
}

function placeChannel(moved, target, after) {
  const ids = channelList.map((x) => x.id).filter((id) => id !== moved);
  const at = ids.indexOf(target);
  if (at < 0) return;
  ids.splice(after ? at + 1 : at, 0, moved);
  reorderChannels(ids);
}

// Un seul channel_reorder en vol (relecture du 19/10) : les ordres voulus entre-temps
// ne partent pas un par un, seul le DERNIER est envoyé quand le précédent revient
// (`orderSending`, `orderWanted`, déclarés avec `dragChan`).

// Liste reçue du cœur pendant un envoi : elle peut porter un ordre déjà dépassé, on
// garde à l'écran celui que l'utilisateur vient de choisir (les canaux inconnus à la fin).
function inWantedOrder(items) {
  if (!orderWanted) return items;
  const rank = new Map(orderWanted.map((id, i) => [id, i]));
  return [...items].sort((a, b) => (rank.get(a.id) ?? rank.size) - (rank.get(b.id) ?? rank.size));
}

async function reorderChannels(ids) {
  if (ids.join("\n") === channelList.map((x) => x.id).join("\n")) return;
  const byId = new Map(channelList.map((x) => [x.id, x]));
  channelList = ids.map((id) => byId.get(id)).filter(Boolean);
  orderWanted = ids;
  renderChannels(); // tout de suite à l'écran ; le cœur confirme par « channels »
  if (orderSending) return; // la boucle en cours enverra cet ordre-ci
  orderSending = true;
  let error = "";
  while (orderWanted) {
    const order = orderWanted;
    const r = await command("channel_reorder", { order });
    if (!r.ok) { error = r.error || t("channels.order_failed"); orderWanted = null; break; }
    if (orderWanted === order) orderWanted = null;
  }
  orderSending = false;
  if (!error) return;
  toast(error, "error");
  const now = await command("channels");
  if (now.ok) applyChannels(now);
}

// 14/10 : créer, renommer, relier à un dossier, supprimer un canal sans éditer
// config/channels.json. Le dossier lié est rappelé à Claude quand on y parle.
// 15/10 : une seule fenêtre du HUD (nom, dossier, mots-clés) au lieu des prompt() du navigateur.
async function createChannel() {
  const v = await openModal({
    title: t("channels.new"),
    text: t("channels.new_text"),
    fields: [
      { name: "label", label: t("channels.name"), placeholder: t("channels.name_ph"), required: true },
      { name: "workdir", label: t("channels.workdir"), placeholder: "~/Projets/…",
        hint: t("channels.workdir_hint") },
      { name: "keywords", label: t("channels.keywords"), placeholder: t("channels.keywords_ph"), hint: t("channels.keywords_hint") },
    ],
    ok: t("channels.create"),
  });
  if (!v) return;
  const { label, workdir, keywords } = v;
  const r = await command("channel_create", { label, workdir, keywords });
  if (!r.ok) { toast(r.error || t("channels.not_created"), "error"); return; }
  toast(t(r.workdir ? "channels.created_dir" : "channels.created", { label: r.label, dir: r.workdir }));
  showChannel(r.channel);
}

function channelMenu(c, e) {
  e.preventDefault();
  document.querySelectorAll(".menu").forEach((x) => x.remove());
  const menu = el("div", "menu");
  menu.append(el("div", "small muted menu-head", `${chanLabel(c)}${c.workdir ? ` · ${c.workdir}` : ""}`));
  const item = (text, fn) => { const i = el("div", "item", text); i.onclick = () => { menu.remove(); fn(); }; menu.append(i); };
  item(t("channels.edit_menu"), async () => {
    const words = (c.keywords || []).join(", ");
    const v = await openModal({
      title: t("channels.edit_title", { label: chanLabel(c) }),
      fields: [
        { name: "label", label: t("channels.name"), value: c.label, required: true },
        { name: "workdir", label: t("channels.workdir"), value: c.workdir || "", placeholder: "~/Projets/…",
          hint: t("channels.workdir_none") },
        { name: "keywords", label: t("channels.keywords"), value: words, hint: t("channels.keywords_hint") },
      ],
      ok: t("common.save"),
    });
    if (!v) return;
    const args = { channel: c.id };
    if (v.label !== c.label) args.label = v.label;
    if (v.workdir !== (c.workdir || "")) args.workdir = v.workdir;
    if (v.keywords !== words) args.keywords = v.keywords;
    if (Object.keys(args).length === 1) return;
    const r = await command("channel_edit", args);
    if (!r.ok) toast(r.error || t("channels.unchanged"), "error");
  });
  if (c.id !== defaultChannel) {
    item(t("channels.delete_menu"), async () => {
      const yes = await openModal({
        title: t("channels.delete_title", { label: chanLabel(c) }),
        text: t("channels.delete_text"),
        ok: t("common.delete"), danger: true,
      });
      if (!yes) return;
      const r = await command("channel_delete", { channel: c.id });
      if (!r.ok) toast(r.error || t("channels.not_deleted"), "error");
    });
  }
  menu.style.left = `${Math.min(e.clientX, innerWidth - 280)}px`;
  menu.style.bottom = `${innerHeight - e.clientY + 4}px`;
  document.body.append(menu);
  setTimeout(() => addEventListener("click", () => menu.remove(), { once: true }), 0);
}

addEventListener("keydown", (e) => {
  // Alt+1…9 : le code de la touche, pas le caractère (sur AZERTY, « 1 » est « & »).
  const digit = /^Digit([1-9])$/.exec(e.code);
  if (!e.altKey || e.ctrlKey || e.metaKey || !digit || confirmId || simpleView()) return; // Simple : pas d'onglets
  const c = channelList[+digit[1] - 1];
  if (!c) return;
  e.preventDefault();
  showChannel(c.id);
});

// 04/11 (raccourci courant des messageries) : « / » hors d'un champ met le curseur dans la barre, au bout du
// texte. Rien pendant une boîte ouverte ; une carte de confirmation ne répond jamais à « / ».
addEventListener("keydown", (e) => {
  if (e.key !== "/" || e.ctrlKey || e.altKey || e.metaKey || e.defaultPrevented || e.repeat || typingIn()) return;
  if (document.querySelector(".modal-back")) return;
  e.preventDefault();
  const ask = $("#ask");
  ask.focus();
  ask.setSelectionRange(ask.value.length, ask.value.length);
});

// Un message mal rangé se déplace à la main. Celui de l'utilisateur emmène la
// réponse d'Astrel ; une réponse seule ne déplace qu'elle-même.
function moveMenu(m, e) {
  if (!m.mid || channelList.length < 2 || simpleView()) return; // Simple : les noms de projets restent cachés
  // 04/11 : clic droit sur un LIEN : le menu de Chrome (« Copier l'adresse du lien »), pas celui des canaux
  if (e.target?.closest?.("a.md-link")) return;
  e.preventDefault();
  document.querySelectorAll(".menu").forEach((x) => x.remove());
  const menu = el("div", "menu");
  menu.append(el("div", "small muted menu-head", t(m.who === "user" ? "channels.move_msg" : "channels.move_reply")));
  for (const c of channelList) {
    if (c.id === shownIn(m.channel)) continue;
    const item = el("div", "item");
    const dot = el("span", "dot");
    dot.style.background = c.color;
    item.append(dot, document.createTextNode(chanLabel(c)));
    item.onclick = async () => {
      menu.remove();
      const r = await command("channel_move", { mid: m.mid, channel: c.id });
      if (!r.ok) toast(r.error || t("channels.not_moved"), "error");
    };
    menu.append(item);
  }
  menu.style.left = `${Math.min(e.clientX, innerWidth - 280)}px`;
  menu.style.bottom = `${innerHeight - e.clientY + 4}px`;
  document.body.append(menu);
  setTimeout(() => addEventListener("click", () => menu.remove(), { once: true }), 0);
}

on("channel_move", (ev) => {
  for (const m of messages) {
    if (m.mid !== ev.mid && m.replyTo !== ev.mid) continue;
    for (const [channel, live] of liveReplies) {
      if (live === m) { liveReplies.delete(channel); liveReplies.set(ev.channel, m); }
    }
    m.channel = ev.channel;
    placeMsg(m);
  }
  renderChannels();
  scrollTranscript();
});

// Un message tapé n'est effacé qu'une fois REÇU par Astrel. Tant qu'il ne
// l'est pas, il est gardé en brouillon (sessionStorage) : il survit à une
// reconnexion ou à un rechargement de la page, et revient dans la barre si
// l'envoi échoue.
const DRAFT_KEY = "astrel-draft";
try {
  const draft = sessionStorage.getItem(DRAFT_KEY);
  if (draft) $("#ask").value = draft;
} catch { /* navigation privée */ }

// 15/10 : la barre est multiligne. Entrée envoie, Maj+Entrée passe à la
// ligne ; pendant une composition (IME, dictée du système : `isComposing`, touche 229)
// l'Entrée valide la composition et n'envoie RIEN. La barre grandit jusqu'à 8 lignes
// (19/10 : marges du champ comprises, 12 px, comme le max-height de styles.css).
const ASK_MAX_PX = 8 * 19 + 12;
function fitAsk() {
  const a = $("#ask");
  a.style.height = "auto";
  a.style.height = `${Math.min(ASK_MAX_PX, a.scrollHeight)}px`;
  a.style.overflowY = a.scrollHeight > ASK_MAX_PX ? "auto" : "hidden";
}
let composing = false;
$("#ask").addEventListener("compositionstart", () => { composing = true; });
$("#ask").addEventListener("compositionend", () => { setTimeout(() => { composing = false; }, 0); });
$("#ask").addEventListener("input", (e) => {
  fitAsk();
  if (!e.target.value.trim() && !e.target.dataset.sending) releaseDictations();
});
fitAsk();

$("#ask").addEventListener("keydown", async (e) => {
  if (e.key !== "Enter" || e.isComposing || composing || e.keyCode === 229) return;
  if (e.shiftKey) return; // Maj+Entrée : retour à la ligne (comportement normal du champ)
  e.preventDefault(); // Entrée seule n'insère pas de ligne : elle envoie
  const input = e.target;
  const text = input.value.trim();
  if ((!text && !attached.length) || input.dataset.sending) return;
  input.dataset.sending = "1";
  input.classList.add("sending");
  try { sessionStorage.setItem(DRAFT_KEY, text); } catch { /* navigation privée */ }
  const release = () => { delete input.dataset.sending; input.classList.remove("sending"); };
  // 22/10 : image collée juste avant Entrée, encore en préparation : on l'attend (≈ 0,1 à 1 s),
  // PREP_WAIT_MS au plus (relecture : un décodage qui ne répond jamais bloquait Entrée pour de bon).
  const waiting = attached.filter((a) => a.pending);
  if (waiting.length) {
    await Promise.race([Promise.allSettled(waiting.map((a) => a.ready)), new Promise((r) => setTimeout(r, PREP_WAIT_MS))]);
  }
  // Préparation ratée pendant l'attente ou juste avant (30 s) : rien ne part, le texte reste dans
  // la barre et la pièce est nommée (le texte partait seul, un simple toast prévenait). Signalé
  // une fois : l'Entrée suivante envoie sans elle.
  const failed = recentFailures.splice(0).filter((f) => Date.now() - f.at < FAIL_BLOCK_MS);
  if (failed.length) {
    release();
    toast(t("att.failed_kept", { names: failed.map((f) => f.name).join(", "), n: failed.length }), "error");
    return;
  }
  if (waiting.length) {
    const stuck = waiting.filter((a) => a.pending && attached.includes(a));
    if (stuck.length) {
      const go = await openModal({
        title: t("att.stuck_title"),
        text: t("att.stuck_text", { names: stuck.map((a) => a.name).join(", "), s: Math.round(PREP_WAIT_MS / 1000) }),
        ok: t(text ? "att.stuck_send" : "att.stuck_remove"), cancel: t("att.stuck_wait"),
      });
      if (go) for (const a of stuck) removeAttachment(a);
      if (!go || !text) { release(); input.focus(); return; } // brouillon gardé, Entrée réessaiera
    }
  }
  const sent = attached.filter((a) => !a.pending);
  if (!text && !sent.length) { release(); return; }
  const args = { text, channel: activeChannel };
  // `id` : le cœur ne joint pas deux fois une pièce déjà partie avec une phrase dite.
  if (sent.length) args.attachments = sent.map(({ id, name, type, data }) => ({ id, name, type, data }));
  // 17/10 : les dictées versées dans la barre partent dans CE message (le cœur les marque
  // envoyées, un HUD rechargé ne propose plus de les envoyer une seconde fois).
  const dicts = [...dictInBar];
  if (dicts.length) args.resend_of = dicts.map((m) => m.mid).filter(Boolean);
  const r = await command("say", args, 10000, sent.length ? 60000 : 15000);
  delete input.dataset.sending;
  input.classList.remove("sending");
  if (r.ok) {
    for (const m of dicts) { dictInBar.delete(m); markResent(m); }
    const left = input.value.trimStart();
    if (left.trim() === text) { input.value = ""; fitAsk(); }
    // Dictée arrivée PENDANT l'envoi (ajoutée derrière) : seule elle reste dans la barre.
    else if (left.startsWith(text)) { input.value = left.slice(text.length).trimStart(); fitAsk(); }
    try { sessionStorage.removeItem(DRAFT_KEY); } catch { /* navigation privée */ }
    // 16/10 : seules les pièces réellement parties quittent la barre ; une pièce refusée y reste.
    const refused = Array.isArray(r.refused) ? r.refused : [];
    const kept = new Set(refused.map((x) => x.id));
    for (const a of sent) if (!kept.has(a.id)) removeAttachment(a, true);
    if (refused.length) {
      toast(t("ask.sent_without", { errors: refused.map((x) => tx(x.error)).join(" ; "), n: refused.length }), "warning");
    }
  } else {
    if (!input.value.trim()) { input.value = text; fitAsk(); }
    toast(t("ask.not_sent", { error: tx(r.error) || t("common.send_failed") }), "error");
  }
});
// 04/11 (comme un terminal) : ↑ dans la barre VIDE reprend le dernier message de l'utilisateur du canal ouvert
// (tapé ou dit, pour le corriger ou le renvoyer), ↑ encore le précédent ; ↓ revient vers le plus récent, puis
// à la barre vide. Seulement tant que le texte repris est intact et le curseur sur sa première (↑) ou sa
// dernière ligne (↓) : sinon les flèches déplacent le curseur, comme d'habitude. Rien n'est envoyé.
let recall = null; // { list: textes du plus récent au plus ancien, at: indice montré (-1 : barre vide) }
function recallList() {
  const out = [];
  for (let k = messages.length - 1; k >= 0 && out.length < 50; k--) {
    const m = messages[k];
    if (m.who !== "user" || !["clavier", "voix"].includes(m.source) || !m.raw.trim() || !m.node.isConnected
      || shownIn(m.channel) !== activeChannel) continue;
    if (out[out.length - 1] !== m.raw) out.push(m.raw); // deux fois le même de suite : une seule entrée
  }
  return out;
}
$("#ask").addEventListener("keydown", (e) => {
  if ((e.key !== "ArrowUp" && e.key !== "ArrowDown") || e.isComposing || composing || e.keyCode === 229
    || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
  const a = e.target;
  const recalled = !!recall && recall.at >= 0 && a.value === recall.list[recall.at];
  if (!recalled && (a.value || e.key === "ArrowDown")) return;
  if (recalled) {
    const start = a.selectionStart, end = a.selectionEnd;
    if (start !== end) return;
    if (e.key === "ArrowUp" ? a.value.slice(0, start).includes("\n") : a.value.slice(end).includes("\n")) return;
  } else recall = { list: recallList(), at: -1 };
  e.preventDefault();
  const at = recall.at + (e.key === "ArrowUp" ? 1 : -1);
  if (at >= recall.list.length) return; // déjà le plus ancien
  recall.at = at;
  a.value = at < 0 ? "" : recall.list[at];
  if (at < 0) recall = null;
  fitAsk();
  a.setSelectionRange(a.value.length, a.value.length);
});

// ------------------------------------------------------------------ pièces jointes (14/10)

// Trombone, Ctrl+V d'une image dans la barre, glisser-déposer n'importe où sur la page.
// Une image est RÉDUITE ici (≈ 256 Ko, côté long ≤ 1568 px) avant de partir :
// une capture brute coûterait bien plus de tokens pour la même lecture.
const MAX_FILE = 5 * 1024 * 1024;
const IMAGE_MAX = 256 * 1024;
const IMAGE_SIDE = 1568;
const PREP_WAIT_MS = 15000; // Entrée attend une pièce en préparation au plus 15 s, puis propose
const FAIL_BLOCK_MS = 30000; // pièce ratée depuis moins de 30 s : la prochaine Entrée ne part pas sans le dire
const recentFailures = []; // { name, at } pas encore signalées par Entrée
function noteFailed(a) {
  a.failed = true;
  // 23/10 (relecture P26) : pièce déjà retirée (croix) pendant sa préparation : l'utilisateur n'en veut plus,
  // la prochaine Entrée ne doit pas être bloquée en la nommant.
  if (!attached.includes(a)) return;
  recentFailures.push({ name: a.name, at: Date.now() });
}
const attached = []; // { id, name, type, data (base64), url (miniature), size, pending, failed, ready }

// URL d'objet affichée par la visionneuse : révoquée seulement à sa fermeture (relecture du
// 22/10 : la vignette d'origine révoquée après la réduction cassait l'image ouverte en grand).
const viewedUrls = new Map(); // url → Promise de fermeture
function revokeUrl(url) {
  if (!url) return;
  const open = viewedUrls.get(url);
  if (open) open.then(() => URL.revokeObjectURL(url));
  else URL.revokeObjectURL(url);
}
function viewAttachment(a) {
  const url = a.url;
  const v = openImage(url, a.name);
  if (!v) return null;
  viewedUrls.set(url, v.closed);
  v.closed.then(() => { if (viewedUrls.get(url) === v.closed) viewedUrls.delete(url); });
  return v;
}

// Phrase DITE partie pendant qu'une pièce se préparait : le cœur n'a joint que les pièces prêtes
// (`attach_stage` exclut celles en préparation). Le dire, au lieu de la laisser partir sans rien.
function warnVoiceWithoutPending() {
  const pending = attached.filter((a) => a.pending);
  if (pending.length) toast(t("att.voice_without", { names: pending.map((a) => a.name).join(", "), n: pending.length }), "warning");
}
const attachmentId = () => (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "").slice(0, 16)
  : Math.random().toString(16).slice(2, 18));

const toBase64 = (blob) => new Promise((resolve, reject) => {
  const r = new FileReader();
  r.onload = () => resolve(String(r.result).split(",", 2)[1] || "");
  r.onerror = () => reject(r.error);
  r.readAsDataURL(blob);
});

async function shrinkImage(file) {
  const bmp = await createImageBitmap(file);
  let scale = Math.min(1, IMAGE_SIDE / Math.max(bmp.width, bmp.height));
  if (file.size <= IMAGE_MAX && scale === 1 && /^image\/(jpeg|png|gif|webp)$/.test(file.type)) return file;
  const canvas = document.createElement("canvas");
  let blob = file;
  for (let i = 0; i < 6; i++) {
    canvas.width = Math.max(1, Math.round(bmp.width * scale));
    canvas.height = Math.max(1, Math.round(bmp.height * scale));
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#fff"; // transparence sur fond blanc, pas noir
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
    for (const q of [0.82, 0.65]) {
      blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", q));
      if (blob && blob.size <= IMAGE_MAX) return blob;
    }
    // Le poids suit la surface : on vise directement la bonne taille (au lieu de paliers fixes).
    scale *= Math.max(0.3, Math.min(0.85, Math.sqrt(IMAGE_MAX / blob.size) * 0.95));
  }
  return blob;
}

// 22/10 (demande : « le copié-collé de screen n'est pas instantané dans le chat ») : la vignette
// attendait la réduction (décodage, redimensionnement, JPEG jusqu'à 6 fois) ET l'encodage base64 de
// TOUTES les pièces. Elle s'affiche maintenant tout de suite (URL d'objet du fichier collé), marquée
// « préparation… » ; la réduction et l'encodage se font derrière (`a.ready`), puis la pièce est
// déposée au cœur. Une pièce en préparation n'est jamais déposée ni envoyée sans ses données :
// Entrée attend qu'elle soit prête.
function addFiles(files) {
  const jobs = [];
  for (const file of files) {
    if (attached.length >= 6) { toast(t("att.max"), "warning"); break; }
    // 16/10 : un fichier vide serait refusé par le cœur ; il ne doit pas entrer dans la barre.
    if (!file.size) { toast(t("att.empty", { name: file.name || t("att.file") }), "warning"); continue; }
    const isImage = file.type.startsWith("image/");
    const a = { id: attachmentId(), name: file.name || "image.png", type: file.type, data: "", size: file.size,
      url: isImage ? URL.createObjectURL(file) : "", pending: true };
    attached.push(a);
    a.ready = prepareAttachment(a, file, isImage);
    jobs.push(a.ready);
  }
  renderAttachments();
  return Promise.all(jobs);
}

async function prepareAttachment(a, file, isImage) {
  try {
    const blob = isImage ? await shrinkImage(file) : file;
    if (blob.size > MAX_FILE) {
      toast(t("att.too_big", { name: file.name, mb: Math.round(file.size / 1048576) }), "warning");
      noteFailed(a); // Entrée qui l'attendait ne part pas sans elle
      removeAttachment(a);
      return;
    }
    const data = await toBase64(blob);
    if (blob !== file) {
      a.name = file.name.replace(/\.\w+$/, "") + ".jpg";
      // la vignette montre ce qui part ; l'original (souvent plusieurs Mo) est libéré
      if (attached.includes(a)) {
        const url = URL.createObjectURL(blob);
        try { const im = new Image(); im.src = url; await im.decode(); } catch { /* vignette redessinée quand même */ }
        if (attached.includes(a)) {
          revokeUrl(a.url); // visionneuse ouverte sur l'original : révoquée à sa fermeture
          a.url = url;
        } else URL.revokeObjectURL(url); // retirée pendant le décodage
      }
    }
    Object.assign(a, { type: blob.type || file.type, data, size: blob.size, pending: false });
  } catch (err) {
    toast(t("att.failed", { name: file.name, error: err.message || err }), "error");
    noteFailed(a);
    removeAttachment(a);
    return;
  }
  if (attached.includes(a)) renderAttachments(); // retirée entre-temps (croix) : rien à redessiner
}

// 16/10 : une pièce PARTIE (`sent`) est gardée un moment : si le cœur n'a pas pu transmettre
// le message (`unsent`), elle revient dans la barre au lieu d'être perdue sans le dire.
const sentAttachments = new Map(); // id → pièce
const SENT_KEEP = 12;
// `unsent` reçu AVANT la réponse de `say` (l'ordre n'est pas garanti) : la pièce est encore
// dans la barre, elle doit y rester quand la réponse arrive.
const unsentPending = new Set();

function removeAttachment(a, sent = false) {
  const i = attached.indexOf(a);
  if (i < 0) return;
  if (sent && unsentPending.delete(a.id)) return;
  attached.splice(i, 1);
  if (sent && a.id) {
    sentAttachments.set(a.id, a);
    while (sentAttachments.size > SENT_KEEP) {
      const [id, old] = sentAttachments.entries().next().value;
      sentAttachments.delete(id);
      revokeUrl(old.url);
    }
  } else revokeUrl(a.url);
  renderAttachments();
}

function restoreAttachments(ids) {
  let back = 0, lost = 0;
  for (const id of ids) {
    const a = sentAttachments.get(id);
    if (attached.some((x) => x.id === id)) { unsentPending.add(id); back++; continue; }
    if (!a || attached.length >= 6) { lost++; continue; }
    sentAttachments.delete(id);
    attached.push(a);
    back++;
  }
  if (back) renderAttachments();
  if (back) toast(t("att.back", { n: back }), "warning");
  if (lost) toast(t("att.lost", { n: lost }), "warning");
}

function renderAttachments() {
  const box = $("#attachments");
  box.replaceChildren();
  box.classList.toggle("hidden", !attached.length);
  for (const a of attached) {
    const item = el("div", a.pending ? "att att-pending" : "att");
    if (a.pending) item.setAttribute("aria-busy", "true");
    if (a.url) {
      // 19/10 : clic sur la miniature = image en grand (hud/src/viewer.js). Le clic ne
      // prend pas le focus : la barre de saisie le retrouve à la fermeture.
      const view = el("button", "att-view");
      view.type = "button";
      view.title = t("att.view");
      view.setAttribute("aria-label", t("att.view_aria", { name: a.name }));
      const img = el("img"); img.src = a.url; img.alt = a.name;
      view.append(img);
      view.addEventListener("mousedown", (e) => e.preventDefault());
      view.onclick = () => {
        const v = viewAttachment(a);
        // bouton redessiné pendant l'affichage (pièce partie) : le focus revient à la barre
        v?.closed.then(() => { if (document.activeElement === document.body) $("#ask").focus(); });
      };
      item.append(view);
    } else { const ic = el("span", "att-icon"); ic.append(icon("clip")); item.append(ic); }
    item.append(el("span", "att-name", `${a.name} · ${a.pending ? t("att.preparing")
      : t("unit.kb", { n: Math.max(1, Math.round(a.size / 1024)) })}`));
    const x = el("button", "close");
    x.append(icon("x"));
    x.title = t("common.remove");
    x.setAttribute("aria-label", t("common.remove"));
    x.onclick = () => removeAttachment(a);
    item.append(x);
    box.append(item);
  }
  $("#ask").placeholder = t(attached.length ? "ask.placeholder_att" : "ask.placeholder");
  stageAttachments();
}

// 16/10 (demande : « je colle une image, je dicte ma phrase, la phrase part seule ») :
// les pièces en attente sont aussi DÉPOSÉES au cœur (`attach_stage`, la liste entière à
// chaque changement, vide = rien en attente). La prochaine phrase DITE part avec elles,
// en un seul message ; le cœur publie alors `attachments_sent` et elles quittent la barre.
// Entrée, elle, les envoie toujours elle-même (commande `say`, inchangée).
let stageTimer = 0;
function stageAttachments() {
  clearTimeout(stageTimer);
  stageTimer = setTimeout(async () => {
    // 22/10 : une pièce encore en préparation n'a pas ses données ; déposée dès qu'elle est prête.
    const items = attached.filter((a) => !a.pending).map(({ id, name, type, data }) => ({ id, name, type, data }));
    const r = await command("attach_stage", { attachments: items }, 10000, items.length ? 60000 : 15000);
    if (!r.ok && items.length) toast(t("att.stage_failed", { error: tx(r.error) || t("att.stage_default") }), "warning");
  }, 150);
}
on("attachments_sent", (ev) => {
  const ids = new Set(Array.isArray(ev.ids) ? ev.ids : []);
  for (const a of attached.filter((x) => ids.has(x.id))) removeAttachment(a, true);
});
// Le cœur relancé a perdu le dépôt : le refaire (rien si la barre est vide).
on("hello", () => { if (attached.length) stageAttachments(); });
on("hello", () => resendMicHold()); // 23/10 (relecture P26) : micro retenu redemandé après une reconnexion
// Page fermée ou rechargée : ses pièces disparaissent, elles ne doivent pas partir avec la phrase suivante.
addEventListener("pagehide", () => { if (attached.length) command("attach_stage", { attachments: [] }, 0); });

$("#attach-btn").onclick = () => $("#attach-input").click();
$("#attach-input").onchange = (e) => { addFiles([...e.target.files]); e.target.value = ""; };
$("#ask").addEventListener("paste", (e) => {
  const files = [...(e.clipboardData?.files || [])];
  if (!files.length) return; // du texte : collage normal
  e.preventDefault();
  addFiles(files);
});
addEventListener("dragover", (e) => {
  if ([...(e.dataTransfer?.types || [])].includes("Files")) { e.preventDefault(); document.body.classList.add("dropping"); }
});
addEventListener("dragleave", (e) => { if (!e.relatedTarget) document.body.classList.remove("dropping"); });
addEventListener("drop", (e) => {
  document.body.classList.remove("dropping");
  if (!e.dataTransfer?.files?.length) return;
  e.preventDefault();
  addFiles([...e.dataTransfer.files]);
  $("#ask").focus();
});

$("#mic-btn").onclick = () => command("toggle");
$("#wake-btn").onclick = () => command(currentState === "listening" ? "endlisten" : "wake");
$("#pause-btn").onclick = togglePause;
$("#paused-note").onclick = togglePause;
// (14/10) Le bouton « couper la voix » faisait doublon avec la pause et ne valait que
// pour le tour en cours : retiré. Ne plus entendre Astrel = mode Silencieux.
$("#stop-btn").onclick = () => command("stop");
$("#reset-btn").onclick = () => command("reset");
$("#log-btn").onclick = () => $("#activity").classList.toggle("hidden");
// 28/10 (T10, décision de l'utilisateur) : bouton « Éteindre » retiré de la barre ; même boîte et même commande
// `quit` depuis Réglages › Système › « Éteindre Astrel » (settings.js, onHudAction), et menu de la bulle.
onHudAction("power", async () => {
  const yes = await openModal({
    title: t("power.confirm_title"),
    text: t("power.confirm_text"),
    note: t("power.confirm_note"),
    ok: t("power.ok"), danger: true,
  });
  if (!yes) return;
  const r = await command("quit");
  if (!r.ok) toast(r.error || t("power.failed"), "error");
});
document.querySelectorAll("[data-close]").forEach((b) => (b.onclick = () => $("#" + b.dataset.close).classList.add("hidden")));
// Le panneau Activité se ferme aussi d'un clic à côté (le bouton qui l'ouvre garde son
// rôle de bascule) ou avec Échap quand aucune fenêtre n'est ouverte par-dessus.
document.addEventListener("pointerdown", (e) => {
  const panel = $("#activity");
  if (panel.classList.contains("hidden")) return;
  if (panel.contains(e.target) || e.target.closest("#log-btn") || e.target.closest(".modal-back")) return;
  panel.classList.add("hidden");
});
addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || e.defaultPrevented || document.querySelector(".modal-back")) return;
  $("#activity").classList.add("hidden");
});
initSettings(toast); // Réglages (15/10) : hud/src/settings.js
initCalibrage(); // 02/11 : assistant de calibrage du micro (hud/src/calibrage.js)
// 28/10 (plan P40 lot A5) : panneau « Images » sous la barre, Réglages › Images, évènement `ia_etat`
initImages({ toast, channel: () => activeChannel });
initGuide(toast); // Guide de démarrage (18/10, plan P5) : hud/src/guide.js
// Page « Tout ce qu'Astrel sait faire » (plan P22) : ouverte par un bouton du Guide. Ses liens
// « Ouvrir » portent un id de GROUPE des Réglages ; openSettings attend une CLÉ : on déplie le
// groupe par son premier réglage (sinon la page s'ouvre simplement).
initCapacites({
  openGuide: (topic) => openGuide(topic),
  openSettings: async (gid) => {
    const s = await loadSettings();
    const g = (s?.groups || []).find((x) => x?.id === gid);
    openSettings((g?.items || []).find((it) => it?.key)?.key);
  },
});
initPlans(toast); // Plans (15/10, mode plan) : hud/src/plans.js
initTodo(toast); // Tâches par projet (26/10, plan P38 lot 2) : hud/src/todo.js
initAudit(); // Journal d'audit (plan P3, lot F) : hud/src/audit.js
initPropositions(toast); // Propositions de mémoire (04/11, T49) : hud/src/propositions.js

// ------------------------------------------------------------------ activité

const toolRows = new Map();

on("tool", (ev) => {
  const row = activity("tool", `${ev.tool}`, ev.desc, ev.ts);
  toolRows.set(ev.id, row);
});
on("tool_result", (ev) => {
  const row = toolRows.get(ev.id);
  if (row) row.classList.add(ev.ok ? "ok" : "err");
});
on("confirm", (ev) => {
  if (ev.pending) {
    if (!ev.replay) openConfirm(ev);
    activity("confirm", t("activity.confirm"), tx(ev.desc), ev.ts);
  } else {
    closeConfirm(ev.id);
    if (!ev.replay) activity("confirm", t(ev.allow ? "activity.accepted" : "activity.refused"), `${tx(ev.desc)} (${tx(ev.by)})`, ev.ts);
  }
});
on("notice", (ev) => {
  // Fin d'une tâche de fond : le toast dit pour quel projet, et y mène d'un clic.
  // P41 : déjà dans le canal ouvert, le toast ne le nomme pas (il y mènerait sans rien changer).
  const c = ev.channel && channelList.length > 1 && (ev.replay || shownIn(ev.channel) !== activeChannel)
    ? chanShown(ev.channel) : null;
  const text = c ? `${chanLabel(c)} · ${tx(ev.text)}` : tx(ev.text);
  if (!ev.replay) {
    const n = toast(text, ev.level);
    if (n && c) { n.classList.add("clickable"); n.onclick = () => showChannel(c.id); }
  }
  activity(ev.level === "error" ? "err" : "", t("activity.info"), text, ev.ts);
});

function activity(kind, title, text, ts) {
  const row = el("div", `act ${kind}`);
  const time = new Date((ts || Date.now() / 1000) * 1000).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  row.append(el("span", "t", time), el("b", "", title + " "), el("span", "muted", stripEmoji(text || "")));
  const list = $("#activity-list");
  list.prepend(row);
  while (list.children.length > 200) list.lastChild.remove();
  return row;
}

// ------------------------------------------------------------------ en cours

// Sous-agents et commandes que Claude fait tourner, surtout en arrière-plan :
// sans ce panneau, rien ne montrait qu'ils travaillaient encore, ni où ils en étaient.
let tasks = [];
// 21/10 (plan P18) : tâches finies (champ `done`, absent d'un cœur ancien ou de la démo : aucune),
// gardées grisées 10 min pour qu'un clic montre leur résultat et leurs échecs.
let doneTasks = [];
const DONE_KEEP_S = 600; // comme astrel/detail_agent.DONE_KEEP_S ; filtré ici aussi (le cœur peut publier en retard)
// 22/10 (plan P23 lot B, point 5 et point 15 de la note du 21/10, l'utilisateur : les tâches finies noient
// les agents vivants) : « En cours » ne garde que ce qui tourne et ce qui a fini il y a moins de
// 2 min ; les autres tâches finies passent dans « Historique », section repliée sous la liste.
// Seuls les AGENTS finis restent ces 2 min (consigne de l'utilisateur) : une commande finie (`kind` autre
// que « agent », ex. local_bash) va tout de suite dans l'Historique, c'est elles qui noyaient la liste.
const RECENT_DONE_S = 120;
let tasksAt = Date.now() / 1000; // réception du dernier `tasks` : `elapsed_s` y avance ensuite seul

const duration = (sec) => {
  const s = Math.max(0, Math.round(sec));
  return s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")}` : `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;
};

// 15/10 (idée 10) : tokens à la française, « 1,2 M », « 340 k », « 4,5 k » (20/10 : « 1.2 M » en anglais).
function formatTokens(n) {
  n = Math.max(0, Number(n) || 0);
  const one = (x) => x.toFixed(1).replace(/\.0$/, "").replace(".", t("unit.decimal"));
  if (n < 1000) return String(Math.round(n));
  if (n < 10_000) return `${one(n / 1000)} k`;
  if (n < 999_500) return `${Math.round(n / 1000)} k`;
  if (n < 99_950_000) return `${one(n / 1e6)} M`;
  return `${Math.round(n / 1e6)} M`;
}

function taskSeconds(t) {
  if (t.done) return Number(t.elapsed_s) || 0; // finie : durée figée
  if (typeof t.elapsed_s === "number") return t.elapsed_s + Math.max(0, Date.now() / 1000 - tasksAt);
  return Date.now() / 1000 - (t.started || Date.now() / 1000);
}

// Lignes GARDÉES d'un rendu à l'autre (par id de tâche) : le panneau se met à jour
// chaque seconde, un bouton refait à chaque fois perdrait le clic en cours.
const taskRows = new Map(); // id → { row, title, time, step, cost, alert, stop }

function taskRow(task) {
  const key = task.id || task.desc;
  let r = taskRows.get(key);
  if (r) return r;
  r = { row: el("div", "task"), title: el("b"), time: el("span", "time"), step: el("div", "step muted"),
    cost: el("div", "cost"), alert: el("span", "alert-dot") };
  r.alert.title = t("tasks.alert");
  const head = el("div", "row");
  const right = el("span", "task-right");
  right.append(r.alert, r.time);
  if (task.id) {
    r.stop = el("button", "mini danger task-stop");
    setLabel(r.stop, "stop", t("tasks.stop"), "lead");
    r.stop.onclick = (e) => { e.stopPropagation(); stopTask(r.task); };
    right.append(r.stop);
    // 21/10 (plan P18) : clic sur la ligne, hors Stop = détail de l'agent. Au clavier (Entrée,
    // Espace), c'est le TITRE qui porte le rôle « button » (relecture P18) : sur la ligne, il
    // englobait Stop (interactif imbriqué, Stop pouvait disparaître des lecteurs d'écran). Le
    // contour de focus reste dessiné sur la ligne (`.task.openable:has(.task-open:focus-visible)`).
    r.title.classList.add("task-open");
    r.title.tabIndex = 0;
    r.title.setAttribute("role", "button");
    r.row.onclick = () => openTaskDetail(r.task);
    r.title.onkeydown = (e) => {
      if (e.target !== r.title || (e.key !== "Enter" && e.key !== " ")) return;
      e.preventDefault();
      e.stopPropagation(); // Entrée ne doit pas répondre à la carte de confirmation
      openTaskDetail(r.task);
    };
  }
  head.append(r.title, right);
  r.row.append(head, r.step, r.cost);
  taskRows.set(key, r);
  return r;
}

const TASK_STATES = {
  running: "tasks.state.running", completed: "tasks.state.completed", failed: "tasks.state.failed",
  killed: "tasks.state.stopped", stopped: "tasks.state.stopped",
};
// Statut d'une tâche finie (statuts du CLI) ; inconnu : montré tel quel, rien d'inventé.
const taskStateLabel = (status) => (TASK_STATES[status] ? t(TASK_STATES[status]) : t("tasks.state.other", { status }));

function visibleTasks() {
  const now = Date.now() / 1000;
  const live = new Set(tasks.map((k) => k.id));
  const done = doneTasks
    .filter((k) => k && k.id && !live.has(k.id) && typeof k.ended === "number" && now - k.ended < DONE_KEEP_S)
    .sort((a, b) => b.ended - a.ended)
    .map((k) => ({ ...k, done: true }));
  const isRecent = (k) => k.kind === "agent" && now - k.ended < RECENT_DONE_S;
  return { current: [...tasks, ...done.filter(isRecent)], history: done.filter((k) => !isRecent(k)) };
}

// Section « Historique » : <details> gardé d'un rendu à l'autre (son ouverture aussi), DERNIER enfant
// de #tasks ; ses lignes sont les mêmes objets que celles d'« En cours » (une ligne y passe seule).
const taskHistory = el("details", "tasks-history hidden");
const taskHistoryHead = el("summary", "tasks-history-head");
const taskHistoryList = el("div", "tasks tasks-history-list");
taskHistory.append(taskHistoryHead, taskHistoryList);

function renderTasks() {
  const box = $("#tasks");
  const { current, history } = visibleTasks();
  const shown = [...current, ...history];
  const empty = !shown.length;
  box.classList.toggle("hidden", empty);
  $("#tasks-title").classList.toggle("hidden", empty);
  $("#tasks-count").textContent = tasks.length ? String(tasks.length) : ""; // agents VIVANTS seulement
  if (taskHistory.parentNode !== box) box.append(taskHistory);
  taskHistory.classList.toggle("hidden", !history.length);
  const head = t("tasks.history", { n: history.length });
  if (taskHistoryHead.textContent !== head) taskHistoryHead.textContent = head;
  const simple = simpleView();
  const keep = new Set();
  // Relecture P18 : réordonner (agent fini qui passe aux grisées) DÉPLACE des lignes, et un nœud
  // déplacé perd le focus (il tombait sur body) : il est rendu à l'élément qui l'avait.
  const focused = box.contains(document.activeElement) ? document.activeElement : null;
  shown.forEach((k, idx) => {
    const inHistory = idx >= current.length;
    const list = inHistory ? taskHistoryList : box;
    const i = inHistory ? idx - current.length : idx;
    const r = taskRow(k);
    r.task = k;
    keep.add(k.id || k.desc);
    const failed = k.done && k.status && k.status !== "completed";
    r.row.className = `task${k.id ? " openable" : ""}${k.background ? " bg" : ""}${k.alert && !k.done ? " alert" : ""}${k.done ? " done" : ""}${failed ? " failed" : ""}`;
    const kindIcon = k.kind === "agent" ? "bot" : "terminal";
    const desc = taskName(k);
    if (r.title.dataset.text !== `${kindIcon}|${desc}`) { // rendu chaque seconde : ne refaire que si ça change
      r.title.dataset.text = `${kindIcon}|${desc}`;
      setLabel(r.title, kindIcon, stripEmoji(desc), "lead");
    }
    r.time.textContent = duration(taskSeconds(k));
    r.alert.classList.toggle("hidden", !k.alert || !!k.done);
    if (r.stop) r.stop.classList.toggle("hidden", !!k.done); // une tâche finie ne s'arrête plus
    if (k.id) {
      const label = t("tasks.open_detail", { desc });
      if (r.title.getAttribute("aria-label") !== label) r.title.setAttribute("aria-label", label);
    }
    // Simple (point 5 du plan P23) : une phrase, ni étape technique (nom d'outil, fichier), ni
    // nombre d'actions, ni requêtes, ni tokens ; intitulé, durée et Stop restent.
    const actions = k.tool_uses && !simple ? t("tasks.actions", { n: k.tool_uses }) : "";
    const step = k.done ? [taskStateLabel(k.status || "completed"), actions].filter(Boolean).join(" · ")
      : [simple ? "" : tx(k.step), actions].filter(Boolean).join(" · ");
    r.step.replaceChildren(document.createTextNode(step || t(k.background ? "tasks.background" : "tasks.running")));
    const c = channelOf(k.channel);
    if (c && channelList.length > 1) {
      // Plusieurs agents en parallèle : on voit pour quel projet chacun travaille.
      const tag = el("span", "chan", chanLabel(c));
      tag.style.color = c.color;
      r.step.prepend(tag);
    }
    // Coût, si le cœur le donne : requêtes et tokens cumulés de l'agent (Avancé seulement).
    const cost = simple ? "" : [
      typeof k.requests === "number" ? t("tasks.requests", { n: k.requests }) : "",
      typeof k.tokens === "number" ? `${formatTokens(k.tokens)} tokens` : "",
    ].filter(Boolean).join(" · ");
    r.cost.textContent = cost;
    r.cost.classList.toggle("hidden", !cost);
    r.row.title = k.alert ? t("tasks.alert_row", { desc }) : desc;
    // « En cours » : toujours AVANT la section Historique, dernier enfant de #tasks
    if (list.children[i] !== r.row) list.insertBefore(r.row, list.children[i] || (inHistory ? null : taskHistory));
  });
  for (const [key, r] of taskRows) {
    if (!keep.has(key)) { r.row.remove(); taskRows.delete(key); }
  }
  // Relecture P23 : un titre focalisé qui passe dans l'Historique REPLIÉ (ou disparaît) n'est plus
  // visible ; le focus va au résumé « Historique », pas sur body.
  if (focused) {
    const gone = !focused.isConnected || (taskHistoryList.contains(focused) && !taskHistory.open);
    const target = !gone ? focused : (!taskHistory.classList.contains("hidden") ? taskHistoryHead : null);
    if (target && document.activeElement !== target) target.focus({ preventScroll: true });
  }
}

// Description vide ou absente (relecture P18 : titre vide, « Détail de l'agent : ») : nom de repli.
function taskName(k) {
  const desc = tx(k?.desc || "").trim();
  return stripEmoji(desc).trim() ? desc : t(k?.kind === "agent" ? "tasks.untitled_agent" : "tasks.untitled_task");
}

async function stopTask(k) {
  if (!k?.id) return;
  if (!(await openModal({
    title: t("tasks.stop_title"),
    text: t("tasks.stop_text", { desc: taskName(k) }),
    ok: t("tasks.stop_ok"), danger: true,
  }))) return;
  // `task_id` et pas `id` : `id` est l'identifiant de la requête WebSocket (conn.js).
  const r = await command("stop_task", { task_id: k.id });
  if (!r.ok) toast(r.error || t("tasks.not_stopped"), "error");
  else toast(t("tasks.stop_asked", { desc: taskName(k) }));
}

on("tasks", (ev) => {
  tasks = ev.items || [];
  // `done` absent (cœur d'avant P18, démo) : on garde les tâches finies déjà connues, filtrées par `ended`.
  if (Array.isArray(ev.done)) doneTasks = ev.done;
  // `elapsed_s` vaut à la publication (`ts`, rejouée telle quelle à la reconnexion), pas à la réception.
  const now = Date.now() / 1000;
  tasksAt = typeof ev.ts === "number" ? Math.min(ev.ts, now) : now;
  renderTasks();
});
setInterval(() => { if (tasks.length || doneTasks.length) renderTasks(); }, 1000);

// ------------------------------------------------------------------ détail d'agent (21/10, plan P18)
// L'utilisateur : « si je clique sur les agents dans le HUD, … des détails sur leur travail : progression,
// tâches accomplies ou non, ce qu'il reste ». Commande `task_detail` du cœur : DONNÉES seules
// (astrel/detail_agent.py), mises en mots ici. Consigne, commandes, erreurs et passation sont des
// données d'agent : TOUJOURS en textContent, jamais de HTML (caractères de contrôle compris).
const DETAIL_REFRESH_MS = 2000;

const data = (tag, cls, text) => el(tag, `ad-data${cls ? ` ${cls}` : ""}`, text); // contenu de l'agent, tel quel

function detailSection(title, ...children) {
  const s = el("section", "ad-section");
  s.append(el("h4", "ad-title", title), ...children.filter(Boolean));
  return s;
}

function detailMeta(d) {
  const type = d.type === "commande" ? t("tasks.detail.type_command") : d.type === "agent" || !d.type ? t("tasks.detail.type_agent") : "";
  const parts = [
    d.live ? t("tasks.state.running") : taskStateLabel(d.state || "completed"),
    t("tasks.detail.duration", { d: duration(d.elapsed_s || 0) }),
    d.tool_uses ? t("tasks.actions", { n: d.tool_uses }) : "",
    d.requests ? t("tasks.requests", { n: d.requests }) : "",
    d.tokens ? `${formatTokens(d.tokens)} tokens` : "",
  ].filter(Boolean);
  const box = el("div", "ad-meta");
  const state = el("span", `ad-state ${d.live ? "running" : d.state === "completed" ? "ok" : "fail"}`, parts.shift());
  box.append(state);
  if (type) box.append(el("span", "ad-chip", type));
  else box.append(data("span", "ad-chip", d.type)); // métier de l'équipe (codeur, testeur…) : nom propre
  for (const p of parts) box.append(el("span", "ad-chip", p));
  const c = channelOf(d.channel);
  if (c && channelList.length > 1) box.append(el("span", "ad-chip", t("tasks.detail.channel", { name: chanLabel(c) })));
  return box;
}

function detailSteps(d) {
  if (!Array.isArray(d.todos)) return el("p", "ad-note", t("tasks.detail.steps_none"));
  if (!d.todos.length) return el("p", "ad-note", t("tasks.detail.steps_empty"));
  const wrap = el("div", "ad-steps");
  const done = d.todos.filter((x) => x.status === "completed").length;
  const bar = el("div", "bar ad-progress");
  const fill = el("div");
  fill.style.width = `${Math.round((100 * done) / d.todos.length)}%`;
  bar.append(fill);
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-valuemin", "0");
  bar.setAttribute("aria-valuemax", String(d.todos.length));
  bar.setAttribute("aria-valuenow", String(done));
  wrap.append(el("div", "ad-count", t("tasks.detail.steps_count", { done, total: d.todos.length })), bar);
  for (const [status, key] of [["in_progress", "tasks.detail.steps_doing"], ["pending", "tasks.detail.steps_todo"],
    ["completed", "tasks.detail.steps_done"]]) {
    const items = d.todos.filter((x) => x.status === status);
    if (!items.length) continue;
    const ul = el("ul", `ad-list ad-todo ${status}`);
    for (const x of items) ul.append(data("li", "", x.text));
    wrap.append(el("div", "ad-sub", t(key)), ul);
  }
  return wrap;
}

const ACT_STATES = { true: ["ok", "tasks.detail.act_ok"], false: ["fail", "tasks.detail.act_fail"] };

function detailActions(d) {
  if (!d.actions?.length) return el("p", "ad-note", t("tasks.detail.actions_none"));
  const ul = el("ul", "ad-list ad-actions");
  for (const a of [...d.actions].reverse()) { // la plus récente en haut
    // ok null : en cours tant que la tâche vit ; une tâche finie n'a plus rien « en cours » (rendu jamais reçu)
    const [cls, key] = ACT_STATES[a.ok] || (d.live ? ["running", "tasks.detail.act_running"] : ["none", "tasks.detail.act_none"]);
    const li = el("li", `ad-act ${cls}`);
    const mark = el("span", "ad-mark");
    mark.title = t(key);
    mark.setAttribute("aria-label", t(key));
    mark.setAttribute("role", "img");
    const when = typeof a.at === "number"
      ? new Date(a.at * 1000).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
    li.append(mark, data("b", "ad-tool", a.tool), data("span", "ad-desc", a.desc), el("span", "ad-when", when));
    ul.append(li);
  }
  return ul;
}

function detailPassation(d) {
  if (d.passation_ambiguous) return el("p", "ad-note", t("tasks.detail.passation_ambiguous"));
  const f = d.passation;
  if (!f) return el("p", "ad-note", t("tasks.detail.passation_none"));
  const box = el("div", "ad-passation");
  // Champs tous vides (relecture P18 : « Fiche ? · ? · vers ? ») : un en-tête lisible.
  const head = f.n == null && !f.mission && !f.a ? t("tasks.detail.passation_head_none")
    : t("tasks.detail.passation_head", { n: f.n ?? "?", mission: f.mission || "?", a: f.a || "?" });
  box.append(data("div", "ad-sub", head));
  const dl = el("dl", "ad-fields");
  for (const k of ["fait", "a_tester", "risques", "preuves"]) {
    if (!f[k]) continue;
    dl.append(el("dt", "", t(`tasks.detail.passation_${k}`)), data("dd", "ad-block", f[k]));
  }
  box.append(dl);
  return box;
}

// 23/10 (plan P28, demande de l'utilisateur) : effort de réflexion de l'agent. Celui reçu au lancement (un agent en
// cours le garde) et, pour un métier d'Astrel, celui des PROCHAINS agents du métier (commande `agent_effort`,
// refusée à Claude). Bloc gardé d'un rafraîchissement à l'autre et mis à jour sur place : la liste
// déroulante ne se referme pas quand l'agent avance.
function updateEffortBox(panel, d) {
  let box = panel.effortBox;
  if (!box) {
    box = el("section", "ad-section ad-effort");
    const title = el("h4", "ad-title", t("tasks.detail.effort"));
    const now = el("p", "ad-note ad-effort-now");
    const row = el("div", "ad-effort-row");
    const label = el("label", "ad-effort-label");
    const sel = el("select", "setting-select ad-effort-select");
    sel.id = `ad-effort-${Date.now()}`;
    label.htmlFor = sel.id;
    row.append(label, sel);
    const note = el("p", "ad-note ad-effort-state");
    const help = el("p", "ad-note ad-effort-help", t("tasks.detail.effort_kept"));
    box.append(title, now, row, note, help);
    sel.onchange = async () => {
      const agent = box.dataset.agent, level = sel.value;
      sel.disabled = true;
      let r;
      try { r = await command("agent_effort", { agent, effort: level }); } catch (err) { r = { ok: false, error: String(err?.message || err) }; }
      sel.disabled = false;
      if (r?.ok === false) { toast(t("tasks.detail.effort_failed", { error: tx(r.error) }), "error"); sel.value = box.dataset.sel || ""; return; }
      box.dataset.sel = level;
    };
    panel.effortBox = box;
  }
  const [now, row, note, help] = [".ad-effort-now", ".ad-effort-row", ".ad-effort-state", ".ad-effort-help"].map((s) => box.querySelector(s));
  now.textContent = d.effort ? t("tasks.detail.effort_this", { level: effortLabel(d.effort) })
    : t(d.effort_settable ? "tasks.detail.effort_this_cli" : "tasks.detail.effort_builtin");
  row.hidden = !d.effort_settable;
  help.hidden = !d.effort_settable;
  note.hidden = !d.effort_settable;
  if (!d.effort_settable) return box;
  const sel = row.querySelector("select");
  box.dataset.agent = d.type;
  row.querySelector("label").textContent = t("tasks.detail.effort_next", { agent: d.type });
  const levels = Array.isArray(d.effort_levels) && d.effort_levels.length ? d.effort_levels : EFFORT_LEVELS;
  const want = ["", ...levels];
  if ([...sel.options].map((o) => o.value).join("|") !== want.join("|")) {
    sel.replaceChildren(...want.map((v) => {
      const o = el("option", "", v ? effortLabel(v) : t("tasks.detail.effort_default", { level: effortLabel(d.effort_default) }));
      o.value = v;
      return o;
    }));
  }
  // « » = l'en-tête du métier (config/agents/*.md) ; jamais changé sous les doigts de l'utilisateur
  box.dataset.sel = d.effort_next && d.effort_next !== d.effort_default ? d.effort_next : "";
  if (document.activeElement !== sel && !sel.disabled) sel.value = box.dataset.sel;
  note.textContent = [d.effort_haiku ? t("tasks.detail.effort_haiku") : "",
    t(d.applique ? "tasks.detail.effort_applied" : "tasks.detail.effort_pending")].filter(Boolean).join(" ");
  return box;
}

// Signature du contenu hors chiffres qui bougent chaque seconde : le corps n'est refait que s'il change
// (sinon une sélection de texte ou le défilement sauteraient toutes les 2 s). L'effort est mis à jour à part.
const detailSig = (d) => JSON.stringify({ ...d, elapsed_s: 0, tokens: 0, requests: 0, tool_uses: 0, effort: 0,
  effort_next: 0, effort_default: 0, effort_settable: 0, applique: 0, effort_haiku: 0, effort_levels: 0 });

function renderTaskDetail(panel, d) {
  const sig = detailSig(d);
  const meta = detailMeta(d);
  const effort = d.type !== "commande" ? updateEffortBox(panel, d) : null;
  if (panel.sig === sig && panel.meta) { panel.meta.replaceWith(meta); panel.meta = meta; return; }
  panel.sig = sig;
  panel.meta = meta;
  if (!panel.rest?.isConnected) {
    // méta (remplacée à chaque fois), effort (gardé), puis le reste (refait quand il change)
    panel.rest = el("div", "ad-rest");
    panel.body.replaceChildren(meta, ...(effort ? [effort] : []), panel.rest);
  } else {
    panel.body.firstElementChild.replaceWith(meta);
  }
  const parts = [];
  parts.push(detailSection(t("tasks.detail.mission"),
    d.consigne ? data("div", "ad-block", d.consigne) : el("p", "ad-note", t("tasks.detail.no_mission"))));
  if (d.live && (d.step || d.doing?.length)) {
    const now = [];
    if (d.step) now.push(data("div", "", tx(d.step)));
    if (d.doing?.length) {
      const ul = el("ul", "ad-list ad-doing");
      for (const x of d.doing) {
        const li = el("li", "ad-act running");
        li.append(el("span", "ad-mark"), data("b", "ad-tool", x.tool), data("span", "ad-desc", x.desc));
        ul.append(li);
      }
      now.push(ul);
    }
    parts.push(detailSection(t("tasks.detail.now"), ...now));
  }
  if (d.type !== "commande") parts.push(detailSection(t("tasks.detail.steps"), detailSteps(d)));
  parts.push(detailSection(t("tasks.detail.actions"), detailActions(d)));
  const errs = d.last_errors?.length ? el("div", "ad-errors") : el("p", "ad-note", t("tasks.detail.errors_none"));
  for (const e of d.last_errors || []) errs.append(data("div", "ad-block ad-error", e));
  parts.push(detailSection(t("tasks.detail.errors", { n: d.errors || 0 }), errs));
  if (d.files?.length) {
    const ul = el("ul", "ad-list ad-files");
    for (const f of d.files) ul.append(data("li", "", f));
    parts.push(detailSection(t("tasks.detail.files"), ul));
  }
  if (!d.live && d.summary) parts.push(detailSection(t("tasks.detail.summary"), data("div", "ad-block", d.summary)));
  if (d.type && d.type !== "commande" && d.type !== "agent") parts.push(detailSection(t("tasks.detail.passation"), detailPassation(d)));
  const foot = el("p", "ad-foot", t("tasks.detail.memory"));
  parts.push(foot);
  if (d.truncated) parts.push(el("p", "ad-foot", t("tasks.detail.truncated")));
  const scroll = panel.body.scrollTop;
  panel.rest.replaceChildren(...parts);
  panel.body.scrollTop = scroll;
}

function openTaskDetail(k) {
  if (!k?.id) return;
  const p = openPanel({ title: stripEmoji(taskName(k)).trim(),
    icon: k.kind === "agent" ? "bot" : "terminal", cls: "agent-detail" });
  p.body.setAttribute("aria-live", "off");
  p.body.append(el("p", "ad-note", t("tasks.detail.loading")));
  const state = { timer: 0, busy: false, stopped: false };
  const tick = async () => {
    if (state.stopped || state.busy) return;
    state.busy = true;
    let r;
    try {
      r = await command("task_detail", { task_id: k.id }); // `task_id` : `id` est celui de la requête WebSocket
    } catch (err) {
      r = { ok: false, error: String(err?.message || err) };
    }
    state.busy = false;
    if (state.stopped) return;
    if (r?.ok) { renderTaskDetail(p, r); return; }
    if (r?.error === "unknown_task") {
      // Expirée (10 min) ou cœur relancé : pas une erreur, on le dit et on arrête de redemander.
      p.sig = null; p.meta = null;
      p.body.replaceChildren(el("p", "ad-note ad-expired", t("tasks.detail.expired")));
      clearInterval(state.timer);
      return;
    }
    if (!p.sig) p.body.replaceChildren(el("p", "ad-note", t("tasks.detail.unavailable", { error: tx(r?.error || "?") })));
  };
  state.timer = setInterval(tick, DETAIL_REFRESH_MS);
  tick();
  p.closed.then(() => {
    state.stopped = true;
    clearInterval(state.timer);
  });
}

// ------------------------------------------------------------------ confirmation

let confirmId = null;

// 14/10 : la question surgissait pendant qu'il TAPAIT, prenait le
// clavier, et la touche suivante (Entrée) y répondait à sa place. Désormais la
// carte s'affiche au-dessus de la barre SANS prendre le focus ; les raccourcis
// (Entrée, Maj+Entrée, Échap) ne valent que hors d'un champ de saisie.
const typingIn = () => {
  const a = document.activeElement;
  return !!a && (a.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(a.tagName));
};

function openConfirm(ev) {
  confirmId = ev.id;
  $("#confirm-always").classList.toggle("hidden", !ev.grantable);
  $("#confirm-question").textContent = stripEmoji(tx(ev.question)); // la commande, elle, reste exacte
  $("#confirm-cmd").textContent = ev.cmd || ev.tool;
  $("#confirm").classList.remove("hidden");
  $("#confirm-typing").classList.toggle("hidden", !typingIn());
}

function closeConfirm(id) {
  if (id && id !== confirmId) return;
  confirmId = null;
  $("#confirm").classList.add("hidden");
}

async function answer(allow, always = false) {
  if (!confirmId) return;
  const id = confirmId;
  closeConfirm(id);
  const r = await command("confirm", { confirm_id: id, allow, always });
  if (!r.ok) toast(r.error || t("confirm.not_taken"), "warning");
}

$("#confirm-yes").onclick = () => answer(true);
$("#confirm-no").onclick = () => answer(false);
$("#confirm-always").onclick = () => answer(true, true);
addEventListener("keydown", (e) => {
  if (!confirmId || typingIn()) return; // ce qui se tape va dans la barre, jamais dans la question
  // Relecture P23 : Entrée sur un bouton (« Voir le cerveau »…) ou un résumé (« Historique ») est
  // à CE contrôle, jamais un « oui » à la carte (les boutons de la carte se cliquent d'eux-mêmes).
  const a = document.activeElement;
  if (e.key === "Enter" && a && (["BUTTON", "SUMMARY", "A"].includes(a.tagName) || a.getAttribute("role") === "button")) return;
  if (e.key === "Enter" && e.shiftKey && !$("#confirm-always").classList.contains("hidden")) { e.preventDefault(); answer(true, true); return; }
  if (e.key === "Enter") { e.preventDefault(); answer(true); }
  if (e.key === "Escape") { e.preventDefault(); answer(false); }
});

// ------------------------------------------------------------------ autonomie

on("autonomy", (ev) => {
  const chip = $("#autonomy-chip");
  const prudent = ev.mode === "prudent";
  chip.classList.toggle("prudent", prudent);
  setToggle(chip, !prudent, t(prudent ? "top.autonomy_no" : "common.yes_caps"));
  chip.title = t(prudent ? "top.autonomy_off_title" : "top.autonomy_on_title");
});
$("#autonomy-chip").onclick = async () => {
  const next = $("#autonomy-chip").classList.contains("prudent") ? "autonome" : "prudent";
  const r = await command("autonomy", { mode: next });
  if (!r.ok) toast(r.error || t("common.change_failed"), "error");
  else toast(t(next === "autonome" ? "top.autonomy_now_on" : "top.autonomy_now_off"));
};

// ------------------------------------------------------------------ Autonome TOTAL (15/10)

// Aucune confirmation, sauf pour le garde-fou lui-même. Ne s'active QUE d'ici
// (ou à la voix) ; le cœur le refuse à toute autre origine. Gardé en mémoire :
// retombe à NON à chaque relance. Bandeau et cadre rouges tant qu'il est actif,
// sans jamais prendre le focus.
on("total", (ev) => {
  const active = !!ev.on;
  const chip = $("#total-chip");
  setToggle(chip, active);
  // 19/10 : interrupteur affiché « Sans confirmation » (même commande `total`).
  chip.title = t(active ? "top.total_title_on" : "top.total_title_off");
  $("#total-banner").hidden = !active;
  $("#total-frame").hidden = !active;
  document.body.classList.toggle("total-on", active);
  const since = ev.since ? new Date(ev.since * 1000) : null;
  // 19/10 : heure seule sur la ligne fine, « depuis … » au survol
  const hhmm = since ? since.toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" }) : "";
  $("#total-since").textContent = active && since ? ` · ${hhmm}` : "";
  $("#total-since").title = active && since ? t("total.since", { time: hhmm }) : "";
  syncAlertLine();
});

// 19/10 (demande : les deux bandeaux empilés poussaient les panneaux) : Total et levée
// partagent UNE ligne fine en surimpression (#alert-line, hors de --head-h). Visible dès
// qu'un des deux est actif ; `alert-on` fait de la place au-dessus du fil et des notifications.
function syncAlertLine() {
  // 21/10 (plan P20) : + mémoire du PC en blocage
  const on = !$("#total-banner").hidden || !$("#lift-banner").hidden || !$("#memory-banner").hidden;
  $("#alert-line").hidden = !on;
  document.body.classList.toggle("alert-on", on);
  fitAlertLine();
}
// 22/10 (relecture P23, 700 px avec trois alertes) : les pastilles passent à la ligne au lieu de se
// chevaucher (styles.css) ; la place réservée sous la ligne (--alert-h, fil et notifications) suit
// le nombre de rangées. Une seule rangée : valeur de styles.css, rien ne bouge.
function fitAlertLine() {
  const GAP = 14; // marge sous la dernière rangée, celle de --alert-h (40 = 26 + 14)
  const line = $("#alert-line");
  const root = document.documentElement.style;
  const segs = [...line.querySelectorAll(".alert-seg")].filter((s) => !s.hidden);
  if (line.hidden || !segs.length) { root.removeProperty("--alert-h"); return; }
  const top = line.getBoundingClientRect().top;
  const bottom = Math.max(...segs.map((s) => s.getBoundingClientRect().bottom));
  const need = Math.ceil(bottom - top) + GAP;
  if (need > 40) { if (root.getPropertyValue("--alert-h") !== `${need}px`) root.setProperty("--alert-h", `${need}px`); }
  else root.removeProperty("--alert-h");
}
new ResizeObserver(fitAlertLine).observe($("#alert-line"));
async function setTotal(want) {
  // Total ne s'active QUE par cette confirmation puis la commande HUD `total`.
  if (want && !(await openModal({
    title: t("total.confirm_title"),
    text: t("total.confirm_text"),
    items: [t("total.confirm_1"), t("total.confirm_2"), t("total.confirm_3"), t("total.confirm_4")],
    ok: t("total.confirm_ok"), danger: true,
  }))) return;
  const r = await command("total", { mode: want ? "on" : "off" });
  if (!r.ok) toast(r.error || t("common.change_failed"), "error");
}
$("#total-chip").onclick = () => setTotal(!$("#total-chip").classList.contains("on"));
$("#total-off").onclick = () => setTotal(false);

// ------------------------------------------------------------------ affichage Simple / Avancé (22/10, plan P23)

// Demande de l'utilisateur : montrer Astrel à quelqu'un qui le découvre sans le graphe de détails,
// les capteurs ni les bandeaux alarmants. Réglage `affichage` (Réglages › Apparence), tous les HUD
// suivent par `settings`. 28/10 (T10, décision de l'utilisateur) : plus d'interrupteur « Simple » dans la barre.
// Le rendu est dans styles.css (« P23 affichage ») : rien n'est retiré du DOM. En Simple, le fil
// montre le canal par défaut (Général) : les onglets et les noms de projets sont cachés.

// 23/10 (plan P28) : plus de graphe, donc plus de « Voir le cerveau » en Simple (lot B de P23).

onAffichage((a) => {
  const simple = a === "simple";
  if (simple && activeChannel !== defaultChannel) showChannel(defaultChannel);
  else {
    for (const m of messages) placeMsg(m); // questions et comptes rendus de projet (simpleShows)
    renderChannels();
  }
  // Lot B : orbe (libellés), « En cours » (phrases, historique)
  renderStateText();
  renderTasks();
});
applyAffichage();

// ------------------------------------------------------------------ levée du garde-fou (18/10, plan P7)

// Se lève UNIQUEMENT dans Réglages › Garde-fou (mot « lever » à taper ; le cœur refuse
// toute autre origine). Événement `levee` {on, since, changed[]} : bandeau et cadre
// ORANGE (Total est rouge), bouton en barre, fichiers du garde-fou modifiés depuis.
// Rétablir : ici, sans question (resserrer). Retombe à « en place » à chaque relance.
on("levee", (ev) => {
  const active = !!ev.on;
  $("#lift-banner").hidden = !active;
  $("#lift-frame").hidden = !active;
  $("#lift-chip").hidden = !active;
  document.body.classList.toggle("lift-on", active);
  const since = ev.since ? new Date(ev.since * 1000) : null;
  const hhmm = since ? since.toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" }) : "";
  $("#lift-since").textContent = active && since ? ` · ${hhmm}` : "";
  $("#lift-since").title = active && since ? t("lift.since", { time: hhmm }) : "";
  // 19/10 : fichiers modifiés dans un dépliant (la ligne reste fine), liste complète au survol
  const files = active && Array.isArray(ev.changed) ? ev.changed.map(String) : [];
  const box = $("#lift-files");
  box.hidden = !files.length;
  if (!files.length) box.open = false;
  const sum = box.querySelector("summary");
  sum.textContent = t("lift.files", { n: files.length });
  sum.title = files.join("\n");
  box.querySelector("ul").replaceChildren(...files.map((f) => el("li", "", f)));
  syncAlertLine();
});
async function restoreGuard() {
  const r = await command("settings_action", { key: "garde_fou_levee", action: "retablir" });
  if (!r.ok) toast(r.error || t("lift.restore_failed"), "error");
}
$("#lift-off").onclick = restoreGuard;
$("#lift-chip").onclick = restoreGuard;
// le dépliant des fichiers se referme d'un clic ailleurs
addEventListener("click", (e) => {
  const box = $("#lift-files");
  if (box.open && !box.contains(e.target)) box.open = false;
});

// ------------------------------------------------------------------ mission autonome (15/10)

// Astrel enchaîne les tours SEUL vers un objectif, dans un périmètre et un budget
// fixés ici. Lancer et reprendre : l'utilisateur seul (le cœur le refuse à une page ou
// un programme ouvert par Claude). Pause et arrêt : toujours. Écrire hors du
// périmètre pendant la mission est demandé par le garde-fou ; « Stop tout » l'arrête.
let mission = { status: "" };
// started_at d'une mission finie masquée (bouton ×). 03/11 (demande : « elle réapparaît ») : retenu par le
// navigateur, sinon un rechargement du HUD (nouvelle version, reconnexion) la faisait revenir.
// 04/11 : le × FERME la mission côté cœur (`mission_close`) ; ce souvenir n'est plus qu'un REPLI, écrit
// seulement si le cœur refuse ou ne connaît pas la commande (cœur ancien).
const MISSION_MASQUEE = "astrel-mission-masquee";
let missionHidden = (() => {
  try { return Number(localStorage.getItem(MISSION_MASQUEE)) || 0; } catch { return 0; }
})();
// 02/11 (plan P43) : trois fins qui ne se confondent pas : « done » = critère VÉRIFIÉ par Astrel,
// « accepted_unverified » = accepté par l'utilisateur SANS preuve, « verification_failed » = vérifié et faux.
const MISSION_STATUS = ["active", "paused", "done", "blocked", "stopped", "budget", "accepted_unverified",
  "verification_failed"];
const missionStatus = (s) => (MISSION_STATUS.includes(s) ? t(`mission.status_${s}`) : s);
const missionRunning = () => ["active", "paused"].includes(mission.status);
const missionPhase = (m) => (m.status === "active" || m.status === "paused" ? m.phase || "travail" : "");
const MISSION_FIN = {
  done: ["ok", "check", "mission.fin_done"],
  accepted_unverified: ["accepted", "eye", "mission.fin_accepted"],
  verification_failed: ["failed", "x", "mission.fin_failed"],
};
const PREUVE_ICON = { ok: "check", echec: "x", bloque: "alert" };

function missionLeft(m) {
  const end = missionRunning() ? Date.now() / 1000 : (m.ended_at || Date.now() / 1000);
  const elapsed = Math.max(0, (end - (m.started_at || end)) / 60);
  return {
    turns: Math.max(0, (m.max_turns || 0) - (m.turns || 0)),
    minutes: Math.max(0, Math.round((m.max_minutes || 0) - elapsed)),
    ktok: Math.max(0, Math.round(((m.max_tokens || 0) - (m.tokens || 0)) / 1000)),
    elapsed: Math.round(elapsed),
  };
}

function budgetRow(label, used, max, unit) {
  const row = el("div", "mission-budget");
  const pct = max ? Math.min(100, Math.round((used / max) * 100)) : 0;
  const head = el("div", "row");
  head.append(el("span", "", label), el("span", "time", t("mission.budget_left", { left: Math.max(0, max - used), unit, max })));
  const bar = el("div", `bar${pct >= 80 ? " warn" : ""}`);
  const fill = el("div");
  fill.style.width = `${pct}%`;
  bar.append(fill);
  row.append(head, bar);
  return row;
}

// Chemin court pour l'affichage : relatif au dossier du périmètre qui le contient.
function missionPath(m, p) {
  const root = (m.scope || []).filter((r) => p === r || p.startsWith(`${r}/`)).sort((a, b) => b.length - a.length)[0];
  return root ? (p === root ? root.split("/").pop() : p.slice(root.length + 1)) : p;
}

// Un contrôle du critère, en clair (rendu ici, dans la langue de l'interface).
function missionControl(m, c) {
  const chemin = missionPath(m, c.chemin || c.depot || c.dossier || "");
  if (c.type === "fichier_existe") {
    if (c.genre === "dossier") return t("mission.c_dossier", { chemin });
    return t(c.taille_min ? "mission.c_taille" : "mission.c_existe", { chemin, n: c.taille_min || 0 });
  }
  if (c.type === "fichier_contient") return t(c.absent ? "mission.c_sans" : "mission.c_contient", { chemin, texte: c.texte });
  if (c.type === "empreinte") return t("mission.c_sha", { chemin, sha: (c.sha256 || "").slice(0, 12) });
  if (c.type === "dossier_contient") return t("mission.c_contenu", { chemin, list: (c.attendus || []).join(", ") });
  if (c.type === "git_head") {
    const parts = [];
    if (c.branche) parts.push(t("mission.c_branche", { chemin, branche: c.branche }));
    if (c.avance) parts.push(t("mission.c_commit", { chemin }));
    return parts.join(" · ") || t("mission.c_depot", { chemin });
  }
  if (c.type === "commande_observee") return t("mission.c_commande", { chemin, commande: c.commande });
  return c.type || "?";
}

// Le rapport de preuve : codes produits par le cœur (jamais le contenu d'un fichier), tout en textContent.
function missionProof(m) {
  const p = m.preuve;
  const box = el("div", `mission-preuve ${p.statut || ""}`);
  const when = p.at ? new Date(p.at * 1000).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" }) : "";
  const head = el("div", "mission-preuve-head");
  setLabel(head, PREUVE_ICON[{ verifiee: "ok", echec: "echec" }[p.statut] || "bloque"],
    t(`mission.preuve_${p.statut || "bloquee"}`, { time: when }), "lead");
  box.append(head);
  const ul = el("ul", "mission-preuve-list");
  for (const c of p.controles || []) {
    const li = el("li", `etat-${c.etat}`);
    const infos = { ...(c.infos || {}) };
    if (Array.isArray(infos.noms)) infos.noms = infos.noms.join(", ");
    const cible = c.cible ? missionPath(m, c.cible) : "";
    setLabel(li, PREUVE_ICON[c.etat] || "alert", `${cible ? `${cible} : ` : ""}${t(`mission.code_${c.code}`, infos)}`, "lead");
    ul.append(li);
  }
  box.append(ul);
  return box;
}

function renderMission() {
  const m = mission;
  const running = missionRunning();
  const paused = m.status === "paused";
  const phase = missionPhase(m);
  const decide = m.status === "active" && phase === "verification_blocked";
  const left = missionLeft(m);
  // 28/10 (T10, décision de l'utilisateur) : plus de bouton « Mission » dans la barre ; le bandeau (pause,
  // arrêt) et le panneau droit restent.
  // bandeau
  $("#mission-banner").hidden = !running;
  document.body.classList.toggle("mission-on", running);
  $("#mission-banner").classList.toggle("paused", paused);
  $("#mission-banner").classList.toggle("decide", decide);
  const bannerKey = paused ? "mission.banner_paused" : decide ? "mission.banner_decide"
    : phase === "verification" ? "mission.banner_verifying" : "mission.banner_running";
  setLabel($("#mission-banner-head"), paused ? "pause" : decide ? "alert" : "target", t(bannerKey), "lead");
  // T25 (29/10) : une mission lancée par Astrel lui-même le dit dans le bandeau.
  const by = m.lancee_par === "astrel" ? ` · ${t("mission.by_assistant", { name: assistantName() })}` : "";
  $("#mission-banner-info").textContent = running
    ? ` · ${m.objective} · ${t("mission.banner_info", { turns: m.turns || 0, max: m.max_turns, minutes: left.minutes, ktok: left.ktok })}${by}` : "";
  $("#mission-banner-info").title = m.objective || "";
  $("#mission-banner-pause").textContent = t(paused ? "mission.resume" : "mission.pause");
  // panneau droit
  const show = !!m.status && (running || missionHidden !== m.started_at);
  $("#mission-title").classList.toggle("hidden", !show);
  $("#mission").classList.toggle("hidden", !show);
  $("#mission-hide").classList.toggle("hidden", running);
  const box = $("#mission");
  box.replaceChildren();
  if (!show) return;
  box.className = `mission ${m.status}${decide ? " decide" : ""}`;
  const head = el("div", "row");
  const pill = decide ? t("mission.phase_blocked") : m.status === "active" && phase === "verification"
    ? t("mission.phase_verification") : missionStatus(m.status);
  head.append(el("b", "", m.objective || "—"), el("span", `pill ${m.status}${decide ? " decide" : ""}`, pill));
  box.append(head);
  const fin = MISSION_FIN[m.status];
  if (fin) {
    const line = el("div", `mission-fin ${fin[0]}`);
    // revue P43 : « vérifiée » selon un critère d'Astrel jamais approuvé le dit dans la fin elle-même.
    const astrel = m.status === "done" && m.origine === "claude" && !m.approuve;
    setLabel(line, fin[1], t(astrel ? "mission.fin_done_astrel" : fin[2]), "lead");
    box.append(line);
  }
  box.append(el("div", "small muted", t("mission.done_when", { text: m.done_when || "—" })));
  if (m.critere?.controles?.length) {
    const ul = el("ul", "mission-critere");
    for (const c of m.critere.controles) ul.append(el("li", "", missionControl(m, c)));
    for (const f of m.critere.figer || []) ul.append(el("li", "", t("mission.c_figer", { chemin: missionPath(m, f) })));
    box.append(el("div", "small", t("mission.criterion_typed")), ul);
    const origin = m.origine !== "claude" ? t("mission.origin_you")
      : t(m.approuve ? "mission.origin_assistant_ok" : "mission.origin_assistant");
    box.append(el("div", `small muted mission-origine ${m.origine === "claude" && !m.approuve ? "astrel" : ""}`, origin));
  } else {
    box.append(el("div", "small mission-origine astrel", t("mission.criterion_free")));
  }
  const step = m.status !== "active" ? t("mission.turns_in", { n: m.turns || 0, minutes: left.elapsed })
    : phase === "verification" ? t("mission.step_verifying")
      : decide ? t("mission.step_decide")
        : t(m.in_turn ? "mission.turn_running" : "mission.turn_next", { n: m.in_turn ? m.turns : (m.turns || 0) + 1 });
  box.append(el("div", "step", step));
  if (m.declaration?.texte) box.append(el("div", "small muted mission-last",
    t("mission.declared", { text: m.declaration.texte })));
  if (m.preuve) box.append(missionProof(m));
  if (m.last && !m.preuve) box.append(el("div", "small muted mission-last", t("mission.last", { text: tx(m.last) })));
  if (m.reason) box.append(el("div", "small mission-reason", tx(m.reason)));
  box.append(budgetRow(t("mission.turns"), m.turns || 0, m.max_turns || 0, t("mission.turns_unit")),
    budgetRow(t("mission.minutes"), Math.min(m.max_minutes || 0, left.elapsed), Math.round(m.max_minutes || 0), "min"),
    budgetRow("Tokens", Math.round((m.tokens || 0) / 1000), Math.round((m.max_tokens || 0) / 1000), "k"));
  if (m.scope?.length) box.append(el("div", "small muted", t("mission.scope", { list: m.scope.join(", ") })));
  if (m.forbidden_paths?.length) box.append(el("div", "small muted", t("mission.forbidden", { list: m.forbidden_paths.join(", ") })));
  const actions = el("div", "mission-actions");
  const btn = (ico, text, cls, fn) => {
    const b = el("button", `mini ${cls}`);
    setLabel(b, ico, text, "lead");
    b.onclick = fn;
    actions.append(b);
  };
  if (decide) {
    if (m.critere) btn("refresh", t("mission.reverify"), "ok", () => missionAction("reverifier"));
    btn("play", t("mission.back_to_work"), "", () => missionAction("reprendre_travail"));
    btn("eye", t("mission.accept"), "", () => missionAction("accepter"));
  }
  if (running && m.critere && m.origine === "claude" && !m.approuve) {
    btn("shield", t("mission.approve"), "", () => missionAction("approuver"));
  }
  if (m.status === "active") btn("pause", t("mission.pause"), "", () => missionAction("pause"));
  if (paused) btn("play", t("mission.resume"), "ok", () => missionAction("resume"));
  if (running) btn("stop", t("mission.stop"), "danger", () => missionAction("stop"));
  else btn("target", t("mission.new"), "", startMission);
  box.append(actions);
}

on("mission", (ev) => { mission = ev; renderMission(); });
setInterval(() => { if (mission.status === "active") renderMission(); }, 5000); // temps restant
// 04/11 (demande : une mission d'essai finie revenait après relance, dans un autre navigateur) : masquée
// tout de suite, puis FERMÉE par le cœur (publiée ensuite comme absente, pour tous les écrans).
$("#mission-hide").onclick = async () => {
  const m = mission;
  missionHidden = m.started_at;
  renderMission();
  const r = await command("mission_close", { mission_id: m.id || "" });
  if (r.ok) return;
  try { localStorage.setItem(MISSION_MASQUEE, String(m.started_at)); } catch { /* navigation privée */ }
};

async function missionAction(action) {
  if (action === "stop" && !(await openModal({
    title: t("mission.stop_title"),
    text: t("mission.stop_text", { objective: mission.objective }),
    ok: t("mission.stop_ok"), danger: true,
  }))) return;
  // P43 : accepter sans preuve se dit clairement avant de le faire (ce n'est jamais « vérifiée »).
  if (action === "accepter" && !(await openModal({
    title: t("mission.accept_title"),
    text: t("mission.accept_text", { objective: mission.objective }),
    ok: t("mission.accept_ok"),
  }))) return;
  const r = await command("mission", { action });
  if (!r.ok) toast(r.error || t("common.refused"), "error");
}

async function startMission() {
  const c = channelOf(activeChannel);
  let warned = "";
  await openModal({
    title: t("mission.start_title"),
    text: t("mission.start_text"),
    fields: [
      { name: "objective", label: t("mission.f_objective"), required: true, placeholder: t("mission.f_objective_ph") },
      { name: "verification_lignes", label: t("mission.f_verification"), multiline: true, rows: 3,
        placeholder: t("mission.f_verification_ph"), hint: t("mission.f_verification_hint") },
      { name: "done_when", label: t("mission.f_done_when"),
        placeholder: t("mission.f_done_when_ph"), hint: t("mission.f_done_when_hint") },
      { name: "scope", label: t("mission.f_scope"), required: true, multiline: true,
        value: c?.workdir || "", placeholder: "~/Projets/…", hint: t("mission.f_scope_hint") },
      { name: "forbidden_paths", label: t("mission.f_forbidden_paths"), multiline: true,
        hint: t("mission.f_forbidden_paths_hint") },
      { name: "forbidden", label: t("mission.f_forbidden"), placeholder: t("mission.f_forbidden_ph") },
      { name: "max_turns", label: t("mission.f_turns"), type: "number", value: "20", min: 1, max: 200, required: true },
      { name: "max_minutes", label: t("mission.f_minutes"), type: "number", value: "60", min: 1, max: 1440, required: true },
      { name: "max_ktokens", label: t("mission.f_ktokens"), type: "number", value: "3000", min: 10, max: 100000,
        required: true, hint: t("mission.f_ktokens_hint") },
    ],
    note: t("mission.start_note"),
    ok: t("mission.start_ok"),
    // P43 : envoyée depuis la boîte, qui reste ouverte avec l'erreur du cœur (critère mal écrit…).
    check: async (v) => {
      if (!v.verification_lignes && !v.done_when) return t("mission.f_need_criterion");
      const outside = missionPathsOutside(v.objective, v.scope, c?.workdir);
      const key = JSON.stringify([v.objective, v.scope]);
      if (outside.length && warned !== key) {
        warned = key;  // second clic sur Lancer : lancé tel quel
        return t("mission.f_outside", { list: outside.slice(0, 3).join(", ") });
      }
      const spec = {
        objective: v.objective, done_when: v.done_when, verification_lignes: v.verification_lignes, scope: v.scope,
        forbidden_paths: v.forbidden_paths, forbidden: v.forbidden, channel: activeChannel,
        max_turns: +v.max_turns, max_minutes: +v.max_minutes, max_tokens: Math.round(+v.max_ktokens * 1000),
      };
      const r = await command("mission", { action: "start", spec });
      return r.ok ? "" : tx(r.error || t("mission.not_started"));
    },
  });
}
// 28/10 (hud-boutons-t10) : plus de bouton « Mission » dans la barre ; Réglages › Garde-fou › « Lancer une
// mission » ouvre la même fenêtre (même commande `mission start`, refusée à Claude par le cœur).
onHudAction("mission_start", startMission);

$("#mission-banner-pause").onclick = () => missionAction(mission.status === "paused" ? "resume" : "pause");
$("#mission-banner-stop").onclick = () => missionAction("stop");

// ------------------------------------------------------------------ modes d'usage (14/10)

// Conversation (voix), Silencieux (réponse à l'écran seulement), Dictée (copié,
// rien vers Claude). Aussi à la voix : « mode silencieux », « fin de dictée ».
const USAGE = {
  conversation: { note: "" },
  silencieux: { icon: "volume-off", note: "usage.silent_note" },
  dictee: { icon: "pen", note: "usage.dictation_note" },
};
let usageMode = "conversation";

function setUsage(mode) {
  if (!USAGE[mode]) return;
  usageMode = mode;
  document.documentElement.dataset.usage = mode;
  for (const b of document.querySelectorAll("#usage button")) {
    const on = b.dataset.mode === mode;
    b.classList.toggle("on", on);
    b.setAttribute("aria-checked", on ? "true" : "false");
  }
  $("#dictation").classList.toggle("hidden", mode !== "dictee");
  renderCuts();
}

// 16/10 : trois coupures dans la barre, comme les « muet » d'un appel.
// Micro seul (#mic-btn, `muted` de l'état) ; voix seule (#voice-btn) = mode Silencieux,
// je t'écoute et je réponds à l'écran (la Dictée ne parle pas non plus) ; les deux
// (#mute-all-btn). Rien n'est gardé ici : tout se déduit de l'état publié par le cœur,
// la bulle, les pastilles et le panneau de droite restent donc d'accord.
let micMuted = false;
const voiceCut = () => usageMode === "silencieux" || usageMode === "dictee";
const allCut = () => micMuted && voiceCut();

function renderCuts() {
  const vc = voiceCut();
  const v = $("#voice-btn");
  v.classList.toggle("muted", vc);
  setIcon(v, vc ? "volume-off" : "volume");
  v.setAttribute("aria-pressed", vc ? "true" : "false");
  v.setAttribute("aria-label", t(vc ? "voice.uncut" : "voice.cut"));
  v.title = t(usageMode === "dictee" ? "voice.dictation_title" : vc ? "voice.off_title" : "voice.on_title");
  const both = allCut();
  const a = $("#mute-all-btn");
  a.classList.toggle("muted", both);
  a.setAttribute("aria-pressed", both ? "true" : "false");
  a.setAttribute("aria-label", t(both ? "muteall.uncut" : "muteall.cut"));
  a.title = t(both ? "muteall.off_title" : "muteall.on_title");
  // Panneau de droite : « je t'écoute » serait faux micro coupé.
  const note = $("#usage-note");
  const u = USAGE[usageMode];
  if (both && usageMode === "silencieux") setLabel(note, "mute-all", t("usage.all_cut_note"), "lead");
  else if (u.icon) setLabel(note, u.icon, t(u.note), "lead");
  else note.textContent = "";
  note.classList.toggle("hidden", !u.note);
  note.title = t(both ? "usage.all_cut_note_title" : "usage.note_title");
}

async function cutCommand(cmd, on) {
  const r = await command(cmd, { on });
  if (!r.ok) toast(r.error || t("common.unchanged"), "error");
}
$("#voice-btn").onclick = () => cutCommand("voice_mute", !voiceCut());
$("#mute-all-btn").onclick = () => cutCommand("mute_all", !allCut());

let followupS = 8;
on("usage", (ev) => { setUsage(ev.mode); if (ev.followup != null) followupS = ev.followup; });
on("followup", (ev) => { followupS = ev.seconds; });

// Clic droit sur le sélecteur : combien de temps j'écoute encore après ma réponse
// (0 = une phrase à la fois : le micro se ferme après chaque réponse).
$("#usage").addEventListener("contextmenu", (e) => {
  e.preventDefault();
  document.querySelectorAll(".menu").forEach((x) => x.remove());
  const menu = el("div", "menu");
  menu.append(el("div", "small muted menu-head", t("usage.followup_head")));
  // 20/10 : une durée réglée ailleurs (Réglages, CLI : 5 s…) apparaît à sa place, cochée.
  const choices = [[0, t("usage.followup_0")], [8, t("usage.followup_8")], [20, "20 s"]];
  if (!choices.some(([sec]) => sec === followupS)) {
    choices.push([followupS, t("usage.followup_custom", { s: String(followupS) })]);
    choices.sort((a, b) => a[0] - b[0]);
  }
  for (const [sec, label] of choices) {
    const item = el("div", `item${followupS === sec ? " active" : ""}`, label);
    item.onclick = async () => {
      menu.remove();
      const r = await command("followup", { seconds: sec });
      if (!r.ok) toast(r.error || t("common.unchanged"), "error");
    };
    menu.append(item);
  }
  menu.style.left = `${Math.min(e.clientX, innerWidth - 300)}px`;
  menu.style.bottom = `${innerHeight - e.clientY + 4}px`;
  document.body.append(menu);
  setTimeout(() => addEventListener("click", () => menu.remove(), { once: true }), 0);
});
for (const b of document.querySelectorAll("#usage button")) {
  b.onclick = async () => {
    if (b.dataset.mode === usageMode) return;
    const r = await command("usage", { mode: b.dataset.mode });
    if (!r.ok) toast(r.error || t("usage.unchanged"), "error");
  };
}
$("#usage-note").onclick = () => (allCut() ? cutCommand("mute_all", false) : command("usage", { mode: "conversation" }));
renderCuts();

on("dictation", (ev) => {
  $("#dictation-text").textContent = ev.text || "…";
  $("#dictation-copy").classList.toggle("done", !!ev.copied);
});
$("#dictation-copy").onclick = async () => {
  const r = await command("dictation", { action: "copy" });
  toast(t(r.ok ? "dictation.copied" : "dictation.copy_failed"), r.ok ? "info" : "error");
};
$("#dictation-clear").onclick = () => command("dictation", { action: "clear" });

// ------------------------------------------------------------------ autorisations « 30 min »

// Ce que « Oui pour 30 min » a ouvert : visible, et révocable d'un clic.
on("grants", (ev) => {
  const items = ev.items || [];
  const box = $("#grants");
  box.replaceChildren();
  box.classList.toggle("hidden", !items.length);
  $("#grants-title").classList.toggle("hidden", !items.length);
  for (const g of items) {
    const row = el("div", "grant");
    const label = String(g.key).replace(/^bash:/, "").replace(/^write:/, t("grants.write")).replace(/^mcp__/, "").replace(/__/g, " · ");
    const left = Math.max(0, Math.round((g.until - Date.now() / 1000) / 60));
    row.append(el("span", "", tx(label)), el("span", "time", `${left} min`));
    const x = el("button", "close");
    x.append(icon("x"));
    x.title = t("grants.remove");
    x.setAttribute("aria-label", t("common.remove"));
    x.onclick = () => command("grants", { action: "revoke", key: g.key });
    row.append(x);
    box.append(row);
  }
});
$("#grants-clear").onclick = () => command("grants", { action: "revoke", key: "" });

// 26/10 (plan P33 lot 3, accord de l'utilisateur) : volet « Courrier » des agents (évènement `courrier`,
// `courrier.etat`), Avancé seulement (CSS). Une ligne par mission : messages, non lus, questions sans
// réponse ; clic ou Entrée : derniers messages. Le texte d'un message est une DONNÉE d'agent :
// textContent seul, jamais traduit ni rendu en markdown. Aucun bouton : on regarde, on n'agit pas.
const COURRIER_KINDS = { question: "courrier.kind_question", reponse: "courrier.kind_reponse", info: "courrier.kind_info" };
const courrierUnread = (m) => (Array.isArray(m.non_lus) ? m.non_lus : []).reduce((s, x) => s + (Number(x?.n) || 0), 0);
on("courrier", (ev) => {
  const missions = Array.isArray(ev.missions) ? ev.missions : [];
  const box = $("#courrier");
  box.replaceChildren();
  box.classList.toggle("hidden", !missions.length);
  $("#courrier-title").classList.toggle("hidden", !missions.length);
  for (const m of missions) {
    const bits = [t("courrier.messages", { n: Number(m.total) || 0 })];
    const unread = courrierUnread(m);
    if (unread) bits.push(t("courrier.unread", { n: unread }));
    if (Number(m.questions) > 0) bits.push(t("courrier.questions", { n: Number(m.questions) }));
    const row = el("div", "courrier-row");
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.title = t("courrier.row_title");
    row.append(el("span", "", String(m.mission || "?")), el("span", "time", bits.join(" · ")));
    row.onclick = () => openCourrier(m);
    row.onkeydown = (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault();
      e.stopPropagation(); // Entrée ne doit pas répondre à la carte de confirmation (comme la ligne Mémoire)
      openCourrier(m);
    };
    box.append(row);
  }
});
function openCourrier(m) {
  const p = openPanel({ title: t("courrier.panel_title", { mission: String(m.mission || "?") }), icon: "message",
    cls: "courrier-panel" });
  const unread = (Array.isArray(m.non_lus) ? m.non_lus : [])
    .map((x) => t("courrier.unread_for", { a: String(x?.a || "?"), n: Number(x?.n) || 0 }));
  const list = el("ol", "courrier-list");
  for (const x of Array.isArray(m.derniers) ? m.derniers : []) {
    const head = el("div", "courrier-head", t("courrier.msg_head", { n: Number(x.n) || 0, de: String(x.de || "?"),
      a: String(x.a || "?"), kind: t(COURRIER_KINDS[x.kind] || COURRIER_KINDS.info) })
      + (Number(x.re) > 0 ? ` · ${t("courrier.re", { re: Number(x.re) })}` : ""));
    const when = msgTime(x.at, Date.now() / 1000, locale());
    const time = el("time", "courrier-time", when.text);
    time.dateTime = when.iso;
    time.title = when.title;
    head.append(" · ", time);
    const li = el("li", "courrier-msg");
    li.append(head, el("div", "courrier-text ad-data", String(x.extrait || "")));
    list.append(li);
  }
  p.body.replaceChildren(el("p", "ad-note", unread.length ? unread.join(" · ") : t("courrier.all_read")), list,
    el("p", "ad-foot", t("courrier.foot")));
}

// ------------------------------------------------------------------ capteurs

// 22/10 (plan P23, proposition 10, question 10 : aussi en Avancé) : capteurs nommés en mots. AFFICHAGE
// seul : la commande `sensor` et la voix gardent le nom brut (EYES, WATCH…), rappelé au survol.
// Capteur inconnu : son nom brut. FOCUS : sa description d'origine (« Ne pas déranger ») devient son
// nom, la carte dit donc ce qu'il fait.
const SENSOR_NAMES = { EYES: "sensors.name_eyes", WATCH: "sensors.name_watch", HOLO: "sensors.name_holo",
  FOCUS: "sensors.name_focus", REFLEX: "sensors.name_reflex" };
const SENSOR_DESCS = { FOCUS: "sensors.desc_focus" };

// 23/10 (demande de l'utilisateur) : le panneau ne montre que WATCH (« Vue de l'écran ») ; les autres capteurs
// se règlent dans Réglages › Capteurs (core.py `_capteurs_view`) et à la voix, rien n'est retiré du cœur.
const SENSORS_SHOWN = new Set(["WATCH"]);

on("sensors", (ev) => {
  const box = $("#sensors");
  box.replaceChildren();
  for (const s of ev.items || []) {
    if (!SENSORS_SHOWN.has(String(s.name || ""))) continue;
    const active = s.on && s.status === "actif";
    const card = el("div", `sensor${active ? " on" : s.on ? " wanted" : ""}`);
    const raw = String(s.name || "");
    card.title = `${t(s.on ? "sensors.turn_off" : "sensors.turn_on")} (${raw})`;
    card.dataset.sensor = raw;
    const name = SENSOR_NAMES[raw] ? t(SENSOR_NAMES[raw]) : raw;
    const desc = SENSOR_DESCS[raw] ? t(SENSOR_DESCS[raw]) : tx(s.desc);
    card.append(el("b", "", name), el("span", "st", s.on ? tx(s.status) : t("sensors.off")), el("span", "desc", desc));
    card.onclick = async () => {
      const r = await command("sensor", { name: s.name, on: !s.on });
      if (!r.ok) toast(r.error, "error");
    };
    box.append(card);
  }
});

// ------------------------------------------------------------------ capteurs en direct

const GESTURES = { yes: ["thumbs-up", "gesture.yes"], no: ["thumbs-down", "gesture.no"], stop: ["hand", "gesture.stop"], wake: ["pointer", "gesture.wake"] };
on("gesture", (ev) => {
  if (ev.replay) return;
  const g = GESTURES[ev.action];
  toast(t("gesture.toast", { name: g ? t(g[1]) : ev.name }), "info", g ? g[0] : "");
});
on("presence", (ev) => {
  const p = $("#presence");
  p.classList.remove("hidden");
  p.classList.toggle("away", !ev.present);
  setLabel(p, ev.present ? "user" : "user-x", t(ev.present ? "presence.here" : "presence.away"), "lead");
});
on("reflex", (ev) => { if (!ev.replay) activity("tool", t("activity.reflex"), ev.name, ev.ts); });

// ------------------------------------------------------------------ quota

on("quota", (ev) => {
  const box = $("#quota");
  box.classList.remove("muted");
  box.replaceChildren();
  const names = { five_hour: t("quota.five_hour"), seven_day: t("quota.week"), seven_day_opus: `${t("quota.week")} · Opus`,
    seven_day_sonnet: `${t("quota.week")} · Sonnet` };
  // 15/10 (idée 10) : `windows` arrive en liste [{name, pct, utilization, resetsAt}] (cœur du
  // 15/10, pct en pour cent), ou encore tel que le CLI le donne ({five_hour: {utilization, resetsAt}}).
  const windows = Array.isArray(ev.windows)
    ? ev.windows.filter((w) => w && typeof w === "object").map((w) => {
      const u = typeof w.pct === "number" ? w.pct / 100
        : typeof w.utilization === "number" ? (w.utilization > 1 ? w.utilization / 100 : w.utilization) : 0;
      return [String(w.name ?? ""), { utilization: u, resetsAt: w.resetsAt ?? w.resets_at }];
    })
    : Object.entries(ev.windows && typeof ev.windows === "object" ? ev.windows : {});
  for (const [k, w] of windows) {
    const pct = Math.round((w.utilization || 0) * 100);
    const reset = w.resetsAt ? new Date(w.resetsAt * 1000).toLocaleString(locale(), { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "";
    const row = el("div");
    const head = el("div", "row");
    head.append(el("span", "", names[k] || k), el("span", "", `${t("unit.pct", { n: pct })}${reset ? ` · ${t("quota.reset", { when: reset })}` : ""}`));
    const bar = el("div", `bar${pct >= 80 ? " warn" : ""}`);
    const fill = el("div");
    fill.style.width = `${pct}%`;
    bar.append(fill);
    row.append(head, bar);
    box.append(row);
  }
  // Prévision du cœur (« limite vers 17 h 40 ») : affichée telle quelle, jamais dite.
  if (typeof ev.forecast === "string" && ev.forecast.trim()) {
    const f = el("div", "quota-forecast");
    setLabel(f, "timer", t("quota.forecast", { text: tx(ev.forecast.trim()) }), "lead");
    f.title = t("quota.forecast_title");
    box.append(f);
  }
  if (ev.status && ev.status !== "allowed") box.append(el("div", "small", t("common.status", { text: tx(ev.status) })));
});

// 22/10 (demande : « le compteur ne se met pas à jour quand je ne l'utilise pas sur Astrel ») : ↻ relève
// le quota tout de suite (`quota_refresh` : /usage du CLI, sans jeton) ; le résultat arrive par `quota`.
$("#quota-refresh").onclick = async () => {
  const b = $("#quota-refresh");
  if (b.getAttribute("aria-busy") === "true") return;
  b.setAttribute("aria-busy", "true");
  try {
    const r = await command("quota_refresh", {}, 10000, 45000);
    if (!r.ok) toast(t("quota.refresh_failed", { error: tx(r.error) || t("common.send_failed") }), "warning");
  } finally {
    b.removeAttribute("aria-busy");
  }
};

// ------------------------------------------------------------------ contexte, arrêt d'urgence

on("context", (ev) => {
  const box = $("#context");
  const k = Math.round((ev.tokens || 0) / 1000);
  const limit = Math.round((ev.limit || 0) / 1000);
  box.classList.toggle("muted", !ev.tokens);
  box.classList.toggle("warn", !!(limit && k >= limit * 0.8));
  box.textContent = ev.tokens
    ? `${t("context.tokens", { k })}${limit ? ` · ${t("context.relief", { limit })}` : ""}`
    : t("context.fresh");
});

// 21/10 (plan P20) : mémoire du PC, ligne « Mémoire » sous « Contexte » (cachée tant que le cœur
// n'a rien mesuré). Ambre en attention, rouge en blocage (jetons --amber/--red) ; en blocage, segment
// de la ligne d'alerte. Clic ou Entrée : classement des gros consommateurs (`memory_top`, refusée à
// Claude), tout en textContent ; AUCUN bouton pour fermer un processus : Astrel ne ferme rien.
const memGo = (v) => Number(v || 0).toLocaleString(locale(), { minimumFractionDigits: 1, maximumFractionDigits: 1 });
let lastMemory = null;
on("memory", (ev) => {
  lastMemory = ev;
  const state = ["attention", "blocage"].includes(ev.state) ? ev.state : "ok";
  const box = $("#memory");
  box.classList.remove("hidden");
  box.classList.toggle("muted", state === "ok");
  box.classList.toggle("warn", state === "attention");
  box.classList.toggle("crit", state === "blocage");
  box.textContent = t("memory.line", { used: memGo(ev.ram_used_go), total: memGo(ev.ram_total_go), swap: ev.swap_pct ?? 0 });
  box.dataset.state = state;
  box.setAttribute("aria-label", `${box.textContent} · ${t(`memory.state_${state}`)}`);
  $("#memory-banner").hidden = state !== "blocage";
  $("#memory-detail").textContent = state === "blocage" ? ` · ${t(ev.active ? "memory.banner_active" : "memory.banner_observe")}` : "";
  syncAlertLine();
});
async function openMemoryTop() {
  const p = openPanel({ title: t("memory.top_title"), icon: "gauge", cls: "memory-top" });
  p.body.append(el("p", "ad-note", t("memory.loading")));
  let r;
  try {
    r = await command("memory_top", {});
  } catch (err) {
    r = { ok: false, error: String(err?.message || err) };
  }
  if (!r?.ok) {
    p.body.replaceChildren(el("p", "ad-note", t("memory.unavailable", { error: tx(r?.error || "?") })));
    return;
  }
  const parts = [];
  const m = r.memory || lastMemory;
  if (m) {
    const state = ["attention", "blocage"].includes(m.state) ? m.state : "ok";
    parts.push(el("p", `mem-sum ${state}`, t("memory.summary", { used: memGo(m.ram_used_go), total: memGo(m.ram_total_go),
      free: memGo(m.ram_dispo_go), swap: m.swap_pct ?? 0, state: t(`memory.state_${state}`) })));
  }
  const list = el("ol", "mem-list");
  for (const it of r.items || []) {
    const li = el("li", "mem-row");
    const extra = [t("memory.procs", { n: it.n || 0 })];
    if ((it.swap_go || 0) >= 0.05) extra.unshift(t("memory.swap_part", { go: memGo(it.swap_go) }));
    li.append(el("span", "mem-name", tx(String(it.label || "?"))), el("span", "mem-size", t("memory.size", { go: memGo(it.go) })),
      el("span", "mem-extra", extra.join(" · ")));
    list.append(li);
  }
  parts.push(r.items?.length ? list : el("p", "ad-note", t("memory.empty")));
  parts.push(el("p", "ad-foot", t("memory.foot", { total: memGo(r.total_go) })));
  p.body.replaceChildren(...parts);
}
$("#memory").onclick = openMemoryTop;
$("#memory").onkeydown = (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  e.preventDefault();
  e.stopPropagation(); // Entrée ne doit pas répondre à la carte de confirmation (comme la ligne d'agent)
  openMemoryTop();
};
$("#memory-show").onclick = openMemoryTop;
on("estop", (ev) => {
  const chip = $("#estop-chip");
  chip.classList.toggle("on", !!ev.on);
  setLabel(chip, ev.on ? "play" : "no-entry", t(ev.on ? "estop.resume_all" : "estop.stop_all"));
});
$("#estop-chip").onclick = async () => {
  const on = !$("#estop-chip").classList.contains("on");
  if (on && !(await openModal({
    title: t("estop.confirm_title"),
    text: t("estop.confirm_text"),
    ok: t("estop.confirm_ok"), danger: true,
  }))) return;
  const r = await command("estop", { mode: on ? "on" : "off" });
  if (!r.ok) toast(r.error || t("common.refused"), "error");
};

// ------------------------------------------------------------------ voix

$("#voice-badge").onclick = async (e) => {
  // Position lue AVANT l'await : ensuite e.currentTarget vaut null.
  const rect = e.currentTarget.getBoundingClientRect();
  const r = await command("voices");
  if (!r.ok) { toast(r.error, "error"); return; }
  const menu = el("div", "menu");
  for (const v of r.voices) {
    const item = el("div", `item${r.active === v.spec ? " active" : ""}`, tx(v.label));
    if (v.online) { item.append(icon("cloud", "trail")); item.title = t("voice.online"); }
    item.onclick = async () => {
      menu.remove();
      const res = await command("voice", { spec: v.spec });
      if (!res.ok) toast(t("voice.unavailable", { error: tx(res.error) }), "error");
    };
    menu.append(item);
  }
  menu.style.top = `${rect.bottom + 6}px`;
  menu.style.right = `${innerWidth - rect.right}px`;
  document.body.append(menu);
  setTimeout(() => addEventListener("click", () => menu.remove(), { once: true }), 0);
};

// ------------------------------------------------------------------ modèle

// Modèle principal du cerveau, changé à chaud. Changer fait relire toute la
// conversation sans cache : c'est l'utilisateur qui choisit, jamais Astrel.
$("#model-badge").onclick = async (e) => {
  // Position lue AVANT l'await : ensuite e.currentTarget vaut null.
  const rect = e.currentTarget.getBoundingClientRect();
  const r = await command("models");
  if (!r.ok) { toast(r.error, "error"); return; }
  const menu = el("div", "menu");
  for (const m of r.models) {
    const item = el("div", `item${r.active === m.model ? " active" : ""}`, tx(m.label));
    item.append(el("div", "small muted", tx(m.desc)));
    item.onclick = async () => {
      menu.remove();
      if (m.model === r.active) return;
      const res = await command("model", { model: m.model });
      if (!res.ok) toast(t("model.unchanged", { error: tx(res.error) }), "error");
    };
    menu.append(item);
  }
  menu.style.top = `${rect.bottom + 6}px`;
  menu.style.right = `${innerWidth - rect.right}px`;
  document.body.append(menu);
  setTimeout(() => addEventListener("click", () => menu.remove(), { once: true }), 0);
};

// ------------------------------------------------------------------ effort de réflexion (23/10, plan P28)

// Demande de l'utilisateur : régler l'effort du cerveau à côté du modèle. Même réglage que Réglages › Cerveau
// (`brain_effort`, commande `settings_set`), appliqué À CHAUD par le cœur ; évènement `effort` (collant).
// Claude ne le change jamais (réglages refusés à Claude). Haiku n'a pas d'effort, un autre fournisseur non plus.
const EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"];
const EFFORT_REASON = { haiku: "effort.title_haiku", local: "effort.title_local", ext: "effort.title_ext" };
let effortNow = { active: "", levels: EFFORT_LEVELS, reason: "", locked: false };

function effortLabel(level) {
  if (EFFORT_LEVELS.includes(level)) return t(`effort.level.${level}`);
  return level ? String(level) : t("effort.level.cli");
}

on("effort", (ev) => {
  effortNow = { ...effortNow, ...ev, levels: Array.isArray(ev.levels) && ev.levels.length ? ev.levels : EFFORT_LEVELS };
  const b = $("#effort-badge");
  const level = effortLabel(ev.active);
  setLabel(b, "gauge", t("effort.badge", { level }), "lead");
  // Haiku, mode léger, autre fournisseur, variable d'environnement : badge grisé, la raison au survol
  b.classList.toggle("off", !!ev.reason || !!ev.locked);
  b.title = ev.locked ? t("effort.title_locked", { level })
    : EFFORT_REASON[ev.reason] ? t(EFFORT_REASON[ev.reason], { level }) : t("effort.title", { level });
});

$("#effort-badge").onclick = (e) => {
  // Position lue AVANT tout await : ensuite e.currentTarget vaut null.
  const rect = e.currentTarget.getBoundingClientRect();
  const level = effortLabel(effortNow.active);
  if (effortNow.locked) { toast(t("effort.title_locked", { level }), "warning"); return; }
  if (effortNow.reason === "ext") { toast(t("effort.title_ext"), "warning"); return; }
  const menu = el("div", "menu effort-menu");
  for (const lvl of effortNow.levels) {
    const item = el("div", `item${effortNow.active === lvl ? " active" : ""}`, effortLabel(lvl));
    // xhigh et max consomment plus de quota : dit dans la ligne (demande du plan, infobulle)
    if (EFFORT_LEVELS.includes(lvl)) item.append(el("div", "small muted", t(`effort.desc.${lvl}`)));
    item.onclick = async () => {
      menu.remove();
      if (lvl === effortNow.active) return;
      const r = await command("settings_set", { key: "brain_effort", value: lvl });
      if (r?.ok === false) toast(t("effort.unchanged", { error: tx(r.error) }), "error");
    };
    menu.append(item);
  }
  menu.append(el("div", "small muted effort-note", t(effortNow.reason === "haiku" ? "effort.menu_haiku" : "effort.menu_note")));
  menu.style.top = `${rect.bottom + 6}px`;
  menu.style.right = `${innerWidth - rect.right}px`;
  document.body.append(menu);
  setTimeout(() => addEventListener("click", () => menu.remove(), { once: true }), 0);
};

// ------------------------------------------------------------------ micro

on("mic", (ev) => {
  setLabel($("#mic-badge"), "mic", ev.label, "lead");
  $("#mic-badge").title = t(ev.system ? "mic.badge_system" : "mic.badge_chosen", { label: ev.label });
});

$("#mic-badge").onclick = async (e) => {
  // Position lue AVANT l'await : ensuite e.currentTarget vaut null.
  const rect = e.currentTarget.getBoundingClientRect();
  const r = await command("mics");
  if (!r.ok) { toast(r.error, "error"); return; }
  const choices = [{ name: "", label: t("mic.system") }, ...r.mics];
  const menu = el("div", "menu");
  for (const m of choices) {
    const item = el("div", `item${r.active === m.name ? " active" : ""}`, m.label + (m.default ? `  ·  ${t("mic.system_tag")}` : ""));
    item.title = m.name || t("mic.system_title");
    item.onclick = async () => {
      menu.remove();
      const res = await command("mic", { source: m.name });
      if (!res.ok) toast(t("mic.unavailable", { error: tx(res.error) }), "error");
      else toast(t("mic.set", { label: m.label }));
    };
    menu.append(item);
  }
  menu.style.top = `${rect.bottom + 6}px`;
  menu.style.right = `${innerWidth - rect.right}px`;
  document.body.append(menu);
  setTimeout(() => addEventListener("click", () => menu.remove(), { once: true }), 0);
};

// ------------------------------------------------------------------ utilitaires

// `ico` : icône facultative devant le texte. Le texte peut venir du serveur (notices) :
// ses émojis sont retirés à l'affichage, il reste posé en textContent. 20/10 : un texte du
// cœur (erreur, notice) est traduit ici (`tx`, sans effet sur un texte déjà traduit).
function toast(text, level = "info", ico = "") {
  if (!text) return;
  const n = el("div", `toast ${level}`, stripEmoji(tx(text)));
  if (ico) n.prepend(icon(ico, "lead"));
  $("#toasts").append(n);
  setTimeout(() => n.remove(), level === "error" ? 9000 : 5000);
  return n;
}

function localStorageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localStorageSet(k, v) { try { localStorage.setItem(k, v); } catch { /* sans stockage */ } }

connect();

