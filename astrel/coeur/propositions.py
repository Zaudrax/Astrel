"""Propositions de mémoire à valider (04/11, carte T49) : commandes du HUD, l'utilisateur seul.

Mixin de `Astrel` (core.py). Les propositions (`memory_propose`, relève de fin de session, ménage de nuit)
attendaient depuis le 21/10 dans `runtime/vault-propositions/` sans écran. Ici :
- `propositions_get {toutes?}`, `proposition_appliquer {proposition_id, empreinte}`,
  `proposition_refuser {proposition_id, raison}` : TOUTES refusées à Claude (comme `plan_*`), la mémoire
  qu'il propose est validée par l'utilisateur ; clé `proposition_id`, jamais `id` (le HUD l'écrase) ;
- par le canal de contrôle (terminal), la liste est réduite à l'identifiant, au type, à la note et à
  l'empreinte (réponse ≤ 4 096 octets) ;
- évènement collant `propositions {en_attente}` : badge du HUD ; relu toutes les 5 s si le dossier a changé
  (`memory_propose` écrit depuis le serveur MCP, pas depuis le cœur) ;
- permis sous Stop tout : ce n'est pas un agent, rien n'est lancé.
"""
from __future__ import annotations

import time

from .. import audit
from ..events import bus
from ..tools import vault_review
from ..core import log  # aides du cœur (le paquet charge core d'abord)

PROPOSITIONS_COMMANDS = ("propositions_get", "proposition_appliquer", "proposition_refuser")
_SOCKET_MAX = 20
_SOCKET_CHAMPS = ("proposition_id", "type", "note", "empreinte")  # réponse du canal ≤ 4 096 octets


class PropositionsCoeur:
    """Écran « Propositions » du HUD."""

    _prop_sig: tuple | None = None
    _prop_vu = 0.0

    def _propositions_command(self, name: str, cmd: dict) -> dict:
        if cmd.get("_from_claude") is True:
            log.warning("commande %r refusée : elle vient de Claude", name)
            bus.publish("notice", level="warning",
                        text=f"Claude a tenté « {name} » : refusé, la mémoire à valider est à toi seul.")
            return {"ok": False, "error": "propositions de mémoire réservées à l'utilisateur (HUD ou terminal), "
                                          "refusées à Claude"}
        via = str(cmd.get("_via") or "terminal")
        if name == "propositions_get":
            items = vault_review.lister(toutes=cmd.get("toutes") is True)
            if via == "socket":
                items = [{k: it[k] for k in _SOCKET_CHAMPS} for it in items[:_SOCKET_MAX]]
            return {"ok": True, "propositions": items}
        pid = str(cmd.get("proposition_id") or "").strip()
        try:
            if name == "proposition_appliquer":
                r = vault_review.appliquer(pid, str(cmd.get("empreinte") or ""), par="utilisateur")
                audit.record("memoire", "utilisateur", f"proposition {pid} appliquée", via, ok=True)
            else:  # proposition_refuser
                r = vault_review.refuser(pid, str(cmd.get("raison") or ""), par="utilisateur")
                audit.record("memoire", "utilisateur", f"proposition {pid} refusée", via, ok=True)
        except (ValueError, FileExistsError, FileNotFoundError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        self._publish_propositions(force=True)
        return r

    def _publish_propositions(self, force: bool = False) -> None:
        sig = _signature()
        if not force and sig == self._prop_sig:
            return
        self._prop_sig = sig
        bus.publish("propositions", en_attente=vault_review.en_attente())

    def _propositions_tick(self) -> None:
        """Toutes les 5 s au plus : republie le nombre si le dossier a changé."""
        now = time.monotonic()
        if now - self._prop_vu < 5.0:
            return
        self._prop_vu = now
        self._publish_propositions()


def _signature() -> tuple:
    folder = vault_review.PROPOSALS_DIR
    try:
        return tuple(sorted((p.name, p.stat().st_mtime_ns) for p in folder.glob("*.json")))
    except OSError:
        return ()
