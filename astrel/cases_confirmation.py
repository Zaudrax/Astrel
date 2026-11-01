"""Cases de confirmation de Réglages › Garde-fou (24/10, plan P29 lot B, demande de l'utilisateur).

« Sur VS Code il n'y a pas de confirmation en mode auto ; je dois pouvoir choisir via
l'interface de désactiver ça avec des cases à cocher. Tout doit être décochable : si je
décoche tout, plus aucune demande de confirmation pour rien. Cochées par défaut pour les
choses sensibles. »

Ce module ne DÉCIDE rien (la décision est dans `limites.sans_carte`, appelée par
`totalgate.confirmation_free`, fichiers protégés) : il garde en mémoire les cases en vigueur,
les relit quand le fichier change, et les change pour l'utilisateur (fichier signé par le cœur,
journal en ajout seul). Qui a le droit de les changer : `totalgate.may_change_confirmations`,
vérifié par le cœur AVANT d'appeler `changer`.

Fichier absent, illisible, mal signé (réécrit par un autre process que le cœur) ou aux types
invalides : défauts (sensible coché), jamais « tout décoché ».
"""
from __future__ import annotations

import logging
import threading
import time

from . import guard, limites

log = logging.getLogger("astrel.cases")


class Cases:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._coches: dict[str, bool] | None = None
        self._stamp: tuple = ()
        self._raison = ""

    @staticmethod
    def _file_stamp() -> tuple:
        """(inode, date, taille) du fichier des cases ET de sa clé : l'un change, on relit."""
        out: tuple = ()
        for p in (guard.CONFIRM_CASES_FILE, guard.CONFIRM_KEY_FILE):
            try:
                st = p.stat()
                out += ((st.st_ino, st.st_mtime_ns, st.st_size),)
            except OSError:
                out += (None,)
        return out

    def reset(self) -> None:
        """Oublie tout (tests) : le fichier sera relu."""
        with self._lock:
            self._coches, self._stamp, self._raison = None, (), ""

    def _etat_verrouille(self) -> tuple[dict[str, bool], str]:
        """Comme `etat`, le verrou DÉJÀ tenu par l'appelant."""
        stamp = self._file_stamp()
        if self._coches is None or stamp != self._stamp:
            coches, raison = limites.lire_cases(guard.CONFIRM_CASES_FILE, guard.CONFIRM_KEY_FILE)
            if raison and raison != "absent":
                log.warning("cases de confirmation : %s, valeurs par défaut (sensible coché)", raison)
            self._coches, self._stamp, self._raison = coches, stamp, raison
        return dict(self._coches), self._raison

    def etat(self) -> tuple[dict[str, bool], str]:
        """(cases en vigueur, raison si ce sont les défauts faute de fichier valide)."""
        with self._lock:
            return self._etat_verrouille()

    def coches(self) -> dict[str, bool]:
        return self.etat()[0]

    def changer(self, nouvelles: dict, qui: str) -> list[str]:
        """Change les cases (objet {id: bool}, partiel permis) ; rend les ids changés. Lève
        ValueError (case inconnue, valeur non booléenne) ou OSError (fichier non écrit)."""
        if not isinstance(nouvelles, dict):
            raise ValueError("il faut un objet {case: vrai ou faux}")
        for k, v in nouvelles.items():
            if k not in limites.CASES_PAR_ID:
                raise ValueError(f"case inconnue : {k}")
            if not isinstance(v, bool):
                raise ValueError(f"« {k} » : il faut vrai ou faux")
        with self._lock:  # lecture ET écriture sous le même verrou : deux changements parallèles ne se perdent pas
            avant = self._etat_verrouille()[0]
            apres = {**avant, **nouvelles}
            if apres == avant:
                return []
            limites.ecrire_cases(guard.CONFIRM_CASES_FILE, guard.CONFIRM_KEY_FILE, apres)
            self._coches, self._stamp, self._raison = dict(apres), self._file_stamp(), ""
        try:
            changes = limites.journaliser(guard.CONFIRM_CASES_LOG, qui, avant, apres,
                                          time.strftime("%Y-%m-%d %H:%M:%S"))
        except OSError as exc:
            log.warning("journal des cases non écrit : %s", exc)
            changes = [k for k in apres if apres[k] != avant.get(k)]
        log.info("cases de confirmation changées par %s : %s", qui,
                 ", ".join(f"{k}={'cochée' if apres[k] else 'décochée'}" for k in changes))
        return changes


cases = Cases()
