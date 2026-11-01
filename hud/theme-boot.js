// 21/10 (plan P17) : thème du HUD posé AVANT la première peinture (chargé en tête de
// index.html, script bloquant de quelques lignes), d'après le dernier choix gardé par
// hud/src/apparence.js (localStorage « astrel-theme »). Même règle que apparence.js
// (resolveMode) ; le choix des Réglages reprend la main dès l'évènement `settings`.
// 25/10 (plan P35) : Premium est le SEUL thème (data-theme="premium" déjà dans index.html, reposé
// ici) ; seuls le mode et l'accent viennent du cache. Un ancien cache « classique » ne compte plus.
(function () {
  // 22/10 (plan P23) : affichage Simple (localStorage « astrel-affichage », apparence.js) ;
  // Avancé : rien n'est posé. Indépendant du thème.
  try {
    if (localStorage.getItem("astrel-affichage") === "simple") document.documentElement.setAttribute("data-affichage", "simple");
  } catch (e) { /* sans stockage : Avancé */ }
  var c = {};
  try { c = JSON.parse(localStorage.getItem("astrel-theme") || "{}") || {}; } catch (e) { /* sans stockage : défauts */ }
  var light = false;
  try { light = !!(window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches); } catch (e) { /* sombre */ }
  var mode = c.mode === "light" || c.mode === "dark" ? c.mode : (light ? "light" : "dark");
  var accents = ["bleu", "graphite", "vert", "orange"];
  var root = document.documentElement;
  root.setAttribute("data-theme", "premium");
  root.setAttribute("data-mode", mode);
  root.setAttribute("data-accent", accents.indexOf(c.accent) >= 0 ? c.accent : "bleu");
})();
