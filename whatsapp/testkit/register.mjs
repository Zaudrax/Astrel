// `node --import ./testkit/register.mjs bridge.mjs …` : le pont tourne sur le faux Baileys.
import { register } from 'node:module'
register('./hooks.mjs', import.meta.url)
