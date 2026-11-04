// Rendu SÛR d'un sous-ensemble de markdown pour le fil du HUD (15/10).
//
// Le texte affiché peut venir d'une page web, d'un mail ou d'un document : il ne
// devient JAMAIS du HTML. Pas d'innerHTML : on construit des nœuds avec
// createElement et des nœuds texte, d'une liste fermée de balises (TAGS) ;
// aucune balise ni aucun attribut ne vient du texte. Pas d'image (le texte de
// remplacement est montré à la place). Un lien n'est cliquable que s'il commence
// par https:// ; sinon il reste du texte inerte.
//
// Rendu : paragraphes (sauts de ligne gardés), intertitres #, listes à puces et
// numérotées (imbriquées par le retrait), citations >, blocs de code ```,
// séparateur ---, **gras**, *italique*, ~~barré~~, `code`, [texte](https://…)
// et https://… nus. Pensé pour le flux : un bloc ``` pas encore fermé s'affiche
// déjà en code, un ** pas encore fermé reste du texte jusqu'à sa fermeture.
//
// 19/10 (demande : « tableau, schéma et autre ? ») : tableaux | a | b | (alignement
// :--: lu, défilement horizontal s'ils sont larges) et bouton « Copier » discret sur
// chaque bloc de code (le code brut, espaces des schémas ASCII gardés).
//
// 04/11 (demande : « les liens ne sont pas ouvrables comme sur ChatGPT, avec un logo qui dit
// que c'est un lien ») : chaque lien porte la classe `md-link` et une petite icône du jeu du HUD
// (icons.js, `external-link`, aria-hidden). Une adresse de plus de LONG_URL caractères est rognée
// À L'AFFICHAGE seulement (CSS, points de suspension) : le texte du DOM et le href restent
// l'adresse entière (sélection et copie à la main donnent l'adresse complète). `linkifyText` :
// le message de l'utilisateur, texte brut, où seules les adresses https:// nues deviennent des liens
// (jamais dans un bloc ``` ni un `code`). Le clic est pris par main.js (navigateur du système).
//
// 04/11 (demande : chemins de fichiers cliquables) : dans une réponse du FIL (`renderMarkdown(…, { paths:
// true })`), un chemin absolu (/data/x.py, ~/Documents/a, C:\Users\b) devient un <span class="md-path">
// (rôle bouton, au clavier aussi) ; dans un `code` d'une ligne aussi, c'est souvent là qu'ils sont (le
// <code> entier quand tout le code est un chemin) ; jamais dans un bloc ```, ni dans le texte d'un lien.
// Un clic COPIE le chemin (main.js, comme les chemins des comptes rendus), rien ne s'ouvre. Détection
// prudente : `pathPieces` (dates, fractions, « et/ou », URL : jamais). Le texte du DOM reste celui de la
// réponse : la sélection à la main et « Copier » du message (m.raw) ne changent pas.

import { icon, stripEmoji } from "./icons.js";
import { t } from "./i18n.js";

const TAGS =new Set(["p", "br", "h3", "h4", "h5", "ul", "ol", "li", "blockquote", "pre", "code", "hr",
  "strong", "em", "del", "a", "span", "div", "button", "table", "thead", "tbody", "tr", "th", "td"]);
const LONG_URL = 60; // au-delà : adresse rognée à l'affichage (classe « long »)
const MAX_COLS = 32; // au-delà : pas un tableau (texte)
const MAX_DEPTH = 6; // listes et citations imbriquées au-delà : du texte

const MAX_TEXT = 200_000; // au-delà : texte brut, sans analyse (le HUD ne doit jamais geler)
const HR = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
const QUOTE = /^ {0,3}>\s?(.*)$/;
const ITEM = /^(\s*)([-*+•]|\d{1,9}[.)])\s+(.*)$/;

const indentOf = (s) => s.replace(/\t/g, "    ").length;
const isSpace = (ch) => ch === " " || ch === "\t" || ch === "\n" || ch === "\r" || ch === "\f" || ch === "\v";

// Clôtures ``` et intertitres # : lus à la main (une regex à motifs qui se
// chevauchent coûtait un temps quadratique sur une longue ligne).
function fenceOf(line) {
  let i = 0;
  while (i < 3 && line[i] === " ") i++;
  const ch = line[i];
  if (ch !== "`" && ch !== "~") return null;
  let k = i;
  while (line[k] === ch) k++;
  if (k - i < 3) return null;
  const info = line.slice(k).trim();
  if (ch === "`" && info.includes("`")) return null;
  return { mark: line.slice(i, k), lang: info.split(/\s/, 1)[0] || "" };
}

function closesFence(line, mark) {
  const t = line.trimEnd();
  let i = 0;
  while (i < 3 && t[i] === " ") i++;
  if (t.length - i < mark.length) return false;
  for (let k = i; k < t.length; k++) if (t[k] !== mark[0]) return false;
  return true;
}

function headingOf(line) {
  let i = 0;
  while (i < 3 && line[i] === " ") i++;
  let k = i;
  while (line[k] === "#" && k - i < 7) k++;
  const level = k - i;
  if (level < 1 || level > 6 || k >= line.length || !isSpace(line[k])) return null;
  let text = line.slice(k).trim();
  let e = text.length; // « ## Titre ## » : la fermeture facultative est retirée
  while (e > 0 && text[e - 1] === "#") e--;
  if (e < text.length && (e === 0 || isSpace(text[e - 1]))) text = text.slice(0, e).trimEnd();
  return { level, text };
}

// Cellules d'une ligne de tableau : coupée aux | non échappés (\| reste un |), bordures
// facultatives retirées. Lu à la main, en une passe. null si ce n'est pas une ligne de tableau.
function cellsOf(line) {
  if (!line.includes("|")) return null;
  const cells = [];
  let cur = "";
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === "\\" && line[i + 1] === "|") { cur += "|"; i++; continue; }
    if (ch === "|") { cells.push(cur); cur = ""; if (cells.length > MAX_COLS + 1) return null; continue; }
    cur += ch;
  }
  cells.push(cur);
  if (!cells[0].trim()) cells.shift();
  if (cells.length && !cells[cells.length - 1].trim()) cells.pop();
  return cells.length && cells.length <= MAX_COLS ? cells.map((c) => c.trim()) : null;
}

// Ligne de séparation « |:---|:--:|---:| » : alignement de chaque colonne, ou null.
function alignsOf(line) {
  if (!line.includes("|") || !line.includes("-")) return null;
  const cells = cellsOf(line);
  if (!cells) return null;
  const out = [];
  for (const c of cells) {
    if (!/^:?-+:?$/.test(c)) return null;
    const l = c[0] === ":", r = c[c.length - 1] === ":";
    out.push(l && r ? "center" : r ? "right" : l ? "left" : "");
  }
  return out;
}

/** Découpe le texte en blocs (données pures, testables sans DOM). */
export function parseBlocks(src, depth = 0) {
  const lines = String(src ?? "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let para = null;
  let list = null; // pile de listes ouvertes : [{ indent, block }]
  const closePara = () => { para = null; };
  const closeList = () => { list = null; };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    const fence = fenceOf(line);
    if (fence) {
      closePara(); closeList();
      const body = [];
      let j = i + 1;
      for (; j < lines.length; j++) {
        if (closesFence(lines[j], fence.mark)) break;
        body.push(lines[j]);
      }
      blocks.push({ type: "code", lang: fence.lang, text: body.join("\n"), open: j >= lines.length });
      i = j;
      continue;
    }

    if (!line.trim()) {
      closePara();
      // une ligne vide n'interrompt pas une liste si la suite en est un élément
      if (list) {
        let k = i + 1;
        while (k < lines.length && !lines[k].trim()) k++;
        if (k >= lines.length || !ITEM.exec(lines[k])) closeList();
      }
      continue;
    }

    // tableau : une ligne d'en-tête avec des |, puis la ligne de séparation au même
    // nombre de colonnes ; les lignes suivantes avec des | en sont les rangées
    if (i + 1 < lines.length && line.includes("|") && !QUOTE.test(line)) {
      const aligns = alignsOf(lines[i + 1]);
      const head = aligns && cellsOf(line);
      if (head && head.length === aligns.length) {
        closePara(); closeList();
        const rows = [];
        let j = i + 2;
        for (; j < lines.length && lines[j].trim() && lines[j].includes("|"); j++) {
          const cells = cellsOf(lines[j]) || [lines[j].trim()];
          rows.push(Array.from(aligns, (_, k) => cells[k] ?? ""));
        }
        blocks.push({ type: "table", aligns, head, rows });
        i = j - 1;
        continue;
      }
    }

    const h = headingOf(line);
    if (h) {
      closePara(); closeList();
      blocks.push({ type: "heading", level: h.level, text: h.text });
      continue;
    }

    if (HR.test(line)) {
      closePara(); closeList();
      blocks.push({ type: "hr" });
      continue;
    }

    if (QUOTE.test(line) && depth < MAX_DEPTH) {
      closePara(); closeList();
      const inner = [];
      for (; i < lines.length && QUOTE.test(lines[i]); i++) inner.push(QUOTE.exec(lines[i])[1]);
      i--;
      blocks.push({ type: "quote", blocks: parseBlocks(inner.join("\n"), depth + 1) });
      continue;
    }

    const item = ITEM.exec(line);
    if (item) {
      closePara();
      const indent = indentOf(item[1]);
      const ordered = /\d/.test(item[2]);
      const entry = { text: item[3], children: [] };
      if (!list) list = [];
      while (list.length && list[list.length - 1].indent > indent + 1) list.pop();
      const top = list[list.length - 1];
      if (top && indent >= top.indent + 2 && top.block.items.length && list.length < MAX_DEPTH) {
        // plus en retrait : sous-liste du dernier élément
        const block = { type: ordered ? "ol" : "ul", start: ordered ? parseInt(item[2], 10) : 1, items: [entry] };
        top.block.items[top.block.items.length - 1].children.push(block);
        list.push({ indent, block });
      } else if (top && top.block.type === (ordered ? "ol" : "ul")) {
        top.block.items.push(entry);
      } else {
        const block = { type: ordered ? "ol" : "ul", start: ordered ? parseInt(item[2], 10) : 1, items: [entry] };
        if (top && list.length > 1) {
          // même niveau, autre sorte de liste : nouvelle sous-liste à côté
          list.pop();
          const parent = list[list.length - 1].block;
          parent.items[parent.items.length - 1].children.push(block);
        } else {
          list = [];
          blocks.push(block);
        }
        list.push({ indent, block });
      }
      continue;
    }

    if (list) {
      // suite d'un élément de liste (ligne en retrait ou paresseuse)
      const top = list[list.length - 1].block;
      top.items[top.items.length - 1].text += "\n" + line.trim();
      continue;
    }

    if (!para) {
      para = { type: "p", text: line };
      blocks.push(para);
    } else {
      para.text += "\n" + line;
    }
  }
  return blocks;
}

// ------------------------------------------------------------------ en ligne
//
// Analyse à la main, en une passe (15/10, relecture) : une regex ambiguë sur
// `[…](…)` coûtait un temps cubique (« [a]( » répété 6 400 fois : 3 s, à chaque
// morceau du flux). Ici chaque recherche de fermeture est MÉMORISÉE (la
// prochaine fermeture valide ne dépend pas d'où l'on part), une URL est bornée
// à MAX_URL caractères et n'a qu'un niveau de parenthèses.

const MAX_URL = 2000;
const URL_STOP = /[\s<>"'`]/;
const SAFE_URL = /^https:\/\/[^\s<>"'`]+$/i;

/** Lien cliquable seulement en https:// (jamais javascript:, data:, http:…). */
export function safeHref(url) {
  const u = String(url || "").trim();
  if (u.length > MAX_URL || !SAFE_URL.test(u)) return null;
  try {
    const parsed = new URL(u);
    return parsed.protocol === "https:" && parsed.hostname ? parsed.href : null;
  } catch {
    return null;
  }
}

// Ponctuation qui termine une URL nue plutôt que d'en faire partie (04/11 : « * », fin d'un **gras**).
const TRAILING = new Set([..."., ;:!?…»\"')]*"].filter((c) => c !== " "));
function trimUrl(u) {
  let end = u.length;
  while (end > 0 && TRAILING.has(u[end - 1])) end--;
  let s = u.slice(0, end);
  // une parenthèse fermante n'est gardée que si l'URL en a ouvert une
  if (end < u.length && u[end] === ")") {
    let open = 0;
    for (const ch of s) open += ch === "(" ? 1 : ch === ")" ? -1 : 0;
    if (open > 0) s += ")";
  }
  return s;
}

// Un texte de lien qui ressemble à une adresse (« https://banque.fr », « www.x.fr »,
// « banque.fr/compte ») : on montre la VRAIE cible à la place, pour ne pas tromper.
const LOOKS_LIKE_URL = /^(?:[a-z][a-z0-9+.-]{0,20}:\/\/|www\.)|^[\w-]{1,63}(?:\.[\w-]{1,63}){1,10}(?:\/\S{0,200})?$/i;

const isWord = (ch) => !!ch && /\w/.test(ch);

// ------------------------------------------------------------------ chemins de fichiers (04/11)

const PATH_MAX = 1024;
// Caractère d'un nom de fichier ou de dossier : ni espace, ni « : », ni joker, ni guillemet, ni barre.
const PATH_CH = /[\p{L}\p{M}\p{N}._+\-@%,=~#]/u;
// Ce qui peut précéder un chemin : début, espace, ouverture. Jamais une lettre, un chiffre, « / » ou « : »
// (« et/ou », « 1/2 », « 04/11/2026 », « https://x.fr/a », « file:///etc »).
const PATH_BEFORE = new Set([..." \t\n\r([{«\"'“‘<"]);
// Ponctuation finale laissée hors du chemin (comme les liens) ; « / » final gardé (un dossier).
const PATH_TRAILING = new Set([...".,;:!?…»\"')]>”’"]);
// Juste après le chemin : un joker ou une variable, c'est un motif (« /data/*.py », « $HOME/x »), pas un chemin.
const PATH_PATTERN = /[*?$[\]{}|<\\]/;
const HAS_EXT = /\.[\p{L}\p{N}]{1,10}$/u;
const DIGITS = /^[\d.,]+$/;

// Chemin qui commence en i : { path, end } s'il est reconnu, { end } sinon (le texte jusqu'à `end` est
// déjà lu : pas de second essai dedans, temps linéaire), null si rien ne commence ici.
function pathAt(s, i, n) {
  if (i > 0 && !PATH_BEFORE.has(s[i - 1])) return null;
  let kind, sep, k;
  if (s[i] === "/") { kind = "unix"; sep = "/"; k = i; }
  else if (s[i] === "~" && s[i + 1] === "/") { kind = "home"; sep = "/"; k = i + 1; }
  else if (/[A-Za-z]/.test(s[i] || "") && s[i + 1] === ":" && s[i + 2] === "\\") { kind = "win"; sep = "\\"; k = i + 2; }
  else return null;
  const limit = Math.min(n, i + PATH_MAX);
  const segs = [];
  while (k < limit && s[k] === sep) {
    let j = k + 1;
    while (j < limit && PATH_CH.test(s[j])) j++;
    if (j === k + 1) { // nom vide : « // » (commentaire, URL) refusé, « / » final gardé
      if (s[j] === sep || !segs.length) return { end: j };
      k = j;
      break;
    }
    segs.push(s.slice(k + 1, j));
    k = j;
  }
  if (k >= limit && limit < n) return { end: k }; // trop long : texte
  if (k < n && PATH_PATTERN.test(s[k]) && s[k] !== sep) return { end: k };
  let end = k;
  while (end > i && PATH_TRAILING.has(s[end - 1])) end--;
  const path = s.slice(i, end);
  // noms après la coupe de la ponctuation finale
  const names = path.slice(kind === "win" ? 3 : kind === "home" ? 2 : 1).split(sep).filter(Boolean);
  if (!names.length || names.every((x) => DIGITS.test(x))) return { end: k };
  if (kind === "unix" && names.length < 2 && !HAS_EXT.test(names[0])) return { end: k };
  if (kind === "unix" && names.length === 1 && DIGITS.test(names[0].replace(HAS_EXT, ""))) return { end: k };
  return { path, end };
}

/** Morceaux d'un texte : texte, et chemins de fichiers reconnus ({ t: "path", v }). Une passe. */
export function pathPieces(src) {
  const s = String(src ?? "");
  const n = s.length;
  if (!/[/\\]/.test(s) || n > MAX_TEXT) return s ? [{ t: "text", v: s }] : [];
  const out = [];
  let buf = "";
  let i = 0;
  while (i < n) {
    const c = s[i];
    const hit = c === "/" || c === "~" || (s[i + 1] === ":" && s[i + 2] === "\\") ? pathAt(s, i, n) : null;
    if (hit) {
      if (hit.path) {
        if (buf) { out.push({ t: "text", v: buf }); buf = ""; }
        out.push({ t: "path", v: hit.path });
        i = hit.end;
      } else {
        buf += s.slice(i, hit.end);
        i = hit.end;
      }
      continue;
    }
    buf += c;
    i++;
  }
  if (buf) out.push({ t: "text", v: buf });
  return out;
}

/** Un `code` d'une ligne qui n'est QU'un chemin (espaces permis : « /data/Mes documents/a.pdf ») :
 *  le chemin, sinon "". Même départ que `pathAt`, jamais d'URL, de joker ni de variable. */
export function wholePath(code) {
  const v = String(code ?? "");
  if (!v || v.length > PATH_MAX || v !== v.trim() || /[\n\r\t\u0000-\u001f*?$<>|"`]/.test(v) || v.includes("://")) return "";
  const win = /^[A-Za-z]:\\/.test(v);
  if (!win && !v.startsWith("/") && !v.startsWith("~/")) return "";
  const sep = win ? "\\" : "/";
  const names = v.slice(win ? 3 : v.startsWith("~/") ? 2 : 1).split(sep);
  if (names.slice(0, -1).some((x) => !x.trim()) || !names.some(Boolean)) return "";
  if (!win && v.includes(":")) return "";
  // Une commande n'est pas un chemin : « /usr/bin/python3 -m pytest », « /a/b /c/d », « /opt/run.sh start »
  // (le chemin qu'elle contient reste copiable, `pathPieces`). Espaces permis dans un nom de dossier, et dans
  // le dernier nom s'il finit par une extension (« /data/Mes documents/Note de frais.pdf »).
  if (/\s[-/~]|\s\s/.test(v)) return "";
  const lastName = names[names.length - 1];
  if (/\s/.test(lastName) && !HAS_EXT.test(lastName)) return "";
  if (names.filter(Boolean).every((x) => DIGITS.test(x))) return "";
  if (!win && !v.startsWith("~/") && names.filter(Boolean).length < 2 && !HAS_EXT.test(names[0])) return "";
  return v;
}

// Adresse https:// nue qui commence en i : { url, href, end } si elle est cliquable (url = le texte
// montré, ponctuation finale exclue, parenthèses équilibrées), { end } si elle ne l'est pas (trop
// longue, invalide : le texte jusqu'à `end` reste inerte), null si rien ne commence ici.
function bareUrlAt(s, i, n) {
  if ((s[i] !== "h" && s[i] !== "H") || s.slice(i, i + 8).toLowerCase() !== "https://") return null;
  if (isWord(s[i - 1]) || s[i - 1] === "/") return null;
  let k = i + 8;
  const limit = Math.min(n, i + MAX_URL);
  while (k < limit && !URL_STOP.test(s[k])) k++;
  const url = trimUrl(s.slice(i, k));
  const href = k < limit || k === n ? safeHref(url) : null;
  return href ? { url, href, end: i + url.length } : { end: k };
}

/** Découpe une ligne en morceaux : texte, gras, italique, barré, code, lien. */
export function parseInline(src, depth = 0) {
  const s = String(src ?? "");
  const n = s.length;
  const out = [];
  let buf = "";
  const flush = () => { if (buf) { out.push({ t: "text", v: buf }); buf = ""; } };

  // Recherches mémorisées : `next(ch, from)` = premier indice ≥ from de ch.
  const nextCache = new Map();
  const next = (ch, from) => {
    const c = nextCache.get(ch);
    if (c && from >= c.from && (c.at === -1 || from <= c.at)) return c.at;
    const at = s.indexOf(ch, from);
    nextCache.set(ch, { from, at });
    return at;
  };
  const closeCache = new Map();
  const closeOf = (mark, c, from) => {
    const k = closeCache.get(mark);
    if (k && from >= k.from && (k.at === -1 || from <= k.at)) return k.at;
    const at = findClose(s, from, mark, c);
    closeCache.set(mark, { from, at });
    return at;
  };

  // `[texte](url "titre")` à partir de i (sur le `[`) : { label, url, end } ou null.
  const link = (i) => {
    const j = next("]", i + 1);
    if (j <= i + 1 || s[j + 1] !== "(") return null;
    const nl = next("\n", i + 1);
    if (nl !== -1 && nl < j) return null;
    let k = j + 2;
    const limit = Math.min(n, k + MAX_URL);
    let parens = 0;
    for (; k < limit; k++) {
      const ch = s[k];
      if (isSpace(ch)) break;
      if (ch === "(") { if (parens) return null; parens = 1; }
      else if (ch === ")") { if (!parens) break; parens = 0; }
    }
    if (k >= limit || parens) return null;
    const url = s.slice(j + 2, k);
    if (s[k] !== ")") {
      // titre facultatif : espaces puis "…" puis )
      let t = k;
      while (t < limit && isSpace(s[t]) && s[t] !== "\n") t++;
      if (t === k || s[t] !== '"') return null;
      const q = next('"', t + 1);
      if (q === -1 || q > t + 1 + MAX_URL) return null;
      const nl2 = next("\n", t + 1);
      if (nl2 !== -1 && nl2 < q) return null;
      if (s[q + 1] !== ")") return null;
      k = q + 1;
    }
    return { label: s.slice(i + 1, j), url, end: k + 1 };
  };

  let i = 0;
  while (i < n) {
    const c = s[i];

    if (c === "\\" && i + 1 < n && "\\`*_~[]()#>!-".includes(s[i + 1])) {
      buf += s[i + 1]; i += 2; continue;
    }

    if (c === "`") {
      let run = 1;
      while (s[i + run] === "`") run++;
      if (run <= 16) {
        const close = closeOf("`".repeat(run), "`", i + run);
        if (close !== -1) {
          flush();
          let code = s.slice(i + run, close);
          if (code.length > 1 && code[0] === " " && code[code.length - 1] === " ") code = code.slice(1, -1);
          out.push({ t: "code", v: code });
          i = close + run;
          continue;
        }
      }
      buf += s.slice(i, i + run); i += run; continue;
    }

    // image : jamais chargée, son texte de remplacement est montré
    if (c === "!" && s[i + 1] === "[") {
      const m = link(i + 1);
      if (m) { buf += t("md.image", { label: m.label || t("md.untitled") }); i = m.end; continue; }
    }

    if (c === "[") {
      const m = link(i);
      if (m) {
        const href = safeHref(m.url);
        if (href && depth < MAX_DEPTH) {
          flush();
          const plain = m.label.trim();
          const shown = LOOKS_LIKE_URL.test(plain) && plain !== href && plain !== m.url
            ? [{ t: "text", v: href }] : parseInline(m.label, depth + 1);
          out.push({ t: "a", href, c: shown });
        } else {
          buf += s.slice(i, m.end); // lien refusé : le texte d'origine, inerte
        }
        i = m.end;
        continue;
      }
    }

    const bare = (c === "h" || c === "H") ? bareUrlAt(s, i, n) : null;
    if (bare) {
      if (bare.href) {
        flush();
        out.push({ t: "a", href: bare.href, c: [{ t: "text", v: bare.url }] });
      } else {
        buf += s.slice(i, bare.end); // trop longue ou invalide : texte
      }
      i = bare.end;
      continue;
    }

    if ((c === "*" || c === "_" || c === "~") && depth < MAX_DEPTH) {
      const strong = s[i + 1] === c;
      if (c === "~" && !strong) { buf += c; i++; continue; }
      const mark = strong ? c + c : c;
      const after = s[i + mark.length];
      // `_` au milieu d'un mot (nom_de_fichier) n'est pas de l'italique
      const intraword = c === "_" && isWord(s[i - 1]);
      if (after && !isSpace(after) && !intraword) {
        let close = closeOf(mark, c, i + mark.length);
        if (close === i + mark.length) close = closeOf(mark, c, close + 1); // pas d'emphase vide
        if (close > 0) {
          flush();
          const t = c === "~" ? "del" : strong ? "strong" : "em";
          out.push({ t, c: parseInline(s.slice(i + mark.length, close), depth + 1) });
          i = close + mark.length;
          continue;
        }
      }
      buf += mark; i += mark.length; continue;
    }

    buf += c; i++;
  }
  flush();
  return out;
}

// Première fermeture VALIDE de `mark` après `from`. La validité d'une position ne
// dépend que d'elle (pas de `from`) : c'est ce qui permet de mémoriser.
function findClose(s, from, mark, c) {
  let k = from;
  while ((k = s.indexOf(mark, k)) !== -1) {
    if (c === "`") {
      // fermeture d'un code : exactement le même nombre d'accents graves
      if (s[k - 1] !== "`" && s[k + mark.length] !== "`") return k;
      k += 1;
      continue;
    }
    const before = s[k - 1];
    const nextCh = s[k + mark.length];
    const okBefore = before && !isSpace(before);
    // pour * ou _ simples, ne pas prendre la moitié d'un ** ou __
    const single = mark.length === 1 && (nextCh === c || before === c);
    const okAfter = c !== "_" || !isWord(nextCh);
    if (okBefore && !single && okAfter) return k;
    k += 1;
  }
  return -1;
}

// ------------------------------------------------------------------ DOM

function node(doc, tag, cls) {
  if (!TAGS.has(tag)) throw new Error(`balise non permise : ${tag}`);
  const e = doc.createElement(tag);
  if (cls) e.className = cls;
  return e;
}

function appendText(doc, parent, text) {
  // les sauts de ligne d'un paragraphe ou d'un élément de liste sont gardés ;
  // les émojis sont retirés du TEXTE affiché (15/10, icons.js), jamais du code
  const parts = stripEmoji(text).split("\n");
  parts.forEach((part, k) => {
    if (k) parent.append(node(doc, "br"));
    if (part) parent.append(doc.createTextNode(part));
  });
}

// Un lien (https seulement : `it.href` sort de safeHref) : nouvel onglet sans opener ni referrer, cible
// entière au survol, icône « ouvrir ailleurs » (décor, cachée aux lecteurs d'écran qui entendent la
// description). `fill(label)` y pose le texte du lien.
function linkNode(doc, it, fill) {
  const plain = it.c.length === 1 && it.c[0].t === "text" ? it.c[0].v : "";
  const a = node(doc, "a", plain.length > LONG_URL ? "md-link long" : "md-link");
  a.href = it.href;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.title = it.href;
  a.setAttribute("aria-description", t("md.link_opens"));
  const label = node(doc, "span", "md-link-text");
  fill(label);
  a.append(label, icon("external-link", "md-link-ico", doc));
  return a;
}

// 04/11 : chemin cliquable (copié par main.js) : rôle bouton, au clavier aussi, nom = le chemin lui-même.
function markPath(e) {
  e.setAttribute("role", "button");
  e.setAttribute("tabindex", "0");
  e.title = t("md.path_title");
  e.setAttribute("aria-description", t("md.path_copies"));
  return e;
}
function pathNode(doc, v) {
  const e = node(doc, "span", "md-path");
  e.textContent = v; // texte tel quel (ni émoji retiré : c'est ce qui est copié)
  return markPath(e);
}

// `paths` (04/11) : chemins de fichiers cliquables (le fil seulement, jamais dans le texte d'un lien).
function inlineTo(doc, parent, items, paths = false) {
  for (const it of items) {
    if (it.t === "text") {
      if (!paths) { appendText(doc, parent, it.v); continue; }
      for (const piece of pathPieces(it.v)) {
        if (piece.t === "path") parent.append(pathNode(doc, piece.v));
        else appendText(doc, parent, piece.v);
      }
    } else if (it.t === "code") {
      const whole = paths ? wholePath(it.v) : "";
      const e = node(doc, "code", whole ? "md-path" : "");
      if (whole) { e.textContent = it.v; markPath(e); }
      else if (!paths) e.textContent = it.v;
      else {
        for (const piece of pathPieces(it.v)) {
          e.append(piece.t === "path" ? pathNode(doc, piece.v) : doc.createTextNode(piece.v));
        }
      }
      parent.append(e);
    } else if (it.t === "a") parent.append(linkNode(doc, it, (label) => inlineTo(doc, label, it.c)));
    else {
      const e = node(doc, it.t);
      inlineTo(doc, e, it.c, paths);
      parent.append(e);
    }
  }
}

// Bouton « Copier » d'un bloc de code : copie le code BRUT (jamais le rendu), libellé
// « Copié » un instant. Sans presse-papiers (page non sûre) : il le dit sans rien casser.
function copyButton(doc, text) {
  const btn = node(doc, "button", "code-copy");
  btn.type = "button";
  btn.title = t("md.copy_title");
  btn.textContent = t("common.copy");
  btn.onclick = async (e) => {
    e.stopPropagation();
    let ok = false;
    try { await globalThis.navigator.clipboard.writeText(text); ok = true; } catch { ok = false; }
    btn.textContent = t(ok ? "md.copied" : "md.copy_failed");
    clearTimeout(btn._t);
    btn._t = setTimeout(() => { btn.textContent = t("common.copy"); }, 1400);
  };
  return btn;
}

function blocksTo(doc, parent, blocks, paths = false) {
  for (const b of blocks) {
    if (b.type === "p") {
      const p = node(doc, "p");
      inlineTo(doc, p, parseInline(b.text), paths);
      parent.append(p);
    } else if (b.type === "heading") {
      // intertitres discrets : # et ## en h3, ### en h4, le reste en h5
      const h = node(doc, b.level <= 2 ? "h3" : b.level === 3 ? "h4" : "h5");
      inlineTo(doc, h, parseInline(b.text), paths);
      parent.append(h);
    } else if (b.type === "hr") {
      parent.append(node(doc, "hr"));
    } else if (b.type === "code") {
      // 19/10 : bloc enveloppé pour porter un bouton « Copier » qui ne défile pas avec le code
      const box = node(doc, "div", "code-block");
      const pre = node(doc, "pre");
      const code = node(doc, "code");
      code.textContent = b.text;
      pre.append(code);
      box.append(copyButton(doc, b.text), pre);
      parent.append(box);
    } else if (b.type === "table") {
      const wrap = node(doc, "div", "table-wrap"); // défile en largeur si le tableau déborde
      const table = node(doc, "table");
      const row = (cells, tag) => {
        const tr = node(doc, "tr");
        cells.forEach((c, k) => {
          const cell = node(doc, tag, b.aligns[k] ? `al-${b.aligns[k]}` : "");
          inlineTo(doc, cell, parseInline(c), paths);
          tr.append(cell);
        });
        return tr;
      };
      const thead = node(doc, "thead");
      thead.append(row(b.head, "th"));
      table.append(thead);
      if (b.rows.length) {
        const tbody = node(doc, "tbody");
        for (const r of b.rows) tbody.append(row(r, "td"));
        table.append(tbody);
      }
      wrap.append(table);
      parent.append(wrap);
    } else if (b.type === "quote") {
      const q = node(doc, "blockquote");
      blocksTo(doc, q, b.blocks, paths);
      parent.append(q);
    } else if (b.type === "ul" || b.type === "ol") {
      const l = node(doc, b.type);
      if (b.type === "ol" && b.start !== 1) l.start = b.start;
      for (const it of b.items) {
        const li = node(doc, "li");
        inlineTo(doc, li, parseInline(it.text), paths);
        blocksTo(doc, li, it.children, paths);
        l.append(li);
      }
      parent.append(l);
    }
  }
}

/** Fragment DOM du texte rendu. `doc` : le document (remplaçable dans les tests). `paths` (04/11) :
 *  chemins de fichiers cliquables (fil du HUD ; ni le Guide ni les Plans, dans une fenêtre). */
export function renderMarkdown(text, doc = globalThis.document, { paths = false } = {}) {
  const frag = doc.createDocumentFragment();
  const src = String(text ?? "");
  if (src.length > MAX_TEXT) {
    const p = node(doc, "p");
    appendText(doc, p, src); // trop long pour être analysé : texte brut, inerte
    frag.append(p);
    return frag;
  }
  blocksTo(doc, frag, parseBlocks(src), paths);
  return frag;
}

// ------------------------------------------------------------------ message de l'utilisateur (04/11)

/** Morceaux d'un TEXTE BRUT : texte tel quel, et liens pour les adresses https:// nues. Rien d'autre
 *  n'est lu (ni gras ni [x](…) : le message s'affiche tel que tapé) ; pas de lien dans un bloc ```
 *  (lignes de clôture comprises) ni dans un `code` d'une ligne (accents graves gardés à l'écran).
 *  Une passe, recherches de fermeture mémorisées comme parseInline (texte hostile : temps linéaire). */
export function linkPieces(src) {
  const s = String(src ?? "");
  const n = s.length;
  if (n > MAX_TEXT || !/https:\/\//i.test(s)) return s ? [{ t: "text", v: s }] : [];
  const out = [];
  let buf = "";
  const flush = () => { if (buf) { out.push({ t: "text", v: buf }); buf = ""; } };
  const closeCache = new Map();
  const closeOf = (mark, from) => {
    const k = closeCache.get(mark);
    if (k && from >= k.from && (k.at === -1 || from <= k.at)) return k.at;
    const at = findClose(s, from, mark, "`");
    closeCache.set(mark, { from, at });
    return at;
  };
  let fence = ""; // marque du bloc ``` ouvert
  let lineEnd = -1; // fin de la ligne en cours (indice du \n, ou n)
  let i = 0;
  while (i < n) {
    if (i > lineEnd) { // début de ligne
      const e = s.indexOf("\n", i);
      lineEnd = e === -1 ? n : e;
      const line = s.slice(i, lineEnd);
      const opens = !fence && fenceOf(line);
      if (fence || opens) {
        if (fence && closesFence(line, fence)) fence = "";
        else if (opens) fence = opens.mark;
        buf += s.slice(i, lineEnd + 1);
        i = lineEnd + 1;
        continue;
      }
    }
    const c = s[i];
    if (c === "`") {
      let run = 1;
      while (s[i + run] === "`") run++;
      const close = run <= 16 ? closeOf("`".repeat(run), i + run) : -1;
      const end = close !== -1 && close < lineEnd ? close + run : i + run; // code sur la même ligne seulement
      buf += s.slice(i, end);
      i = end;
      continue;
    }
    const bare = (c === "h" || c === "H") ? bareUrlAt(s, i, n) : null;
    if (bare) {
      if (bare.href) {
        flush();
        out.push({ t: "a", href: bare.href, c: [{ t: "text", v: bare.url }] });
      } else {
        buf += s.slice(i, bare.end);
      }
      i = bare.end;
      continue;
    }
    buf += c;
    i++;
  }
  flush();
  return out;
}

/** Fragment DOM d'un message de l'utilisateur : nœuds texte (sauts de ligne gardés tels quels, la bulle est
 *  en `pre-wrap`, émojis retirés à l'affichage) et liens https. Jamais de HTML venu du texte. */
export function linkifyText(text, doc = globalThis.document) {
  const frag = doc.createDocumentFragment();
  for (const it of linkPieces(text)) {
    if (it.t === "a") frag.append(linkNode(doc, it, (label) => label.append(doc.createTextNode(it.c[0].v))));
    else frag.append(doc.createTextNode(stripEmoji(it.v)));
  }
  return frag;
}
