"""Capture d'écran sous GNOME Wayland, par le portail de PARTAGE D'ÉCRAN.

Sous Wayland, une application ne peut pas lire l'écran d'elle-même : `mss` et
`pyautogui` rendent une image noire. Deux portails existent :

- `Screenshot` : GNOME REFUSE la capture silencieuse (code 2) aux applications
  hors bac à sable, sauf à l'autoriser pour TOUTES les applications de la
  session (entrée « '' » du magasin de permissions). Écarté : ce serait ouvrir
  l'écran de l'utilisateur à n'importe quel programme ;
- `ScreenCast` (retenu) : le chemin d'OBS ou de Meet. La PREMIÈRE fois, GNOME
  demande quel écran partager ; l'utilisateur coche « se souvenir », et le portail
  rend un jeton de restauration propre à Astrel (`runtime/screencast-token`,
  0600). Les captures suivantes se font sans fenêtre. Révocable à tout moment
  en supprimant ce fichier.

Une image est tirée du flux PipeWire par GStreamer (`pipewiresrc` → PNG), puis
la session est refermée : l'indicateur de partage de GNOME ne reste pas allumé.

Processus à part (`python -m astrel.tools.screenshot`) : le portail répond par
signaux D-Bus, qui exigent une boucle GLib. Imprime le chemin du PNG.

Code d'origine de `astrel/tools/screenshot.py`, extrait sans changement (P2,
lot 4) ; `screenshot.py` choisit ce moteur sous Linux.

PLUSIEURS ÉCRANS (22/10, plan P24, demande de l'utilisateur) : le portail est appelé
avec `multiple: True` ; chaque flux rendu est rapproché d'un écran de GNOME
(`astrel/ecrans.py` : position du flux, sinon sa taille, sinon celle de l'image)
et seul le flux de l'écran voulu est tiré (« tous » : images assemblées comme sur
le bureau). Un écran que le jeton ne couvre pas donne une erreur claire quand il
est demandé par son nom ; `autoriser=True` ignore le jeton et rouvre le dialogue
de GNOME (l'utilisateur y coche tous les écrans), jamais depuis l'outil MCP (délai de
45 s trop court pour répondre au dialogue).
NON ESSAYÉ en réel au 22/10 (il ouvrirait le dialogue chez l'utilisateur) : le
comportement du portail avec `multiple` et un jeton déjà accordé, et la présence
des propriétés `position`/`size` dans les flux. Repli : taille de l'image.
"""
from __future__ import annotations

import os
import secrets
import struct
import sys
import tempfile
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gio, GLib, Gst  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from astrel import config, ecrans  # noqa: E402

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
CAST = "org.freedesktop.portal.ScreenCast"
TOKEN_FILE = config.RUNTIME / "screencast-token"

SOURCE_MONITOR = 1
CURSOR_EMBEDDED = 2
PERSIST_UNTIL_REVOKED = 2


class Portal:
    def __init__(self) -> None:
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.sender = self.bus.get_unique_name().lstrip(":").replace(".", "_")

    def request(self, method: str, args: tuple, signature: str, timeout_s: int = 120) -> dict:
        """Appelle une méthode du portail et attend sa réponse (signal Response)."""
        token = f"astrel{secrets.token_hex(6)}"
        path = f"{PORTAL_PATH}/request/{self.sender}/{token}"
        loop = GLib.MainLoop()
        result: dict = {}

        def on_response(_c, _s, _p, _i, _sig, params):
            code, results = params.unpack()
            result.update(code=code, results=results)
            loop.quit()

        sub = self.bus.signal_subscribe(PORTAL, "org.freedesktop.portal.Request", "Response", path,
                                        None, Gio.DBusSignalFlags.NO_MATCH_RULE, on_response)
        # Le jeton de requête va dans le dictionnaire d'options, dernier argument.
        *head, options = args
        options = {**options, "handle_token": GLib.Variant("s", token)}
        self.bus.call_sync(PORTAL, PORTAL_PATH, CAST, method, GLib.Variant(signature, (*head, options)),
                           None, Gio.DBusCallFlags.NONE, -1, None)
        timer = GLib.timeout_add_seconds(timeout_s, loop.quit)
        loop.run()
        GLib.source_remove(timer) if result else None
        self.bus.signal_unsubscribe(sub)
        if result.get("code") != 0:
            reasons = {1: "partage refusé par l'utilisateur", 2: "partage impossible"}
            raise RuntimeError(reasons.get(result.get("code"), "le portail n'a pas répondu"))
        return result["results"]

    def open_remote(self, session: str) -> int:
        """Descripteur de la connexion PipeWire de la session (à fermer par l'appelant)."""
        ret, fds = self.bus.call_with_unix_fd_list_sync(
            PORTAL, PORTAL_PATH, CAST, "OpenPipeWireRemote", GLib.Variant("(oa{sv})", (session, {})),
            GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE, -1, None, None,
        )
        return fds.get(ret.unpack()[0])

    def close(self, session: str) -> None:
        try:
            self.bus.call_sync(PORTAL, session, "org.freedesktop.portal.Session", "Close",
                               None, None, Gio.DBusCallFlags.NONE, -1, None)
        except GLib.Error:
            pass


def _read_token() -> str:
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _save_token(token: str) -> None:
    if not token:
        return
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)


def _grab_png(fd: int, node: int, timeout_s: float = 6.0) -> bytes:
    Gst.init(None)
    pipeline = Gst.parse_launch(
        f"pipewiresrc fd={fd} path={node} always-copy=true do-timestamp=true "
        "! videoconvert ! video/x-raw,format=RGB ! pngenc snapshot=false "
        "! appsink name=sink max-buffers=3 drop=true sync=false"
    )
    sink = pipeline.get_by_name("sink")
    pipeline.set_state(Gst.State.PLAYING)
    try:
        data = b""
        # La première image d'un flux qui démarre peut être noire : on en prend deux.
        for _ in range(2):
            sample = sink.emit("try-pull-sample", int(timeout_s * Gst.SECOND))
            if sample is None:
                break
            buf = sample.get_buffer()
            ok, info = buf.map(Gst.MapFlags.READ)
            if ok:
                data = bytes(info.data)
                buf.unmap(info)
        if not data:
            raise RuntimeError("le flux d'écran n'a rendu aucune image")
        return data
    finally:
        pipeline.set_state(Gst.State.NULL)


AUTORISER = ("L'utilisateur doit l'autoriser une fois : « .venv/bin/python -m astrel.tools.screenshot --autoriser » "
             "dans son terminal, puis cocher TOUS les écrans dans le dialogue de GNOME")


def _flux(started: dict) -> list[dict]:
    """Flux rendus par Start : [{"node", "position"?, "size"?}] (propriétés du portail, version 4+)."""
    out = []
    for node, props in started.get("streams") or []:
        f: dict = {"node": int(node)}
        for cle in ("position", "size"):
            val = (props or {}).get(cle)
            if isinstance(val, (tuple, list)) and len(val) == 2:
                f[cle] = (int(val[0]), int(val[1]))
        out.append(f)
    return out


def _png_taille(png: bytes) -> tuple[int, int]:
    """Largeur et hauteur lues dans l'en-tête IHDR d'un PNG."""
    if len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n":
        return (0, 0)
    return struct.unpack(">II", png[16:24])


def assembler(morceaux: list[tuple[bytes, dict | None]]) -> bytes:
    """« tous » : une image par écran → UNE image, chaque écran à sa place sur le bureau
    (taille logique) ; côte à côte si un écran n'est pas connu."""
    import cv2
    import numpy as np

    imgs = []
    for png, e in morceaux:
        img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("image d'écran illisible")
        imgs.append((img, e))
    if all(e for _, e in imgs):
        vides = [f"{e['nom']} ({e['id']})" for _, e in imgs if e["w"] <= 0 or e["h"] <= 0]
        if vides:  # mode en cours absent chez Mutter (taille 0) : cv2.resize planterait
            raise RuntimeError(f"écran sans mode en cours, assemblage impossible : {', '.join(vides)}")
        x0, y0 = min(e["x"] for _, e in imgs), min(e["y"] for _, e in imgs)
        largeur = max(e["x"] + e["w"] for _, e in imgs) - x0
        hauteur = max(e["y"] + e["h"] for _, e in imgs) - y0
        toile = np.zeros((hauteur, largeur, 3), np.uint8)
        for img, e in imgs:
            if (img.shape[1], img.shape[0]) != (e["w"], e["h"]):
                img = cv2.resize(img, (e["w"], e["h"]), interpolation=cv2.INTER_AREA)
            x, y = e["x"] - x0, e["y"] - y0
            toile[y:y + e["h"], x:x + e["w"]] = img
    else:
        toile = np.zeros((max(i.shape[0] for i, _ in imgs), sum(i.shape[1] for i, _ in imgs), 3), np.uint8)
        x = 0
        for img, _ in imgs:
            toile[:img.shape[0], x:x + img.shape[1]] = img
            x += img.shape[1]
    ok, buf = cv2.imencode(".png", toile)
    if not ok:
        raise RuntimeError("assemblage des écrans impossible")
    return buf.tobytes()


def _tirer(portal: Portal, session: str, node: int) -> bytes:
    fd = portal.open_remote(session)
    try:
        return _grab_png(fd, node)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def capture(ecran: str = "", autoriser: bool = False, explicite: bool | None = None) -> tuple[Path, str]:
    """Capture l'écran `ecran` (nom accepté par `ecrans.choisir` ; vide = principal) et rend
    (chemin du PNG, légende d'une ligne). `explicite` faux (écran par défaut, WATCH ; par défaut :
    vrai si `ecran` est donné) : un écran non couvert par le jeton est remplacé par un écran
    couvert, avec une remarque, au lieu d'une erreur."""
    if explicite is None:
        explicite = bool(ecran.strip())
    liste = ecrans.lister()
    choix = ecrans.choisir(liste, ecran)
    remarques = list(choix.remarques)
    portal = Portal()
    res = portal.request("CreateSession", ({"session_handle_token": GLib.Variant("s", f"astrel{secrets.token_hex(4)}")},),
                         "(a{sv})")
    session = res["session_handle"]
    try:
        options = {
            "types": GLib.Variant("u", SOURCE_MONITOR),
            "multiple": GLib.Variant("b", True),
            "cursor_mode": GLib.Variant("u", CURSOR_EMBEDDED),
            "persist_mode": GLib.Variant("u", PERSIST_UNTIL_REVOKED),
        }
        saved = "" if autoriser else _read_token()
        if saved:
            options["restore_token"] = GLib.Variant("s", saved)
        portal.request("SelectSources", (session, options), "(oa{sv})")
        started = portal.request("Start", (session, "", {}), "(osa{sv})", timeout_s=180)
        _save_token(started.get("restore_token", ""))
        flux = _flux(started)
        if not flux:
            raise RuntimeError("aucun écran partagé")
        images: dict[int, bytes] = {}

        def tirer(i: int) -> bytes:
            if i not in images:
                images[i] = _tirer(portal, session, flux[i]["node"])
            return images[i]

        if not liste:
            # Écrans de GNOME illisibles : comme avant le 22/10, le premier flux partagé.
            png = tirer(0)
            legende = "Écran vu : le premier écran partagé (liste des écrans de GNOME illisible)."
            if len(flux) > 1:
                legende += f" {len(flux)} écrans sont partagés."
        else:
            vus, indices = _selection(liste, choix, flux, tirer, remarques, explicite)
            if len(indices) > 1:
                png = assembler([(tirer(i), e) for i, e in zip(indices, vus)])
            else:
                png = tirer(indices[0])
            legende = ecrans.legende(vus, liste, remarques) or "Écran vu : non identifié."
    finally:
        portal.close(session)
    dest = Path(tempfile.mkstemp(prefix="astrel-ecran-", suffix=".png")[1])
    dest.write_bytes(png)
    return dest, legende


def _selection(liste: list[dict], choix: ecrans.Choix, flux: list[dict], tirer, remarques: list[str],
               explicite: bool) -> tuple[list[dict], list[int]]:
    """(écrans montrés, indices de leurs flux). Ajoute les remarques ; lève si l'écran demandé
    par son nom n'est pas partagé."""
    assoc = ecrans.associer_flux(flux, liste)
    if any(e["id"] not in assoc for e in choix.cibles) and len(assoc) < len(flux):
        # Repli du plan P24 : un flux sans position reconnue est identifié par la taille de son image.
        for i, f in enumerate(flux):
            if i not in assoc.values():
                f["pixels"] = _png_taille(tirer(i))
        assoc = ecrans.associer_flux(flux, liste)
    inconnus = [i for i in range(len(flux)) if i not in assoc.values()]
    couverts = [e for e in choix.cibles if e["id"] in assoc]
    manquants = [e for e in choix.cibles if e["id"] not in assoc]
    noms = ", ".join(f"{e['nom']} ({e['id']})" for e in manquants)
    if couverts:
        if manquants:
            remarques.append(f"Non partagé : {noms}. {AUTORISER}.")
        return couverts, [assoc[e["id"]] for e in couverts]
    cible = manquants[0]
    if explicite:
        if inconnus:
            raise RuntimeError(f"impossible de savoir quel écran partagé est {cible['nom']} ({cible['id']}) : "
                               f"{len(inconnus)} flux du portail sans position ni taille reconnue")
        partages = ", ".join(f"{e['nom']} ({e['id']})" for e in liste if e["id"] in assoc) or "aucun"
        raise RuntimeError(f"l'écran {cible['nom']} ({cible['id']}) n'est pas couvert par l'autorisation de "
                           f"partage d'écran (partagé : {partages}). {AUTORISER}.")
    autres = [e for e in liste if e["id"] in assoc]
    if autres:
        remarques.append(f"{cible['nom']} ({cible['id']}) non partagé : {autres[0]['nom']} montré à la place. "
                         f"{AUTORISER}.")
        return [autres[0]], [assoc[autres[0]["id"]]]
    remarques.append("Écran partagé non identifié (correspondance flux/écran impossible) : premier flux montré.")
    return [], [inconnus[0]]
