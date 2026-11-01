// Ancien module du rythme (plan P14). Depuis P40 lot A2, tout est dans `humain.mjs` (pauses,
// frappe, souris, défilement, horaires) ; ce fichier n'en est plus qu'un renvoi, gardé pour un
// import resté ailleurs. Code nouveau : importer `humain.mjs`.
export * from './humain.mjs'
