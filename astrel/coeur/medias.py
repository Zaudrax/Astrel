"""Médias riches du fil, images et vidéos IA : outils `show_media`, `generate_image`,
`generate_video` et boutons des cartes (P26, P14, P40, P41).

Mixin de `Astrel` (core.py), découpé du cœur le 29/10/2026 : méthodes déplacées telles quelles.
"""
from __future__ import annotations

import threading
import uuid

from .. import attachments
from .. import config
from .. import images_ia
from .. import medias
from .. import videos_ia
from ..events import bus
from ..core import MEDIA_HOLD_MAX_S, _MEDIA_LOCK, _MEDIA_TITLE_CONTROLS, log  # aides du cœur (le paquet charge core d'abord)


class MediasCoeur:
    """Médias riches du fil, images et vidéos IA : outils `show_media`, `generate_image`,"""

    def _show_media(self, cmd: dict) -> dict:
        """Outil MCP `show_media` : carte de médias dans le fil du HUD (évènement `media`),
        jamais dite. Libre pour Claude : de l'affichage seul, les fichiers sont vérifiés
        (sources permises, secrets refusés, type lu dans les octets) par `medias.publish`."""
        ch, error = self._channel_for_tool(str(cmd.get("channel") or ""))
        if error:
            return {"ok": False, "error": error}
        shown, refused = medias.publish_many(cmd.get("items"))
        summary = medias.summary(shown, refused)
        if not shown:
            return {"ok": False, "error": "aucun fichier affichable", **summary}
        title = " ".join(_MEDIA_TITLE_CONTROLS.sub(" ", str(cmd.get("title") or "")).split())[:200]
        mid = uuid.uuid4().hex[:12]
        bus.publish("media", mid=mid, channel=ch, title=title, items=shown)
        return {"ok": True, "mid": mid, "canal": ch, **summary,
                "message": "Affiché dans le fil du HUD, rien n'est dit. Ne le décris pas à voix haute "
                           "et n'en dis jamais le chemin."}

    def _media_command(self, name: str, cmd: dict) -> dict:
        """Boutons des cartes de médias, RÉSERVÉS au HUD (refusés si `_from_claude`) :
        `media_open {media_id}` ouvre le fichier dans l'application du système ;
        `media_playing {playing}` retient le micro pendant qu'un son ou une vidéo joue
        (question 4 du plan : Whisper prendrait la vidéo pour un ordre). Retenue PAR
        connexion (`_conn`, posé par le serveur) : relâchée par la page, à sa déconnexion,
        ou au bout de `MEDIA_HOLD_MAX_S`."""
        if cmd.get("_from_claude") is True:
            log.warning("%s refusé : vient de Claude", name)
            return {"ok": False, "error": f"{name} est réservé au HUD de l'utilisateur"}
        if name == "media_open":
            target = medias.open_target(cmd.get("media_id"))
            if target is None:
                return {"ok": False, "error": "média introuvable ou effacé"}
            from ..tools import desktop

            try:
                desktop.open_path(str(target))
            except Exception as exc:  # application absente, système sans « ouvrir »
                return {"ok": False, "error": str(exc)}
            return {"ok": True}
        conn = str(cmd.get("_conn") or "api")
        playing = bool(cmd.get("playing"))
        hold = release = None
        with _MEDIA_LOCK:
            holds = self.__dict__.setdefault("_media_holds", {})
            if playing and conn not in holds:
                hold = threading.Timer(MEDIA_HOLD_MAX_S, self._media_command,
                                       args=("media_playing", {"playing": False, "_conn": conn}))
                hold.daemon = True
                holds[conn] = hold
            elif not playing and conn in holds:
                release = holds.pop(conn)
            held = conn in holds
        mic = getattr(self, "mic", None)
        if hold is not None:
            if mic is not None:
                mic.hold_mute()
            hold.start()
            log.info("micro retenu : un média joue dans le HUD")
        if release is not None:
            release.cancel()
            if mic is not None:
                mic.release_mute()
            log.info("micro relâché : média arrêté%s", " (HUD fermé)" if cmd.get("_gone") else "")
        return {"ok": True, "playing": playing, "micro_retenu": held}

    def _image_command(self, name: str, cmd: dict) -> dict:
        """Outil MCP `generate_image` (`image_generate`), bouton « Réessayer » d'une carte
        (`image_retry`, `job_id` : « id » est celui de la requête WebSocket), état des comptes
        (`image_status`). Non bloquant : l'image arrive au HUD par l'évènement `image_job`.
        Libre pour Claude (Q4 du plan). P40 A4 (28/10) : `model` ; comptes (`image_compte_ajouter`,
        `_retirer`, `_ordre`, `_connecter` : fenêtre Chrome VISIBLE sur le profil, sans pilotage)
        réservés à l'utilisateur (HUD, terminal), refusés si `_from_claude` ; `image_retry` accepte
        `engine` (bouton « Essayer avec X ») ; `image_status` republie aussi `ia_etat`."""
        if name in images_ia.COMMANDES_COMPTES:
            if cmd.get("_from_claude") is True:
                log.warning("commande %s refusée : elle vient de Claude", name)
                return {"ok": False, "error": "comptes d'images réservés à l'utilisateur (HUD ou terminal), refusé à Claude"}
            return images_ia.images.compte_commande(name, cmd)
        if name == "image_piece":
            return self._image_piece(cmd)
        if name == "image_status":
            images_ia.images.publier_etat()
            return images_ia.images.status()
        if name == "image_retry":
            engine = str(cmd.get("engine") or "").strip()
            if engine and cmd.get("_from_claude") is True:
                # « Essayer avec X » = changer de moteur : jamais sans l'utilisateur (plan P40 étape 5)
                log.warning("image_retry avec engine=%r refusé : il vient de Claude", engine)
                return {"ok": False, "error": "changer de moteur est réservé à l'utilisateur (bouton « Essayer avec » du HUD), "
                                              "refusé à Claude"}
            return images_ia.images.retry(str(cmd.get("job_id") or ""), engine)
        if config.ESTOP_FILE.exists():
            return {"ok": False, "error": "arrêt d'urgence en cours : pas d'image"}
        ch, error = self._channel_for_tool(str(cmd.get("channel") or ""))
        if error:
            return {"ok": False, "error": error}
        result = images_ia.images.submit(cmd.get("prompt") or cmd.get("text"), cmd.get("engine") or "auto",
                                         cmd.get("files"), ch, cmd.get("model") or "")
        if result.get("ok"):
            log.info("image %s demandée (%s, %s)", result["job"], result["moteur"], ch)
        return result

    def _video_command(self, name: str, cmd: dict) -> dict:
        """Outil MCP `generate_video` (`video_generate`), bouton « Annuler » d'une carte
        (`video_cancel {job_id}` : « id » est celui de la requête WebSocket), crédits par compte
        (`video_status`). Libre pour Claude, comme les images. Non bloquant : la carte suit
        `video_job`, la vidéo finie est montrée dans le fil (`_video_finie`)."""
        if name == "video_status":
            videos_ia.videos.publier_etat()
            return videos_ia.videos.status()
        if name == "video_cancel":
            return videos_ia.videos.cancel(str(cmd.get("job_id") or ""))
        ch, error = self._channel_for_tool(str(cmd.get("channel") or ""))
        if error:
            return {"ok": False, "error": error}
        result = videos_ia.videos.submit(cmd.get("prompt") or cmd.get("text"), cmd.get("engine") or "auto",
                                         cmd.get("model") or "", cmd.get("duree") or 0, cmd.get("image") or "", ch,
                                         image_fin=cmd.get("image_fin") or "", format=cmd.get("format") or "")
        if result.get("ok"):
            log.info("vidéo %s demandée (%s, %s s, %s crédits, %s)", result["job"], result["service"],
                     result["duree"], result["credits_estimes"], ch)
        return result

    @staticmethod
    def _video_finie(job: videos_ia.Job) -> None:
        """Vidéo finie MONTRÉE dans le fil de son canal (`medias`, P26 : lecteur au clic, jamais
        d'autolecture), sous sa carte ; rien n'est dit. Légende : le prompt."""
        shown, refused = medias.publish_many([job.chemin])
        if not shown:
            log.warning("vidéo %s non affichable : %s", job.id, refused)
            bus.publish("notice", level="warning", text=f"Vidéo prête mais pas affichable dans le HUD : {job.nom}")
            return
        bus.publish("media", mid=uuid.uuid4().hex[:12], channel=job.channel, title=job.prompt[:200], items=shown)

    @staticmethod
    def _image_piece(cmd: dict) -> dict:
        """P40 A5 (28/10) : pièce jointe du panneau « Images » du HUD, `image_piece {name, data}`
        (base64, UNE par message : le WebSocket prend 8 Mo). Enregistrée dans runtime/attachments
        (0600, purgée comme les autres pièces), puis passée au MÊME filtre que les chemins de
        `generate_image` (`ia_web.piece` : PNG, JPEG ou WebP lus dans les octets, 5 Mo) ; refusée :
        effacée aussitôt. Rend le chemin, que le panneau joint à `image_generate` (`files`).
        L'utilisateur seul : Claude passe ses fichiers par chemin, il n'a rien à déposer ici."""
        import base64
        import binascii

        from .. import ia_web
        if cmd.get("_from_claude") is True:
            log.warning("image_piece refusée : elle vient de Claude")
            return {"ok": False, "error": "pièces jointes du panneau Images : l'utilisateur seul (HUD)"}
        brut = cmd.get("data")
        if not isinstance(brut, str) or not brut or len(brut) > (ia_web.PIECE_MAX_OCTETS * 4) // 3 + 8:
            return {"ok": False, "error": "image trop lourde (5 Mo au plus)" if isinstance(brut, str) and brut
                    else "pièce jointe illisible"}
        try:
            data = base64.b64decode(brut, validate=True)
        except (binascii.Error, ValueError):
            return {"ok": False, "error": "pièce jointe illisible"}
        if not data:
            return {"ok": False, "error": "pièce jointe illisible"}
        if len(data) > ia_web.PIECE_MAX_OCTETS:
            return {"ok": False, "error": "image trop lourde (5 Mo au plus)"}
        try:
            path = attachments.save_bytes(str(cmd.get("name") or "image.png"), data)
        except (OSError, ValueError) as exc:
            log.warning("image_piece : enregistrement impossible (%s)", exc)
            return {"ok": False, "error": "pièce jointe illisible"}
        reel, raison = ia_web.piece(str(path))
        if raison:
            path.unlink(missing_ok=True)
            return {"ok": False, "error": raison}
        return {"ok": True, "path": reel, "name": path.name}
