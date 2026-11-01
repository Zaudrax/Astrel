"""Passerelle locale vers un fournisseur à clé API (20/10, plan P11, lot 2).

Le CLI `claude` reste le cerveau ; seuls `ANTHROPIC_BASE_URL` et le modèle changent. La
passerelle (`passerelle_serveur.py`, bibliothèque LiteLLM) traduit l'API Messages vers
OpenAI, Gemini, OpenRouter ou Grok (xAI). Ce module, côté cœur :

- l'INSTALLE à la demande (bouton de Réglages › Fournisseur) dans une venv À PART
  (`config.PASSERELLE_VENV`, version FIXÉE `config.PASSERELLE_LITELLM`) : jamais dans la
  venv d'Astrel (piège 4 du CLAUDE.md : `--system-site-packages`) ;
- la LANCE à la demande (`Brain.start`, fournisseur à clé choisi), enfant du cœur, sur
  127.0.0.1, port choisi par le système, avec un jeton local nouveau à chaque lancement
  (`runtime/passerelle-jeton`, 0600, lu par `cle_api.py` pour le CLI) ;
- l'ARRÊTE (retour sur Claude, mode léger, fin du cœur : son entrée standard se ferme).

La clé du fournisseur n'est jamais passée : la passerelle lit elle-même le fichier des
secrets (son NOM seul en argument). Ni ligne de commande, ni environnement, ni journal.
"""
from __future__ import annotations

import logging
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from . import config
from .platform import enfants

log = logging.getLogger("astrel.passerelle")

START_TIMEOUT_S = 60.0       # import de LiteLLM : ~2 s mesurées le 20/10, large pour un disque lent
INSTALL_TIMEOUT_S = 1800.0   # pip : ~270 Mo à télécharger
# `XAI_` : XAI_API_KEY, mais aussi XAI_API_BASE / XAI_OAUTH_* que LiteLLM lit (une adresse du shell
# enverrait la clé ailleurs).
_SECRET_ENV = ("ANTHROPIC_", "OPENAI_", "GEMINI_", "GOOGLE_API_KEY", "OPENROUTER_", "CLE_API_", "AZURE_", "XAI_")


def python() -> Path:
    venv = config.PASSERELLE_VENV
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _pinned() -> tuple[str, str]:
    name, _, version = config.PASSERELLE_LITELLM.partition("==")
    return name.strip(), version.strip()


def installed_version() -> str:
    """Version de LiteLLM installée dans la venv de la passerelle ("" : absente). Lue sur le
    disque (dossier `*.dist-info`), sans lancer Python."""
    venv = config.PASSERELLE_VENV
    for site in [*venv.glob("lib/python*/site-packages"), venv / "Lib" / "site-packages"]:
        for info in site.glob("litellm-*.dist-info"):
            return info.name[len("litellm-"):-len(".dist-info")]
    return ""


def installed() -> bool:
    """Installée À LA VERSION FIXÉE (une autre version n'est pas lancée : elle n'a pas été mesurée)."""
    return python().exists() and installed_version() == _pinned()[1]


def size_mb() -> int:
    total = 0
    for root, _dirs, files in os.walk(config.PASSERELLE_VENV):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                continue
    return total // (1024 * 1024)


def _clean_env(extra: dict | None = None) -> dict:
    """Environnement d'un enfant (pip, passerelle) : sans AUCUNE clé de fournisseur."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(_SECRET_ENV)}
    env.update(extra or {})
    return env


def install(on_line: Callable[[str], Any] | None = None) -> dict:
    """Crée la venv et y installe la version fixée (bloquant : à lancer dans un fil).
    Fichiers temporaires DANS la venv (sous /data chez l'utilisateur : / n'a que 7 Go)."""
    venv = config.PASSERELLE_VENV
    tmp = venv / ".tmp"
    try:
        if not python().exists():
            subprocess.run([str(config.VENV_PYTHON if config.VENV_PYTHON.exists() else sys.executable),
                            "-m", "venv", str(venv)], check=True, capture_output=True, timeout=300)
        tmp.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            [str(python()), "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check",
             "--progress-bar", "off", config.PASSERELLE_LITELLM],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env=_clean_env({"TMPDIR": str(tmp), "PIP_NO_INPUT": "1"}))
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"venv de la passerelle impossible à créer : {exc}"}
    timer = threading.Timer(INSTALL_TIMEOUT_S, proc.kill)
    timer.daemon = True
    timer.start()
    try:
        for line in proc.stdout or ():
            line = line.strip()
            if line and on_line and line.startswith(("Collecting", "Installing", "Successfully")):
                on_line(line[:160])
        code = proc.wait()
    finally:
        timer.cancel()
    if code != 0 or not installed():
        return {"ok": False, "error": f"installation de la passerelle en échec (code {code})"}
    log.info("passerelle installée : %s (%d Mo)", config.PASSERELLE_LITELLM, size_mb())
    return {"ok": True}


def _write_token(token: str) -> None:
    path = config.PASSERELLE_JETON_FILE
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class Passerelle:
    """Un process de passerelle au plus, pour UN fournisseur et UN modèle."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self.url = ""
        self.key = ("", "", "")  # (fournisseur, modèle, adresse forcée) du process en cours

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, fournisseur: str, modele: str, cle: str, base: str = "") -> str:
        """Lance la passerelle (ou garde celle qui tourne pour le même choix) ; rend son adresse.
        `cle` : NOM de la clé dans le fichier des secrets. Lève RuntimeError (message sans secret)."""
        with self._lock:
            if self.alive and self.key == (fournisseur, modele, base):
                return self.url
            self._stop_locked()
            if not installed():
                raise RuntimeError("passerelle non installée : Réglages › Fournisseur › « Installer la passerelle »")
            token = secrets.token_urlsafe(32)
            cmd = [str(python()), "-I", str(config.PASSERELLE_SCRIPT), "--fournisseur", fournisseur,
                   "--modele", modele, "--secrets", str(config.SECRETS_FILE), "--cle", cle]
            if base:
                cmd += ["--base", base]
            if _oauth(fournisseur):
                from . import xai_oauth

                # Plan P16 : `cle` nomme l'objet OAuth, relu à chaque requête ; l'état (non secret)
                # dit à la passerelle que le cœur a conclu « refusé » (pas d'attente de rejeu).
                cmd += ["--oauth", "--etat", str(xai_oauth.ETAT_FILE)]
            try:
                # 01/11 : la passerelle meurt avec le cœur (Linux, `platform.enfants`).
                proc = enfants.popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, bufsize=1,
                                        env=_clean_env({"LITELLM_LOCAL_MODEL_COST_MAP": "True"}))
            except OSError as exc:
                raise RuntimeError(f"passerelle impossible à lancer : {exc}") from None
            assert proc.stdin is not None and proc.stdout is not None
            try:
                proc.stdin.write(token + "\n")  # jeton par l'entrée standard, jamais en argument
                proc.stdin.flush()
            except OSError:
                pass
            port = self._read_port(proc)
            if not port:
                err = ""
                try:
                    proc.kill()
                    err = (proc.communicate(timeout=3)[1] or "").strip().splitlines()[-1:]
                    err = err[0][:200] if err else ""
                except Exception:  # noqa: BLE001
                    pass
                raise RuntimeError("la passerelle n'a pas démarré" + (f" : {err}" if err else ""))
            _write_token(token)
            self._proc, self.url, self.key = proc, f"http://127.0.0.1:{port}", (fournisseur, modele, base)
            threading.Thread(target=self._read_stderr, args=(proc,), name="passerelle-err", daemon=True).start()
        log.info("passerelle lancée : %s, modèle %s, %s", fournisseur, modele, self.url)
        return self.url

    @staticmethod
    def _read_port(proc: subprocess.Popen) -> int:
        q: queue.Queue[str] = queue.Queue()

        def read() -> None:
            q.put(proc.stdout.readline() if proc.stdout else "")

        threading.Thread(target=read, name="passerelle-port", daemon=True).start()
        try:
            line = q.get(timeout=START_TIMEOUT_S)
        except queue.Empty:
            return 0
        parts = line.split()
        return int(parts[1]) if len(parts) == 2 and parts[0] == "PORT" and parts[1].isdigit() else 0

    @staticmethod
    def _read_stderr(proc: subprocess.Popen) -> None:
        # La passerelle masque déjà la clé et le jeton ; elle n'écrit ni requête ni réponse.
        for line in proc.stderr or ():
            if line.strip():
                log.info("passerelle : %s", line.strip()[:300])
                _relayer_refus(line)

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        proc, self._proc, self.url, self.key = self._proc, None, "", ("", "", "")
        try:
            config.PASSERELLE_JETON_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()  # la passerelle s'arrête quand son entrée se ferme
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()
        log.info("passerelle arrêtée")


passerelle = Passerelle()


def _oauth(fournisseur: str) -> bool:
    """Fournisseur connecté par OAuth (abonnement xAI, plan P16) : jeton relu par la passerelle."""
    from .fournisseurs import CATALOGUE

    f = CATALOGUE.get(fournisseur)
    return bool(f is not None and f.oauth)


_REFUS = re.compile(r"^XAI_REFUSE (\d{3})(?: ([0-9a-f]{16}))?$")


def _relayer_refus(line: str) -> None:
    """`XAI_REFUSE <code>` (passerelle en `--oauth`) → `xai_oauth.signaler_refus` : le cœur est le
    SEUL rafraîchisseur ; 401 : rafraîchir puis la passerelle rejoue ; 403 : état « refusé »."""
    m = _REFUS.match(line.strip())
    if not m:
        return
    try:
        from . import xai_oauth

        xai_oauth.signaler_refus(int(m.group(1)), m.group(2))
    except Exception:  # noqa: BLE001 — jamais de lecteur de stderr mort
        log.exception("signal de refus xAI non traité")
