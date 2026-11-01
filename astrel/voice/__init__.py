"""Voix d'Astrel : préparation du texte, moteurs de synthèse, orateur."""
from .engines import VoiceInfo, available_voices, build_engine
from .speaker import Speaker
from .text import SentenceSplitter, clean_for_speech

__all__ = ["Speaker", "SentenceSplitter", "VoiceInfo", "available_voices", "build_engine", "clean_for_speech"]
