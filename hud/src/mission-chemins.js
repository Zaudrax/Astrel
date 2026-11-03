// 02/11 (essai réel) : chemins absolus de l'objectif hors du périmètre (relatif : depuis le dossier du canal,
// comme le cœur ; /tmp toujours permis). Un avertissement, pas un refus : l'objectif peut citer un dossier à LIRE.
export function missionPathsOutside(objective, scopeText, workdir) {
  const norm = (p) => p.replace(/\/+$/, "") || "/";
  const scope = String(scopeText || "").split(/[\n,;]/).map((l) => l.trim().replace(/^['"]|['"]$/g, ""))
    .filter(Boolean).map((l) => norm(l.startsWith("/") || !workdir ? l : `${workdir}/${l}`))
    .filter((l) => l.startsWith("/")).concat("/tmp");
  const out = [];
  for (const m of String(objective || "").matchAll(/(?:^|[\s«"'(])(\/[^\s»"'(),;]+)/g)) {
    const p = norm(m[1].replace(/[.:!?]+$/, ""));
    if (!scope.some((s) => p === s || p.startsWith(s === "/" ? "/" : `${s}/`)) && !out.includes(p)) out.push(p);
  }
  return out;
}
