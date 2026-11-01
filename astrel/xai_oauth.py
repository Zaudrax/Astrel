"""Grok par abonnement : absent de ce dépôt.

Interface vide, gardée parce que le reste d'Astrel l'appelle (fournisseurs, cerveau, passerelle) :
rien n'est jamais connecté, le choix n'apparaît pas dans Réglages › Fournisseur. Grok reste
disponible par une clé d'API (fournisseur `xai`, Réglages › Fournisseur)."""
from __future__ import annotations

from . import config

ETAT_FILE = config.RUNTIME / "xai-oauth-etat.json"
LOCK_FILE = config.RUNTIME / "xai-oauth.lock"
ISSUER = DEVICE_URL = TOKEN_URL = REVOKE_URL = API_URL = ""
INDISPONIBLE = "aucune connexion xAI sur cette machine"


def connecte() -> bool:
    return False


def accord() -> bool:
    return False


def etat() -> dict:
    return {"etat": "absent", "connecte": False, "accord": False}


def raison_indisponible() -> str:
    return INDISPONIBLE


def _refus() -> dict:
    return {"ok": False, "error": INDISPONIBLE}


connecter = annuler = deconnecter = _refus


def controler(effacer: bool = False) -> dict:
    return _refus()


def rafraichir(empreinte: str | None = None) -> str:
    return ""


def ouvrir(url: str) -> None:
    return None


class Gardien:
    def actif(self) -> bool:
        return False

    def demarrer(self) -> None:
        return None

    def arreter(self) -> None:
        return None

    def forcer(self, empreinte: str = "") -> None:
        return None

    def refus_repete(self, empreinte: str = "") -> bool:
        return False


gardien = Gardien()


def signaler_refus(code: int, empreinte: str | None = None) -> None:
    return None


def lignes() -> list[dict]:
    return []


def lien() -> dict | None:
    return None


def actions() -> list[dict]:
    return []
