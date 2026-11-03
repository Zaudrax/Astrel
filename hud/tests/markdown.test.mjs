// Tests du rendu markdown sûr du fil du HUD (hud/src/markdown.js).
// Lancés par `node --test hud/tests/` (et par tests/test_hud_markdown.py dans pytest).
// Faux DOM minimal : il refuse innerHTML et note chaque balise créée, pour
// prouver qu'aucune balise ni attribut ne vient du texte.

import assert from "node:assert/strict";
import { test } from "node:test";
import { parseBlocks, parseInline, renderMarkdown, safeHref } from "../src/markdown.js";

class FakeNode {
  constructor(tag) { this.tag = tag; this.children = []; this.props = {}; }
  append(...kids) { this.children.push(...kids); }
  set textContent(v) { this.children = [new FakeText(String(v))]; }
  set innerHTML(_) { throw new Error("innerHTML interdit"); }
  set outerHTML(_) { throw new Error("outerHTML interdit"); }
  setAttribute(k, v) { this.props[k] = String(v); }
}
class FakeText { constructor(v) { this.text = v; } }

function fakeDoc() {
  const created = [];
  return {
    created,
    createElement(tag) { created.push(tag); return new Proxy(new FakeNode(tag), {
      set(t, k, v) { if (k in t || k === "children") t[k] = v; else t.props[k] = v; return true; },
    }); },
    createTextNode(v) { return new FakeText(v); },
    createDocumentFragment() { return new FakeNode("#frag"); },
  };
}

const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
function html(n) {
  if (n instanceof FakeText) return esc(n.text);
  const inner = n.children.map(html).join("");
  if (n.tag === "#frag") return inner;
  // les gestionnaires posés par le code (onclick du bouton Copier) ne sont pas des attributs
  const attrs = Object.entries(n.props).filter(([, v]) => typeof v !== "function" && !(v && typeof v === "object"))
    .map(([k, v]) => ` ${k}="${esc(String(v))}"`).join("");
  return n.tag === "br" || n.tag === "hr" ? `<${n.tag}${attrs}>` : `<${n.tag}${attrs}>${inner}</${n.tag}>`;
}
function render(md) {
  const doc = fakeDoc();
  return { out: html(renderMarkdown(md, doc)), created: doc.created };
}
const text = (n) => (n instanceof FakeText ? n.text : n.children.map(text).join(""));

test("listes à puces, numérotées et imbriquées", () => {
  const { out } = render("Voici :\n- un\n- deux\n  - deux bis\n- trois\n\n1. premier\n2. second");
  assert.equal(out, "<p>Voici :</p><ul><li>un</li><li>deux<ul><li>deux bis</li></ul></li><li>trois</li></ul>"
    + "<ol><li>premier</li><li>second</li></ol>");
});

test("liste numérotée qui ne commence pas à 1", () => {
  assert.equal(render("3. trois\n4. quatre").out, '<ol start="3"><li>trois</li><li>quatre</li></ol>');
});

test("intertitres discrets, gras, italique, code en ligne", () => {
  const { out } = render("## Résultat\nLes **469 tests** passent, *vraiment*, voir `npm test`.");
  assert.equal(out, "<h3>Résultat</h3><p>Les <strong>469 tests</strong> passent, <em>vraiment</em>, voir <code>npm test</code>.</p>");
});

test("paragraphes séparés et sauts de ligne gardés", () => {
  assert.equal(render("ligne un\nligne deux\n\nautre paragraphe").out,
    "<p>ligne un<br>ligne deux</p><p>autre paragraphe</p>");
});

test("bloc de code : contenu brut, rien n'y est interprété", () => {
  const { out } = render("```python\nprint('**pas gras**')\n<b>x</b>\n```\nfin");
  assert.equal(out, `<div className="code-block"><button className="code-copy" type="button" title="Copier ce bloc de code">Copier</button><pre><code>print('**pas gras**')\n&lt;b&gt;x&lt;/b&gt;</code></pre></div><p>fin</p>`);
});

test("flux : un bloc ``` pas encore fermé est déjà du code, un ** ouvert reste du texte", () => {
  assert.equal(render("Avant\n```\nligne en cours").out, `<p>Avant</p><div className="code-block"><button className="code-copy" type="button" title="Copier ce bloc de code">Copier</button><pre><code>ligne en cours</code></pre></div>`);
  assert.equal(render("Les **tests pas").out, "<p>Les **tests pas</p>");
  // chaque préfixe d'une réponse se rend sans erreur
  const full = "# Titre\n\n- **a** `b`\n- [lien](https://ex.fr/x)\n\n```js\nconst x = 1;\n```\n> cité\n\n---\nfin";
  for (let k = 0; k <= full.length; k++) render(full.slice(0, k));
});

test("noms de fichiers et calculs : pas d'italique parasite", () => {
  assert.equal(render("le fichier mon_fichier_test.py et 2 * 3 * 4").out,
    "<p>le fichier mon_fichier_test.py et 2 * 3 * 4</p>");
});

test("citation, séparateur, barré", () => {
  assert.equal(render("> attention\n\n---\n~~vieux~~ neuf").out,
    "<blockquote><p>attention</p></blockquote><hr><p><del>vieux</del> neuf</p>");
});

test("liens : https cliquable (noopener), le reste en texte inerte", () => {
  const { out } = render("[doc](https://exemple.fr/a?b=1) et https://astrel.local/x. et http://non.fr");
  assert.match(out, /<a href="https:\/\/exemple.fr\/a\?b=1" target="_blank" rel="noopener noreferrer" title="[^"]*">doc<\/a>/);
  assert.match(out, /<a href="https:\/\/astrel.local\/x"[^>]*>https:\/\/astrel.local\/x<\/a>\. et http:\/\/non.fr/);
});

test("ATTAQUE : balises, onerror, javascript:, data:, images → texte inerte", () => {
  const evil = [
    "<script>alert(1)</script>",
    '<img src=x onerror="alert(2)">',
    "[clique](javascript:alert(3))",
    "[x](JaVaScRiPt:alert(4))",
    "[y](data:text/html,<script>alert(5)</script>)",
    "![photo](https://pisteur.example/p.gif)",
    '<a href="javascript:alert(6)">a</a>',
    "**<iframe src=https://x.fr>**",
    "`<svg onload=alert(7)>`",
    "[z](https://ok.fr\" onclick=\"alert(8))",
  ].join("\n\n");
  const doc = fakeDoc();
  const frag = renderMarkdown(evil, doc);
  const allowed = new Set(["p", "br", "strong", "code", "a"]);
  for (const tag of doc.created) assert.ok(allowed.has(tag), `balise créée : ${tag}`);
  const out = html(frag);
  // aucune vraie balise dangereuse : tout est échappé, donc du texte
  assert.doesNotMatch(out, /<(script|img|iframe|svg)\b/i);
  assert.match(out, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(out, /&lt;img src=x onerror="alert\(2\)"&gt;/);
  assert.match(out, /\[clique\]\(javascript:alert\(3\)\)/);
  assert.match(out, /\[image : photo\]/);
  // le seul lien éventuel est en https, sans attribut d'événement
  const links = [];
  (function walk(n) { if (n.tag === "a") links.push(n); (n.children || []).forEach(walk); })(frag);
  for (const a of links) {
    assert.ok(a.props.href.startsWith("https://"), a.props.href);
    assert.ok(!Object.keys(a.props).some((k) => /^on/i.test(k)));
  }
  assert.ok(!out.includes("pisteur.example/p.gif\""), "image distante jamais chargée");
  // le texte visible garde tout le contenu
  assert.match(text(frag), /<script>alert\(1\)<\/script>/);
});

test("safeHref", () => {
  assert.equal(safeHref("https://ex.fr/a b"), null);
  assert.equal(safeHref("javascript:alert(1)"), null);
  assert.equal(safeHref("http://ex.fr"), null);
  assert.equal(safeHref("https://"), null);
  assert.equal(safeHref("https://ex.fr/a"), "https://ex.fr/a");
});

// 03/11 (CI Windows, machine ~4 fois plus lente : 126 ms pour 100) : seuils de temps rapportés à la vitesse de
// la machine, mesurée sur un rendu ORDINAIRE (le meilleur de 7). Les régressions visées (temps cubique) coûtent
// des secondes : elles restent prises. Étalon sur la machine de l'utilisateur : 8,6 à 9,5 ms (lenteur 1).
const ETALON_REF_MS = 9;
const LENTEUR = (() => {
  const md = Array.from({ length: 2000 }, (_, i) => `- **point ${i}** avec \`code\` et [lien](https://ex.fr/${i}) _x_`).join("\n");
  let best = Infinity;
  for (let k = 0; k < 7; k++) {
    const t0 = performance.now();
    renderMarkdown(md, fakeDoc());
    best = Math.min(best, performance.now() - t0);
  }
  return Math.max(1, best / ETALON_REF_MS);
})();

test("parseBlocks et parseInline restent des données pures", () => {
  assert.deepEqual(parseBlocks("# T"), [{ type: "heading", level: 1, text: "T" }]);
  assert.deepEqual(parseInline("a `b`"), [{ t: "text", v: "a " }, { t: "code", v: "b" }]);
});

test("rapide : une longue réponse se re-rend en quelques millisecondes", () => {
  const md = Array.from({ length: 60 }, (_, i) => `- **point ${i}** avec \`code\` et [lien](https://ex.fr/${i})`).join("\n");
  const t0 = performance.now();
  for (let k = 0; k < 20; k++) render(md);
  assert.ok((performance.now() - t0) / 20 < 20 * LENTEUR, `plus de ${(20 * LENTEUR).toFixed(0)} ms par rendu`);
});

// 15/10 (relecture) : une regex ambiguë sur `[…](…)` coûtait un temps cubique
// (« [a]( » × 6 400 : 3 s, et le fil se re-rend à chaque morceau du flux).
test("PERFORMANCE : entrées hostiles de 100 000 caractères en moins de 100 ms", () => {
  const N = 100_000;
  const rep = (u) => u.repeat(Math.ceil(N / u.length)).slice(0, N);
  const hostile = {
    "[a](": rep("[a]("), "![a](": rep("![a]("), "*a ": rep("*a "), "_a ": rep("_a "), "**a ": rep("**a "),
    "``a`": rep("``a`"), "[": "[".repeat(N), "https": rep("https://a"), "[a](b \"": rep("[a](b \""),
    "fence": "```" + "a".repeat(N) + "`", "heading": "# " + " ".repeat(N) + "x", "url": "https://" + "a".repeat(N),
    "lignes": rep("- a\n\n"), "citations": rep("> a\n"), "mélange": rep("**[a](https://x.fr/(a)) `b` _c_ ~~d~~ "),
  };
  const doc = fakeDoc();
  for (const [name, s] of Object.entries(hostile)) {
    const t0 = performance.now();
    renderMarkdown(s, doc);
    const ms = performance.now() - t0;
    assert.ok(ms < 100 * LENTEUR, `${name} : ${ms.toFixed(0)} ms (lenteur ${LENTEUR.toFixed(1)})`);
  }
});

test("au-delà de 200 000 caractères : texte brut, sans analyse", () => {
  const big = "**gras** <b>x</b>\n".repeat(12_000);
  const { out, created } = render(big);
  assert.ok(created.every((t) => t === "p" || t === "br"));
  assert.ok(out.startsWith("<p>**gras** &lt;b&gt;x&lt;/b&gt;<br>"));
});

test("lien dont le texte est une autre adresse : la vraie cible est montrée", () => {
  const { out } = render("[https://banque.fr](https://pirate.example/x) [www.banque.fr](https://pirate.example/) "
    + "[banque.fr/compte](https://pirate.example/) [Expo](https://expo.dev/) [https://ok.fr/](https://ok.fr/)");
  assert.equal((out.match(/>https:\/\/pirate\.example\/x?<\/a>/g) || []).length, 3);
  assert.doesNotMatch(out, />[^<]*banque\.fr[^<]*<\/a>/);
  assert.match(out, />Expo<\/a>/);
  assert.match(out, />https:\/\/ok\.fr\/<\/a>/);
});

// 19/10 (demande : « tableau, schéma et autre ? »)
test("tableau : en-tête, alignements, cellules en ligne, rangées incomplètes complétées", () => {
  const { out } = render("Bilan :\n| Nom | État | Durée |\n|:----|:---:|----:|\n| **Android** | `n°9` | 14 min |\n| iOS | ok |\n\nfin");
  assert.equal(out, '<p>Bilan :</p><div className="table-wrap"><table><thead><tr>'
    + '<th className="al-left">Nom</th><th className="al-center">État</th><th className="al-right">Durée</th></tr></thead>'
    + '<tbody><tr><td className="al-left"><strong>Android</strong></td><td className="al-center"><code>n°9</code></td>'
    + '<td className="al-right">14 min</td></tr><tr><td className="al-left">iOS</td><td className="al-center">ok</td>'
    + '<td className="al-right"></td></tr></tbody></table></div><p>fin</p>');
});

test("tableau : sans bordures, \\| échappé, nombre de colonnes différent = pas un tableau", () => {
  const { out } = render("a | b\n--- | ---\nx \\| y | z");
  assert.match(out, /<th>a<\/th><th>b<\/th>.*<td>x \| y<\/td><td>z<\/td>/);
  assert.doesNotMatch(render("| a | b |\n|---|").out, /<table>/);
  assert.doesNotMatch(render("a\n---").out, /<table>/);
  // en flux : l'en-tête seul reste du texte, la ligne de séparation en fait un tableau
  assert.doesNotMatch(render("| a | b |\n|--").out, /<table>/);
  assert.match(render("| a | b |\n|---|---|").out, /<table><thead>/);
});

test("ATTAQUE dans un tableau : balises et liens non https restent du texte", () => {
  const doc = fakeDoc();
  const frag = renderMarkdown("| x | y |\n|---|---|\n| <img src=x onerror=alert(1)> | [a](javascript:alert(2)) |", doc);
  const allowed = new Set(["div", "table", "thead", "tbody", "tr", "th", "td"]);
  for (const tag of doc.created) assert.ok(allowed.has(tag), `balise créée : ${tag}`);
  assert.match(text(frag), /<img src=x onerror=alert\(1\)>/);
  assert.match(text(frag), /\[a\]\(javascript:alert\(2\)\)/);
});

test("schéma ASCII dans un bloc ``` : espaces et traits gardés à l'identique", () => {
  const art = " micro ──► VAD\n          │\n   ◄──────┘  ";
  const doc = fakeDoc();
  const frag = renderMarkdown("```\n" + art + "\n```", doc);
  let code = null;
  (function walk(n) { if (n.tag === "code") code = n; (n.children || []).forEach(walk); })(frag);
  assert.equal(text(code), art);
});

test("bouton Copier d'un bloc de code : copie le code brut, sans le rendu", async () => {
  const doc = fakeDoc();
  const frag = renderMarkdown("```js\nconst a = `**x**`;\n```", doc);
  let btn = null;
  (function walk(n) { if (n.tag === "button") btn = n; (n.children || []).forEach(walk); })(frag);
  assert.ok(btn && typeof btn.props.onclick === "function");
  let got = null;
  const nav = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  Object.defineProperty(globalThis, "navigator", { value: { clipboard: { writeText: async (t) => { got = t; } } }, configurable: true });
  try {
    await btn.props.onclick({ stopPropagation() {} });
  } finally {
    if (nav) Object.defineProperty(globalThis, "navigator", nav);
  }
  clearTimeout(btn.props._t);
  assert.equal(got, "const a = `**x**`;");
  assert.equal(text(btn), "Copié");
});

test("PERFORMANCE : lignes à | hostiles de 100 000 caractères en moins de 100 ms", () => {
  const N = 100_000;
  const doc = fakeDoc();
  for (const s of ["|".repeat(N), "|a".repeat(N / 2) + "\n" + "|-".repeat(N / 2),
    "| a | b |\n|---|---|\n" + "| x | y |\n".repeat(N / 10), ("a|b\n").repeat(N / 4)]) {
    const t0 = performance.now();
    renderMarkdown(s, doc);
    const ms = performance.now() - t0;
    assert.ok(ms < 100 * LENTEUR, `${s.slice(0, 12)} : ${ms.toFixed(0)} ms (lenteur ${LENTEUR.toFixed(1)})`);
  }
});
