"""Langue de la CONVERSATION : français (défaut) ou anglais (02/11, demande de l'utilisateur : « une
conversation anglaise complète »). Distincte de la langue de l'INTERFACE (`i18n.py`, `ui_language`).

Réglage `conversation_language` (Réglages › Voix, `ASTREL_CONVERSATION_LANGUAGE` prioritaire), relu à
chaque appel. En anglais :
- reconnaissance vocale en anglais (`stt`), lexique de Whisper en anglais (`listener.stt_prompt`) ;
- voix anglaise retenue à part (`voix`, `runtime/voice-en.txt`), secours local Piper anglais ;
- consignes de Claude en anglais (`brain.voice_system_prompt(langue="en")`) : figées à la création de la
  session, d'où une session neuve au message suivant de l'utilisateur (sans tâche de fond en cours) ;
- phrases DITES par le code (annonces, confirmations, adieux) : traduites par le catalogue de
  l'interface (`parle`, `astrel/locales/en.json`, même clé = texte français exact) ;
- commandes vocales en anglais (adieux, modes, absence, « oui pour 30 min », dictée).

Décision (02/11) : les motifs vocaux sont PAR LANGUE (seuls ceux de la langue en cours comptent), car
Whisper est forcé dans cette langue : une phrase anglaise transcrite en français (ou l'inverse) n'arrive
de toute façon jamais telle quelle. Les mots oui/non des confirmations vivent dans `guard.py` (protégé) :
oui anglais seulement en conversation anglaise, refus des deux langues toujours (`guard.decision_from_speech`).
Le mot d'éveil ne change pas : « Astrel » est reconnu tel quel dans une transcription anglaise (essai du
02/11, voix Edge Andrew, Ryan et Rémy, Whisper base forcé en anglais).
"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from . import config

LANGS = ("fr", "en")


def conversation() -> str:
    """« fr » ou « en » : `ASTREL_CONVERSATION_LANGUAGE`, sinon le réglage, sinon le français."""
    env = os.environ.get("ASTREL_CONVERSATION_LANGUAGE", "").strip().lower()
    if env in LANGS:
        return env
    try:
        from .settings import reglages

        value = str(reglages.value("conversation_language") or config.CONVERSATION_LANGUAGE)
    except Exception:
        value = config.CONVERSATION_LANGUAGE
    return value if value in LANGS else "fr"


def anglais() -> bool:
    return conversation() == "en"


def stt() -> str:
    """Langue de Whisper : l'anglais pour une conversation anglaise, sinon « Langue parlée »."""
    if anglais():
        return "en"
    try:
        from .settings import reglages

        return str(reglages.value("stt_language") or config.STT_LANGUAGE)
    except Exception:
        return config.STT_LANGUAGE


def parle(text: str) -> str:
    """Phrase écrite par le code (en français) dans la langue de la conversation, pour la voix."""
    if not text or not anglais():
        return text
    from .i18n import tr

    return tr(text, "en")


# --------------------------------------------------------------------------- voix

def fichier_voix(lang: str | None = None) -> Path:
    return config.VOICE_EN_FILE if (lang or conversation()) == "en" else config.VOICE_FILE


def voix(lang: str | None = None) -> str:
    """Voix de la langue : variable (`ASTREL_TTS_ENGINE`, `_EN`), sinon choix retenu, sinon défaut."""
    lang = lang or conversation()
    env = os.environ.get("ASTREL_TTS_ENGINE_EN" if lang == "en" else "ASTREL_TTS_ENGINE", "").strip()
    if env:
        return env
    try:
        saved = fichier_voix(lang).read_text(encoding="utf-8").strip()
    except OSError:
        saved = ""
    return saved or (config.TTS_ENGINE_EN_DEFAULT if lang == "en" else "edge:fr-FR-RemyMultilingualNeural")


def voix_secours(lang: str | None = None) -> str:
    return config.TTS_FALLBACK_EN if (lang or conversation()) == "en" else config.TTS_FALLBACK


def retenir_voix(spec: str, lang: str | None = None) -> None:
    fichier_voix(lang).write_text(spec + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- commandes vocales en anglais

# Formules de fin (comme `config.STOP_WORDS`) : mot entier, en fin de phrase si la demande est longue.
STOP_WORDS_EN = (
    "stop astrel", "astrel stop", "bye astrel", "bye bye",
    "good night", "goodnight", "that's all", "that is all", "that's all for now", "that will be all",
    "that'll be all", "that's it for now", "stop listening", "thanks that's all", "thank you that's all",
)
# Relecture du 02/11 : ces formules-là sont aussi des mots d'une demande (« write a goodbye email », « check if
# nothing else is running », « can my laptop go to sleep », « I'm done, send it ») : elles ne congédient que si elles
# font TOUTE la phrase, à des mots de politesse près (`_STOP_SEULES_EN`).
STOP_SEULS_EN = ("goodbye", "good bye", "see you later", "see you soon", "talk to you later", "nothing else", "go to sleep", "you can go to sleep", "i'm done", "i am done",
                 "we're done", "we are done")
_POLI_EN = r"(?:ok|okay|right|alright|so|well|then|now|for now|for today|thanks|thank you|please|great|perfect|good|no|bye|astrel|jarvis)"
_STOP_SEULES_EN = re.compile(r"(?:" + _POLI_EN + r"[\s,]+)*(?:" + "|".join(re.escape(w) for w in STOP_SEULS_EN)
                             + r")(?:[\s,]+" + _POLI_EN + r")*")


def stop_seul_en(bare: str) -> bool:
    """`bare` (minuscules, sans ponctuation finale) est-il une formule de fin dite seule ?"""
    return bool(_STOP_SEULES_EN.fullmatch(bare.strip(" ,")))


STOP_ENDINGS_RE_EN = (
    r"^(?:(?:ok|okay|so|right|alright|astrel)[\s,]+)*(?:you can\s+(?:now\s+)?)?(?:stop|rest|sleep|be quiet|stop listening)"
    r"(?:\s+(?:now|for now|for today|thanks|thank you|astrel|jarvis))*$"
    r"|^(?:(?:ok|okay|right|thanks|thank you|alright)[\s,]*)*(?:stop|thanks|thank you)(?:[\s,]+astrel)?$"
)

# Modes (silencieux, dictée, conversation) dits en anglais : toute la phrase, 7 mots au plus.
MODE_FILLER_EN = re.compile(r"^(?:(?:ok|okay|so|hey|astrel|jarvis|please|switch to|go to|go back to|back to|"
                            r"turn on|enable|start|enter|use|go)\s+)*")
MODE_TAIL_EN = re.compile(r"(?:\s+(?:astrel|please|now|thanks|thank you))*$")
MODE_PHRASES_EN = (
    (re.compile(r"(?:the )?(?:silent|quiet|text|mute) mode|(?:go )?silent|be silent"), "silencieux"),
    (re.compile(r"(?:the )?dictation(?: mode)?|dictating|take dictation"), "dictee"),
    (re.compile(r"(?:the )?(?:conversation|normal|voice|spoken) mode|(?:end|stop|finish|exit) (?:the )?dictation"
                r"|you can (?:talk|speak)(?: again)?|talk to me again"), "conversation"),
)

# Absence (comme `whatsapp.away_phrase`, qui reste français) : toute la phrase, 8 mots au plus.
_AWAY_EN = re.compile(r"(?:i'?m|i am) (?:leaving|heading out|stepping out|going out|away)(?: now| for a while)?"
                      r"|i'?ll be (?:away|back later)|i have to go(?: now)?")
_BACK_EN = re.compile(r"(?:i'?m|i am) back(?: now)?|i'?m home")

# « Oui pour 30 min » en anglais (le garde-fou garde ses mots français, `guard.ALWAYS_WORDS`).
# Relecture du 02/11 : « every time » / « always » ne valent 30 min que SEULS après le oui (« yes, but ask me
# every time » demande l'inverse) ; « don't ask again » retiré (une négation, toujours lue comme un refus).
WANTS_30_EN = ("30 min", "thirty min", "half an hour", "half hour", "for 30", "for thirty", "yes to all",
               "yes to everything", "yes for everything", "without asking")
_WANTS_30_SEUL_EN = re.compile(r"(?:(?:yes|yeah|yep|okay|ok)\s+)*(?:every ?time|always)")

# Ponctuation dite en dictée (comme `core._DICTATION_WORDS`).
DICTATION_WORDS_EN = (
    (r"question mark", "?"), (r"exclamation (?:mark|point)", "!"), (r"ellipsis|dot dot dot", "…"),
    (r"semicolon", ";"), (r"colon", ":"), (r"full stop new line|period new line", ".\n"),
    (r"new paragraph", "\n\n"), (r"new line|next line", "\n"), (r"full stop|period", "."),
    (r"comma", ","), (r"open quote", " “"), (r"close quote|end quote", "”"),
)

# Phrases que Whisper invente sur du bruit ou du silence (sous-titres de vidéos de son corpus) : jetées
# quand elles forment TOUTE la transcription (jamais au milieu d'une vraie phrase).
HALLUCINATIONS = {
    "fr": frozenset({"sous titres realises par la communaute d amara org", "sous titrage st 501",
                     "sous titrage societe radio canada", "merci d avoir regarde cette video",
                     "sous titres realises para la communaute d amara org"}),
    "en": frozenset({"thanks for watching", "thank you for watching", "thank you so much for watching",
                     "please subscribe", "like and subscribe", "subtitles by the amara org community",
                     "thanks for watching and see you next time"}),
}


def _plie(text: str) -> str:
    t = unicodedata.normalize("NFKD", str(text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def hallucination(text: str, lang: str | None = None) -> bool:
    """La transcription entière est-elle une hallucination connue de Whisper dans cette langue ?"""
    return _plie(text) in HALLUCINATIONS.get(lang or stt(), frozenset())


# Réponse à une confirmation (02/11, contre-relecture) : une QUESTION n'est jamais une réponse (« okay, what time is
# it? », « c'est quoi ce fichier ? ») : point d'interrogation, mot interrogatif, ou auxiliaire en tête (« is it… »).
_INTERROGATIFS = frozenset({"what", "who", "whom", "whose", "why", "when", "where", "how", "which", "quoi", "quel",
                            "quelle", "quels", "quelles", "pourquoi", "comment", "combien", "ou", "quand",
                            "lequel", "laquelle", "est-ce", "qu'est-ce"})
_AUX_EN_TETE = frozenset({"is", "are", "was", "were", "can", "could", "would", "should", "will", "shall", "do",
                          "does", "did", "have", "has", "may", "might", "must"})
_AMORCES = frozenset({"ok", "okay", "yes", "yeah", "so", "and", "but", "hmm", "oui", "ouais", "alors", "euh", "et",
                      "mais", "astrel"})


def question(text: str) -> bool:
    """La phrase est-elle une question (et donc pas une réponse oui/non) ?"""
    raw = str(text or "")
    if "?" in raw:
        return True
    t = unicodedata.normalize("NFKD", raw.lower().replace("’", "'"))
    t = "".join(c for c in t if not unicodedata.combining(c))
    words = re.sub(r"[^\w' -]", " ", t).split()
    if any(w in _INTERROGATIFS for w in words):
        return True
    head = [w for w in words if w not in _AMORCES]
    # « do it » n'est pas une question : l'auxiliaire en tête doit être suivi d'un sujet
    if len(head) < 2 or head[0] not in _AUX_EN_TETE:
        return False
    sujets = {"i", "you", "we", "he", "she", "they"}  # « do it », « do that » : des ordres
    if head[0] not in ("do", "does", "did"):
        sujets |= {"it", "that", "this", "there"}
    return head[1] in sujets


def away_phrase_en(text: str) -> str:
    """« I'm heading out » → "absent", « I'm back » → "present", vide sinon (toute la phrase)."""
    t = re.sub(r"[^\w' -]", " ", str(text or "").lower().replace("’", "'"))
    t = re.sub(r"\s+", " ", t).strip()
    if not t or len(t.split()) > 8:
        return ""
    t = re.sub(r"^(?:(?:ok|okay|so|hey|astrel)\s+)+", "", t)
    t = re.sub(r"(?:\s+(?:astrel|bye|see you))+$", "", t).strip()
    if _AWAY_EN.fullmatch(t):
        return "absent"
    if _BACK_EN.fullmatch(t):
        return "present"
    return ""


def wants_30_en(text: str) -> bool:
    low = " " + str(text or "").lower().replace("’", "'") + " "
    return any(w in low for w in WANTS_30_EN) or bool(_WANTS_30_SEUL_EN.fullmatch(_plie(text)))


# --------------------------------------------------------------------------- notes du tour pour Claude

NOTES_EN = {
    "[Mode silencieux : l'utilisateur lit ta réponse à l'écran, elle n'est pas dite.]":
        "[Silent mode: the user reads your reply on screen, it is not spoken.]",
    "[l'utilisateur te parle pendant que tu travailles.]": "[the user is talking to you while you work.]",
    "[Réflexe programmé, l'utilisateur n'a rien demandé à l'instant.]":
        "[Scheduled reflex, the user did not just ask anything.]",
    "[Capture de l'écran de l'utilisateur jointe par le capteur WATCH.]":
        "[Screenshot of the user's screen attached by the WATCH sensor.]",
}
SWITCH_NOTE_EN = ("[the user switched the conversation to English: from now on, answer in English only. A fresh "
                  "session with the English instructions starts once the background tasks are finished.]")


def note(text: str) -> str:
    """Note d'un tour (consigne dans le MESSAGE) dans la langue de la conversation."""
    return NOTES_EN.get(text, text) if anglais() else text
