"""Bulle flottante animée, toujours au-dessus des autres fenêtres.

⚠️ GNOME tourne sous Wayland, où un client ne peut pas se placer au-dessus des
autres. On passe donc par XWayland (GDK_BACKEND=x11). `set_keep_above` seul ne
suffisait pas : Mutter repassait la bulle sous la fenêtre qui prenait le focus.
La bulle est donc une fenêtre override-redirect (type POPUP), que Mutter garde
dans sa couche la plus haute ; le glisser est fait à la main.

⚠️ GTK : épingler `Gdk` en 3.0 AUSSI. Importé avant `Gtk`, `gi` prend Gdk 4.0
et lève « Requiring namespace 'Gdk' version '3.0', but '4.0' is already loaded ».

La bulle écoute le bus d'Astrel par WebSocket (état ET niveau sonore, pour
respirer au rythme de la voix) ; si Astrel ne répond pas, elle retombe sur
`runtime/state.json`. Elle n'envoie que des commandes : on peut la fermer et la
relancer sans gêner l'assistant.

14/10 (le HUD n'est pas toujours ouvert) : une question de confirmation
s'affiche en carte À CÔTÉ de la bulle (fenêtre POPUP : elle ne prend jamais le
clavier, on y répond à la souris), et un CLIC sur la bulle ouvre un mini-chat
(derniers échanges, une ligne pour taper). Logique dans `minichat.py`.
23/10 (demande de l'utilisateur : bulle SIMPLE) : l'orbe ne montre que l'état de
Astrel ; Autonome TOTAL, modes d'usage, pause, mission et levée du garde-fou
ne s'affichent que dans le HUD (le menu du clic droit garde ses actions).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import cairo

# Forcé, pas `setdefault` : la session GNOME exporte GDK_BACKEND=wayland, et une
# fenêtre POPUP sans parent sous Wayland est centrée d'office, move() ignoré.
os.environ["GDK_BACKEND"] = "x11"  # avant l'import de Gtk

import gi  # noqa: E402

from gi.repository import Gio  # noqa: E402  (n'ouvre pas l'écran, contrairement à Gtk)

log = logging.getLogger("astrel.bubble")  # sans configuration : avertissements sur stderr (journal du service)


def _wait_for_gnome(timeout: float = 90.0) -> None:
    """Attend que GNOME publie l'échelle d'écran AVANT d'initialiser GTK.

    28/09 : lancée au démarrage de session une seconde avant GNOME Shell, la
    bulle lisait une échelle ×1 sur un écran ×2 (option xwayland-native-scaling)
    et GTK 3 la gardait figée : orbe au quart en haut à gauche, reste noir.
    L'échelle des clients X11 vient de XSETTINGS, publié par le service
    org.gnome.SettingsDaemon.XSettings : on attend lui et le Shell.
    """
    if "GNOME" not in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
        return
    names = ("org.gnome.Shell", "org.gnome.SettingsDaemon.XSettings")
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except Exception:
        return
    waited, deadline = False, time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            ready = all(bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                      "org.freedesktop.DBus", "NameHasOwner",
                                      GLib.Variant("(s)", (n,)), GLib.VariantType("(b)"),
                                      Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
                        for n in names)
        except Exception:
            return
        if ready:
            # Le nom D-Bus peut exister avant que XSETTINGS soit publié : juste
            # après le démarrage du PC, on laisse toujours un peu de marge.
            try:
                uptime = float(Path("/proc/uptime").read_text().split()[0])
            except Exception:
                uptime = 0.0
            if waited or uptime < 180:
                time.sleep(3.0)
            return
        waited = True
        time.sleep(0.5)


from gi.repository import GLib  # noqa: E402

_wait_for_gnome()

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gtk, Pango  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astrel import bubble_premium, config, state  # noqa: E402
from astrel import theme as theme_mod  # noqa: E402  (21/10, plan P17 : thème relu à chaud)
from astrel.i18n import assistant_name, tr  # noqa: E402  (20/10 : langue de l'interface, relue à chaque affichage)
from astrel.minichat import MiniChat  # noqa: E402

SIZE = 148
COMPACT = 52
FPS = 60
CALME_PAS = 4  # au repos stable : une image sur 4 (15 images/s), voir `_tick`
FADE_STEP = 0.06  # par image : fondu d'un état à l'autre en ≈ 0,3 s
POS_FILE = config.RUNTIME / "bubble.json"
# 29/10 : une seule bulle à la fois (le service systemd ET l'autostart GNOME en avaient lancé deux)
LOCK_FILE = config.RUNTIME / "bubble.lock"

# 23/10 (plan P31, demande de l'utilisateur) : nom AFFICHÉ du produit (« Astrel » par défaut), réglage
# `assistant_name` relu à chaque affichage par `i18n.assistant_name()` (même source que le HUD) ;
# `tr()` le pose dans les textes connus du catalogue (menu, invites, erreurs de la carte).

# 25/10 (plan P35, demande de l'utilisateur) : thème Classique abandonné, la bulle se dessine en Premium
# seul (`bubble_premium.draw`). Repli si config/themes.json est illisible : Premium sombre, accent
# bleu, couleurs figées ici (les mêmes que themes.json), pour que la bulle reste visible.
def _fallback_look() -> bubble_premium.Look:
    accent, gray, warn = (10 / 255, 132 / 255, 1.0), (174 / 255, 174 / 255, 178 / 255), (1.0, 214 / 255, 10 / 255)
    states = {s: accent for s in (state.SLEEPING, state.LISTENING, state.THINKING, state.WORKING, state.SPEAKING)}
    states.update({state.BOOTING: gray, state.OFF: gray, state.ASKING: warn})
    return bubble_premium.Look(fill=(44 / 255, 44 / 255, 46 / 255), fill_alpha=0.92, line=(1.0, 1.0, 1.0),
                               line_alpha=0.12, shade_alpha=0.35, states=states, warn=warn,
                               ok=(48 / 255, 209 / 255, 88 / 255), ink_2=gray, ink=(245 / 255, 245 / 255, 247 / 255))


class Feed:
    """Reçoit l'état et le niveau sonore d'Astrel, dans un thread à part."""

    def __init__(self) -> None:
        self.state = state.OFF
        self.muted = False
        self.paused = False  # voix en pause (⏸)
        self.detail = ""
        self.level = 0.0
        self.connected = False
        self.chat = MiniChat()
        self.closed_confirms: dict[str, None] = {}  # questions closes (réponse, délai), les plus récentes
        self.on_change = lambda: None  # appelé (fil du flux) quand le mini-chat ou la question change
        threading.Thread(target=self._run, name="bubble-feed", daemon=True).start()

    def _run(self) -> None:
        while True:
            try:
                asyncio.run(self._listen())
            except Exception:
                pass
            self.connected = False
            self._from_file()
            time.sleep(1.0)

    def _from_file(self) -> None:
        data = state.read_state()
        self.state = data.get("state", state.OFF)
        self.muted = bool(data.get("muted"))
        self.paused = bool(data.get("paused"))
        self.detail = data.get("detail", "")

    async def _listen(self) -> None:
        import aiohttp

        token = config.HUD_TOKEN_FILE.read_text(encoding="utf-8").strip()
        url = f"http://{config.HUD_HOST}:{config.HUD_PORT}/ws?t={token}"
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url, heartbeat=20) as ws:
                self.connected = True
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    ev = json.loads(msg.data)
                    kind = ev.get("type")
                    if kind == "hello":
                        for sticky in ev.get("sticky", []):
                            self._apply(sticky)
                        for old in ev.get("history", []):
                            self._apply({**old, "replay": True})
                    else:
                        self._apply(ev)

    def _apply(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "confirm" and not ev.get("pending"):
            self.closed_confirms[str(ev.get("id") or "")] = None  # `Bubble._confirm_failed`
            while len(self.closed_confirms) > 50:
                self.closed_confirms.pop(next(iter(self.closed_confirms)))
        if self.chat.apply(ev):
            self.on_change()
        if kind == "state":
            self.state = ev.get("state", self.state)
            self.muted = bool(ev.get("muted"))
            self.paused = bool(ev.get("paused"))
            self.detail = ev.get("detail", "")
        elif kind == "level":
            # La voix d'Astrel compte plus que le micro, pour l'animation.
            if ev.get("source") == "voice" or self.state in (state.LISTENING, state.ASKING):
                self.level = float(ev.get("value", 0.0))


# CSS d'origine des cartes : posé au démarrage, puis remplacé par `theme.css_gtk` (Premium). Ne sert
# plus que de repli si config/themes.json est illisible (25/10, plan P35).
CSS = b"""
.astrel-card { background: rgba(14, 18, 30, 0.96); color: #d7e6ff; border: 1px solid rgba(255, 181, 71, 0.6);
               border-radius: 12px; padding: 10px; }
.astrel-chat { background: rgba(9, 14, 26, 0.97); color: #d7e6ff; border: 1px solid rgba(90, 170, 255, 0.35);
               border-radius: 12px; }
.astrel-card .head { color: #ffb547; font-weight: bold; }
.astrel-card .cmd { font-family: monospace; font-size: 10px; color: #ffd9a0; }
.astrel-card .question { color: #e6efff; }
.astrel-card .error { color: #ff9aa8; }
.astrel-chat .user { color: #9fe6ff; }
.astrel-chat .who { color: #6f84a8; font-size: 10px; }
.astrel-chat entry { background: rgba(0, 0, 0, 0.35); color: #fff; border-radius: 8px; }
.astrel-card button, .astrel-chat button { padding: 2px 10px; }
/* 15/10 : boutons de la carte au theme d'Astrel (le theme GTK par defaut les
   laissait gris clair, avec degrade et ombre). ASCII seulement : chaine d'octets. */
.astrel-card button { background-image: none; background-color: rgba(255, 255, 255, 0.06); color: #d7e6ff;
                      border: 1px solid rgba(120, 160, 220, 0.45); border-radius: 999px; box-shadow: none;
                      text-shadow: none; -gtk-icon-shadow: none; padding: 3px 14px; min-height: 0; }
.astrel-card button:hover { background-color: rgba(255, 255, 255, 0.12); border-color: #9fc3ff; color: #fff; }
.astrel-card button.yes { background-color: rgba(52, 227, 164, 0.16); border-color: rgba(52, 227, 164, 0.7);
                          color: #b8ffe4; }
.astrel-card button.yes:hover { background-color: rgba(52, 227, 164, 0.3); color: #fff; }
.astrel-card button.no { border-color: rgba(255, 90, 110, 0.6); color: #ffc2cb; }
.astrel-card button.no:hover { background-color: rgba(255, 90, 110, 0.22); color: #fff; }
.astrel-card button.always { border-color: rgba(255, 181, 71, 0.6); color: #ffd9a0; }
.astrel-card button.always:hover { background-color: rgba(255, 181, 71, 0.2); color: #fff; }
.astrel-card button label { color: inherit; }
/* 15/10 : meme theme pour le mini-chat (boutons HUD et fermer, fond des listes, champ de saisie) */
.astrel-chat button { background-image: none; background-color: rgba(255, 255, 255, 0.06); color: #d7e6ff;
                      border: 1px solid rgba(120, 160, 220, 0.45); border-radius: 999px; box-shadow: none;
                      text-shadow: none; -gtk-icon-shadow: none; padding: 2px 12px; min-height: 0; }
.astrel-chat button:hover { background-color: rgba(255, 255, 255, 0.12); border-color: #9fc3ff; color: #fff; }
.astrel-chat button label { color: inherit; }
.astrel-chat label { color: #d7e6ff; }
.astrel-chat scrolledwindow, .astrel-chat viewport { background-color: transparent; background-image: none; }
.astrel-chat scrollbar slider { background-color: rgba(120, 160, 220, 0.35); border-radius: 999px; min-width: 4px; }
.astrel-chat scrollbar, .astrel-chat scrollbar trough { background: transparent; border: none; }
.astrel-chat entry { border: 1px solid rgba(120, 160, 220, 0.4); box-shadow: none; background-image: none; padding: 4px 8px; }
.astrel-chat entry:focus { border-color: #9fc3ff; }
.astrel-chat .user { color: #9fe6ff; }
"""


def answer_refusal(reply: str | None) -> str:
    """Réponse du cœur à une réponse de la carte : "" si elle est prise, sinon la
    raison à montrer à l'utilisateur (18/10)."""
    if not reply:
        return "Astrel ne répond pas : réponse non transmise, réessaie ou réponds dans le HUD"
    try:
        res = json.loads(reply)
    except ValueError:
        return "réponse illisible d'Astrel : réponds dans le HUD"
    if isinstance(res, dict) and res.get("ok") is True:
        return ""
    err = str(res.get("error") or "") if isinstance(res, dict) else ""
    if "refusée à Claude" in err:
        # Bulle lancée par Astrel ou Claude (cgroup du service) : ses clics ne valent rien.
        return "refusé : cette bulle a été lancée par Astrel ; relance-la depuis ta session ou réponds dans le HUD"
    return err or "réponse refusée par Astrel"


def _send_async(payload: dict | str, done=None) -> None:
    """Commande au cœur hors du fil GTK (la socket peut attendre 3 s)."""
    raw = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

    def run() -> None:
        reply = state.send_command(raw)
        if done is not None:
            GLib.idle_add(done, reply)

    threading.Thread(target=run, daemon=True).start()


def _transparent(win: Gtk.Window) -> None:
    """15/10 : fenetre a fond transparent, pour que les coins arrondis du CSS
    ne laissent pas voir un rectangle brut derriere (carte et mini-chat)."""
    # PAS de set_app_paintable : GTK cesserait de peindre le fond CSS (fenetre invisible).
    visual = win.get_screen().get_rgba_visual()
    if visual is not None:
        win.set_visual(visual)


class ConfirmCard(Gtk.Window):
    """La question en attente, à côté de la bulle. Fenêtre POPUP : elle ne prend
    JAMAIS le clavier (l'utilisateur peut être en train de taper ailleurs) ; on y
    répond à la souris, à la voix, ou dans le HUD."""

    def __init__(self, chat: MiniChat, on_failed=None) -> None:
        super().__init__(type=Gtk.WindowType.POPUP)
        self.chat = chat
        self.on_failed = on_failed  # (question, raison) : réponse refusée ou non reçue par le cœur
        self.set_keep_above(True)
        _transparent(self)
        self.get_style_context().add_class("astrel-card")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_border_width(10)
        head = Gtk.Label(label=tr("⚠ Confirmation"), xalign=0)
        head.get_style_context().add_class("head")
        self.head = head
        # 15/10 : largeur FIXE des textes repliés. Sans `width_chars`, GTK calcule la
        # hauteur pour la largeur minimale (un mot par ligne) : fenêtre bien trop
        # haute, grand vide sous les boutons. La commande tient en 3 lignes au plus,
        # coupée par « … » (entière dans l'infobulle).
        self.question = Gtk.Label(xalign=0)
        self.question.set_line_wrap(True)
        self.question.set_width_chars(40)
        self.question.set_max_width_chars(40)
        self.question.get_style_context().add_class("question")
        self.cmd = Gtk.Label(xalign=0)
        self.cmd.set_line_wrap(True)
        self.cmd.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.cmd.set_width_chars(46)
        self.cmd.set_max_width_chars(46)
        self.cmd.set_lines(3)
        self.cmd.set_ellipsize(Pango.EllipsizeMode.END)
        self.cmd.get_style_context().add_class("cmd")
        row = Gtk.Box(spacing=6)
        no = Gtk.Button(label=tr("Non"))
        self.always = Gtk.Button(label=tr("Oui 30 min"))
        self.always.set_tooltip_text(tr("Ne plus demander pour ce type d'action pendant 30 minutes"))
        yes = Gtk.Button(label=tr("Oui"))
        self.no, self.yes = no, yes
        for b, cls in ((no, "no"), (self.always, "always"), (yes, "yes")):
            b.set_can_focus(False)
            b.get_style_context().add_class(cls)
        no.connect("clicked", lambda *_: self._answer(False))
        self.always.connect("clicked", lambda *_: self._answer(True, True))
        yes.connect("clicked", lambda *_: self._answer(True))
        row.pack_end(yes, False, False, 0)
        row.pack_end(self.always, False, False, 0)
        row.pack_end(no, False, False, 0)
        self.row = row
        # 18/10 : raison d'une réponse refusée ou non reçue (`Bubble._confirm_failed`).
        self.error = Gtk.Label(xalign=0)
        self.error.set_line_wrap(True)
        self.error.set_width_chars(40)
        self.error.set_max_width_chars(40)
        self.error.get_style_context().add_class("error")
        for w in (head, self.question, self.cmd, self.error, row):
            box.pack_start(w, False, False, 0)
        self.add(box)

    def _texts(self) -> None:
        """20/10 : libellés dans la langue de l'interface du moment (changée à chaud)."""
        self.head.set_text(tr("⚠ Confirmation"))
        self.no.set_label(tr("Non"))
        self.yes.set_label(tr("Oui"))
        self.always.set_label(tr("Oui 30 min"))
        self.always.set_tooltip_text(tr("Ne plus demander pour ce type d'action pendant 30 minutes"))

    def show_for(self, ev: dict, anchor: tuple[int, int, int], above: Gtk.Window | None = None) -> None:
        self._texts()
        self.question.set_text(tr(str(ev.get("question") or ev.get("desc") or "Je continue ?")))
        cmd = " ".join(str(ev.get("cmd") or ev.get("tool") or "").split())
        self.cmd.set_text(cmd[:400])
        self.cmd.set_tooltip_text(cmd or None)
        if not self.get_visible() and self.get_realized():
            # 19/10 (carte NOIRE) : fenêtre X neuve à chaque affichage. Mutter gèle une
            # fenêtre Xwayland pendant qu'elle se peint (_XWAYLAND_ALLOW_COMMITS=0) ; une
            # POPUP masquée pendant ce gel le garde pour toujours (l'état du gel meurt avec
            # sa MetaWindow, la propriété reste), et Xwayland n'envoie plus rien : rectangle
            # noir aux réaffichages suivants. Relevé sur la vraie carte (xprop : 0, carte
            # masquée) ; la bulle ne peut pas remettre 1 elle-même (BadAccess).
            self.unrealize()
        self.show_all()
        self.always.set_visible(bool(ev.get("grantable")))
        self.cmd.set_visible(bool(cmd))
        self.error.set_text(tr(str(ev.get("error") or "")))
        self.error.set_visible(bool(ev.get("error")))
        self.row.set_visible(not ev.get("closed"))  # question close : plus rien à répondre
        # Une fenêtre GTK ne rétrécit pas d'elle-même : sans ça, elle gardait la
        # hauteur d'une question précédente plus longue (vide sous les boutons).
        self.resize(1, 1)
        if above is not None and above.get_visible():
            # Mini-chat ouvert : au-dessus de lui, jamais sur la ligne où l'utilisateur tape.
            x, y = above.get_position()
            _w, h = self.get_size()
            self.move(x, max(0, y - h - 8))
        else:
            _place_beside(self, anchor)

    def _answer(self, allow: bool, always: bool = False) -> None:
        with self.chat.lock:
            ev = dict(self.chat.confirm) if self.chat.confirm else None
        cmd = self.chat.answer_command(allow, always)
        self.hide()
        if cmd:
            _send_async(cmd, done=lambda reply: self._answered(reply, ev))

    def _answered(self, reply: str | None, ev: dict | None) -> bool:
        # 18/10 : la réponse du cœur était jetée. Refusée ou jamais reçue, la carte
        # disparaissait sans un mot (la question restait en attente) : elle revient, avec la raison.
        why = answer_refusal(reply)
        if why and ev is not None and self.on_failed is not None:
            self.on_failed(ev, why)
        return False


class ChatWindow(Gtk.Window):
    """Mini-chat : les derniers échanges et une ligne pour écrire à Astrel sans
    ouvrir le HUD. Ouvert par un clic sur la bulle (le clavier lui va alors, à
    la demande de l'utilisateur) ; rien de ce qui arrive ensuite ne le lui reprend."""

    def __init__(self, chat: MiniChat) -> None:
        super().__init__(title=assistant_name())
        self.chat = chat
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_default_size(360, 320)
        _transparent(self)
        self.get_style_context().add_class("astrel-chat")
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_border_width(8)
        top = Gtk.Box(spacing=6)
        title = Gtk.Label(label=assistant_name(), xalign=0)
        self.title_lbl = title
        hud = Gtk.Button(label="HUD")
        hud.connect("clicked", lambda *_: subprocess.Popen([str(config.ROOT / "hud.sh")], start_new_session=True))
        close = Gtk.Button(label="×")
        self.hud_btn, self.close_btn = hud, close
        close.connect("clicked", lambda *_: self.hide())
        top.pack_start(title, True, True, 0)
        top.pack_end(close, False, False, 0)
        top.pack_end(hud, False, False, 0)
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.add(self.list)
        self.entry = Gtk.Entry()
        self._texts()
        self.entry.connect("activate", self._send)
        self.pending: list[Path] = []  # pièces jointes prêtes (runtime/attachments/)
        self.att_box = Gtk.Box(spacing=4)
        outer.pack_start(top, False, False, 0)
        outer.pack_start(self.scroll, True, True, 0)
        outer.pack_start(self.att_box, False, False, 0)
        outer.pack_start(self.entry, False, False, 0)
        self.add(outer)
        self.connect("key-press-event", self._on_key)
        # Fichiers glissés depuis le gestionnaire de fichiers.
        self.drag_dest_set(Gtk.DestDefaults.ALL, [Gtk.TargetEntry.new("text/uri-list", 0, 0)], Gdk.DragAction.COPY)
        self.connect("drag-data-received", self._on_drop)
        self.connect("delete-event", lambda *_: self.hide() or True)

    def _texts(self) -> None:
        """20/10 : infobulles et invite dans la langue de l'interface (relu à chaque ouverture)."""
        self.hud_btn.set_tooltip_text(tr("Ouvrir le HUD complet"))
        self.close_btn.set_tooltip_text(tr("Fermer (Échap)"))
        self.entry.set_placeholder_text(tr("Écris à Astrel… (Entrée ; Ctrl+V colle une image)"))
        self.title_lbl.set_text(assistant_name())  # 23/10 (P31) : nom affiché, relu à chaque ouverture
        self.set_title(assistant_name())

    def _on_key(self, _w, ev) -> bool:
        if ev.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        if ev.keyval in (Gdk.KEY_v, Gdk.KEY_V) and ev.state & Gdk.ModifierType.CONTROL_MASK:
            return self._paste_image()
        return False

    # --- pièces jointes : Ctrl+V d'une image, fichiers déposés ---

    def _paste_image(self) -> bool:
        """Une IMAGE dans le presse-papiers : jointe (réduite à ≈ 256 Ko). Du
        texte : collage normal (rend False)."""
        clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        if not clip.wait_is_image_available():
            return False
        pixbuf = clip.wait_for_image()
        if pixbuf is None:
            return False
        try:
            self._attach(_pixbuf_jpeg(pixbuf), "image-collee.jpg")
        except Exception as exc:
            self.entry.set_placeholder_text(tr(f"Image non jointe : {exc}"))
        return True

    def _on_drop(self, _w, _ctx, _x, _y, data, _info, _time) -> None:
        for uri in data.get_uris() or []:
            try:
                path = Path(GLib.filename_from_uri(uri)[0])
                if path.is_file():
                    self._attach(path.read_bytes(), path.name)
            except Exception as exc:
                self.entry.set_placeholder_text(tr(f"Fichier non joint : {exc}"))
        self.present()
        self.entry.grab_focus_without_selecting()

    def _attach(self, data: bytes, name: str) -> None:
        from astrel import attachments

        if len(self.pending) >= attachments.MAX_FILES:
            raise ValueError(f"{attachments.MAX_FILES} au plus")
        self.pending.append(attachments.save_bytes(name, data))
        self._render_attachments()

    def _render_attachments(self) -> None:
        for child in self.att_box.get_children():
            self.att_box.remove(child)
        for path in list(self.pending):
            item = Gtk.Box(spacing=2)
            try:
                from gi.repository import GdkPixbuf

                thumb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), 40, 40, True)
                item.pack_start(Gtk.Image.new_from_pixbuf(thumb), False, False, 0)
            except Exception:
                item.pack_start(Gtk.Label(label="📎 " + path.name.split("-", 2)[-1][:24]), False, False, 0)
            x = Gtk.Button(label="×")
            x.set_can_focus(False)
            x.set_tooltip_text(tr("Retirer"))
            x.connect("clicked", lambda *_a, p=path: (self.pending.remove(p), self._render_attachments()))
            item.pack_start(x, False, False, 0)
            self.att_box.pack_start(item, False, False, 0)
        self.att_box.show_all()

    def refresh(self) -> None:
        for child in self.list.get_children():
            self.list.remove(child)
        for line in self.chat.snapshot():
            who = Gtk.Label(label=tr("Toi") if line.who == "user" else assistant_name(), xalign=0)
            who.get_style_context().add_class("who")
            text = Gtk.Label(label=line.text + (" ▍" if line.live else ""), xalign=0)
            text.set_line_wrap(True)
            text.set_max_width_chars(44)
            text.set_selectable(True)
            if line.who == "user":
                text.get_style_context().add_class("user")
            self.list.pack_start(who, False, False, 0)
            self.list.pack_start(text, False, False, 0)
        self.list.show_all()
        GLib.idle_add(self._to_bottom)

    def _to_bottom(self) -> bool:
        adj = self.scroll.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return False

    def _send(self, entry) -> None:
        text = entry.get_text().strip()
        sent = list(self.pending)
        if not text and not sent:
            return
        entry.set_sensitive(False)

        def done(reply) -> bool:
            entry.set_sensitive(True)
            ok = False
            try:
                ok = bool(json.loads(reply or "{}").get("ok"))
            except ValueError:
                pass
            if ok and entry.get_text().strip() == text:
                entry.set_text("")  # effacé seulement une fois REÇU, comme dans le HUD
            if ok:
                self.pending = [p for p in self.pending if p not in sent]
                self._render_attachments()
            elif not ok:
                entry.set_placeholder_text(tr("Astrel injoignable : message gardé, réessaie"))
            entry.grab_focus_without_selecting()
            return False

        payload: dict = {"cmd": "say", "text": text, "source": "clavier"}
        if sent:
            payload["attachments"] = [{"path": str(p)} for p in sent]
        _send_async(payload, done)

    def toggle(self, anchor: tuple[int, int, int]) -> None:
        if self.get_visible():
            self.hide()
            return
        self._texts()
        self.refresh()
        self.show_all()
        _place_beside(self, anchor)
        self.present()
        self.entry.grab_focus_without_selecting()


def _pixbuf_jpeg(pixbuf, max_bytes: int = 256 * 1024, max_side: int = 1568) -> bytes:
    """Image collée → JPEG d'au plus ≈ 256 Ko (côté long ≤ 1568 px)."""
    from gi.repository import GdkPixbuf

    if pixbuf.get_has_alpha():  # transparence sur fond blanc
        pixbuf = pixbuf.composite_color_simple(pixbuf.get_width(), pixbuf.get_height(),
                                               GdkPixbuf.InterpType.BILINEAR, 255, 1, 0xFFFFFF, 0xFFFFFF)
    scale = min(1.0, max_side / max(pixbuf.get_width(), pixbuf.get_height()))
    data = b""
    for _ in range(8):
        cur = pixbuf if scale >= 1.0 else pixbuf.scale_simple(max(1, int(pixbuf.get_width() * scale)),
                                                              max(1, int(pixbuf.get_height() * scale)),
                                                              GdkPixbuf.InterpType.BILINEAR)
        for quality in (85, 70, 55):
            ok, data = cur.save_to_bufferv("jpeg", ["quality"], [str(quality)])
            if ok and len(data) <= max_bytes:
                return bytes(data)
        scale *= 0.75
    return bytes(data)


def _place_beside(win: Gtk.Window, anchor: tuple[int, int, int]) -> None:
    """À gauche de la bulle s'il y a la place, sinon à droite ; bas alignés."""
    bx, by, size = anchor
    w, h = win.get_size()
    display = Gdk.Display.get_default()
    mon = display.get_monitor_at_point(bx + size // 2, by + size // 2) or display.get_monitor(0)
    g = mon.get_geometry()
    x = bx - w - 8 if bx - w - 8 >= g.x else bx + size + 8
    y = max(g.y + 8, min(by + size - h, g.y + g.height - h - 8))
    win.move(int(x), int(y))


def visible_position(x: int, y: int, size: int, monitors: list[tuple[int, int, int, int]],
                     home: tuple[int, int] | None) -> tuple[int, int] | None:
    """Place de la bulle ramenée dans la zone visible (18/10). Centre sur un écran : la
    bulle est remise ENTIÈRE dans cet écran ; centre hors de tout écran (écran débranché) :
    `home`, la place par défaut. `monitors` : rectangles (x, y, largeur, hauteur) ; aucun
    écran connu : None, on ne bouge pas."""
    if not monitors:
        return None
    cx, cy = x + size // 2, y + size // 2
    for gx, gy, gw, gh in monitors:
        if gx <= cx < gx + gw and gy <= cy < gy + gh:
            return max(gx, min(x, gx + gw - size)), max(gy, min(y, gy + gh - size))
    return home


def _monitor_rects() -> list[tuple[int, int, int, int]]:
    display = Gdk.Display.get_default()
    rects = []
    for i in range(display.get_n_monitors()):
        g = display.get_monitor(i).get_geometry()
        rects.append((g.x, g.y, g.width, g.height))
    return rects


def _interface_settings():
    """Réglages du bureau (clair/sombre, animations) ; None hors GNOME ou sans le schéma."""
    try:
        src = Gio.SettingsSchemaSource.get_default()
        if src is None or src.lookup("org.gnome.desktop.interface", True) is None:
            return None
        return Gio.Settings.new("org.gnome.desktop.interface")
    except Exception:
        return None


def _home_position(size: int = SIZE) -> tuple[int, int] | None:
    """En bas à droite de l'écran principal."""
    display = Gdk.Display.get_default()
    mon = display.get_primary_monitor() or (display.get_monitor(0) if display.get_n_monitors() else None)
    if mon is None:
        return None
    geo = mon.get_geometry()
    return geo.x + geo.width - size - 40, geo.y + geo.height - size - 80


class Bubble(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(type=Gtk.WindowType.POPUP)
        self.set_app_paintable(True)
        self.set_keep_above(True)
        self._size = SIZE
        self.set_default_size(SIZE, SIZE)
        self.set_size_request(SIZE, SIZE)
        self.set_title(assistant_name())
        visual = self.get_screen().get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)

        self.feed = Feed()
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        # 21/10 (plan P17) : thème (Réglages › Apparence) relu à chaud toutes les ~60 images
        # (un `stat` de runtime/settings.json) ; au changement, fondu de 300 ms et CSS des cartes
        # rechargé. « auto » suit le clair/sombre de GNOME ; animations coupées = motif figé.
        self._provider = provider
        self._gsettings = _interface_settings()
        self._theme_key: tuple | None = None
        # P35 : Premium seul ; repli Premium sombre tant que le thème n'a pas été lu
        self._theme: dict = {"theme": "premium", "mode": "dark", "look": _fallback_look()}
        self._theme_from: dict | None = None
        self._theme_mix = 1.0
        self._theme_error = ""  # dernière panne de thème journalisée (une fois, pas à chaque essai)
        self._ticks = 0
        self._check_theme()
        self._card = ConfirmCard(self.feed.chat, on_failed=self._confirm_failed)
        self._chat = ChatWindow(self.feed.chat)
        self._shown_confirm = ""
        self._closed_notice = ""  # carte « question déjà close » affichée (id), effacée seule
        self._click_timer = 0
        self._double = False  # le relâchement qui suit un double clic n'ouvre pas le mini-chat
        self._press_at: tuple[int, int] | None = None
        self._refresh_due = False
        self.feed.on_change = self._schedule_refresh
        self._t0 = time.monotonic()
        self._level = 0.0          # niveau lissé
        self._shown_state = state.OFF
        # Opacité du motif de chaque état : celui de l'état courant monte vers 1, les autres
        # s'effacent au même rythme (FADE_STEP). Sans ça, le motif changeait d'un coup.
        self._layers: dict[str, float] = {state.OFF: 1.0}
        self._drag: tuple[int, int] | None = None
        self._scale0: int | None = None  # échelle au démarrage (_check_scale)
        self._pressed_at = 0.0

        area = Gtk.DrawingArea()
        area.connect("draw", self._on_draw)
        self.add(area)
        self._area = area

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("button-press-event", self._on_click)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("button-release-event", self._on_release)
        self.connect("delete-event", lambda *_a: Gtk.main_quit())

        self._restore_position()
        GLib.timeout_add(int(1000 / FPS), self._tick)
        GLib.timeout_add(1000, self._raise)

    # --- position ---

    def _restore_position(self) -> None:
        home = _home_position()
        try:
            pos = json.loads(POS_FILE.read_text(encoding="utf-8"))
            x, y = int(pos["x"]), int(pos["y"])
        except Exception:
            x, y = home if home is not None else (0, 0)
        # Position hors de tout écran (écran débranché, ancien placement) : place par défaut.
        target = visible_position(x, y, SIZE, _monitor_rects(), home)
        if target is not None:
            self.move(*target)
        # 18/10 : un écran débranché PENDANT que la bulle tourne la laissait hors écran
        # (Mutter ne déplace pas une fenêtre POPUP). Revue à chaque changement d'écrans.
        screen = self.get_screen()
        screen.connect("monitors-changed", self._on_monitors_changed)
        screen.connect("size-changed", self._on_monitors_changed)

    def _on_monitors_changed(self, *_a) -> None:
        # Un court délai : la géométrie n'est pas toujours à jour au moment du signal.
        GLib.timeout_add(400, self._keep_visible)

    def _keep_visible(self) -> bool:
        if self._drag is None:
            x, y = self.get_position()
            target = visible_position(x, y, self._size, _monitor_rects(), _home_position(self._size))
            if target is not None and target != (x, y):
                self.move(*target)  # pas enregistré : l'écran rebranché, la place choisie revient au démarrage
        return False

    def _save_position(self) -> None:
        try:
            x, y = self.get_position()
            POS_FILE.write_text(json.dumps({"x": x, "y": y}), encoding="utf-8")
        except Exception:
            pass

    # --- interactions ---

    # --- carte de confirmation, mini-chat ---

    def _anchor(self) -> tuple[int, int, int]:
        x, y = self.get_position()
        return x, y, self._size

    def _schedule_refresh(self) -> None:
        """Appelé par le fil du flux à chaque événement : un seul rafraîchissement
        en attente à la fois (une réponse arrive mot à mot)."""
        if not self._refresh_due:
            self._refresh_due = True
            GLib.timeout_add(80, self._refresh_panels)

    def _refresh_panels(self) -> bool:
        self._refresh_due = False
        ev = self.feed.chat.confirm
        if ev and ev.get("id") != self._shown_confirm:
            self._shown_confirm, self._closed_notice = str(ev.get("id")), ""
            self._card.show_for(ev, self._anchor(), above=self._chat)
        elif not ev and self._card.get_visible() and not self._closed_notice:
            self._shown_confirm = ""
            self._card.hide()
        if self._chat.get_visible():
            self._chat.refresh()
        return False

    def _confirm_failed(self, ev: dict, why: str) -> None:
        """Réponse de la carte refusée ou non reçue (18/10). Question encore ouverte : la
        carte revient, raison en rouge, on peut répondre de nouveau. Question déjà close
        (délai, réponse ailleurs) : la carte le dit quelques secondes, sans boutons."""
        cid = str(ev.get("id") or "")
        closed = cid in self.feed.closed_confirms or why.startswith("aucune confirmation en attente")
        with self.feed.chat.lock:
            if self.feed.chat.confirm is not None:
                return  # une autre question est déjà à l'écran : elle passe d'abord
            if not closed:
                self.feed.chat.confirm = dict(ev)
        shown = {**ev, "error": "Question déjà close : réponse non prise en compte." if closed else why,
                 "closed": closed}
        self._shown_confirm, self._closed_notice = cid, (cid if closed else "")
        self._card.show_for(shown, self._anchor(), above=self._chat)
        if closed:
            GLib.timeout_add(6000, self._hide_closed_card, cid)

    def _hide_closed_card(self, cid: str) -> bool:
        if self._closed_notice == cid:
            self._closed_notice = ""
            if self.feed.chat.confirm is None:
                self._shown_confirm = ""
                self._card.hide()
        return False

    def _single_click(self) -> bool:
        self._click_timer = 0
        self._chat.toggle(self._anchor())
        return False

    def _on_click(self, _w, ev) -> bool:
        if ev.button == 1 and ev.type == Gdk.EventType._2BUTTON_PRESS:
            if self._click_timer:
                GLib.source_remove(self._click_timer)  # c'était un double clic, pas un clic
                self._click_timer = 0
            self._double = True
            state.send_command("wake")  # double clic : parler tout de suite
            return True
        if ev.button == 1:
            x, y = self.get_position()
            self._drag = (int(ev.x_root) - x, int(ev.y_root) - y)
            self._press_at = (int(ev.x_root), int(ev.y_root))
            return True
        if ev.button == 3:
            self._menu(ev)
            return True
        if ev.button == 2:
            state.send_command("toggle")
            return True
        return False

    def _on_motion(self, _w, ev) -> bool:
        if self._drag is None:
            return False
        dx, dy = self._drag
        self.move(int(ev.x_root) - dx, int(ev.y_root) - dy)
        return True

    def _on_release(self, _w, ev) -> bool:
        if ev.button == 1 and self._drag is not None:
            self._drag = None
            px, py = self._press_at or (int(ev.x_root), int(ev.y_root))
            if self._double:
                self._double = False
            elif abs(int(ev.x_root) - px) + abs(int(ev.y_root) - py) <= 4:
                # Un clic sans glisser : mini-chat, après le délai d'un éventuel double clic.
                if not self._click_timer:
                    settings = Gtk.Settings.get_default()
                    delay = settings.get_property("gtk-double-click-time") if settings else 300
                    self._click_timer = GLib.timeout_add(int(delay) + 20, self._single_click)
            else:
                self._save_position()
            return True
        return False

    def _raise(self) -> bool:
        win = self.get_window()
        if win is not None and self._drag is None:
            win.raise_()
        if win is not None:
            self._check_scale(win)
        return True

    def _check_scale(self, win) -> None:
        """Relance la bulle si l'échelle de l'écran a changé sous elle.

        Lancée au démarrage de la session (28/09, écran HiDPI ×2, option
        « xwayland-native-scaling »), elle peut naître avant que GNOME annonce
        l'échelle : GTK dessine alors en ×1 dans une fenêtre ×2 — orbe réduite
        au quart en haut à gauche, le reste noir. GTK 3 ne s'en remet pas seul.
        """
        scale = self.get_scale_factor()
        monitor = Gdk.Display.get_default().get_monitor_at_window(win)
        if self._scale0 is None:
            self._scale0 = scale
        if scale == self._scale0 and (monitor is None or monitor.get_scale_factor() == scale):
            return
        restarts = int(os.environ.get("ASTREL_BUBBLE_RESTARTS", "0"))
        if restarts >= 3:  # pas de boucle si l'écran change sans cesse
            return
        self._save_position()
        os.environ["ASTREL_BUBBLE_RESTARTS"] = str(restarts + 1)
        os.execv(sys.executable, [sys.executable, "-m", "astrel.bubble", *sys.argv[1:]])

    def _menu(self, ev) -> None:
        menu = Gtk.Menu()
        busy = self.feed.state in (state.SPEAKING, state.THINKING, state.WORKING)
        usage = self.feed.chat.usage
        items = [
            ("Mini-chat (clic sur la bulle)", lambda *_: self._chat.toggle(self._anchor())),
            ("Parler maintenant", lambda *_: state.send_command("wake")),
            ("Mode conversation" if usage != "conversation" else "Mode silencieux (réponse à l'écran)",
             lambda *_: _send_async({"cmd": "usage", "mode": "conversation" if usage != "conversation"
                                     else "silencieux"})),
            ("Interrompre", lambda *_: state.send_command("stop")) if busy else None,
            ("Réactiver le micro" if self.feed.muted else "Couper le micro", lambda *_: state.send_command("toggle")),
            ("Reprendre la voix" if self.feed.paused else "Mettre la voix en pause",
             lambda *_: state.send_command("pause off" if self.feed.paused else "pause on")),
            ("Nouvelle conversation", lambda *_: state.send_command("reset")),
            ("Ouvrir le HUD", lambda *_: subprocess.Popen([str(config.ROOT / "hud.sh")], start_new_session=True)),
            (None, None),
            ("Éteindre Astrel (libère la mémoire)" if self.feed.connected else "Allumer Astrel",
             lambda *_: self._power_toggle()),
            (None, None),
            ("Agrandir la bulle" if self._size == COMPACT else "Réduire la bulle", lambda *_: self._toggle_compact()),
            ("Fermer la bulle", lambda *_: Gtk.main_quit()),
        ]
        for entry in items:
            if entry is None:
                continue
            label, cb = entry
            if label is None:
                menu.append(Gtk.SeparatorMenuItem())
                continue
            item = Gtk.MenuItem(label=tr(label))  # 23/10 (P31) : « Éteindre Astrel »
            item.connect("activate", cb)
            menu.append(item)
        menu.show_all()
        menu.popup_at_pointer(ev)

    def _power_toggle(self) -> None:
        """Éteint Astrel proprement (`quit`, déjà géré par le cœur : il libère la
        mémoire, le GPU et le process `claude`) ou le rallume via systemd,
        puisque la bulle est indépendante et reste seule debout une fois Astrel
        arrêté (docstring du module)."""
        if self.feed.connected:
            state.send_command("quit")
        else:
            subprocess.Popen(["systemctl", "--user", "start", "astrel.service"], start_new_session=True)

    def _toggle_compact(self) -> None:
        self._size = COMPACT if self._size == SIZE else SIZE
        self.set_size_request(self._size, self._size)
        self.resize(self._size, self._size)

    # --- animation ---

    def _tick(self) -> bool:
        self._ticks += 1
        if self._ticks % 60 == 0:
            self._check_theme()
        if self._theme_mix < 1.0:
            self._theme_mix = min(1.0, self._theme_mix + FADE_STEP)
        target = self.feed.level
        # Montée rapide, descente douce : c'est ce qui rend l'animation vivante.
        k = 0.45 if target > self._level else 0.12
        self._level += (target - self._level) * k
        st = self.feed.state
        if st != self._shown_state:
            self._shown_state = st
            self._layers.setdefault(st, 0.0)
        for s in list(self._layers):
            if s == self._shown_state:
                self._layers[s] = min(1.0, self._layers[s] + FADE_STEP)
            else:
                self._layers[s] -= FADE_STEP
                if self._layers[s] <= 0.0:
                    del self._layers[s]
        # 29/10 (fluidité) : au repos stable (veille : respiration de 4 s à ±2 % ; micro coupé :
        # image fixe), 15 images/s suffisent ; tout changement (voix, état, fondu, thème) : 60.
        calme = (st in (state.SLEEPING, state.OFF) and self._theme_mix >= 1.0 and self._level < 0.005
                 and len(self._layers) == 1 and self._layers.get(st, 0.0) >= 1.0)
        if not calme or self._ticks % CALME_PAS == 0:
            self._area.queue_draw()
        return True

    # --- thème (21/10, plan P17) ---

    def _system_dark(self) -> bool | None:
        try:
            scheme = self._gsettings.get_string("color-scheme") if self._gsettings else ""
        except Exception:
            return None
        return {"prefer-dark": True, "prefer-light": False, "default": False}.get(scheme)

    def _animations(self) -> bool:
        try:
            return bool(self._gsettings.get_boolean("enable-animations")) if self._gsettings else True
        except Exception:
            return True

    def _check_theme(self) -> None:
        try:
            choice = theme_mod.current()
            mode = theme_mod.resolved_mode(choice, self._system_dark())
            key = ("premium", mode, choice["accent"])
            if key == self._theme_key:
                self._theme_error = ""
                return
            new = {"theme": "premium", "mode": mode, "look": bubble_premium.look(mode, choice["accent"])}
            css = theme_mod.css_gtk(mode, choice["accent"])
        except Exception as exc:
            # thème illisible (config/themes.json abîmé…) : la bulle garde ce qu'elle affiche
            # (au démarrage : le repli Premium sombre, `_fallback_look`, et le CSS d'origine des cartes)
            if self._theme_new_error("lecture", exc):
                log.warning("thème illisible, la bulle garde son apparence : %s: %s", type(exc).__name__, exc)
            return
        self._theme_error = ""
        if self._theme_key is not None:
            self._theme_from, self._theme_mix = self._theme, 0.0
        self._theme_key, self._theme = key, new
        try:
            self._provider.load_from_data(css)
        except Exception as exc:
            if self._theme_new_error("css", exc):
                log.warning("CSS du thème refusé par GTK : %s: %s", type(exc).__name__, exc)

    def _theme_new_error(self, where: str, exc: Exception) -> bool:
        """Vrai pour une panne de thème NOUVELLE : `_check_theme` réessaie toutes les ~60 images, la
        même erreur n'est journalisée qu'une fois (relecture P17), une autre l'est."""
        text = f"{where}: {type(exc).__name__}: {exc}"
        if text == self._theme_error:
            return False
        self._theme_error = text
        return True

    def _on_draw(self, widget, cr) -> bool:
        """Dessin du thème en cours ; pendant un changement de thème, fondu enchaîné."""
        now, before, mix = self._theme, self._theme_from, self._theme_mix
        if before is None or mix >= 1.0:
            return self._paint(widget, cr, now)
        cr.set_operator(cairo.Operator.SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.Operator.OVER)
        for th, alpha in ((before, 1.0 - mix), (now, mix)):
            cr.push_group()
            self._paint(widget, cr, th)
            cr.pop_group_to_source()
            cr.paint_with_alpha(max(0.0, min(1.0, alpha)))
        return False

    def _paint(self, widget, cr, th: dict) -> bool:
        # 25/10 (plan P35) : Premium seul ; le dessin Classique (`_draw_classic`) est retiré.
        return self._draw_premium(widget, cr, th.get("look") or _fallback_look())

    def _draw_premium(self, widget, cr, look) -> bool:
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        cr.set_operator(cairo.Operator.SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.Operator.OVER)
        moving = self._animations()
        # 23/10 (demande : bulle SIMPLE) : ni mode (Total, silencieux, dictée), ni pause, ni
        # mission, ni levée ; ces états restent dans le HUD. Seul l'état d'Astrel est dessiné.
        bubble_premium.draw(
            cr, w, h, layers=[(s, min(1.0, a)) for s, a in self._layers.items() if a > 0.0],
            t=(time.monotonic() - self._t0) if moving else 0.0, level=self._level if moving else 0.0, lk=look)
        return False


def seule_bulle() -> int | None:
    """Verrou de `LOCK_FILE`, tenu jusqu'à la sortie du processus (le noyau le rend même après un
    plantage) : son descripteur, ou None si une autre bulle le tient déjà."""
    config.RUNTIME.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            import fcntl
        except ImportError:  # Windows : verrou d'un octet, non bloquant (comme banc._lock)
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def main() -> int:
    if seule_bulle() is None:  # code 0 : le service systemd ne la relance pas (Restart=on-failure)
        log.warning("une bulle tourne déjà : celle-ci ne s'ouvre pas")
        return 0
    win = Bubble()
    win.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
