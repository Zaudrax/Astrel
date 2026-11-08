"""Propositions de modification du modèle local (05/11/2026, lot 5, docs/chantiers/HYBRIDE-QWEN.md) :
panneau « Propositions de Qwen » du HUD, l'utilisateur seul.

Mixin de `Astrel` (core.py). Le modèle local DÉPOSE (`mcp_local.proposer_modification` →
`propositions_local.deposer`, dans `runtime/propositions-local/`, protégé) ; ici :
- `propositions_local_get {toutes?}` : liste et diff ; `proposition_local_appliquer {proposition_id,
  empreinte}` : HUD de l'utilisateur SEUL (refusé à Claude ET hors HUD : terminal, bulle, WhatsApp) ;
  `proposition_local_refuser {proposition_id, raison}` : HUD ou terminal. Toutes refusées à Claude ;
  clé `proposition_id`, jamais `id` (le HUD l'écrase) ;
- l'application est rejugée par `propositions_local.appliquer` (empreinte du fichier, périmètre du canal ou
  de `config.WORKDIR`, garde-fou, secret, instantané avant écriture, écriture atomique) ;
- évènement collant `propositions_local {en_attente}` (badge) et une notice dans le fil du canal à chaque
  nouvelle proposition, relus toutes les 5 s si le dossier a changé (le serveur MCP écrit, pas le cœur).
"""
from __future__ import annotations

import time
from pathlib import Path

from .. import audit, config, propositions_local
from ..channels import channels
from ..events import bus
from ..core import log  # aides du cœur (le paquet charge core d'abord)

PROPOSITIONS_LOCAL_COMMANDS = ("propositions_local_get", "proposition_local_appliquer", "proposition_local_refuser")
_SOCKET_MAX = 20
_SOCKET_CHAMPS = ("proposition_id", "fichier", "statut", "empreinte")  # réponse du canal ≤ 4 096 octets

REFUS_CLAUDE = "propositions du modèle local réservées à l'utilisateur (HUD), refusées à Claude"
REFUS_HUD = "une proposition du modèle local ne s'applique que depuis le HUD"
NOTICE_CLAUDE = "Claude a tenté « {name} » : refusé, les propositions du modèle local sont à toi seul."
NOTICE_NOUVELLE = ("Le modèle local propose une modification de {fichier} : à relire dans « Propositions de Qwen » "
                   "(rien n'est écrit sans toi).")
NOTICE_APPLIQUEE = "Modification de {fichier} appliquée (instantané gardé : ./run.sh annule la défait)."
NOTICE_REFUSEE = "Proposition du modèle local refusée : {fichier}."


class PropositionsLocalCoeur:
    """Panneau « Propositions de Qwen » du HUD."""

    _qprop_sig: tuple | None = None
    _qprop_vu = 0.0
    _qprop_connues: set | None = None

    def _perimetres_canal(self, canal: str) -> list[Path]:
        """Où une proposition de ce canal peut écrire : le dossier de projet du canal, et `config.WORKDIR`. 06/11 (lot
        9) : et la racine d'Astrel (une proposition du modèle local sur ses consignes ou son code, `mcp_local`) ; le
        garde-fou refuse toujours ses fichiers protégés (`propositions_local._juge`)."""
        out = [Path(config.WORKDIR), Path(config.ROOT)]
        try:
            wd = channels.workdir_of(canal) if canal else ""
        except Exception:
            wd = ""
        if wd:
            out.insert(0, Path(wd))
        return out

    def _propositions_local_command(self, name: str, cmd: dict) -> dict:
        if cmd.get("_from_claude") is True:
            log.warning("commande %r refusée : elle vient de Claude", name)
            bus.publish("notice", level="warning", text=NOTICE_CLAUDE.format(name=name))
            return {"ok": False, "error": REFUS_CLAUDE}
        via = str(cmd.get("_via") or "terminal")
        if name == "propositions_local_get":
            items = propositions_local.lister(toutes=cmd.get("toutes") is True)
            if via == "socket":
                items = [{k: it[k] for k in _SOCKET_CHAMPS} for it in items[:_SOCKET_MAX]]
            return {"ok": True, "propositions": items}
        pid = str(cmd.get("proposition_id") or "").strip()
        try:
            if name == "proposition_local_appliquer":
                if via != "hud":
                    log.warning("proposition %s : application refusée hors du HUD (%s)", pid, via)
                    return {"ok": False, "error": REFUS_HUD}
                item = next((p for p in propositions_local.en_attente() if p["id"] == pid), None)
                canal = item["canal"] if item else ""
                r = propositions_local.appliquer(pid, str(cmd.get("empreinte") or ""), self._perimetres_canal(canal),
                                                 par="utilisateur")
                audit.record("propositions-local", "utilisateur", f"proposition {pid} appliquée", via, ok=True)
                bus.publish("notice", level="info", channel=canal or channels.active,
                            text=NOTICE_APPLIQUEE.format(fichier=r["fichier"]))
            else:  # proposition_local_refuser
                r = propositions_local.refuser(pid, str(cmd.get("raison") or ""), par="utilisateur")
                audit.record("propositions-local", "utilisateur", f"proposition {pid} refusée", via, ok=True)
                bus.publish("notice", level="info", text=NOTICE_REFUSEE.format(fichier=r["fichier"]))
        except (propositions_local.Refus, OSError) as exc:
            log.info("proposition %s : %s", pid, exc)
            return {"ok": False, "error": str(exc)}
        self._publish_propositions_local(force=True)
        return r

    def _publish_propositions_local(self, force: bool = False) -> None:
        sig = propositions_local.signature()
        if not force and sig == self._qprop_sig:
            return
        self._qprop_sig = sig
        attente = propositions_local.en_attente()
        connues = self._qprop_connues
        if connues is not None:  # au démarrage : aucune notice pour ce qui attendait déjà
            for p in attente:
                if p["id"] not in connues:
                    bus.publish("notice", level="info", channel=p["canal"] or channels.active,
                                text=NOTICE_NOUVELLE.format(fichier=p["fichier"]))
        self._qprop_connues = {p["id"] for p in attente}
        bus.publish("propositions_local", en_attente=len(attente))

    def _propositions_local_tick(self) -> None:
        """Toutes les 5 s au plus : republie le nombre (et une notice par nouvelle) si le dossier a changé."""
        now = time.monotonic()
        if now - self._qprop_vu < 5.0:
            return
        self._qprop_vu = now
        self._publish_propositions_local()
