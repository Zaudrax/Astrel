"""Modèle local de secours, par Ollama (19/10, plan P8, demande de l'utilisateur).

Pour partager Astrel avec un ami sur un abonnement Claude modeste : quand la limite
approche ou tombe, Astrel peut continuer à DISCUTER (mode léger, sans outil) avec un
modèle installé sur la machine. Ici, la logique seule : catalogue, ce qui tient sur ce
PC, client Ollama, téléchargement, état choisi. La bascule du cerveau est dans le cœur
(`core.py`) et la commande dans `brain.py`.

Règles (plan P8, étape 6), en Go binaires (`materiel.GO`) :

- « rapide (carte graphique) » si taille + `MARGE_GO` ≤ VRAM − `RESERVE_VRAM_GO` ;
- « mixte, plus lent » (ou « lent », sans carte) si taille + `MARGE_GO` ≤ RAM − `RESERVE_RAM_GO` ;
- sinon le modèle n'est PAS proposé, ni téléchargeable, ni choisissable ;
- téléchargement seulement si le disque qui porte RÉELLEMENT le dossier des modèles a
  ≥ taille × 1,2 + 2 Go libres, ET qu'il lui restera ensuite au moins 10 Go et 15 % de sa
  taille (règle de l'utilisateur du 19/10 : un disque plein est très gênant) ; sinon refus clair
  qui propose de changer le dossier des modèles (`OLLAMA_MODELS`).

La taille fait foi au MANIFESTE du registre Ollama (lu en fond, gardé un jour) ; hors
ligne, la taille mesurée le 19/10/2026 sert de repli (« estimée »).

Astrel n'installe jamais Ollama (droits d'administrateur) : lien et commande seulement.
Rien ici n'envoie de donnée personnelle : le registre ne reçoit que le nom d'un modèle.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import config, hors_ligne
from .platform import materiel

log = logging.getLogger("astrel.local")

GO = materiel.GO
MARGE_GO = 1.5          # contexte et calculs, au-delà du poids du modèle
RESERVE_VRAM_GO = 0.5
RESERVE_RAM_GO = 4.0    # le système et les applications ouvertes
DISQUE_FACTEUR, DISQUE_MARGE_GO = 1.2, 2.0
DISQUE_RESTE_GO, DISQUE_RESTE_PART = 10.0, 0.15  # libres APRÈS le téléchargement (l'utilisateur, 19/10)
SIZES_TTL_S = 86_400.0
TAGS_TTL_S = 3.0
VERSION_VUE_S = 5.0  # sonde « Ollama répond ? » des vues de la page (03/11)
INSTALL_URL = "https://ollama.com/download"
INSTALL_LINUX = "curl -fsSL https://ollama.com/install.sh | sh"
PLACES = {"gpu": "rapide (carte graphique)", "mixte": "mixte, plus lent (carte et mémoire vive)",
          "cpu": "lent (processeur seul)"}


@dataclass(frozen=True)
class Modele:
    tag: str
    famille: str
    ref_octets: int    # taille lue au manifeste le 19/10/2026 : repli hors ligne
    note: str = ""


# Petits ET gros (demande de l'utilisateur) ; seuls ceux qui tiennent sur la machine sont montrés.
# Tailles mesurées au manifeste du registre le 19/10/2026.
CATALOGUE: tuple[Modele, ...] = (
    Modele("qwen3.5:0.8b", "Qwen 3.5", 1_320_000_000, "minuscule, réponses simples"),
    Modele("llama3.2:1b", "Llama 3.2", 1_320_000_000, "minuscule"),
    Modele("llama3.2:3b", "Llama 3.2", 2_020_000_000, "petit et rapide"),
    Modele("granite4.1:3b", "Granite 4.1", 2_100_000_000, "petit"),
    Modele("qwen3.5:2b", "Qwen 3.5", 2_680_000_000, "petit"),
    Modele("ministral-3:3b", "Ministral 3", 2_950_000_000, "petit, bon en français"),
    Modele("qwen3.5:4b", "Qwen 3.5", 3_320_000_000, "bon compromis"),
    Modele("gemma4:e2b", "Gemma 4", 4_590_000_000, "compact"),
    Modele("granite4.1:8b", "Granite 4.1", 5_350_000_000, "moyen"),
    Modele("ministral-3:8b", "Ministral 3", 6_020_000_000, "moyen, bon en français"),
    Modele("qwen3.5:9b", "Qwen 3.5", 6_550_000_000, "moyen, réfléchit avant de répondre"),
    Modele("gemma4:e4b", "Gemma 4", 6_580_000_000, "moyen"),
    Modele("ministral-3:14b", "Ministral 3", 9_080_000_000, "grand, bon en français"),
    Modele("gpt-oss:20b", "GPT-OSS", 13_790_000_000, "grand, raisonne"),
    Modele("mistral-small3.2:24b", "Mistral Small 3.2", 15_180_000_000, "grand, bon en français"),
    Modele("qwen3.5:27b", "Qwen 3.5", 17_480_000_000, "grand"),
    Modele("gemma4:26b", "Gemma 4", 18_730_000_000, "grand"),
    Modele("gemma4:31b", "Gemma 4", 20_390_000_000, "très grand"),
    Modele("qwen3.5:35b", "Qwen 3.5", 22_070_000_000, "très grand"),
    Modele("llama3.3:70b", "Llama 3.3", 42_520_000_000, "énorme"),
    Modele("gpt-oss:120b", "GPT-OSS", 65_370_000_000, "énorme"),
    Modele("qwen3.5:122b", "Qwen 3.5", 81_370_000_000, "énorme"),
)
BY_TAG = {m.tag: m for m in CATALOGUE}


def go(octets: float) -> float:
    return octets / GO


def fmt_go(value: float) -> str:
    """« 3,1 Go » (virgule française)."""
    return f"{value:.1f}".replace(".", ",") + " Go"


def place(size_go: float, mat: materiel.Materiel) -> str:
    """« gpu », « mixte », « cpu », ou vide : ne tient pas (jamais proposé)."""
    need = size_go + MARGE_GO
    if mat.vram_go > 0 and need <= mat.vram_go - RESERVE_VRAM_GO:
        return "gpu"
    if need <= mat.ram_go - RESERVE_RAM_GO:
        return "mixte" if mat.vram_go > 0 else "cpu"
    return ""


def disk_need_go(size_go: float, total_go: float = 0.0) -> float:
    """Place libre exigée AVANT de télécharger un modèle de `size_go` sur un disque de `total_go`."""
    keep = max(DISQUE_RESTE_GO, DISQUE_RESTE_PART * total_go)
    return max(size_go * DISQUE_FACTEUR + DISQUE_MARGE_GO, size_go + keep)


def disk_refusal(need_go: float, mat: materiel.Materiel) -> str:
    """Phrase du refus pour manque de place (vide si la place suffit)."""
    if mat.disque_go >= need_go:
        return ""
    return (f"pas assez de place sur le disque des modèles ({mat.dossier}) : il faut {fmt_go(need_go)} libres pour "
            f"en garder assez après le téléchargement, il en reste {fmt_go(mat.disque_go)}. Libère de la place, "
            f"ou range les modèles d'Ollama sur un disque plus grand (variable OLLAMA_MODELS, puis relance Ollama).")


# --------------------------------------------------------------------------- client Ollama (HTTP local)

def _http():
    import httpx  # importé au premier usage : le cœur démarre sans payer l'import

    return httpx


def ollama_version(timeout: float = 1.0) -> str:
    """Version d'Ollama s'il répond, vide sinon (absent ou arrêté)."""
    try:
        r = _http().get(config.OLLAMA_URL + "/api/version", timeout=timeout)
        return str(r.json().get("version") or "?") if r.status_code == 200 else ""
    except Exception:
        return ""


def ollama_models(timeout: float = 1.5) -> dict[str, int] | None:
    """{nom: octets} des modèles installés ; None si Ollama ne répond pas."""
    try:
        r = _http().get(config.OLLAMA_URL + "/api/tags", timeout=timeout)
        if r.status_code != 200:
            return None
        out = {}
        for m in r.json().get("models") or []:
            if isinstance(m, dict) and m.get("name"):
                out[str(m["name"])] = int(m.get("size") or 0)
        return out
    except Exception:
        return None


def ollama_installed() -> bool:
    """Le programme Ollama est-il présent (même arrêté) ?"""
    if shutil.which("ollama"):
        return True
    if sys.platform == "darwin":
        return Path("/Applications/Ollama.app").exists()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", "")
        return bool(base) and (Path(base) / "Programs" / "Ollama" / "ollama.exe").exists()
    return False


def manifest_size(tag: str, timeout: float = 10.0) -> int:
    """Taille exacte d'un modèle au registre (somme des couches), -1 si le tag n'existe pas.
    Lève sur une erreur réseau."""
    if not config.OLLAMA_REGISTRY:
        raise RuntimeError("registre désactivé")
    hors_ligne.verifier("registre d'Ollama")  # 04/11
    name, _, version = tag.partition(":")
    if "/" not in name:
        name = f"library/{name}"
    r = _http().get(f"{config.OLLAMA_REGISTRY}/v2/{name}/manifests/{version or 'latest'}", timeout=timeout,
                    headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"})
    if r.status_code == 404:
        return -1
    r.raise_for_status()
    data = r.json()
    layers = data.get("layers") or []
    return sum(int(x.get("size") or 0) for x in layers) + int((data.get("config") or {}).get("size") or 0)


def pull(tag: str, on_progress: Callable[[int, int, str], None], stop: threading.Event) -> bool:
    """`/api/pull` en flux. Faux si annulé (`stop`), lève sur une erreur d'Ollama."""
    hors_ligne.verifier("téléchargement du modèle")  # 04/11 : Ollama irait le chercher sur Internet
    httpx = _http()
    totals: dict[str, int] = {}
    done: dict[str, int] = {}
    with httpx.stream("POST", config.OLLAMA_URL + "/api/pull", json={"model": tag, "stream": True},
                      timeout=httpx.Timeout(10.0, read=120.0)) as r:
        if r.status_code != 200:
            raise RuntimeError(f"Ollama a refusé ({r.status_code}) : {r.read().decode(errors='replace')[:200]}")
        for line in r.iter_lines():
            if stop.is_set():
                return False  # la connexion fermée arrête le téléchargement côté Ollama
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("error"):
                raise RuntimeError(str(ev["error"])[:300])
            digest = str(ev.get("digest") or "")
            if digest and ev.get("total"):
                totals[digest] = int(ev["total"])
                done[digest] = int(ev.get("completed") or 0)
            on_progress(sum(done.values()), sum(totals.values()), str(ev.get("status") or ""))
            if ev.get("status") == "success":
                return True
    if stop.is_set():
        return False
    raise RuntimeError("téléchargement interrompu par Ollama")


def delete(tag: str) -> None:
    r = _http().request("DELETE", config.OLLAMA_URL + "/api/delete", json={"model": tag}, timeout=30.0)
    if r.status_code not in (200, 404):
        raise RuntimeError(f"Ollama a refusé la suppression ({r.status_code}) : {r.text[:200]}")


def derived_name(tag: str) -> str:
    return "astrel-" + tag.replace(":", "-").replace("/", "-")


def create_derived(tag: str, num_ctx: int) -> str:
    """Modèle dérivé avec un contexte plus long (`/api/create`, sans copier les poids).
    Lève si Ollama refuse (vu ici le 19/10 : « chtimes … operation not permitted »)."""
    name = derived_name(tag)
    r = _http().post(config.OLLAMA_URL + "/api/create", timeout=120.0,
                     json={"model": name, "from": tag, "parameters": {"num_ctx": int(num_ctx)}, "stream": False})
    if r.status_code != 200 or (r.headers.get("content-type", "").startswith("application/json")
                                and r.json().get("error")):
        raise RuntimeError(r.text[:200])
    return name


# --------------------------------------------------------------------------- état et actions

class ModelesLocaux:
    """Catalogue filtré par le matériel, téléchargement, modèle choisi, mode léger.
    `runtime/local-model.json` : {model (choisi), active (mode léger en cours), derived, active_tag,
    return_at (retour prévu sur Claude, epoch s), frozen (messages gelés en mode léger),
    offer_for (fenêtre de quota déjà proposée, clé `resets_at`)}."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sizes: dict[str, int] = {}
        self._sizes_at = 0.0
        self._sizes_busy = False
        self._installed: tuple[float, dict[str, int] | None] = (0.0, None)
        self._version: tuple[float, str, str] = (0.0, "", "")  # (quand, adresse, version) : vues seulement
        self._dl: dict[str, Any] | None = None      # téléchargement en cours ou dernier résultat
        self._stop = threading.Event()
        self._last_push = 0.0
        self.on_change: Callable[[], None] | None = None

    # --- fichier

    def state(self) -> dict:
        try:
            data = json.loads(config.LOCAL_MODEL_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, **changes: Any) -> None:
        with self._lock:
            data = {**self.state(), **changes}
            path = config.LOCAL_MODEL_FILE
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)

    @property
    def chosen(self) -> str:
        return str(self.state().get("model") or "")

    @property
    def active(self) -> bool:
        return bool(self.state().get("active"))

    @property
    def active_tag(self) -> str:
        """Modèle du mode léger EN COURS (le choix peut changer entre-temps : pris à la prochaine bascule)."""
        return str(self.state().get("active_tag") or "") if self.active else ""

    def set_active(self, on: bool, derived: str = "", tag: str = "", return_at: float = 0.0) -> None:
        self._save(active=bool(on), derived=derived if on else "", active_tag=(tag or derived) if on else "",
                   return_at=float(return_at) if on else 0.0)

    @property
    def return_at(self) -> float:
        """Heure (epoch s) du retour automatique sur Claude ; 0 si aucun n'est prévu."""
        v = self.state().get("return_at") if self.active else 0
        return float(v) if isinstance(v, (int, float)) else 0.0

    def remember(self, **changes: Any) -> None:
        """Retient d'une relance à l'autre un champ du cœur (`frozen`, `offer_for`, `return_at`)."""
        self._save(**changes)

    # --- Ollama

    def installed(self, fresh: bool = False) -> dict[str, int] | None:
        with self._lock:
            at, value = self._installed
            if not fresh and time.monotonic() - at < TAGS_TTL_S:
                return value
        value = ollama_models()
        with self._lock:
            self._installed = (time.monotonic(), value)
        return value

    def size_go(self, tag: str) -> tuple[float, bool]:
        """(taille en Go, lue au manifeste ?) ; un modèle installé hors catalogue : sa taille d'Ollama."""
        with self._lock:
            exact = self._sizes.get(tag, 0)
        if exact > 0:
            return go(exact), True
        model = BY_TAG.get(tag)
        if model:
            return go(model.ref_octets), False
        local = (self.installed() or {}).get(tag, 0)
        return go(local), bool(local)

    def _refresh_sizes(self) -> None:
        """Tailles lues au manifeste, en fond (jamais pendant le dessin de la page)."""
        with self._lock:
            if (self._sizes_busy or not config.OLLAMA_REGISTRY or time.time() - self._sizes_at < SIZES_TTL_S
                    or hors_ligne.actif()):
                return
            self._sizes_busy = True

        def work() -> None:
            sizes: dict[str, int] = {}
            try:
                for m in CATALOGUE:
                    try:
                        n = manifest_size(m.tag)
                    except Exception as exc:
                        log.info("manifeste de %s illisible : %s", m.tag, exc)
                        continue
                    if n > 0:
                        sizes[m.tag] = n
                    else:
                        log.warning("modèle %s absent du registre : retiré des propositions", m.tag)
                        sizes[m.tag] = -1
            finally:
                with self._lock:
                    self._sizes.update(sizes)
                    # Hors ligne : nouvel essai dans 10 min, pas à chaque dessin de la page.
                    self._sizes_at = time.time() if sizes else time.time() - SIZES_TTL_S + 600
                    self._sizes_busy = False
            if sizes:
                self._changed(force=True)

        threading.Thread(target=work, name="ollama-manifestes", daemon=True).start()

    def proposals(self, mat: materiel.Materiel | None = None) -> tuple[list[dict], int]:
        """Modèles qui TIENNENT sur la machine (catalogue + installés hors catalogue), du plus
        petit au plus gros, et le nombre de modèles du catalogue écartés (trop gros)."""
        mat = mat or materiel.detect()
        installed = self.installed() or {}
        out, hidden = [], 0
        tags = [m.tag for m in CATALOGUE] + [t for t in installed if t not in BY_TAG and not t.startswith("astrel-")]
        for tag in tags:
            with self._lock:
                if self._sizes.get(tag) == -1:
                    continue  # absent du registre
            size, exact = self.size_go(tag)
            if size <= 0:
                continue
            where = place(size, mat)
            if not where:
                hidden += tag in BY_TAG
                continue
            need = disk_need_go(size, mat.disque_total_go)
            model = BY_TAG.get(tag)
            out.append({"tag": tag, "famille": model.famille if model else "installé à part",
                        "note": model.note if model else "", "size_go": size, "exact": exact,
                        "place": where, "installed": tag in installed, "disk_need_go": need,
                        "disk_ok": mat.disque_go >= need})
        out.sort(key=lambda p: p["size_go"])
        return out, hidden

    def fits(self, tag: str, mat: materiel.Materiel | None = None) -> bool:
        return any(p["tag"] == tag for p in self.proposals(mat)[0])

    def ready(self) -> str:
        """Le modèle choisi, s'il est installé, tient sur la machine et qu'Ollama répond ; vide sinon."""
        tag = self.chosen
        if not tag or tag not in (self.installed() or {}) or not self.fits(tag):
            return ""
        return tag

    def prepare(self, tag: str) -> str:
        """Nom à donner au cerveau : le modèle dérivé à contexte long, sinon le modèle tel quel."""
        try:
            name = create_derived(tag, config.LOCAL_NUM_CTX)
        except Exception as exc:
            log.warning("modèle dérivé de %s impossible (%s) : contexte par défaut d'Ollama", tag, exc)
            return tag
        return name

    # --- téléchargement

    def downloading(self) -> str:
        with self._lock:
            return str(self._dl["tag"]) if self._dl and self._dl.get("status") == "en cours" else ""

    def download(self, tag: str) -> dict:
        if hors_ligne.actif():  # 04/11 : refusé net, avant même de demander à Ollama
            return {"ok": False, "error": f"pas de téléchargement : {hors_ligne.REFUS}"}
        if not ollama_version():
            return {"ok": False, "error": "Ollama ne répond pas : installe-le ou lance-le d'abord."}
        mat = materiel.detect(fresh=True)
        proposal = next((p for p in self.proposals(mat)[0] if p["tag"] == tag), None)
        if proposal is None:
            return {"ok": False, "error": f"{tag} ne tient pas sur cette machine : jamais téléchargé."}
        if proposal["installed"]:
            return {"ok": True, "text": f"{tag} est déjà installé."}
        refusal = disk_refusal(proposal["disk_need_go"], mat)
        if refusal:
            return {"ok": False, "error": refusal}
        with self._lock:
            if self.downloading():
                return {"ok": False, "error": f"un téléchargement est déjà en cours ({self.downloading()})."}
            self._stop = threading.Event()
            self._dl = {"tag": tag, "status": "en cours", "done": 0, "total": int(proposal["size_go"] * GO),
                        "step": "", "error": ""}
            stop = self._stop
        threading.Thread(target=self._pull, args=(tag, stop), name="ollama-pull", daemon=True).start()
        log.info("téléchargement du modèle local %s", tag)
        return {"ok": True, "text": f"Téléchargement de {tag} lancé ({fmt_go(proposal['size_go'])})."}

    def _pull(self, tag: str, stop: threading.Event) -> None:
        def progress(done: int, total: int, step: str) -> None:
            with self._lock:
                if self._dl and self._dl["tag"] == tag:
                    self._dl.update(done=done, total=total or self._dl["total"], step=step)
            self._changed()

        try:
            finished = pull(tag, progress, stop)
            status, error = ("fini", "") if finished else ("annulé", "")
        except Exception as exc:
            if stop.is_set():
                # Annulé pendant une lecture bloquée (réseau coupé) : l'erreur vient de l'arrêt.
                log.info("téléchargement de %s annulé (%s)", tag, exc)
                status, error = "annulé", ""
            else:
                log.warning("téléchargement de %s : %s", tag, exc)
                status, error = "échec", str(exc) or type(exc).__name__
        if status == "fini":
            log.info("modèle local %s installé", tag)
            # 04/11 : choisi AVANT d'afficher « fini » (sinon la page voyait un modèle fini et non choisi)
            if not self.chosen:
                self._save(model=tag)  # le premier modèle installé devient celui du mode léger
        with self._lock:
            if self._dl and self._dl["tag"] == tag:
                self._dl.update(status=status, error=error)
            self._installed = (0.0, None)
        materiel.forget()
        self._changed(force=True)

    def cancel(self) -> dict:
        tag = self.downloading()
        if not tag:
            return {"ok": True, "text": "Aucun téléchargement en cours."}
        self._stop.set()
        return {"ok": True, "text": f"Téléchargement de {tag} annulé (Ollama garde la partie déjà reçue)."}

    def remove(self, tag: str) -> dict:
        if tag not in (self.installed(fresh=True) or {}):
            return {"ok": False, "error": f"{tag} n'est pas installé."}
        if self.active and tag in (self.active_tag, self.chosen):
            return {"ok": False, "error": "c'est le modèle du mode léger en cours : reviens d'abord sur Claude."}
        delete(tag)
        try:
            delete(derived_name(tag))
        except Exception:
            pass
        if self.chosen == tag:
            self._save(model="")
        with self._lock:
            self._installed = (0.0, None)
        materiel.forget()
        log.info("modèle local %s supprimé", tag)
        return {"ok": True, "text": f"{tag} supprimé."}

    def choose(self, tag: str) -> dict:
        if tag not in (self.installed(fresh=True) or {}):
            return {"ok": False, "error": f"{tag} n'est pas installé."}
        if not self.fits(tag):
            return {"ok": False, "error": f"{tag} ne tient pas sur cette machine."}
        self._save(model=tag)
        return {"ok": True, "text": f"{tag} sera le modèle du mode léger."}

    def _changed(self, force: bool = False) -> None:
        """Vue republiée au plus une fois par seconde pendant un téléchargement."""
        now = time.monotonic()
        if not force and now - self._last_push < 1.0:
            return
        self._last_push = now
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                log.exception("republication des réglages")

    # --- vues de la page « Réglages » (groupe « Modèle local »)

    def _version_vue(self) -> str:
        """`ollama_version` pour les vues de la page (deux par page) : une sonde par `VERSION_VUE_S` au plus.
        03/11 (CI Windows) : un port local fermé n'y est « refusé » qu'au bout du délai (SYN réessayés), soit
        1 s par sonde ; Ollama absent, chaque page des Réglages coûtait 2 s. « Relire » oublie la valeur."""
        now, url = time.monotonic(), config.OLLAMA_URL
        with self._lock:
            at, seen_url, version = self._version
            if seen_url == url and at and now - at < VERSION_VUE_S:
                return version
        version = ollama_version()
        with self._lock:
            self._version = (time.monotonic(), url, version)
        return version

    def materiel_view(self) -> dict:
        mat = materiel.detect()
        lines = []
        if mat.faux:
            lines.append({"label": "Profil", "text": "factice (ASTREL_MATERIEL_FAKE)", "level": "warn"})
        if mat.gpu:
            kind = "mémoire unifiée utilisable" if mat.unifiee else "mémoire graphique"
            lines.append({"label": "Carte graphique", "text": f"{mat.gpu}, {fmt_go(mat.vram_go)} de {kind}",
                          "level": "ok"})
        else:
            lines.append({"label": "Carte graphique", "level": "info",
                          "text": "aucune reconnue : les modèles tourneront sur le processeur, plus lentement"})
        lines.append({"label": "Mémoire vive", "text": fmt_go(mat.ram_go), "level": "info"})
        lines.append({"label": "Disque des modèles", "text": f"{fmt_go(mat.disque_go)} libres ({mat.dossier})",
                      "level": "info" if mat.disque_go >= 10 else "warn"})
        if mat.wsl:
            lines.append({"label": "WSL", "level": "info",
                          "text": "Ollama installé dans Windows ou dans WSL : les deux marchent s'il répond ici"})
        version = self._version_vue()
        out: dict[str, Any] = {}
        if version:
            lines.append({"label": "Ollama", "text": f"version {version}, en marche", "level": "ok"})
        elif ollama_installed():
            lines.append({"label": "Ollama", "level": "warn",
                          "text": "installé mais arrêté : lance l'application Ollama (ou « ollama serve »)"})
            out["block"] = "ollama serve"
        else:
            lines.append({"label": "Ollama", "level": "warn",
                          "text": "pas installé. Astrel ne l'installe pas lui-même : télécharge-le sur le site "
                                  "officiel" + (", ou colle la commande ci-dessous dans un terminal"
                                                if mat.systeme == "linux" else "") + "."})
            out["link"] = {"url": INSTALL_URL, "label": "Télécharger Ollama (site officiel)"}
            if mat.systeme == "linux":
                out["block"] = INSTALL_LINUX
        return {"value": {"lines": lines, **out}, "actions": [{"id": "refresh", "label": "Relire"}]}

    def models_view(self) -> dict:
        self._refresh_sizes()
        mat = materiel.detect()
        running = bool(self._version_vue())
        props, hidden = self.proposals(mat)
        chosen, active = self.chosen, self.active
        lines, actions = [], []
        with self._lock:
            dl = dict(self._dl) if self._dl else None
        busy = bool(dl and dl["status"] == "en cours")
        if dl:
            if busy:
                pct = f" ({dl['done'] * 100 // dl['total']} %)" if dl.get("total") else ""
                lines.append({"label": "Téléchargement", "level": "info",
                              "text": f"{dl['tag']} : {fmt_go(go(dl['done']))} sur {fmt_go(go(dl['total']))}{pct}"})
                actions.append({"id": "cancel", "label": "Annuler le téléchargement"})
            elif dl["status"] == "fini":
                lines.append({"label": "Téléchargement", "text": f"{dl['tag']} installé", "level": "ok"})
            elif dl["status"] == "annulé":
                lines.append({"label": "Téléchargement", "text": f"{dl['tag']} annulé", "level": "info"})
            else:
                # Vu ici le 19/10 : le service `ollama` n'avait pas le droit d'écrire dans son dossier.
                hint = (" : le service Ollama n'a pas le droit d'écrire dans le dossier des modèles "
                        f"({mat.dossier}), droits à corriger" if "permission denied" in dl["error"].lower()
                        or "operation not permitted" in dl["error"].lower() else "")
                lines.append({"label": "Téléchargement", "text": f"{dl['tag']} : échec ({dl['error']}){hint}",
                              "level": "error"})
        if not props:
            lines.append({"label": "Modèles", "level": "warn",
                          "text": "aucun modèle du catalogue ne tient sur cette machine (mémoire trop juste)"})
        for p in props:
            state = []
            if p["installed"]:
                state.append("installé")
            if p["tag"] == chosen:
                state.append("EN COURS (mode léger)" if active else "choisi pour le mode léger")
            if not p["installed"] and not p["disk_ok"]:
                state.append(f"place insuffisante (il faut {fmt_go(p['disk_need_go'])})")
            size = fmt_go(p["size_go"]) + ("" if p["exact"] else " estimés")
            note = f", {p['note']}" if p["note"] else ""
            lines.append({"label": p["tag"], "level": "ok" if p["tag"] == chosen else "info",
                          "text": f"{p['famille']}{note} · {size} · {PLACES[p['place']]}"
                                  + (" · " + ", ".join(state) if state else "")})
            if not p["installed"]:
                act = {"id": f"pull:{p['tag']}", "label": f"Télécharger {p['tag']} ({fmt_go(p['size_go'])})"}
                why = ("Ollama ne répond pas" if not running else "un téléchargement est en cours" if busy else
                       "" if p["disk_ok"] else f"il faut {fmt_go(p['disk_need_go'])} libres sur le disque des "
                                               f"modèles, il en reste {fmt_go(mat.disque_go)} : change le dossier "
                                               f"des modèles (OLLAMA_MODELS) ou libère de la place")
                if why:
                    act.update(disabled=True, reason=why)
                actions.append(act)
                continue
            if p["tag"] != chosen:
                actions.append({"id": f"choose:{p['tag']}", "label": f"Choisir {p['tag']}"})
            rm = {"id": f"delete:{p['tag']}", "label": f"Supprimer {p['tag']}", "danger": True, "word": "supprimer",
                  "confirm": f"{p['tag']} sera effacé du disque ({fmt_go(p['size_go'])} libérés)."}
            if active and p["tag"] in (chosen, self.active_tag):
                rm.update(disabled=True, reason="mode léger en cours")
            actions.append(rm)
        if hidden:
            lines.append({"label": "Non proposés", "level": "info",
                          "text": f"{hidden} modèle(s) du catalogue trop gros pour la mémoire de cette machine"})
        return {"value": {"lines": lines}, "actions": actions}

    def run(self, action: str, cmd: dict) -> dict:
        verb, _, tag = action.partition(":")
        if verb == "refresh":
            materiel.forget()
            with self._lock:
                self._installed, self._version = (0.0, None), (0.0, "", "")
            return {"ok": True}
        if verb == "pull":
            return self.download(tag)
        if verb == "cancel":
            return self.cancel()
        if verb == "delete":
            return self.remove(tag)
        if verb == "choose":
            return self.choose(tag)
        return {"ok": False, "error": f"action inconnue : {action}"}


local = ModelesLocaux()
