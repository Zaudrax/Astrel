"""Raccourcis clavier globaux de Windows et macOS (03/11/2026, portage). Linux : `hotkeys.sh` (GNOME).

Les MÊMES qu'avec GNOME :
    Ctrl+Alt+J  parler maintenant          Ctrl+Alt+M  couper / réactiver le micro
    Ctrl+Alt+X  interrompre Astrel         Ctrl+Alt+P  voix en pause / reprise
    Ctrl+Alt+H  ouvrir le HUD

Écoutés par le cœur lui-même (aucun process de plus) avec pynput, qui passe par un crochet clavier
sous Windows et par Quartz sous macOS (autorisation « Surveillance de l'entrée » à donner une fois,
dans Réglages Système › Confidentialité et sécurité). Choix retenu dans `runtime/raccourcis.json` ;
chaque raccourci envoie la commande par le canal local, comme `./run.sh --send …` sous Linux : le
cœur la reçoit de lui-même, jamais comme venant de Claude.
Écrit d'après la documentation de pynput ; jamais lancé sur un vrai Windows ni un vrai Mac."""
from __future__ import annotations

import json
import logging
import threading

from .. import config

log = logging.getLogger("astrel.raccourcis")

FICHIER = config.RUNTIME / "raccourcis.json"
TOUCHES = {  # format de pynput.keyboard.GlobalHotKeys
    "<ctrl>+<alt>+j": "wake",
    "<ctrl>+<alt>+m": "toggle",
    "<ctrl>+<alt>+x": "stop",
    "<ctrl>+<alt>+p": "pause toggle",
    "<ctrl>+<alt>+h": "hud",
}
MESSAGE_ON = "Raccourcis posés : Ctrl+Alt+J parler, Ctrl+Alt+M micro, Ctrl+Alt+X interrompre."
MESSAGE_OFF = "Raccourcis retirés."

_ecouteur = None
_verrou = threading.Lock()


def actif() -> bool:
    try:
        return bool(json.loads(FICHIER.read_text(encoding="utf-8")).get("actif"))
    except (OSError, ValueError, AttributeError):
        return False


def _agir(commande: str) -> None:
    def run() -> None:
        try:
            if commande == "hud":
                from . import ouvrir_hud  # même lanceur que le bouton HUD de la bulle
                ouvrir_hud()
            else:
                from .. import state
                state.send_command(commande)
        except Exception:
            log.exception("raccourci %s", commande)

    threading.Thread(target=run, name="raccourci", daemon=True).start()


def canonique(key, key_code, defaut, systeme: str | None = None):
    """07/11 (essai Windows, lu dans pynput 1.8.1) : sous Windows, une lettre tapée avec Ctrl+Alt arrive avec le
    caractère que donne le clavier DANS cet état (`ToUnicodeEx` : aucun pour J, M, X, P, H en QWERTY comme en
    AZERTY, où Ctrl+Alt vaut AltGr) et son code de balayage ; `GlobalHotKeys` la comparait alors à la touche
    « j » de `HotKey.parse` par son code virtuel, absent de celle-ci : aucun raccourci ne partait jamais. Le
    code virtuel d'une lettre ou d'un chiffre (VK_A… = « A »…, le même quelle que soit la disposition) suffit
    à la reconnaître. Ailleurs, et pour le reste, la normalisation de pynput (`defaut`)."""
    from . import SYSTEM

    vk = getattr(key, "vk", None)
    if (systeme or SYSTEM) == "windows" and isinstance(key, key_code) and isinstance(vk, int) \
            and (0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39):
        return key_code.from_char(chr(vk).lower())
    return defaut(key)


def _ecouteur_pynput(keyboard):
    """`GlobalHotKeys` de pynput, avec la normalisation de `canonique`."""
    class Raccourcis(keyboard.GlobalHotKeys):
        def canonical(self, key):
            return canonique(key, keyboard.KeyCode, super().canonical)

    return Raccourcis({t: (lambda c=c: _agir(c)) for t, c in TOUCHES.items()})


def demarrer() -> bool:
    """Écoute les raccourcis (rien si déjà fait). Faux si pynput manque ou refuse."""
    global _ecouteur
    with _verrou:
        if _ecouteur is not None:
            return True
        try:
            from pynput import keyboard
            _ecouteur = _ecouteur_pynput(keyboard)
            _ecouteur.daemon = True
            _ecouteur.start()
        except Exception as exc:
            log.warning("raccourcis clavier indisponibles : %s", exc)
            _ecouteur = None
            return False
    log.info("raccourcis clavier globaux écoutés (Ctrl+Alt+J, M, X, P, H)")
    return True


def arreter() -> None:
    global _ecouteur
    with _verrou:
        if _ecouteur is not None:
            try:
                _ecouteur.stop()
            finally:
                _ecouteur = None


def regler(on: bool) -> str:
    """Retenu dans `runtime/raccourcis.json` ; dans le cœur, l'écoute suit tout de suite."""
    config.RUNTIME.mkdir(parents=True, exist_ok=True)
    FICHIER.write_text(json.dumps({"actif": bool(on)}), encoding="utf-8")
    if on:
        demarrer()
    else:
        arreter()
    return MESSAGE_ON if on else MESSAGE_OFF


def au_demarrage() -> None:
    """Appelé par le cœur au démarrage (Windows et macOS)."""
    if actif():
        demarrer()
