// Crochet de chargement pour `node --test` (plan P5) : « baileys » → faux Baileys.
// Utilisé par test/bridge.test.mjs : node --import ./testkit/register.mjs bridge.mjs …
export async function resolve (specifier, context, next) {
  if (specifier === 'baileys') return { url: new URL('./fake-baileys.mjs', import.meta.url).href, shortCircuit: true }
  return next(specifier, context)
}
