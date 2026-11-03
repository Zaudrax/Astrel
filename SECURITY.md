# Security policy

[Version française plus bas](#politique-de-sécurité)

Astrel runs Claude Code without the CLI's permission prompts. What stands between the model and
your machine is the `PreToolUse` hook (`hooks/permission_hook.py`, logic in `astrel/guard.py`,
`astrel/totalgate.py`, `astrel/state.py`). A way around it is a security bug, and the kind of report
this project wants most.

## Reporting a vulnerability

Please report privately, through GitHub: **Security › Report a vulnerability** on this repository
(private vulnerability reporting). Do not open a public issue for a vulnerability.

A useful report says:
- what an attacker controls (a web page, an email, a document, a WhatsApp message read by Astrel,
  or a local process);
- the steps, and the exact tool call or command that gets through;
- what it achieves (an action that should have asked for confirmation and did not, a write to a
  protected file, a secret read, the HUD reached from outside, a confirmation answered by Claude
  itself…);
- the OS, Python version and Claude Code version (`claude --version`).

You will get an answer within 7 days. A fix comes with a test that reproduces the problem; the
report is credited in the release notes unless you prefer otherwise.

## What is in scope

- Getting a CRITICAL action (sudo, disks, shutdown, `git push`, deleting, sending, paying,
  deploying, sharing) through without the user's confirmation, in the default modes.
- Writing to the guardrail's own files or protected code, loosening the guardrail, or approving a
  confirmation from inside the Claude session.
- Reading secrets: `config/secrets.json`, the HUD token and session key, browser profiles, Claude
  Code credentials.
- Reaching the HUD or the control channel from another machine or another local user.
- Prompt injection from content Astrel reads that leads to any of the above.

## What is not

- Anything done after the user deliberately turned on "No confirmation" (Total autonomy) or lifted
  the guardrail from the HUD: these modes exist to remove the checks, and say so on screen.
- Attacks that need the attacker to already run code as the user on the machine, outside the
  Claude session.
- Bugs in Claude Code, Claude itself or third-party MCP servers: please report those upstream.

## Supported versions

There is no release yet: only the `main` branch receives security fixes.

---

# Politique de sécurité

Astrel fait tourner Claude Code sans les demandes de permission du CLI. Ce qui protège la machine,
c'est le hook `PreToolUse` (`hooks/permission_hook.py`, logique dans `astrel/guard.py`,
`astrel/totalgate.py`, `astrel/state.py`). Le contourner est une faille, et c'est le type de
signalement le plus attendu.

**Pour signaler une faille** : en privé, par GitHub, **Security › Report a vulnerability** sur ce
dépôt. Pas d'issue publique pour une faille. Dites ce que l'attaquant contrôle (page web, mail,
document, message WhatsApp lu par Astrel, ou processus local), les étapes, l'appel d'outil ou la
commande exacte qui passe, ce qu'elle obtient, et le système, la version de Python et celle de
Claude Code. Réponse sous 7 jours ; chaque correctif arrive avec un test qui reproduit le problème.

**Dans le périmètre** : une action CRITIQUE sans confirmation dans les modes par défaut, une écriture
dans les fichiers du garde-fou, un garde-fou desserré ou une confirmation acceptée depuis la session
de Claude, la lecture d'un secret, le HUD ou le canal de contrôle joints depuis une autre machine ou
un autre utilisateur, une injection de prompt qui mène à l'un de ces cas.

**Hors périmètre** : ce qui se passe une fois « Sans confirmation » activé ou le garde-fou levé par
l'utilisateur, une attaque qui demande déjà d'exécuter du code sous son compte hors de la session de
Claude, les défauts de Claude Code, de Claude ou des serveurs MCP tiers.

**Versions suivies** : pas encore de release ; seule la branche `main` reçoit les correctifs.
