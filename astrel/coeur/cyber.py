"""Capacité de pentest côté cœur (09/11/2026, branche cyber-local).

Mixin de `Astrel` (core.py). L'utilisateur dit ou tape « pentest <cible> », « teste <cible> » ou
« scan <cible> » (voix ou HUD) ; le cœur :
- vérifie que la cible est DANS LE PÉRIMÈTRE autorisé (`astrel/cyber.py`, même logique et même
  fichier que `cyberlab/pentest.sh`) AVANT tout lancement ; hors périmètre = refus net, rien n'est
  lancé ;
- lance `cyberlab/pentest.sh` comme TÂCHE DE FOND, enfant du cœur (`platform.enfants.popen` :
  meurt avec le cœur, jamais un subprocess brut non suivi) ; le worker tourne hors du cœur ;
- à la fin, remonte les findings par le mécanisme des comptes rendus (`reports.py`, à la demande) :
  le résumé (score, drapeaux) est AFFICHÉ dans le fil avec un bouton « 🔊 Lire », jamais dit d'office.

INVARIANTS DE SÉCURITÉ (voir `docs/CLAUDE-DETAIL.md` § Cybersécurité autorisée) :
- les outils offensifs (nmap/sqlmap) ne tournent que dans le conteneur isolé `cyberlab-toolbox`,
  jamais sur l'hôte : c'est le lanceur `--outils` qui s'en charge, le cœur ne lance jamais d'outil
  offensif directement ;
- la cible est TOUJOURS vérifiée contre le périmètre avant tout lancement (ici ET dans le lanceur) ;
- la commande est REFUSÉE à Claude (le cerveau ne lance jamais de pentest) : `_from_claude` → refus ;
- « Stop tout » (ESTOP) arrête les pentests en cours (`_pentest_estop`, appelé par `emergency_stop`) ;
- le mode « rien ne sort » (hors ligne) empêche tout nouveau pentest.
"""
from __future__ import annotations

import subprocess
import threading
import time
import uuid

from .. import config, cyber, hors_ligne
from ..events import bus
from ..platform import enfants
from ..reports import reports
from ..core import log  # aides du cœur (le paquet charge core d'abord)

# Message exact du refus à Claude (figé par `tests/test_table_commandes.py`).
REFUS_CLAUDE = "pentest réservé à l'utilisateur (voix ou HUD), refusé à Claude"

_LOCK = threading.Lock()  # protège les tables `_pentests` de chaque Astrel (singleton en pratique)


class CyberCoeur:
    """Commande `_cmd_pentest`, lancement en tâche de fond, remontée des findings, coupures."""

    @property
    def _pentests(self) -> dict[str, dict]:
        """Pentests en cours : id → {cible, host, outils, started, proc, channel}. En mémoire seulement."""
        d = self.__dict__.get("_pentests_map")
        if d is None:
            d = self.__dict__["_pentests_map"] = {}
        return d

    def _cmd_pentest(self, name: str, cmd: dict) -> dict:
        """« pentest/teste/scan <cible> » (voix, HUD, `--send pentest`). Refusée à Claude ; la cible est
        vérifiée contre le périmètre avant tout lancement ; lancée en tâche de fond, findings à la demande."""
        if cmd.get("_from_claude") is True:
            # Le cerveau ne lance JAMAIS de pentest : refusé d'emblée (comme les commandes réservées à l'utilisateur).
            log.warning("pentest refusé : il vient de Claude")
            return {"ok": False, "error": REFUS_CLAUDE}
        cible = str(cmd.get("cible") or cmd.get("text") or "").strip()
        outils = bool(cmd.get("outils"))
        if not cible:
            return {"ok": False, "error": "précise une cible à tester (IP ou URL)"}
        if hors_ligne.actif():
            return {"ok": False, "error": "mode « rien ne sort » actif : pas de pentest, rien ne part de la machine"}
        if config.ESTOP_FILE.exists():
            return {"ok": False, "error": "arrêt d'urgence en cours : pas de pentest"}
        host, autorisee = cyber.verifier_cible(cible)
        if not host:
            return {"ok": False, "error": f"cible illisible : {cible} (donne une IP ou une URL)"}
        if not autorisee:
            # Hors périmètre : NE LANCE RIEN. L'utilisateur ajoute la cible à la main (ses biens seulement).
            fichier = cyber.fichier_perimetre()
            return {"ok": False, "error":
                    f"cible hors périmètre : {host}. Ajoute-la dans {fichier} (tes biens ou ton entreprise "
                    "seulement), puis réessaie."}
        lanceur = cyber.lanceur()
        if not lanceur.exists():
            return {"ok": False, "error": f"outillage de pentest introuvable : {lanceur}"}
        ch, erreur = self._channel_for_tool(str(cmd.get("channel") or ""), alert=True)
        if erreur:
            return {"ok": False, "error": erreur}
        jid = uuid.uuid4().hex[:12]
        argv = [str(lanceur), cible] + (["--outils"] if outils else [])
        with _LOCK:
            self._pentests[jid] = {"cible": cible, "host": host, "outils": outils,
                                   "started": time.time(), "proc": None, "channel": ch}
        threading.Thread(target=self._pentest_worker, args=(jid, cible, host, outils, argv, ch),
                         name="pentest", daemon=True).start()
        log.info("pentest %s lancé contre %s (hôte %s, outils=%s)", jid, cible, host, outils)
        mode = "vrais outils, conteneur isolé" if outils else "mode HTTP"
        text = f"Pentest lancé contre {cible} ({mode}) : je t'affiche le résultat quand c'est fini."
        bus.publish("notice", level="info", channel=ch, text=text)
        return {"ok": True, "job": jid, "cible": cible, "host": host, "outils": outils, "text": text}

    def _pentest_worker(self, jid: str, cible: str, host: str, outils: bool, argv: list[str], ch: str) -> None:
        """Fil de fond : lance `pentest.sh` (enfant du cœur), attend sa fin, remonte un compte rendu.
        « Stop tout » tue le process (`_pentest_estop`) : la run compte alors pour interrompue."""
        sortie, code = "", None
        try:
            proc = enfants.popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except OSError as exc:
            log.warning("pentest %s : lancement impossible (%s)", jid, exc)
            self._pentests.pop(jid, None)
            texte = f"Pentest de {cible} non lancé : {exc}."
            bus.publish("notice", level="error", channel=ch, text=texte)
            return
        with _LOCK:
            entry = self._pentests.get(jid)
            if entry is not None:
                entry["proc"] = proc
        if config.ESTOP_FILE.exists():  # arrêt d'urgence posé pendant le lancement
            proc.terminate()
        try:
            sortie, _ = proc.communicate()
            code = proc.returncode
        except Exception:
            log.exception("pentest %s : attente du worker", jid)
        finally:
            with _LOCK:
                annule = self._pentests.pop(jid, None)
            annule = bool(annule and annule.get("annule")) or config.ESTOP_FILE.exists()
        log.info("pentest %s fini (code %s, cible %s)", jid, code, cible)
        if annule or (code is not None and code < 0):
            # Tué (Stop tout) : pas de compte rendu de findings, une notice suffit.
            texte = f"Pentest de {cible} interrompu (Stop tout)."
            bus.publish("notice", level="warning", channel=ch, text=texte)
            return
        if code == 2:
            # Le lanceur a refusé la cible (hors périmètre selon sa propre vérification) : filet de sécurité.
            texte = f"Pentest de {cible} refusé par le lanceur : cible hors périmètre."
            bus.publish("notice", level="warning", channel=ch, text=texte)
            return
        self._pentest_rapport(cible, outils, sortie, ch)

    def _pentest_rapport(self, cible: str, outils: bool, sortie: str, ch: str) -> None:
        """Compte rendu des findings (score, drapeaux, durée) : AFFICHÉ dans le fil avec le bouton
        « 🔊 Lire », jamais dit d'office (comptes rendus à la demande, `reports.py`)."""
        resume = cyber.lire_resume(sortie)
        fichier = cyber.fichier_resultat(outils)
        fichiers = [str(fichier)] if fichier.exists() else []
        mode = "vrais outils (conteneur isolé)" if outils else "HTTP"
        if resume is None:
            texte = (f"Pentest de {cible} ({mode}) terminé, mais le résumé n'a pas pu être lu. "
                     f"Détails dans {fichier.parent}.")
        else:
            score, sur = resume.get("score"), resume.get("sur")
            flags = resume.get("flags") or {}
            duree = resume.get("duree_s")
            morceaux = [f"Pentest de {cible} ({mode}) terminé"]
            if score is not None and sur is not None:
                morceaux.append(f"score {score}/{sur}")
            if flags:
                morceaux.append(f"{len(flags)} drapeau(x) : " + ", ".join(sorted(flags)))
            else:
                morceaux.append("aucun drapeau (findings détaillés dans le journal)")
            if duree is not None:
                morceaux.append(f"{duree} s")
            texte = morceaux[0] + " — " + ", ".join(morceaux[1:]) + "."
        mid = uuid.uuid4().hex[:12]
        pending = not self._voice_muted()
        reports.add(mid, texte, ch, pending=pending, kind="resultat", files=fichiers)
        bus.publish("report", text=texte, channel=ch, mid=mid, report=True, pending_read=pending,
                    report_kind="resultat", report_files=fichiers)
        log.info("pentest : compte rendu posé pour %s (%s)", cible, "résumé lu" if resume else "sans résumé")

    def _pentest_estop(self) -> int:
        """« Stop tout » : tue chaque pentest en cours et le marque annulé. Rend le nombre arrêté."""
        with _LOCK:
            jobs = list(self._pentests.items())
            for _jid, job in jobs:
                job["annule"] = True
        n = 0
        for jid, job in jobs:
            proc = job.get("proc")
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    n += 1
                except Exception:
                    log.exception("pentest %s non arrêté", jid)
        if n:
            log.warning("arrêt d'urgence : %d pentest(s) arrêté(s)", n)
        return n

    def _pentest_dit(self, cible: str, outils: bool, text: str, source: str, channel: str) -> None:
        """« pentest/teste/scan <cible> », dit ou tapé par l'utilisateur : affiché, lancé, réponse dite (voix).
        Calqué sur `_hors_ligne_dit` : la demande ne part JAMAIS à Claude."""
        from ..channels import channels

        ch = channel or channels.active
        bus.publish("transcript", who="user", text=text, source=source, channel=ch, mid=uuid.uuid4().hex[:12])
        res = self._cmd_pentest("pentest", {"cible": cible, "outils": outils, "channel": ch})
        texte = res.get("text") or res.get("error") or ""
        if not res.get("ok"):
            bus.publish("notice", level="warning", channel=ch, text=texte)
        if source == "voix" and texte:
            self.say(texte, raw=True)
