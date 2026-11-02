"""Préparer du texte pour l'oral, et le découper en phrases au fil de l'eau.

Claude écrit sa réponse mot à mot. Attendre la réponse entière avant de parler
ajoutait plusieurs secondes de silence ; on découpe donc les phrases à mesure
qu'elles se terminent, et la première part à la synthèse pendant que la suite
s'écrit encore.
"""
from __future__ import annotations

import re

_CODE_BLOCK = re.compile(r"```.*?```", re.S)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_MD_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+", re.M)
_MD_EMPHASIS = re.compile(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1")
_MD_TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", re.M)
_URL = re.compile(r"https?://\S+")
_PATH = re.compile(r"(?<![\w.])(?:~|\.{1,2})?(/[\w.\-]+){2,}/?")
_SPACES = re.compile(r"[ \t]+")
# Point collé à la phrase suivante : minuscule (ou chiffre) avant, majuscule après.
# « README.md », « 3.5 » ou « M.Dupont » ne correspondent pas.
_GLUED_SENTENCE = re.compile(r"(?<=[a-zà-ÿ0-9])([.!?…]+)(?=[A-ZÀ-Ý][a-zà-ÿ])")
_BLANK_LINES = re.compile(r"\n\s*\n+")


# 02/11 (conversation anglaise, `astrel/langue.py`) : ce que la voix dit à la place du code et des liens.
_SPOKEN = {"fr": (" (voir le code à l'écran) ", "le lien"), "en": (" (see the code on screen) ", "the link")}


def _lang() -> str:
    try:
        from .. import langue

        return langue.conversation()
    except Exception:
        return "fr"


def clean_for_speech(text: str, max_chars: int = 1200, lang: str | None = None) -> str:
    """Ce qui est visuel n'a pas de sens à l'oral : on le retire ou le résume."""
    code, link = _SPOKEN.get(lang or _lang(), _SPOKEN["fr"])
    text = _CODE_BLOCK.sub(code, text)
    text = _MD_TABLE_RULE.sub("", text)
    text = text.replace("|", ", ")
    text = _MD_LINK.sub(r"\1", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _MD_HEADING.sub("", text)
    text = _MD_BULLET.sub("", text)
    text = _MD_EMPHASIS.sub(r"\2", text)
    text = _URL.sub(link, text)
    # Un chemin complet est illisible à l'oral : on garde le dernier élément.
    text = _PATH.sub(lambda m: m.group(0).rstrip("/").split("/")[-1], text)
    # « Astrel.Et » : sans espace, la voix enchaîne sans marquer la fin de phrase.
    text = _GLUED_SENTENCE.sub(r"\1 ", text)
    text = _BLANK_LINES.sub(". ", text)
    text = text.replace("\n", " ")
    text = _SPACES.sub(" ", text).strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([.!?])\s*\.", r"\1", text)
    if len(text) > max_chars:
        cut = text[:max_chars]
        dot = cut.rfind(". ")
        text = (cut[: dot + 1] if dot > max_chars // 2 else cut) + " …"
    return text


# Fin de phrase : ponctuation forte suivie d'un blanc, ou saut de ligne.
# Les décimales (« 3.5 ») et les abréviations collées ne coupent pas, faute de blanc.
_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"»)\]]*\s+|\n+")
_ABBREVIATIONS = ("M.", "Mme.", "Dr.", "p.", "ex.", "cf.", "etc.", "env.", "vs.", "St.",
                  # 02/11 (conversation anglaise) : abréviations anglaises courantes, sans effet en français
                  "Mr.", "Mrs.", "Ms.", "Jr.", "Sr.", "e.g.", "i.e.", "approx.", "No.", "Prof.", "U.S.")


class SentenceSplitter:
    """Accumule des fragments et rend les phrases complètes dès qu'elles le sont.

    Les blocs de code ne sont jamais coupés : tant qu'un ``` est ouvert, on
    attend sa fermeture (sinon la moitié d'un bloc serait lue à voix haute).
    """

    MIN_CHARS = 12  # une « phrase » plus courte est rattachée à la suivante

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, fragment: str) -> list[str]:
        self._buf += fragment
        out: list[str] = []
        while True:
            cut = self._find_cut(self._buf)
            if cut is None:
                break
            sentence, self._buf = self._buf[:cut].strip(), self._buf[cut:]
            if sentence:
                out.append(sentence)
        return out

    def flush(self) -> list[str]:
        rest, self._buf = self._buf.strip(), ""
        return [rest] if rest else []

    @staticmethod
    def _fences(buf: str) -> tuple[list[tuple[int, int]], int | None]:
        """Blocs de code fermés (début, fin) et début d'un bloc encore ouvert."""
        marks = [i for i in range(len(buf)) if buf.startswith("```", i)]
        closed = [(marks[i], marks[i + 1] + 3) for i in range(0, len(marks) - 1, 2)]
        open_start = marks[-1] if len(marks) % 2 == 1 else None
        return closed, open_start

    def _find_cut(self, buf: str) -> int | None:
        closed, open_start = self._fences(buf)
        limit = open_start if open_start is not None else len(buf)
        start = 0
        while True:
            m = _SENTENCE_END.search(buf, start, limit)
            if m is None:
                # Rien avant le bloc ouvert : on attend sa fermeture. Mais la
                # phrase qui l'annonce (« Voici le code : ») part tout de suite.
                if open_start is not None and buf[:open_start].strip() and start == 0:
                    head = buf[:open_start].rstrip()
                    if len(head) >= self.MIN_CHARS or head.endswith(":"):
                        return len(head) + (len(buf[:open_start]) - len(head))
                return None
            inside = next(((a, b) for a, b in closed if a <= m.start() < b), None)
            if inside is not None:
                start = inside[1]  # une fin de phrase DANS un bloc de code ne compte pas
                continue
            head = buf[: m.start()].rstrip()
            last_word = head.rsplit(" ", 1)[-1] if head else ""
            if last_word in _ABBREVIATIONS or len(head.strip()) < self.MIN_CHARS:
                start = m.end()
                continue
            return m.end()
