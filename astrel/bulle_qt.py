"""Bulle flottante de Windows et macOS, en Qt (03/11/2026, portage ; décision de l'utilisateur : Qt sur
Windows et Mac, la bulle GTK de Linux reste telle quelle).

    python -m astrel.bulle_qt

Même comportement que `bubble.py` : orbe animée dessinée par `bubble_premium` (cairo, dans une image
posée dans la fenêtre Qt : rendu identique), toujours au-dessus, glissée à la souris, place retenue
et ramenée dans l'écran ; clic = mini-chat, double clic = parler, clic droit = menu, clic du milieu
= micro ; question de confirmation en carte À CÔTÉ de la bulle, qui ne prend JAMAIS le clavier ;
mini-chat (image collée par Ctrl+V, fichiers déposés) ; thème Premium clair ou sombre relu à chaud ;
une seule bulle à la fois. Mêmes textes que la bulle GTK (déjà au catalogue de traduction).

Qt gère seul l'échelle de chaque écran : pas de relance quand l'échelle change (`_check_scale` GTK).
Écrit d'après la documentation de Qt ; jamais lancé sur un vrai Windows ni un vrai Mac."""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path

import cairo
from PySide6 import QtCore, QtGui, QtWidgets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from astrel import bubble_premium, config, state  # noqa: E402
from astrel import platform as plateforme  # noqa: E402
from astrel import theme as theme_mod  # noqa: E402
from astrel.bulle_commun import (CALME_PAS, COMPACT, FADE_STEP, FPS, SIZE, Feed, answer_refusal,  # noqa: E402
                                 ecrire_position, fallback_look, lire_position, seule_bulle, visible_position)
from astrel.i18n import assistant_name, tr  # noqa: E402
from astrel.minichat import MiniChat  # noqa: E402

log = logging.getLogger("astrel.bulle_qt")
Qt = QtCore.Qt


def _hex(c: tuple[float, float, float], alpha: float | None = None) -> str:
    r, g, b = (round(max(0.0, min(1.0, x)) * 255) for x in c)
    return f"rgba({r}, {g}, {b}, {alpha:.2f})" if alpha is not None else f"rgb({r}, {g}, {b})"


def feuille_de_style(look: bubble_premium.Look) -> str:
    """Cartes et mini-chat aux couleurs du thème (l'équivalent de `theme.css_gtk`)."""
    fond, encre, encre2, alerte, ok = look.fill, look.ink, look.ink_2, look.warn, look.ok
    trait = _hex(look.line, max(0.18, look.line_alpha))
    return f"""
    QWidget#carte, QWidget#chat {{ background: {_hex(fond, 0.97)}; color: {_hex(encre)};
        border: 1px solid {trait}; border-radius: 12px; }}
    QWidget#carte {{ border-color: {_hex(alerte, 0.6)}; }}
    QLabel {{ color: {_hex(encre)}; background: transparent; border: none; }}
    QLabel#tete {{ color: {_hex(alerte)}; font-weight: bold; }}
    QLabel#cmd {{ font-family: monospace; font-size: 10px; color: {_hex(encre2)}; }}
    QLabel#erreur {{ color: #ff9aa8; }}
    QLabel#qui {{ color: {_hex(encre2)}; font-size: 10px; }}
    QLabel#toi {{ color: {_hex(look.states.get(state.LISTENING, ok))}; }}
    QPushButton {{ background: rgba(255, 255, 255, 0.06); color: {_hex(encre)}; border: 1px solid {trait};
        border-radius: 11px; padding: 3px 14px; }}
    QPushButton:hover {{ background: rgba(255, 255, 255, 0.12); }}
    QPushButton#oui {{ border-color: {_hex(ok, 0.7)}; }}
    QPushButton#non {{ border-color: rgba(255, 90, 110, 0.6); }}
    QPushButton#toujours {{ border-color: {_hex(alerte, 0.6)}; }}
    QLineEdit {{ background: rgba(0, 0, 0, 0.35); color: {_hex(encre)}; border: 1px solid {trait};
        border-radius: 8px; padding: 4px 8px; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
    """


def _envoyer(payload: dict | str, fini=None) -> None:
    """Commande au cœur hors du fil de l'interface (la socket peut attendre 3 s) ; `fini(reponse)`
    est rappelé dans le fil de l'interface."""
    brut = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

    def run() -> None:
        reponse = state.send_command(brut)
        if fini is not None:
            _Relais.instance.rappel.emit(lambda: fini(reponse))

    threading.Thread(target=run, daemon=True).start()


class _Relais(QtCore.QObject):
    """Objet du fil de l'interface : les rappels d'autres fils y sont postés."""
    instance: "_Relais"
    changement = QtCore.Signal()
    rappel = QtCore.Signal(object)  # une fonction, appelée dans le fil de l'interface

    def __init__(self) -> None:
        super().__init__()
        self.rappel.connect(lambda f: f())


ouvrir_hud, allumer_astrel = plateforme.ouvrir_hud, plateforme.allumer_astrel


def _ecrans() -> list[tuple[int, int, int, int]]:
    return [(g.x(), g.y(), g.width(), g.height()) for g in (s.availableGeometry() for s in QtGui.QGuiApplication.screens())]


def _place_par_defaut(taille: int = SIZE) -> tuple[int, int] | None:
    ecran = QtGui.QGuiApplication.primaryScreen()
    if ecran is None:
        return None
    g = ecran.availableGeometry()
    return g.x() + g.width() - taille - 40, g.y() + g.height() - taille - 80


def _a_cote(win: QtWidgets.QWidget, ancre: tuple[int, int, int]) -> None:
    """À gauche de la bulle s'il y a la place, sinon à droite ; bas alignés (comme `_place_beside`)."""
    bx, by, taille = ancre
    w, h = win.width(), win.height()
    ecran = QtGui.QGuiApplication.screenAt(QtCore.QPoint(bx + taille // 2, by + taille // 2)) \
        or QtGui.QGuiApplication.primaryScreen()
    g = ecran.availableGeometry()
    x = bx - w - 8 if bx - w - 8 >= g.x() else bx + taille + 8
    y = max(g.y() + 8, min(by + taille - h, g.y() + g.height() - h - 8))
    win.move(int(x), int(y))


def _fenetre_flottante(win: QtWidgets.QWidget, clavier: bool) -> None:
    flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
    if not clavier:
        flags |= Qt.WindowType.WindowDoesNotAcceptFocus
        win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    win.setWindowFlags(flags)
    win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    win.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)  # macOS : visible même inactive


class CarteConfirmation(QtWidgets.QWidget):
    """La question en attente, à côté de la bulle. Ne prend JAMAIS le clavier : on y répond à la
    souris, à la voix, ou dans le HUD."""

    def __init__(self, chat: MiniChat, echec=None) -> None:
        super().__init__()
        self.chat, self.echec = chat, echec
        _fenetre_flottante(self, clavier=False)
        cadre = QtWidgets.QWidget(self)
        cadre.setObjectName("carte")
        dehors = QtWidgets.QVBoxLayout(self)
        dehors.setContentsMargins(0, 0, 0, 0)
        dehors.addWidget(cadre)
        boite = QtWidgets.QVBoxLayout(cadre)
        boite.setContentsMargins(10, 10, 10, 10)
        self.tete = QtWidgets.QLabel(objectName="tete")
        self.question = QtWidgets.QLabel(wordWrap=True)
        self.question.setFixedWidth(300)
        self.cmd = QtWidgets.QLabel(objectName="cmd", wordWrap=True)
        self.cmd.setFixedWidth(300)
        self.erreur = QtWidgets.QLabel(objectName="erreur", wordWrap=True)
        self.erreur.setFixedWidth(300)
        ligne = QtWidgets.QHBoxLayout()
        ligne.addStretch(1)
        self.non = QtWidgets.QPushButton(objectName="non")
        self.toujours = QtWidgets.QPushButton(objectName="toujours")
        self.oui = QtWidgets.QPushButton(objectName="oui")
        for b in (self.non, self.toujours, self.oui):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            ligne.addWidget(b)
        self.non.clicked.connect(lambda: self._repondre(False))
        self.toujours.clicked.connect(lambda: self._repondre(True, True))
        self.oui.clicked.connect(lambda: self._repondre(True))
        self.boutons = QtWidgets.QWidget()
        self.boutons.setLayout(ligne)
        for w in (self.tete, self.question, self.cmd, self.erreur, self.boutons):
            boite.addWidget(w)

    def _textes(self) -> None:
        self.tete.setText(tr("⚠ Confirmation"))
        self.non.setText(tr("Non"))
        self.oui.setText(tr("Oui"))
        self.toujours.setText(tr("Oui 30 min"))
        self.toujours.setToolTip(tr("Ne plus demander pour ce type d'action pendant 30 minutes"))

    def montrer(self, ev: dict, ancre: tuple[int, int, int], dessus: QtWidgets.QWidget | None = None) -> None:
        self._textes()
        self.question.setText(tr(str(ev.get("question") or ev.get("desc") or "Je continue ?")))
        cmd = " ".join(str(ev.get("cmd") or ev.get("tool") or "").split())
        self.cmd.setText(cmd[:400])
        self.cmd.setToolTip(cmd)
        self.cmd.setVisible(bool(cmd))
        self.toujours.setVisible(bool(ev.get("grantable")))
        self.erreur.setText(tr(str(ev.get("error") or "")))
        self.erreur.setVisible(bool(ev.get("error")))
        self.boutons.setVisible(not ev.get("closed"))
        self.adjustSize()
        if dessus is not None and dessus.isVisible():
            self.move(dessus.x(), max(0, dessus.y() - self.height() - 8))
        else:
            _a_cote(self, ancre)
        self.show()

    def _repondre(self, permis: bool, toujours: bool = False) -> None:
        with self.chat.lock:
            ev = dict(self.chat.confirm) if self.chat.confirm else None
        cmd = self.chat.answer_command(permis, toujours)
        self.hide()
        if cmd:
            _envoyer(cmd, lambda reponse: self._repondu(reponse, ev))

    def _repondu(self, reponse: str | None, ev: dict | None) -> None:
        pourquoi = answer_refusal(reponse)
        if pourquoi and ev is not None and self.echec is not None:
            self.echec(ev, pourquoi)


class FenetreChat(QtWidgets.QWidget):
    """Mini-chat : derniers échanges et une ligne pour écrire à Astrel sans ouvrir le HUD."""

    def __init__(self, chat: MiniChat) -> None:
        super().__init__()
        self.chat = chat
        self.en_attente: list[Path] = []
        _fenetre_flottante(self, clavier=True)
        self.setAcceptDrops(True)
        self.resize(360, 320)
        cadre = QtWidgets.QWidget(self)
        cadre.setObjectName("chat")
        dehors = QtWidgets.QVBoxLayout(self)
        dehors.setContentsMargins(0, 0, 0, 0)
        dehors.addWidget(cadre)
        boite = QtWidgets.QVBoxLayout(cadre)
        boite.setContentsMargins(8, 8, 8, 8)
        haut = QtWidgets.QHBoxLayout()
        self.titre = QtWidgets.QLabel()
        self.hud = QtWidgets.QPushButton("HUD")
        self.fermer = QtWidgets.QPushButton("×")
        self.hud.clicked.connect(ouvrir_hud)
        self.fermer.clicked.connect(self.hide)
        haut.addWidget(self.titre, 1)
        haut.addWidget(self.hud)
        haut.addWidget(self.fermer)
        self.liste = QtWidgets.QWidget()
        self.lignes = QtWidgets.QVBoxLayout(self.liste)
        self.lignes.addStretch(1)
        self.defile = QtWidgets.QScrollArea(widgetResizable=True)
        self.defile.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.defile.setWidget(self.liste)
        self.pieces = QtWidgets.QHBoxLayout()
        self.saisie = QtWidgets.QLineEdit()
        self.saisie.returnPressed.connect(self._envoyer)
        boite.addLayout(haut)
        boite.addWidget(self.defile, 1)
        boite.addLayout(self.pieces)
        boite.addWidget(self.saisie)
        QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_Escape), self, self.hide)
        self.saisie.installEventFilter(self)  # Ctrl+V : la QLineEdit garde le raccourci pour elle

    def eventFilter(self, obj: QtCore.QObject, ev: QtCore.QEvent) -> bool:  # noqa: N802
        if obj is self.saisie and ev.type() == QtCore.QEvent.Type.KeyPress \
                and ev.matches(QtGui.QKeySequence.StandardKey.Paste):
            return self._coller()
        return super().eventFilter(obj, ev)

    def _textes(self) -> None:
        self.hud.setToolTip(tr("Ouvrir le HUD complet"))
        self.fermer.setToolTip(tr("Fermer (Échap)"))
        self.saisie.setPlaceholderText(tr("Écris à Astrel… (Entrée ; Ctrl+V colle une image)"))
        self.titre.setText(assistant_name())
        self.setWindowTitle(assistant_name())

    # --- pièces jointes ---

    def _coller(self) -> bool:
        """Une IMAGE dans le presse-papiers : jointe (réduite à ≈ 256 Ko), rend True ; du texte : la
        QLineEdit le colle elle-même (rend False)."""
        image = QtGui.QGuiApplication.clipboard().image()
        if image.isNull():
            return False
        try:
            self._joindre(_jpeg(image), "image-collee.jpg")
        except Exception as exc:
            self.saisie.setPlaceholderText(tr(f"Image non jointe : {exc}"))
        return True

    def dragEnterEvent(self, ev: QtGui.QDragEnterEvent) -> None:  # noqa: N802 (nom imposé par Qt)
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev: QtGui.QDropEvent) -> None:  # noqa: N802
        for url in ev.mimeData().urls():
            try:
                chemin = Path(url.toLocalFile())
                if chemin.is_file():
                    self._joindre(chemin.read_bytes(), chemin.name)
            except Exception as exc:
                self.saisie.setPlaceholderText(tr(f"Fichier non joint : {exc}"))
        self.activateWindow()
        self.saisie.setFocus()

    def _joindre(self, data: bytes, nom: str) -> None:
        from astrel import attachments

        if len(self.en_attente) >= attachments.MAX_FILES:
            raise ValueError(f"{attachments.MAX_FILES} au plus")
        self.en_attente.append(attachments.save_bytes(nom, data))
        self._pieces()

    def _pieces(self) -> None:
        while self.pieces.count():
            w = self.pieces.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for chemin in list(self.en_attente):
            b = QtWidgets.QPushButton("📎 " + chemin.name.split("-", 2)[-1][:24] + "  ×")
            b.setToolTip(tr("Retirer"))
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _c=False, p=chemin: (self.en_attente.remove(p), self._pieces()))
            self.pieces.addWidget(b)

    # --- échanges ---

    def rafraichir(self) -> None:
        while self.lignes.count() > 1:
            w = self.lignes.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for ligne in self.chat.snapshot():
            qui = QtWidgets.QLabel(tr("Toi") if ligne.who == "user" else assistant_name(), objectName="qui")
            texte = QtWidgets.QLabel(ligne.text + (" ▍" if ligne.live else ""), wordWrap=True)
            texte.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            texte.setTextFormat(Qt.TextFormat.PlainText)  # jamais de HTML venu d'une réponse
            if ligne.who == "user":
                texte.setObjectName("toi")
            self.lignes.insertWidget(self.lignes.count() - 1, qui)
            self.lignes.insertWidget(self.lignes.count() - 1, texte)
        QtCore.QTimer.singleShot(0, lambda: self.defile.verticalScrollBar().setValue(
            self.defile.verticalScrollBar().maximum()))

    def _envoyer(self) -> None:
        texte = self.saisie.text().strip()
        envoyees = list(self.en_attente)
        if not texte and not envoyees:
            return
        self.saisie.setEnabled(False)

        def fini(reponse) -> None:
            self.saisie.setEnabled(True)
            try:
                ok = bool(json.loads(reponse or "{}").get("ok"))
            except ValueError:
                ok = False
            if ok and self.saisie.text().strip() == texte:
                self.saisie.clear()  # effacé seulement une fois REÇU, comme dans le HUD
            if ok:
                self.en_attente = [p for p in self.en_attente if p not in envoyees]
                self._pieces()
            else:
                self.saisie.setPlaceholderText(tr("Astrel injoignable : message gardé, réessaie"))
            self.saisie.setFocus()

        payload: dict = {"cmd": "say", "text": texte, "source": "clavier"}
        if envoyees:
            payload["attachments"] = [{"path": str(p)} for p in envoyees]
        _envoyer(payload, fini)

    def basculer(self, ancre: tuple[int, int, int]) -> None:
        if self.isVisible():
            self.hide()
            return
        self._textes()
        self.rafraichir()
        _a_cote(self, ancre)
        self.show()
        self.raise_()
        self.activateWindow()
        self.saisie.setFocus()


def _jpeg(image: QtGui.QImage, max_octets: int = 256 * 1024, max_cote: int = 1568) -> bytes:
    """Image collée → JPEG d'au plus ≈ 256 Ko (côté long ≤ 1568 px), transparence sur fond blanc."""
    fond = QtGui.QImage(image.size(), QtGui.QImage.Format.Format_RGB32)
    fond.fill(Qt.GlobalColor.white)
    p = QtGui.QPainter(fond)
    p.drawImage(0, 0, image)
    p.end()
    echelle = min(1.0, max_cote / max(fond.width(), fond.height(), 1))
    data = b""
    for _ in range(8):
        img = fond if echelle >= 1.0 else fond.scaled(max(1, int(fond.width() * echelle)),
                                                       max(1, int(fond.height() * echelle)),
                                                       Qt.AspectRatioMode.KeepAspectRatio,
                                                       Qt.TransformationMode.SmoothTransformation)
        for qualite in (85, 70, 55):
            tampon = QtCore.QBuffer()
            tampon.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
            img.save(tampon, "JPEG", qualite)
            data = bytes(tampon.data())
            if len(data) <= max_octets:
                return data
        echelle *= 0.75
    return data


def peindre(w: int, h: int, ratio: float, couches: list[tuple[str, float]], t: float, niveau: float,
            maintenant: bubble_premium.Look, avant: bubble_premium.Look | None = None, mix: float = 1.0) -> QtGui.QImage:
    """L'orbe dessinée par `bubble_premium` (cairo) dans une image, posée telle quelle par Qt.
    Pendant un changement de thème, fondu enchaîné de l'ancien au nouveau (comme `bubble._on_draw`)."""
    lw, lh = max(1, round(w * ratio)), max(1, round(h * ratio))
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, lw, lh)
    cr = cairo.Context(surface)
    cr.scale(ratio, ratio)
    if avant is None or mix >= 1.0:
        bubble_premium.draw(cr, w, h, layers=couches, t=t, level=niveau, lk=maintenant)
    else:
        for lk, alpha in ((avant, 1.0 - mix), (maintenant, mix)):
            cr.push_group()
            bubble_premium.draw(cr, w, h, layers=couches, t=t, level=niveau, lk=lk)
            cr.pop_group_to_source()
            cr.paint_with_alpha(max(0.0, min(1.0, alpha)))
    surface.flush()
    # cairo ARGB32 et Qt Format_ARGB32_Premultiplied : même mot de 32 bits prémultiplié ; copie, car
    # la surface est libérée en sortant.
    image = QtGui.QImage(bytes(surface.get_data()), lw, lh, surface.get_stride(),
                         QtGui.QImage.Format.Format_ARGB32_Premultiplied).copy()
    image.setDevicePixelRatio(ratio)
    return image


class Bulle(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        _Relais.instance = _Relais()
        _fenetre_flottante(self, clavier=False)
        self._taille = SIZE
        self.setFixedSize(SIZE, SIZE)
        self.setWindowTitle(assistant_name())
        self.flux = Feed()
        self.flux.on_change = _Relais.instance.changement.emit  # fil du flux → fil de l'interface
        _Relais.instance.changement.connect(self._programmer)
        self._theme_cle: tuple | None = None
        self._theme: bubble_premium.Look = fallback_look()
        self._theme_avant: bubble_premium.Look | None = None
        self._theme_mix = 1.0
        self._images = 0
        self._carte = CarteConfirmation(self.flux.chat, echec=self._echec)
        self._chat = FenetreChat(self.flux.chat)
        self._verifier_theme()
        self._question_montree = ""
        self._avis_clos = ""
        self._clic = QtCore.QTimer(self, singleShot=True, timeout=self._clic_simple)
        self._double = False
        self._appui: QtCore.QPoint | None = None
        self._glisse: QtCore.QPoint | None = None
        self._rafraichir_du = False
        self._t0 = time.monotonic()
        self._niveau = 0.0
        self._etat_montre = state.OFF
        self._couches: dict[str, float] = {state.OFF: 1.0}
        self._restaurer_position()
        self._minuteur = QtCore.QTimer(self, timeout=self._tick)
        self._minuteur.start(int(1000 / FPS))
        QtCore.QTimer(self, timeout=self._remonter, interval=1000).start()
        app = QtWidgets.QApplication.instance()
        for signal in (app.screenAdded, app.screenRemoved):
            signal.connect(lambda *_: QtCore.QTimer.singleShot(400, self._garder_visible))

    # --- position ---

    def _restaurer_position(self) -> None:
        maison = _place_par_defaut()
        x, y = lire_position() or maison or (0, 0)
        cible = visible_position(x, y, SIZE, _ecrans(), maison)
        if cible is not None:
            self.move(*cible)

    def _garder_visible(self) -> None:
        if self._glisse is None:
            cible = visible_position(self.x(), self.y(), self._taille, _ecrans(), _place_par_defaut(self._taille))
            if cible is not None and cible != (self.x(), self.y()):
                self.move(*cible)

    def _ancre(self) -> tuple[int, int, int]:
        return self.x(), self.y(), self._taille

    # --- carte de confirmation, mini-chat ---

    def _programmer(self) -> None:
        """Un seul rafraîchissement en attente à la fois (une réponse arrive mot à mot)."""
        if not self._rafraichir_du:
            self._rafraichir_du = True
            QtCore.QTimer.singleShot(80, self._rafraichir)

    def _rafraichir(self) -> None:
        self._rafraichir_du = False
        ev = self.flux.chat.confirm
        if ev and ev.get("id") != self._question_montree:
            self._question_montree, self._avis_clos = str(ev.get("id")), ""
            self._carte.montrer(ev, self._ancre(), dessus=self._chat)
        elif not ev and self._carte.isVisible() and not self._avis_clos:
            self._question_montree = ""
            self._carte.hide()
        if self._chat.isVisible():
            self._chat.rafraichir()

    def _echec(self, ev: dict, pourquoi: str) -> None:
        cid = str(ev.get("id") or "")
        clos = cid in self.flux.closed_confirms or pourquoi.startswith("aucune confirmation en attente")
        with self.flux.chat.lock:
            if self.flux.chat.confirm is not None:
                return
            if not clos:
                self.flux.chat.confirm = dict(ev)
        montre = {**ev, "error": "Question déjà close : réponse non prise en compte." if clos else pourquoi,
                  "closed": clos}
        self._question_montree, self._avis_clos = cid, (cid if clos else "")
        self._carte.montrer(montre, self._ancre(), dessus=self._chat)
        if clos:
            QtCore.QTimer.singleShot(6000, lambda: self._cacher_carte_close(cid))

    def _cacher_carte_close(self, cid: str) -> None:
        if self._avis_clos == cid:
            self._avis_clos = ""
            if self.flux.chat.confirm is None:
                self._question_montree = ""
                self._carte.hide()

    # --- souris ---

    def _clic_simple(self) -> None:
        self._chat.basculer(self._ancre())

    def mouseDoubleClickEvent(self, ev: QtGui.QMouseEvent) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self._clic.stop()
            self._double = True
            _envoyer("wake")  # double clic : parler tout de suite

    def mousePressEvent(self, ev: QtGui.QMouseEvent) -> None:  # noqa: N802
        pos = ev.globalPosition().toPoint()
        if ev.button() == Qt.MouseButton.LeftButton:
            self._glisse = pos - self.pos()
            self._appui = pos
        elif ev.button() == Qt.MouseButton.RightButton:
            self._menu(pos)
        elif ev.button() == Qt.MouseButton.MiddleButton:
            _envoyer("toggle")

    def mouseMoveEvent(self, ev: QtGui.QMouseEvent) -> None:  # noqa: N802
        if self._glisse is not None:
            self.move(ev.globalPosition().toPoint() - self._glisse)

    def mouseReleaseEvent(self, ev: QtGui.QMouseEvent) -> None:  # noqa: N802
        if ev.button() != Qt.MouseButton.LeftButton or self._glisse is None:
            return
        self._glisse = None
        pos = ev.globalPosition().toPoint()
        depart = self._appui or pos
        if self._double:
            self._double = False
        elif (pos - depart).manhattanLength() <= 4:
            if not self._clic.isActive():  # un clic sans glisser : mini-chat, après le délai d'un double clic
                self._clic.start(QtWidgets.QApplication.doubleClickInterval() + 20)
        else:
            ecrire_position(self.x(), self.y())

    def _remonter(self) -> None:
        if self._glisse is None:
            self.raise_()

    def _menu(self, pos: QtCore.QPoint) -> None:
        menu = QtWidgets.QMenu(self)
        occupe = self.flux.state in (state.SPEAKING, state.THINKING, state.WORKING)
        usage = self.flux.chat.usage
        entrees = [
            ("Mini-chat (clic sur la bulle)", lambda: self._chat.basculer(self._ancre())),
            ("Parler maintenant", lambda: _envoyer("wake")),
            ("Mode conversation" if usage != "conversation" else "Mode silencieux (réponse à l'écran)",
             lambda: _envoyer({"cmd": "usage", "mode": "conversation" if usage != "conversation" else "silencieux"})),
            ("Interrompre", lambda: _envoyer("stop")) if occupe else None,
            ("Réactiver le micro" if self.flux.muted else "Couper le micro", lambda: _envoyer("toggle")),
            ("Reprendre la voix" if self.flux.paused else "Mettre la voix en pause",
             lambda: _envoyer("pause off" if self.flux.paused else "pause on")),
            ("Nouvelle conversation", lambda: _envoyer("reset")),
            ("Ouvrir le HUD", ouvrir_hud),
            (None, None),
            ("Éteindre Astrel (libère la mémoire)" if self.flux.connected else "Allumer Astrel", self._allumer_eteindre),
            (None, None),
            ("Agrandir la bulle" if self._taille == COMPACT else "Réduire la bulle", self._compacte),
            ("Fermer la bulle", QtWidgets.QApplication.quit),
        ]
        for entree in entrees:
            if entree is None:
                continue
            libelle, action = entree
            if libelle is None:
                menu.addSeparator()
            else:
                menu.addAction(tr(libelle), action)
        menu.exec(pos)

    def _allumer_eteindre(self) -> None:
        if self.flux.connected:
            _envoyer("quit")
        else:
            allumer_astrel()

    def _compacte(self) -> None:
        self._taille = COMPACT if self._taille == SIZE else SIZE
        self.setFixedSize(self._taille, self._taille)

    # --- animation ---

    def _tick(self) -> None:
        self._images += 1
        if self._images % 60 == 0:
            self._verifier_theme()
        if self._theme_mix < 1.0:
            self._theme_mix = min(1.0, self._theme_mix + FADE_STEP)
        cible = self.flux.level
        k = 0.45 if cible > self._niveau else 0.12  # montée rapide, descente douce
        self._niveau += (cible - self._niveau) * k
        st = self.flux.state
        if st != self._etat_montre:
            self._etat_montre = st
            self._couches.setdefault(st, 0.0)
        for s in list(self._couches):
            if s == self._etat_montre:
                self._couches[s] = min(1.0, self._couches[s] + FADE_STEP)
            else:
                self._couches[s] -= FADE_STEP
                if self._couches[s] <= 0.0:
                    del self._couches[s]
        calme = (st in (state.SLEEPING, state.OFF) and self._theme_mix >= 1.0 and self._niveau < 0.005
                 and len(self._couches) == 1 and self._couches.get(st, 0.0) >= 1.0)
        if not calme or self._images % CALME_PAS == 0:
            self.update()

    # --- thème ---

    def _systeme_sombre(self) -> bool | None:
        try:
            schema = QtGui.QGuiApplication.styleHints().colorScheme()
        except AttributeError:
            return None
        return {Qt.ColorScheme.Dark: True, Qt.ColorScheme.Light: False}.get(schema)

    def _verifier_theme(self) -> None:
        try:
            choix = theme_mod.current()
            mode = theme_mod.resolved_mode(choix, self._systeme_sombre())
            cle = ("premium", mode, choix["accent"])
            if cle == self._theme_cle:
                return
            nouveau = bubble_premium.look(mode, choix["accent"])
        except Exception as exc:
            log.warning("thème illisible, la bulle garde son apparence : %s: %s", type(exc).__name__, exc)
            return
        if self._theme_cle is not None:
            self._theme_avant, self._theme_mix = self._theme, 0.0
        self._theme_cle, self._theme = cle, nouveau
        style = feuille_de_style(nouveau)
        self._carte.setStyleSheet(style)
        self._chat.setStyleSheet(style)

    def paintEvent(self, _ev: QtGui.QPaintEvent) -> None:  # noqa: N802
        image = peindre(self.width(), self.height(), self.devicePixelRatioF(),
                        [(s, min(1.0, a)) for s, a in self._couches.items() if a > 0.0],
                        time.monotonic() - self._t0, self._niveau, self._theme, self._theme_avant, self._theme_mix)
        p = QtGui.QPainter(self)
        p.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceOver)
        p.drawImage(0, 0, image)
        p.end()


def main() -> int:
    if seule_bulle() is None:
        log.warning("une bulle tourne déjà : celle-ci ne s'ouvre pas")
        return 0
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # fermer le mini-chat ne ferme pas la bulle
    bulle = Bulle()
    bulle.show()
    return app.exec()


if __name__ == "__main__":
    plateforme.en_utf8("astrel.bulle_qt")  # Windows : relancée en mode UTF-8 (03/11)
    sys.exit(main())
