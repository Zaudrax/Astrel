"""Point d'entrée : `./run.sh [options]` ou `python -m astrel`."""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import signal
import sys
import time

from . import audio, config, state


def setup_logging(verbose: bool) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fileh = logging.handlers.RotatingFileHandler(
        config.LOGS / "astrel.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    fileh.setFormatter(fmt)
    root.addHandler(fileh)
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    root.addHandler(stream)
    # Les bibliothèques bavardes n'ont rien à dire à l'utilisateur.
    for noisy in ("aiohttp.access", "httpx", "httpcore", "faster_whisper", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def diag_mode(seconds: float = 12.0, wav: str = "") -> None:
    """Niveau du micro, probabilité de parole et mot d'éveil, en direct.

    25/10 (plan P36) : les deux voies du mot d'éveil, « Astrel » (voie texte) et l'alias
    l'ancien mot d'éveil s'il est actif ; `wav` : rejoue un fichier WAV (mono, 16 bits, 16 kHz) au
    rythme réel par le moteur audio `fichier`, à la place du micro."""
    import os
    import wave

    import numpy as np

    from .i18n import assistant_name
    from .listener import VoiceRecorder, WakeWord

    if wav:
        with wave.open(wav, "rb") as w:
            seconds = w.getnframes() / max(1, w.getframerate()) + 2.0  # + le temps de la transcription
        os.environ["ASTREL_AUDIO_ENGINE"] = "fichier"
        os.environ["ASTREL_AUDIO_IN"] = wav
    rec = VoiceRecorder()
    wake = WakeWord()
    frames = int(seconds * config.SAMPLE_RATE / config.FRAME_SAMPLES)
    alias = " (ou « Hey Jarvis »)" if wake.legacy else ""
    print(f"Parle normalement pendant {seconds:.0f}s. Dis aussi « {assistant_name()} »{alias}.\n")
    model = wake.model_mode != "off"  # voie modèle entraîné (P36 lot 6) : son score en dernière colonne
    print("  niveau                        RMS   VAD   alias" + ("  modèle" if model else ""))
    with audio.MicStream() as mic:
        for _ in range(frames):
            frame = mic.read_frame()
            pcm = np.frombuffer(frame, dtype=np.int16)
            rms = float(np.sqrt((pcm.astype(np.float64) ** 2).mean()))
            prob = rec.speech_prob(frame)
            fired = wake.feed(frame, mic.profile.wake, speech=prob, vad=mic.profile.vad)
            bars = int(min(rms / 120, 24))
            mark = "  PAROLE" if prob >= mic.profile.vad else ""
            hit = ""
            if fired:
                hit = ("  <<< ANCIEN MOT D'ÉVEIL (alias)" if wake.last_source == "alias"
                       else f"  <<< ÉVEIL « {wake.last_name} » ({wake.last_source})")
            extra = f"  {wake.model_score:.2f}  " if model else ""
            print(f"  {'#' * bars:<24} {rms:6.0f}  {prob:.2f}  {wake.last_score:.2f}{extra}{mark}{hit}")
        print(f"\nMicro {mic.identity()[1] or 'du système'} : {mic.profile.describe()}")


def calibrate_mode(noise_s: float = 4.0, speech_s: float = 10.0) -> int:
    """Mesure le micro courant (bruit de fond, puis voix) et retient son profil.

    Pour un micro à plusieurs canaux, garde ceux où le VAD reconnaît la voix (un
    canal mort ou noyé de bruit ne fait que dégrader le mélange de PipeWire).
    Le gain vise une voix normale autour de `TARGET_P90` (RMS, sur 16 bits) : assez
    fort pour que le VAD et le mot de réveil l'entendent, assez bas pour ne pas
    saturer de près (×4 saturait la voix sur le micro USB). Le seuil VAD est réglé
    entre le bruit de fond et la voix mesurés.

    02/11 : la mesure vit dans `astrel/calibrage.py` (partagée avec l'assistant du HUD), mêmes calculs
    et mêmes messages qu'avant."""
    from . import calibrage, micprofile
    from .listener import VoiceRecorder

    rec = VoiceRecorder()
    name, label = audio.MicStream(gain=1.0).identity()
    n = audio.source_channels(name)
    proc = calibrage.ouvrir_flux(name, n)
    try:
        print(f"Micro : {label or name or 'du système'} ({n} {'canaux' if n > 1 else 'canal'})\n")
        print(f"1/2 Reste silencieux {noise_s:.0f} s (bruit de fond)…")
        noise = calibrage.lire(proc, n, noise_s)
        print(f"2/2 Parle normalement {speech_s:.0f} s, comme à Astrel (mais sans dire « Astrel »)…")
        speech = calibrage.lire(proc, n, speech_s)
    finally:
        proc.terminate()

    m = calibrage.analyser(noise, speech, lambda x: calibrage.probas(rec._vad, x))
    if n > 1:
        print("Voix reconnue par canal : " + ", ".join(f"{c}: {m.parts[c]:.0%}" for c in range(n)))
        print(f"Canaux gardés : {'+'.join(map(str, m.canaux)) or 'aucun'}\n")
    if m.erreur:
        print(("\n" if m.erreur == calibrage.PRESQUE_RIEN else "") + m.erreur)
        return 1
    print(f"Bruit de fond : RMS {m.bruit:.0f} (brut), VAD max {m.bruit_vad_max:.2f}")
    print(f"Voix : RMS {m.voix:.0f} (brut, 90e centile), pic {m.pic:.0f}, VAD médian {m.voix_vad_mediane:.2f}")
    print(f"Rapport voix / bruit : {m.rapport:.1f}")
    if m.faible:
        print("⚠ " + calibrage.TROP_FAIBLE)
    prof = micprofile.save(name, label, channels=m.canaux if n > 1 else None, gain=m.gain, vad=m.vad)
    print(f"\nProfil retenu pour ce micro : {prof.describe()}")
    if state.send_command("micprofile") is not None:
        print("Astrel tourne : il a pris ce profil tout de suite.")
    else:
        print("Astrel ne tourne pas : il le prendra à son démarrage.")
    return 0


def voices_mode() -> None:
    """Fait parler toutes les voix utilisables, pour choisir à l'oreille."""
    from .voice import Speaker, available_voices

    phrase = ("Bonjour. J'ai fini de relire le projet, "
              "il reste deux points à vérifier avant de lancer les tests.")
    voices = available_voices()
    if not voices:
        print("Aucune voix installée.")
        return
    print(f"{len(voices)} voix disponibles. Écoute, puis choisis.\n")
    for i, v in enumerate(voices, 1):
        print(f"{i}. {v.label}\n   ASTREL_TTS_ENGINE={v.spec}")
        t0 = time.time()
        sp = Speaker(v.spec, fallback="")
        try:
            sp.load()
            sp.say(phrase, raw=True)
            sp.wait_idle(60)
            print(f"   ({time.time() - t0:.1f}s)\n")
        except Exception as exc:
            print(f"   indisponible : {exc}\n")
        finally:
            sp.close()
    print("Pour garder une voix : le sélecteur du HUD ou ./run.sh --send \"voice <spec>\", "
          "retenu dans runtime/voice.txt.")


def open_hud() -> int:
    """Ouvre le HUD dans une fenêtre d'application (Chrome en mode app)."""
    import shutil
    import subprocess

    from .server import login_url, request_ticket

    if not config.HUD_TOKEN_FILE.exists():
        print("Astrel ne tourne pas : lance-le d'abord (./run.sh ou le service).")
        return 1
    # 15/10 : un ticket à usage unique (60 s) dans la ligne de commande de Chrome,
    # plus le jeton : `ps` montrait `--app=…#t=jeton` à tous les process.
    ticket = request_ticket()
    if not ticket:
        print("Astrel ne répond pas sur le port du HUD : est-il bien lancé ?")
        return 1
    url = login_url(ticket)
    for browser in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(browser):
            # 19/10 : HUD déjà ouvert ? Noté AVANT d'ouvrir la nouvelle fenêtre.
            opened_at, pages = time.time(), _hud_pages(replaceable=True)
            subprocess.Popen(
                # 15/10 (demande : « fais une page HUD plus grande ») : ouverte maximisée,
                # donc à la taille de l'écran ; 1600×1000 en sortie de plein écran.
                [browser, f"--app={url}", "--start-maximized", "--window-size=1600,1000", "--class=AstrelHUD",
                 f"--user-data-dir={config.HUD_PROFILE_DIR}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
            )
            if pages:
                _close_older_huds(opened_at)
            return 0
    subprocess.Popen(["xdg-open", url], start_new_session=True)
    return 0


# 19/10 (demande : « HUD » sur la bulle, ou Ctrl+Alt+H, ouvrait une DEUXIÈME fenêtre
# au lieu de ramener la première). Ramener une fenêtre de Chrome au premier plan est
# impossible de l'extérieur sous GNOME Wayland (Chrome y est client Wayland natif :
# pas de _NET_ACTIVE_WINDOW ; Shell.Eval, Introspect, FocusApp refusés ; un port de
# débogage Chrome laisserait Claude piloter le HUD de l'utilisateur). Donc : la nouvelle
# fenêtre s'ouvre comme avant, et une fois CONNECTÉE, les pages plus anciennes se ferment.
# Ordre voulu : fermer d'abord pouvait faire quitter Chrome pendant qu'il recevait la
# demande d'ouverture (et sans nouvelle page connectée, on ne ferme rien).
HUD_REPLACE_WAIT_S = 15.0
HUD_REPLACE_POLL_S = 0.25


def _core_json(cmd: dict) -> dict | None:
    """Commande JSON au cœur par le canal de contrôle ; sa réponse, ou None."""
    import json

    reply = state.send_command(json.dumps(cmd))
    try:
        data = json.loads(reply) if reply else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _hud_pages(replaceable: bool = False) -> int:
    """Pages du HUD connectées au cœur (0 si Astrel ne répond pas). `replaceable` :
    0 aussi quand le cœur refuse de les fermer (`./hud.sh` lancé par Claude)."""
    data = _core_json({"cmd": "hud_pages"})
    if data is None or data.get("ok") is not True:
        return 0  # Astrel plus ancien, qui ne connaît pas la commande
    if replaceable and data.get("replace") is not True:
        return 0
    pages = data.get("pages")
    return pages if isinstance(pages, int) and not isinstance(pages, bool) and pages > 0 else 0


def _close_older_huds(opened_at: float, wait_s: float | None = None) -> bool:
    """Attend que la nouvelle page se connecte, puis fait fermer celles chargées avant
    `opened_at`. La nouvelle est reconnue à son heure de chargement (`newest`, envoyée
    par chaque page avec `hud_here`), pas au nombre de pages : deux clics rapides ou une
    ancienne page qui se reconnecte faussaient le compte. Faux (rien fermé) si elle
    n'arrive pas à temps : mieux vaut deux fenêtres qu'aucune."""
    deadline = time.monotonic() + (HUD_REPLACE_WAIT_S if wait_s is None else wait_s)
    while time.monotonic() < deadline:
        time.sleep(HUD_REPLACE_POLL_S)
        data = _core_json({"cmd": "hud_pages"}) or {}
        newest = data.get("newest")
        if isinstance(newest, (int, float)) and not isinstance(newest, bool) and newest >= opened_at:
            data = _core_json({"cmd": "hud_replace", "before": opened_at})
            return data is not None and data.get("ok") is True
    return False


def _pretty_number(jid: str) -> str:
    digits = jid.split("@")[0].split(":")[0]
    return f"+{digits}" if digits.isdigit() else jid


def whatsapp_link(phone: str = "") -> int:
    """Appairage du compte WhatsApp de l'utilisateur (plan P1), dans SON terminal : QR
    (ou code à 8 caractères avec `--phone`), puis il confirme le numéro détecté.
    Les identifiants restent dans `runtime/whatsapp-auth/` (0700)."""
    import json
    import os
    import shutil
    import subprocess

    from .whatsapp import bridge_argv

    auth = config.WHATSAPP_AUTH_DIR
    if (auth / "self.json").exists():
        print("WhatsApp est déjà relié. Pour en relier un autre : retire « Astrel » des appareils connectés "
              "sur ton téléphone, efface runtime/whatsapp-auth, puis relance cette commande.")
        return 1
    argv = bridge_argv(link=True, phone=phone)
    if not argv:
        print("Pont WhatsApp introuvable : node absent, ou dépendances à installer (cd whatsapp && npm ci).")
        return 1
    if auth.exists():
        shutil.rmtree(auth)  # appairage précédent inachevé : on repart propre
    auth.mkdir(mode=0o700, parents=True)
    os.chmod(auth, 0o700)
    print("Sur ton téléphone : WhatsApp › Réglages › Appareils connectés › Connecter un appareil.")
    if phone:
        print("Puis « Connecter avec un numéro de téléphone » et tape le code qui va s'afficher.\n")
    else:
        print("Puis scanne le QR qui va s'afficher (il se renouvelle toutes les 20 s environ).\n")
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8",
                            bufsize=1, cwd=str(config.WHATSAPP_BRIDGE_DIR))
    saved = False
    try:
        assert proc.stdout is not None and proc.stdin is not None
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            st = ev.get("state")
            if st == "code":
                print(f"\nCode d'appairage : {ev.get('code')}\n")
            elif st == "linked":
                pn = str(ev.get("pn") or "")
                print(f"\nCompte détecté : {_pretty_number(pn)} {('(' + ev['name'] + ')') if ev.get('name') else ''}")
                try:
                    answer = input("C'est bien TON numéro ? Astrel ne lira que ton chat « message à soi-même ». "
                                   "(oui/non) ").strip().lower()
                except EOFError:
                    answer = ""
                ok = answer in ("o", "oui", "y", "yes")
                proc.stdin.write(json.dumps({"t": "confirm", "ok": ok}) + "\n")
                proc.stdin.flush()
            elif st == "saved":
                saved = True
            elif st == "refused":
                print("Appairage annulé : l'appareil a été délié.")
            elif st == "error":
                print(f"Erreur : {ev.get('reason')}")
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        print("\nInterrompu.")
    finally:
        if proc.poll() is None:
            proc.terminate()
    if not saved:
        shutil.rmtree(auth, ignore_errors=True)  # rien de relié à moitié ne reste sur le disque
        print("Rien n'a été relié.")
        return 1
    print("Relié. Astrel lira ton chat « message à soi-même » quand il tourne.")
    reply = state.send_command("whatsapp on")
    try:
        started = bool(reply) and bool(json.loads(reply).get("ok"))
    except (ValueError, AttributeError):
        started = False
    # Astrel arrêté, ou lancé avant cette version (commande inconnue) : au prochain lancement.
    print("Astrel prévenu : le pont démarre." if started else "Le pont démarrera au prochain lancement d'Astrel.")
    return 0


def whatsapp_main(args: list[str]) -> int:
    """`./run.sh whatsapp link [--phone 336…]` (appairage) ; sinon la commande est
    passée à l'Astrel en marche : status, absent, présent, on, off."""
    sub = args[0].lower() if args else "status"
    if sub == "link":
        phone = ""
        if "--phone" in args[1:]:
            i = args.index("--phone")
            phone = "".join(c for c in (args[i + 1] if i + 1 < len(args) else "") if c.isdigit())
        from .whatsapp import link_lock, link_unlock

        fd = link_lock()  # plan P5 : un seul appairage à la fois, HUD ou terminal
        if fd is None:
            print("Un appairage WhatsApp est déjà en cours (page Réglages du HUD) : termine-le ou annule-le d'abord.")
            return 1
        try:
            return whatsapp_link(phone)
        finally:
            link_unlock(fd)
    reply = state.send_command(f"whatsapp {' '.join(args) or 'status'}")
    print(reply if reply is not None else "Astrel ne tourne pas.")
    return 0 if reply is not None else 1


def text_mode(j) -> None:
    j.boot()
    j.status.set(state.SLEEPING)
    print("Mode texte. Ctrl-D pour quitter.\n")
    try:
        while True:
            try:
                line = input("> ").strip()
            except EOFError:
                break
            if not line:
                continue
            if j.is_stop(line):
                break
            j.handle(line, source="clavier")
            j.wait_turn()
            j.speaker.wait_idle(120)
    finally:
        j.shutdown()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="astrel", description="Astrel — assistant vocal local branché sur Claude Code")
    ap.add_argument("--text", action="store_true", help="mode clavier : ni micro, ni mot de réveil")
    ap.add_argument("--no-voice-out", action="store_true", help="affiche les réponses au lieu de les dire")
    ap.add_argument("--voice", metavar="SPEC", help="voix : edge:fr-FR-HenriNeural, edge:fr-FR-DeniseNeural…")
    ap.add_argument("--say", metavar="TEXTE", help="teste la synthèse vocale et quitte")
    ap.add_argument("--listen", action="store_true", help="teste micro + transcription et quitte")
    ap.add_argument("--diag", action="store_true", help="vumètre micro + VAD + mot de réveil en direct")
    ap.add_argument("--wav", metavar="FICHIER", help="avec --diag : rejoue ce WAV (mono 16 bits 16 kHz) au lieu du micro")
    ap.add_argument("--calibrate", action="store_true", help="mesure le micro courant (silence puis voix) et retient son profil")
    ap.add_argument("--voices", action="store_true", help="fait parler toutes les voix disponibles")
    ap.add_argument("--mics", action="store_true", help="liste les micros disponibles et quitte")
    ap.add_argument("--chimes", action="store_true", help="joue les signaux sonores et quitte")
    ap.add_argument("--hud", action="store_true", help="ouvre le HUD d'Astrel déjà lancé")
    ap.add_argument("--send", metavar="CMD", help="commande à Astrel : toggle, mute, unmute, wake, stop, reset, status, quit, "
                    "\"model sonnet|opus|haiku\", \"pause on|off|toggle\" (voix en pause, reprise où elle s'est arrêtée), "
                    "estop (arrêt d'urgence : stop tout, tâches de fond comprises), \"estop off\" (le lever), "
                    "\"whatsapp absent|présent|on|off|status\" (appairage : ./run.sh whatsapp link)")
    ap.add_argument("--doctor", action="store_true", help="contrôle de santé en lecture seule (hook, garde-fou, MCP, "
                    "droits, socket, coffre) ; code 1 s'il reste une alerte. Aussi : ./run.sh doctor")
    ap.add_argument("--accept", action="store_true", help="avec --doctor : retient l'empreinte actuelle du garde-fou "
                    "(depuis ton terminal seulement, refusé à Claude)")
    ap.add_argument("-v", "--verbose", action="store_true")
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["whatsapp"]:
        return whatsapp_main(argv[1:])  # plan P1 : appairage et réglages WhatsApp
    if argv[:1] == ["annule"]:  # 01/11 : rétablit le dernier lot d'écritures de Claude (`instantanes.py`)
        from . import instantanes

        return instantanes.cli_main(argv[1:])
    if argv[:1] == ["doctor"]:
        argv = ["--doctor", *argv[1:]]
    args = ap.parse_args(argv)

    if args.doctor:
        from . import doctor

        return doctor.main(accept_now=args.accept)
    if args.send:
        reply = state.send_command(args.send)
        print(reply if reply is not None else "Astrel ne tourne pas.")
        return 0 if reply is not None else 1
    if args.hud:
        return open_hud()

    setup_logging(args.verbose)

    if args.mics:
        for m in audio.list_mics():
            print(f"{m['name']}\n    {m['label']}{'  (système)' if m['default'] else ''}")
        print(f"\nActuel : {config.MIC_SOURCE or '(source par défaut du système)'}")
        return 0
    if args.chimes:
        for kind in ("wake", "confirm", "done", "error"):
            print(kind)
            audio.chime(kind)
            time.sleep(0.3)
        return 0
    if args.voices:
        voices_mode()
        return 0
    if args.diag:
        diag_mode(wav=args.wav or "")
        return 0
    if args.calibrate:
        return calibrate_mode()
    if args.say:
        from .voice import Speaker

        sp = Speaker(args.voice)
        sp.load()
        sp.say(args.say, raw=True)
        sp.wait_idle(120)
        sp.close()
        return 0
    if args.listen:
        from .listener import Transcriber, VoiceRecorder

        stt, rec = Transcriber(), VoiceRecorder()
        stt.load()
        with audio.MicStream() as mic:
            print("Parle maintenant…")
            pcm = rec.record(mic)
            print("→", stt.transcribe(pcm) or "(rien entendu)")
        return 0

    from .core import Astrel

    j = Astrel(voice_out=not args.no_voice_out, keyboard=args.text, voice_spec=args.voice)
    signal.signal(signal.SIGTERM, lambda *_: j.shutdown())
    signal.signal(signal.SIGINT, lambda *_: (j.shutdown(), sys.exit(0)))

    if args.text:
        text_mode(j)
        return 0
    try:
        j.run()
    finally:
        j.shutdown()
    if j.restart_requested:
        import os

        logging.getLogger("astrel").info("relance d'Astrel")
        # L'interpréteur de la venv À L'EMPLACEMENT ACTUEL, pas `sys.executable` :
        # après un déplacement du dossier (26/09, vers Studio/), l'ancien chemin
        # n'existe plus et la relance échouait.
        python = str(config.VENV_PYTHON) if config.VENV_PYTHON.exists() else sys.executable
        os.execv(python, [python, "-m", "astrel", *sys.argv[1:]])
    return 0
