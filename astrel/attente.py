"""Messages de l'utilisateur non perdus pendant un redémarrage (01/11, fiabilité ;
HERMES-cron-voix-telegram.md F4 et F5).

Un message dit ou tapé part tout de suite chez le CLI (`Astrel.handle`) ; s'il arrive pendant
une relève, il est gardé en mémoire (`relief.hold`). Si Astrel s'arrête (relance du service,
plantage) avant la fin du tour qui le traite, il était perdu sans le dire. Ici :

- `noter` : chaque message de l'utilisateur (voix, clavier) est écrit dans `runtime/messages-en-attente.json`
  AVANT d'être envoyé (0600, écriture atomique, 20 au plus) ;
- `agi` : un outil d'Astrel a démarré depuis : des actions ont peut-être déjà tourné ;
- `traites` : fin d'un tour (réponse ou erreur sans rejeu programmé) : ce qui était noté pendant
  CE démarrage est pris en charge, effacé ;
- au démarrage suivant, `a_proposer` rend ce qui reste d'un démarrage précédent (24 h au plus).

PROPOSÉ, JAMAIS REJOUÉ (le plus sûr) : le cœur l'affiche dans le fil comme « non envoyé » avec
le bouton « Renvoyer » qui existe déjà, et le dit en une notice. Rejouer seul serait dangereux :
le message a pu être lu par le CLI et des actions ont pu tourner (double exécution), il parle
peut-être d'un état qui a changé, et en mode autonome une action SENSIBLE partirait sans que
l'utilisateur l'ait redemandée. Un renvoi est un nouveau message de l'utilisateur : garde-fou et
confirmations jouent comme d'habitude. Une proposition reste jusqu'au renvoi, jusqu'à la fin
d'un tour de l'utilisateur du nouveau démarrage (il l'a vue dans le fil, ou entendue), ou 24 h.

Chaque entrée est signée (HMAC, clé dérivée de celle des cookies du HUD, secret du garde-fou) :
une entrée écrite par un autre que le cœur (Claude par Bash) n'est jamais affichée comme un
message de l'utilisateur. WhatsApp n'est pas noté : le HUD ne peut pas le renvoyer.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import threading
import time

from . import config

log = logging.getLogger("astrel.attente")

SOURCES = ("voix", "clavier")
MAX_ITEMS = 20
TEXTE_MAX = 20_000   # au-delà, pas noté : un message coupé ne doit pas être renvoyé
BOOT = secrets.token_hex(8)  # ce démarrage du cœur
_CHAMPS = ("mid", "text", "source", "channel", "at", "boot", "acted", "pieces")


def _cle_hud() -> bytes:
    from .server import _session_key

    return _session_key()


_cle_cache: bytes | None = None


def _cle() -> bytes:
    global _cle_cache
    if _cle_cache is None:
        _cle_cache = hmac.new(_cle_hud(), b"astrel-messages-en-attente", hashlib.sha256).digest()
    return _cle_cache


def _signe(item: dict) -> str:
    corps = json.dumps({k: item.get(k) for k in _CHAMPS}, sort_keys=True, ensure_ascii=False).encode()
    return hmac.new(_cle(), corps, hashlib.sha256).hexdigest()


class Attente:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[dict] | None = None  # copie du fichier (lu au premier besoin)

    # --- fichier ---

    def _lire(self) -> list[dict]:
        try:
            raw = json.loads(config.ATTENTE_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            log.warning("messages en attente illisibles, ignorés : %s", exc)
            return []
        items = raw.get("items") if isinstance(raw, dict) else None
        out = []
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict) or not isinstance(it.get("text"), str) or not isinstance(it.get("mid"), str):
                continue
            try:
                ok = hmac.compare_digest(str(it.get("sig") or ""), _signe(it))
            except Exception:  # noqa: BLE001 — clé illisible : rien n'est cru
                ok = False
            if not ok:
                log.warning("message en attente non signé par le cœur : ignoré")
                continue
            out.append({k: it.get(k) for k in (*_CHAMPS, "propose")})
        return out

    def _ecrire(self, items: list[dict]) -> None:
        path = config.ATTENTE_FILE
        if not items:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.warning("messages en attente non effacés : %s", exc)
            return
        try:
            data = {"v": 1, "items": [{**it, "sig": _signe(it)} for it in items[-MAX_ITEMS:]]}
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".attente-", dir=str(path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False)
                os.chmod(tmp, 0o600)
                os.replace(tmp, path)  # jamais à moitié écrit si le cœur meurt pendant l'écriture
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception as exc:  # noqa: BLE001 — clé du HUD illisible, disque plein : jamais d'exception à l'appelant
            log.warning("messages en attente non écrits : %s", exc)

    def _charger(self) -> list[dict]:
        if self._items is None:
            self._items = self._lire()
        return self._items

    # --- cycle ---

    def noter(self, mid: str, text: str, source: str, channel: str = "", pieces: int = 0) -> bool:
        if source not in SOURCES or not mid or not text.strip() or len(text) > TEXTE_MAX:
            return False
        with self._lock:
            items = [it for it in self._charger() if it.get("mid") != mid]
            items.append({"mid": mid, "text": text, "source": source, "channel": channel or "",
                          "at": round(time.time(), 3), "boot": BOOT, "acted": False, "pieces": int(pieces or 0)})
            self._items = items[-MAX_ITEMS:]
            self._ecrire(self._items)
        return True

    def agi(self) -> None:
        """Un outil a démarré : les messages de ce démarrage ont peut-être déjà fait agir."""
        with self._lock:
            items = self._charger()
            todo = [it for it in items if it.get("boot") == BOOT and not it.get("acted")]
            if not todo:
                return
            for it in todo:
                it["acted"] = True
            self._ecrire(items)

    def traites(self, utilisateur: bool = False) -> None:
        """Fin d'un tour : messages de ce démarrage pris en charge. Tour de l'utilisateur : les
        propositions d'un démarrage précédent ont été vues (fil du HUD, voix), oubliées aussi."""
        with self._lock:
            items = self._charger()
            keep = [it for it in items if it.get("boot") != BOOT and not (utilisateur and it.get("propose"))]
            if len(keep) != len(items):
                self._items = keep
                self._ecrire(keep)

    def oublier(self, mid: str) -> None:
        """Renvoyé depuis le HUD (`resend_of`) : il repart comme un nouveau message."""
        with self._lock:
            items = self._charger()
            keep = [it for it in items if it.get("mid") != mid]
            if len(keep) != len(items):
                self._items = keep
                self._ecrire(keep)

    def a_proposer(self, now: float | None = None) -> list[dict]:
        """Au démarrage : messages d'un démarrage précédent, de moins de `ATTENTE_MAX_AGE_S`,
        marqués « proposés » (gardés jusqu'au renvoi ou au prochain tour de l'utilisateur)."""
        now = time.time() if now is None else now
        with self._lock:
            items = self._charger()
            keep = []
            for it in items:
                if it.get("boot") == BOOT:
                    keep.append(it)
                    continue
                at = it.get("at")
                if not isinstance(at, (int, float)) or now - float(at) > config.ATTENTE_MAX_AGE_S:
                    continue
                it["propose"] = True
                keep.append(it)
            if keep != items or any(it.get("propose") for it in keep):
                self._items = keep
                self._ecrire(keep)
            return [dict(it) for it in keep if it.get("boot") != BOOT]

    def oublier_tout(self) -> None:
        with self._lock:
            self._items = []
            self._ecrire([])


def notice(items: list[dict]) -> str:
    """Texte du HUD pour les messages retrouvés au démarrage."""
    n = len(items)
    tete = ("Un message d'avant l'arrêt d'Astrel n'a pas été traité : il est dans le fil, « Renvoyer » "
            "pour l'envoyer." if n == 1 else
            f"{n} messages d'avant l'arrêt d'Astrel n'ont pas été traités : ils sont dans le fil, "
            "« Renvoyer » pour les envoyer.")
    parts = [tete]  # une phrase par ligne : chacune a sa traduction (catalogue)
    if any(it.get("acted") for it in items):
        parts.append("Des actions avaient peut-être déjà commencé : vérifie avant de renvoyer.")
    if any(it.get("pieces") for it in items):
        parts.append("Les pièces jointes ne sont pas gardées.")
    return "\n".join(parts)


def phrase(items: list[dict]) -> str:
    """Dit au démarrage (un message dicté d'avant l'arrêt), sans en répéter le contenu."""
    return ("Un message d'avant l'arrêt n'a pas été traité : il est dans le fil du HUD, avec Renvoyer."
            if len(items) == 1 else
            "Des messages d'avant l'arrêt n'ont pas été traités : ils sont dans le fil du HUD, avec Renvoyer.")


attente = Attente()
