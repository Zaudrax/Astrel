# Contributing to Astrel

[Version française plus bas](#contribuer-à-astrel)

Thanks for looking. Bug reports, tests on a machine nobody has tried yet (Windows, macOS), fixes and
translations are all welcome.

## Before you start

- **Security problems go through private reporting**, not issues: see [SECURITY.md](SECURITY.md).
- **Changes to the guardrail** (`astrel/guard.py`, `astrel/totalgate.py`, `astrel/state.py`,
  `hooks/`, `astrel/confirm.py`, `astrel/preuve.py`, `astrel/instantanes.py`) start with an issue that
  describes the case. A pull request that loosens a check without a reason and a test will not be
  merged. When in doubt, the guardrail refuses: keep it that way.
- For anything bigger than a fix, open an issue first so we can agree on the shape.

## Setting up

Follow the installation for your OS in the [README](README.en.md#installation). To work without a
microphone or a Claude subscription, the HUD has a demo mode:

```bash
.venv/bin/python tools/hud_demo.py & ./hud.sh
```

## Checks before a pull request

```bash
.venv/bin/python -m pytest -q                  # the whole suite (Linux: a few minutes)
.venv/bin/python tools/hud_check_rendu.py      # the HUD in headless Chrome, nothing on screen
.venv/bin/python tools/i18n_inventaire.py      # must report 0 screen text without English
cd hud && npm run build                        # after any change under hud/src
cd imagegen && node --test --test-concurrency=1   # the image/video worker
```

CI runs the Linux job on every push (it must be green) and measures Windows and macOS.

## Conventions

- **The code, comments and screen texts are written in French.** Every new screen text gets an
  English translation in `astrel/locales/en.json` (Python) or `hud/src/langues.js` (HUD).
  Spoken phrases follow the same rule, and both system prompts keep the same rules
  (`tests/test_langue.py`).
- **Anything OS-specific goes through `astrel/platform/`**: it is the only place that picks
  the system. Use its helpers (`platform/systeme.py`: no-follow open, process tree, owner-only
  files…) rather than `os.getuid`, `os.O_NOFOLLOW` or `os.killpg` directly.
- **Never `sounddevice` on Linux** (audio goes through `parecord`/`paplay`), never `innerHTML` in
  the HUD (`hud/src/markdown.js`), and the HUD only listens on 127.0.0.1.
- **Third-party MCP servers and Node packages are pinned** to a fixed version.
- A fix comes with the test that failed before it. Keep the change small and in the style of the
  surrounding code.

## Licence

By contributing, you agree that your contribution is released under the [MIT License](LICENSE).

---

# Contribuer à Astrel

Merci de votre intérêt. Les signalements de bug, les essais sur une machine encore jamais essayée
(Windows, macOS), les correctifs et les traductions sont bienvenus.

- **Une faille de sécurité** se signale en privé : voir [SECURITY.md](SECURITY.md).
- **Toucher au garde-fou** (`guard.py`, `totalgate.py`, `state.py`, `hooks/`, `confirm.py`,
  `preuve.py`, `instantanes.py`) commence par une issue qui décrit le cas ; on ne desserre jamais un
  contrôle sans raison ni test. Dans le doute, le garde-fou refuse.
- **Avant une pull request** : la suite `pytest`, `tools/hud_check_rendu.py`,
  `tools/i18n_inventaire.py` à 0, `npm run build` dans `hud/` après une modification de `hud/src`, et
  les tests du worker `imagegen`.
- **Conventions** : code, commentaires et textes d'écran en français, chaque nouveau texte d'écran
  traduit en anglais (`astrel/locales/en.json` ou `hud/src/langues.js`) ; tout ce qui dépend du
  système passe par `astrel/platform/` ; dépendances tierces à version fixée ; un correctif arrive
  avec le test qui échouait avant lui.
- **Licence** : une contribution est publiée sous [licence MIT](LICENSE).
