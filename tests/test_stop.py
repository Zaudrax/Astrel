"""Formules de fin de conversation : congé reconnu, demande laissée passer."""

import pytest

from astrel.core import Astrel


@pytest.mark.parametrize("text", [
    "C'est gentil, tu peux t'arrêter maintenant.",
    "Tu peux arrêter là.",
    "Stop.",
    "Merci.",
    "Arrête d'écouter.",
    "OK merci, tu peux te taire",
    "Au revoir Astrel",
    "Bon, c'est super, merci pour tout ce que tu as fait ce soir, bonne nuit Astrel",
])
def test_conge(text):
    assert Astrel.is_stop(None, text)


@pytest.mark.parametrize("text", [
    "Tu peux arrêter la musique ?",
    "Dis-moi comment arrêter",
    "Merci de regarder mes mails",
    "Quand je te demande d'arrêter, tu continues d'écouter",
    "Oui, je veux que tu exécutes tous ces travaux dont tu m'as parlé. Ensuite, "
    "je t'entends bien. La bulle, je n'ai pas eu le temps de vérifier, mais on "
    "verra ça plus tard. Et quand je parlais d'agents IA, je parlais effectivement "
    "d'un agent Intelligence Artificielle.",
    "J'ai fini le rapport, tu peux l'envoyer à Paul et le ranger dans mes documents ?",
])
def test_demande(text):
    assert not Astrel.is_stop(None, text)
