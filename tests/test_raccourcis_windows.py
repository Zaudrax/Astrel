"""Raccourcis Ctrl+Alt+… sous Windows (07/11, avant le premier essai sur un vrai PC).

Lu dans pynput 1.8.1 (`_util/win32.KeyTranslator`, `keyboard._base.KeyCode.__eq__`, `HotKey.parse`) : sous
Windows, une lettre tapée avec Ctrl+Alt arrive sans caractère (aucun pour Ctrl+Alt+J en QWERTY ni en AZERTY)
mais avec son code virtuel et son code de balayage ; `GlobalHotKeys` la comparait à la touche « j » de
`HotKey.parse`, qui n'a qu'un caractère : jamais égales, aucun raccourci ne partait.

Sous Linux, pynput n'est pas installé : `FauxKeyCode` reprend la comparaison de pynput (caractère si les
deux en ont un, sinon code virtuel ET extensions de la plateforme). Sous Windows (CI), les vraies classes."""
import sys

import pytest

from astrel.platform import raccourcis


class FauxKeyCode:
    """Comme `pynput.keyboard._win32.KeyCode` : égalité et hachage de pynput 1.8.1."""
    _PLATFORM_EXTENSIONS = ("_flags", "_scan")

    def __init__(self, vk=None, char=None, is_dead=False, **kwargs):
        self.vk, self.char, self.is_dead = vk, char, is_dead
        for f in self._PLATFORM_EXTENSIONS:
            setattr(self, f, kwargs.get(f))

    @classmethod
    def from_char(cls, char):
        return cls(char=char)

    def __repr__(self):
        return repr(self.char) if self.char is not None else f"<{self.vk}>"

    def __eq__(self, other):
        if not isinstance(other, self.__class__):
            return False
        if self.char is not None and other.char is not None:
            return self.char == other.char and self.is_dead == other.is_dead
        return self.vk == other.vk and all(getattr(self, f) == getattr(other, f) for f in self._PLATFORM_EXTENSIONS)

    def __hash__(self):
        return hash(repr(self))


def canonique_pynput(key):
    """`Listener.canonical` de pynput 1.8.1, pour une touche de lettre."""
    return FauxKeyCode.from_char(key.char.lower()) if key.char is not None else key


def test_ctrl_alt_lettre_reconnue_sous_windows():
    attendu = FauxKeyCode.from_char("j")  # ce que donne HotKey.parse("<ctrl>+<alt>+j")
    tapee = FauxKeyCode(vk=0x4A, char=None, _scan=36)  # J avec Ctrl+Alt enfoncés
    assert canonique_pynput(tapee) != attendu  # le défaut de pynput : raccourci jamais reconnu
    assert raccourcis.canonique(tapee, FauxKeyCode, canonique_pynput, systeme="windows") == attendu
    # Une disposition qui donnerait un caractère avec AltGr : c'est toujours la touche J
    assert raccourcis.canonique(FauxKeyCode(vk=0x4A, char="ĵ"), FauxKeyCode, canonique_pynput,
                                systeme="windows") == attendu


def test_reste_inchange():
    defaut = []
    autre = FauxKeyCode(vk=0x70)  # F1 : pas une lettre
    raccourcis.canonique(autre, FauxKeyCode, defaut.append, systeme="windows")
    lettre = FauxKeyCode(vk=0x26, char=None)  # macOS : 0x26 est kVK_ANSI_J, pas « & »
    raccourcis.canonique(lettre, FauxKeyCode, defaut.append, systeme="macos")
    modif = object()  # Key.ctrl_l… : normalisé par pynput
    raccourcis.canonique(modif, FauxKeyCode, defaut.append, systeme="windows")
    assert defaut == [autre, lettre, modif]


def test_tous_les_raccourcis_avec_les_vraies_classes_de_pynput(monkeypatch):
    """Sous Windows seulement (pynput n'est installé que hors Linux, et son import veut un bureau)."""
    if sys.platform != "win32":
        pytest.skip("vraies classes de pynput : Windows seulement")
    from pynput import keyboard

    vus = []
    monkeypatch.setattr(raccourcis, "_agir", vus.append)
    ecouteur = raccourcis._ecouteur_pynput(keyboard)
    for touche, commande in raccourcis.TOUCHES.items():
        lettre = touche[-1]
        ecouteur._on_press(keyboard.Key.ctrl_l, False)
        ecouteur._on_press(keyboard.Key.alt_l, False)
        ecouteur._on_press(keyboard.KeyCode(vk=ord(lettre.upper()), char=None, _scan=36), False)
        for k in (keyboard.KeyCode(vk=ord(lettre.upper()), char=None, _scan=36), keyboard.Key.alt_l,
                  keyboard.Key.ctrl_l):
            ecouteur._on_release(k, False)
        assert vus[-1:] == [commande], touche
