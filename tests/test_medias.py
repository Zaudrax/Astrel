"""Médias riches dans le fil du HUD (22/10, plan P26 lot 1a) : publier un fichier (sources
permises, type lu dans les octets, plafonds, copie 0600), le servir par /api/media/<id>
(miniature, page de PDF, Range, CSP), post_report(files), show_media, ask_choice(images),
commandes réservées au HUD (media_open, media_playing : micro retenu)."""
import chemins_machine  # chemins de la machine qui lance la suite
import io
import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

import pytest

from astrel import config, core, medias, reports as reports_mod
from astrel.events import bus
from conftest import mode_prive, remplacer_coeur

PNG_HEAD = b"\x89PNG\r\n\x1a\n"


def _png(w=40, h=30) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), "red").save(buf, "PNG")
    return buf.getvalue()


def _pdf(pages=2) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    imgs = [Image.new("RGB", (200, 100), c) for c in ("red", "blue", "green")[:pages]]
    imgs[0].save(buf, "PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x10\x00\x00\x00\x01\x00\x01\x00\x40\x1f\x00\x00\x80\x3e\x00\x00\x02\x00\x10\x00" \
      + b"data\x00\x00\x00\x00"
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 64


@pytest.fixture
def med(tmp_path, monkeypatch):
    """Copies dans un dossier à part ; source = un dossier de test permis."""
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setattr(medias, "MEDIA_DIR", tmp_path / "medias")
    monkeypatch.setattr(medias, "source_roots", lambda: [src])
    return src


def write(d: Path, name: str, data: bytes) -> Path:
    p = d / name
    p.write_bytes(data)
    return p


def dehors(med: Path) -> str:
    """Fichier qui existe, absolu, hors des dossiers permis (`med` seul) : remplace « /etc/passwd », qui n'est pas
    absolu sous Windows (03/11, CI Windows : « chemin absolu exigé »)."""
    d = med.parent / "ailleurs"
    d.mkdir(exist_ok=True)
    return str(write(d, "passwd", b"root:x:0:0"))


# ------------------------------------------------------------------ type lu dans les octets

@pytest.mark.parametrize("head, want", [
    (PNG_HEAD + b"x" * 8, ("image", "image/png")), (b"\xff\xd8\xff\xe0", ("image", "image/jpeg")),
    (b"GIF89a", ("image", "image/gif")), (b"RIFF\0\0\0\0WEBPVP8 ", ("image", "image/webp")),
    (b"%PDF-1.7", ("pdf", "application/pdf")), (WAV, ("audio", "audio/wav")), (b"ID3\x04", ("audio", "audio/mpeg")),
    (b"\xff\xfb\x90\x00", ("audio", "audio/mpeg")), (b"OggS\0", ("audio", "audio/ogg")), (b"fLaC", ("audio", "audio/flac")),
    (b"\0\0\0\x20ftypM4A \0", ("audio", "audio/mp4")), (MP4, ("video", "video/mp4")),
    (b"\0\0\0\x14ftypqt  ", ("video", "video/quicktime")),
    (b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\x82\x84webm", ("video", "video/webm")),
    (b"\x1a\x45\xdf\xa3\x9f\x42\x82\x88matroska", ("video", "video/x-matroska")),
    (b"<svg xmlns='http://www.w3.org/2000/svg'>", None), (b"<html><script>", None), (b"PK\x03\x04", None),
    (b"\0\0\0\x18ftypheic", None), (b"", None), (b"\xff\xd9", None),
])
def test_type_lu_dans_les_octets(head, want):
    assert medias.sniff(head) == want


def test_le_nom_ne_compte_pas(med):
    d = medias.publish(write(med, "rapport.pdf", _png()))  # une image nommée .pdf
    assert d["kind"] == "image" and d["mime"] == "image/png"
    with pytest.raises(ValueError, match="type non affiché"):
        medias.publish(write(med, "photo.png", b"<svg onload=alert(1)>"))  # du texte nommé .png


# ------------------------------------------------------------------ sources permises

def test_racines_du_plan():
    roots = set(medias.source_roots())
    home = Path.home()
    for want in (config.RUNTIME / "attachments", config.IMAGES_RUNTIME, Path(tempfile.gettempdir()),
                 Path(f"{chemins_machine.PROJETS}"), home / "Images", home / "Téléchargements", home / "Bureau"):
        assert want in roots
    # 03/11 : « /tmp » sous Unix seulement (sous Windows, « \\tmp » à la racine du lecteur n'est pas le temporaire)
    assert (Path("/tmp") in roots) == (os.name != "nt")
    assert home not in roots and config.RUNTIME not in roots


def test_refus_secrets_dossiers_prives_caches(monkeypatch):
    from astrel import guard

    root = config.ROOT.resolve()
    assert medias.refusal(Path("/etc/passwd")) == "hors des dossiers permis"
    assert medias.refusal(Path(f"{chemins_machine.PROJETS}")) == "hors des dossiers permis"  # la racine elle-même
    for secret in guard.SECRET_PATHS:
        if secret.is_relative_to(f"{chemins_machine.PROJETS}"):
            assert medias.refusal(secret) != ""
    monkeypatch.setattr(config, "WORKDIR", root.parent)  # le clone, dans le dossier des projets
    assert "privé" in medias.refusal(root / "config" / "secrets.json") or "secret" in medias.refusal(root / "config" / "secrets.json")
    assert "privé" in medias.refusal(root / "runtime" / "session.json")
    assert "privé" in medias.refusal(root / "logs" / "astrel.log")
    assert "privé" in medias.refusal(root / "vault" / "note.png")
    assert "privé" in medias.refusal(config.IMAGES_PROFILS.resolve() / "gemini" / "Cookies")
    assert medias.refusal((root / "runtime" / "attachments" / "photo.png").resolve()) == ""
    projets = Path(config.WORKDIR)  # lu ici : le dépôt public le change plus haut (le clone, dans les projets)
    assert medias.refusal(projets / "x" / ".git" / "objects" / "a.png") == "fichier ou dossier caché"
    temp = Path(tempfile.gettempdir()).resolve()  # /private/tmp (macOS), %TEMP% (Windows) : chemin RÉSOLU attendu
    assert medias.refusal(temp / ".cache" / "a.png") == "fichier ou dossier caché"
    assert medias.refusal(projets / "x" / "api_keys.png") == "le nom ressemble à un secret"
    assert medias.refusal(projets / "x" / "photo.png") == ""


def test_lien_sortant_refuse(med, tmp_path):
    outside = write(tmp_path, "dehors.png", _png())
    (med / "lien.png").symlink_to(outside)
    with pytest.raises(ValueError, match="hors des dossiers permis"):
        medias.publish(med / "lien.png")
    with pytest.raises(ValueError, match="chemin absolu"):
        medias.publish("src/photo.png")
    with pytest.raises(ValueError, match="introuvable"):
        medias.publish(med / "rien.png")
    with pytest.raises(ValueError, match="vide"):
        medias.publish(write(med, "vide.png", b""))
    if hasattr(os, "mkfifo"):  # 03/11 : pas de tube nommé dans le système de fichiers sous Windows
        with pytest.raises(ValueError, match="ordinaire"):
            os.mkfifo(med / "tube.png")
            medias.publish(med / "tube.png")


# ------------------------------------------------------------------ publication

def test_copie_privee_et_descripteur(med):
    png = _png(40, 30)
    d = medias.publish(write(med, "vue‮.png", png))
    assert medias.ID_RE.fullmatch(d["id"]) and d["name"] == "vue_.png"  # contrôle bidi retiré
    assert (d["kind"], d["mime"], d["size"], d["w"], d["h"], d["open_only"]) == ("image", "image/png", len(png), 40, 30, False)
    copy = medias.MEDIA_DIR / f"{d['id']}.png"
    assert copy.read_bytes() == png and mode_prive(copy)
    assert mode_prive(medias.MEDIA_DIR / f"{d['id']}.json")
    assert mode_prive(medias.MEDIA_DIR, 0o700)
    assert medias.get(d["id"])["path"] == str(med / "vue‮.png")
    assert medias.get("../" + d["id"]) is None and medias.get(d["id"].upper()) is None and medias.get(None) is None


def test_nom_long_coupe(med):
    d = medias.publish(write(med, "a" * 200 + ".png", _png()))
    assert len(d["name"]) <= medias.NAME_MAX and d["name"].endswith("….png")


def test_plafonds_et_budget_ouvrir_seul(med, monkeypatch):
    monkeypatch.setitem(medias.LIMITS, "video", 50)
    d = medias.publish(write(med, "film.mp4", MP4 * 2))
    assert d["kind"] == "video" and d["open_only"] and not list(medias.MEDIA_DIR.glob(f"{d['id']}.mp4"))
    assert medias.serve(d["id"]) is None  # rien à servir
    budget = [len(_png()) + 10]
    a = medias.publish(write(med, "a.png", _png()), budget)
    b = medias.publish(write(med, "b.png", _png()), budget)
    assert not a["open_only"] and b["open_only"]


def test_publier_plusieurs_et_resume(med):
    ok, refused = medias.publish_many([str(write(med, "a.png", _png())), dehors(med), str(write(med, "n.txt", b"bonjour")),
                                       ""] + [str(med / "a.png")] * 20)
    assert len(ok) + len(refused) == medias.MAX_ITEMS
    assert {"fichier": "passwd", "raison": "hors des dossiers permis"} in refused
    assert any(r["fichier"] == "n.txt" and "type non affiché" in r["raison"] for r in refused)
    s = medias.summary(ok, refused)
    assert s["acceptes"][0] == "a.png (image)" and len(json.dumps(s, ensure_ascii=False).encode()) < 4096
    assert medias.publish_many(None) == ([], []) and medias.publish_many(42) == ([], [])


def test_purge_sept_jours_et_deux_go(med, monkeypatch):
    old = medias.publish(write(med, "vieux.png", _png()))
    new = medias.publish(write(med, "neuf.png", _png()))
    past = time.time() - medias.KEEP_S - 60
    for f in medias.MEDIA_DIR.glob(f"{old['id']}.*"):
        os.utime(f, (past, past))
    assert medias.purge() == 1 and medias.get(old["id"]) is None and medias.get(new["id"])
    monkeypatch.setattr(medias, "MAX_TOTAL", 10)
    assert medias.purge() == 1 and medias.get(new["id"]) is None


# ------------------------------------------------------------------ variantes servies

# 03/11 (CI macOS, Linux) : sans poppler, pas d'aperçu de PDF ; ces tests demandent les vrais pdfinfo et pdftoppm.
poppler = pytest.mark.skipif(not (shutil.which("pdfinfo") and shutil.which("pdftoppm")), reason="poppler absent")


@poppler
def test_miniature_page_de_pdf_et_original(med):
    img = medias.publish(write(med, "grand.png", _png(1600, 900)))
    path, mime = medias.serve(img["id"], "thumb")
    from PIL import Image

    with Image.open(path) as im:
        assert mime == "image/webp" and max(im.size) == medias.THUMB_PX
    assert medias.serve(img["id"], "page", "1") is None and medias.serve(img["id"], "autre") is None
    pdf = medias.publish(write(med, "doc.pdf", _pdf(2)))
    assert pdf["kind"] == "pdf" and pdf["pages"] == 2
    path, mime = medias.serve(pdf["id"], "page", "2")
    assert mime == "image/png" and path.read_bytes()[:8] == PNG_HEAD and mode_prive(path)
    assert medias.serve(pdf["id"], "page", "2")[0] == path  # en cache
    for bad in ("0", "3", "-1", "x", "99999"):
        assert medias.serve(pdf["id"], "page", bad) is None
    assert medias.serve(pdf["id"], "thumb") is None
    assert medias.serve(pdf["id"])[1] == "application/pdf"
    snd = medias.publish(write(med, "son.wav", WAV))
    assert medias.serve(snd["id"]) == (medias.MEDIA_DIR / f"{snd['id']}.wav", "audio/wav")


def test_pdf_illisible_sans_page(med):
    d = medias.publish(write(med, "faux.pdf", b"%PDF-1.4\nrien du tout"))
    assert d["kind"] == "pdf" and d["pages"] is None and medias.serve(d["id"], "page", "1") is None


def test_poppler_borne_en_memoire_et_en_temps(monkeypatch):
    if shutil.which("prlimit"):
        cmd = medias._limited(["pdftoppm", "x"])
        assert cmd[1].startswith("--as=") and cmd[2] == "--" and cmd[3:] == ["pdftoppm", "x"]
    assert medias.POPPLER_TIMEOUT_S <= 10


def test_ouvrir_la_copie_ou_l_original_revérifié(med, monkeypatch):
    d = medias.publish(write(med, "a.png", _png()))
    assert medias.open_target(d["id"]) == medias.MEDIA_DIR / f"{d['id']}.png"
    monkeypatch.setitem(medias.LIMITS, "image", 10)
    big = medias.publish(write(med, "b.png", _png()))
    assert big["open_only"] and medias.open_target(big["id"]) == (med / "b.png").resolve()
    (med / "b.png").write_bytes(b"plus une image")
    assert medias.open_target(big["id"]) is None  # le contenu a changé de type
    assert medias.open_target("0" * 24) is None


# ------------------------------------------------------------------ correctifs après audit et test (fiches 1 et 2)

def test_ouvrir_seul_refuse_une_extension_qui_ne_colle_pas(med, monkeypatch):
    """Fiche 1 (sécurité) : un original « Ouvrir seul » nommé .html (contenu PNG) serait ouvert
    par xdg-open dans le navigateur, d'après son nom. Refusé à la publication ET à l'ouverture."""
    monkeypatch.setitem(medias.LIMITS, "image", 10)
    for name in ("evil.html", "lanceur.desktop", "dessin.svg", "sans_extension", "photo.png.html"):
        with pytest.raises(ValueError, match="extension"):
            medias.publish(write(med, name, _png()))
    for name in ("photo.JPG", "photo.jpeg"):
        jpg = io.BytesIO()
        from PIL import Image

        Image.new("RGB", (20, 20)).save(jpg, "JPEG")
        d = medias.publish(write(med, name, jpg.getvalue()))
        assert d["open_only"] and medias.open_target(d["id"]) == (med / name).resolve()
    # descripteur écrit avant le correctif (ou retouché) : l'ouverture revérifie
    ok = medias.publish(write(med, "b.png", _png()))
    meta = medias.get(ok["id"])
    write(med, "evil2.html", _png())
    meta["path"] = str(med / "evil2.html")
    (medias.MEDIA_DIR / f"{ok['id']}.json").write_text(json.dumps(meta))
    assert medias.open_target(ok["id"]) is None


def test_lien_dur_refuse(med, monkeypatch):
    """Fiche 1 : un lien dur dans un dossier permis peut désigner un fichier interdit
    (même disque) ; le chemin ne le dit pas, le compte de liens si."""
    a = write(med, "a.png", _png())
    os.link(a, med / "b.png")
    for p in (a, med / "b.png"):
        with pytest.raises(ValueError, match="lien dur"):
            medias.publish(p)
    monkeypatch.setitem(medias.LIMITS, "image", 10)
    big = medias.publish(write(med, "c.png", _png()))
    assert medias.open_target(big["id"]) == (med / "c.png").resolve()
    os.link(med / "c.png", med / "c2.png")
    assert medias.open_target(big["id"]) is None


def test_miniature_plafond_de_pixels(med, monkeypatch):
    """Fiche 1 : une PNG de 8000×8000 coûtait 537 Mo au cœur. Plafond ~25 Mpx compté APRÈS
    le décodage réduit (`draft`) : un grand JPEG garde sa miniature, une grande PNG non."""
    assert medias.THUMB_MAX_PIXELS <= 25_000_000
    monkeypatch.setattr(medias, "THUMB_MAX_PIXELS", 1_000_000)
    from PIL import Image

    jpg = io.BytesIO()
    Image.new("RGB", (2000, 2000), "blue").save(jpg, "JPEG")
    big_jpg = medias.publish(write(med, "grand.jpg", jpg.getvalue()))
    big_png = medias.publish(write(med, "grand.png", _png(2000, 2000)))
    assert medias.serve(big_jpg["id"], "thumb") is not None  # décodé au 1/4 : 250 000 px
    assert medias.serve(big_png["id"], "thumb") is None


@poppler
def test_echec_poppler_en_cache(med, monkeypatch):
    """Fiche 1 : un PDF piégé (10 s de calcul, 1 Go) ne doit pas relancer poppler à chaque
    requête du HUD : l'échec est retenu."""
    import subprocess

    pdf = medias.publish(write(med, "d.pdf", _pdf(2)))
    calls = []

    def boom(cmd, **k):
        calls.append(cmd)
        raise subprocess.TimeoutExpired(cmd, 10)

    monkeypatch.setattr(medias.subprocess, "run", boom)
    for _ in range(3):
        assert medias.serve(pdf["id"], "page", "1") is None
    assert len(calls) == 1
    assert medias.serve(pdf["id"], "page", "2") is None and len(calls) == 2


@poppler
def test_poppler_en_parallele_borne(med, monkeypatch):
    """Fiche 1 : six pages demandées d'un coup = au plus POPPLER_PARALLEL processus poppler."""
    a = medias.publish(write(med, "a.pdf", _pdf(3)))
    b = medias.publish(write(med, "b.pdf", _pdf(3)))
    state = {"now": 0, "max": 0}
    lock = threading.Lock()

    def slow(cmd, **k):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.15)
        with lock:
            state["now"] -= 1
        raise medias.subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(medias.subprocess, "run", slow)
    threads = [threading.Thread(target=medias.serve, args=(d["id"], "page", str(n))) for d in (a, b) for n in (1, 2, 3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert 1 <= state["max"] <= medias.POPPLER_PARALLEL <= 2


def test_dimensions_photo_pivotee_exif(med):
    """Fiche 2 : photo de téléphone 600×300 marquée « tourner de 90° » (orientation 6) : la
    miniature est en portrait, le descripteur doit l'être aussi (sinon le cadre a le mauvais ratio)."""
    from PIL import Image

    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.new("RGB", (600, 300), "green").save(buf, "JPEG", exif=exif)
    d = medias.publish(write(med, "rot.jpg", buf.getvalue()))
    assert (d["w"], d["h"]) == (300, 600)
    path, _ = medias.serve(d["id"], "thumb")
    with Image.open(path) as im:
        assert im.width < im.height


def test_libelle_du_refus_est_le_nom():
    assert medias.label(f"{chemins_machine.PROJETS}/un/très/long/chemin/" + "x" * 80 + "/photo.png") == "photo.png"
    assert medias.label("") == "" and medias.label(None) == ""


# ------------------------------------------------------------------ le cœur

@pytest.fixture
def astrel(monkeypatch, med):
    from test_pump import make_astrel

    j, said = make_astrel(monkeypatch)
    monkeypatch.setattr(core.sensors, "is_on", lambda name: False)
    j.brain.send = lambda *a, **k: None
    j._send_lock = threading.Lock()
    return j, said


@pytest.fixture
def published():
    out = []
    unsub = bus.subscribe(out.append)
    yield out
    unsub()


def test_post_report_montre_les_fichiers(astrel, published, med):
    j, said = astrel
    png, pdf, passwd = write(med, "vue.png", _png()), write(med, "doc.pdf", _pdf(1)), dehors(med)
    r = j.on_command({"cmd": "report", "text": "Voici le rendu.", "kind": "resultat",
                      "files": [str(png), str(pdf), passwd]})
    ev = [e for e in published if e["type"] == "report"][-1]
    assert ev["report_files"] == [str(png), str(pdf), passwd]
    assert [m["kind"] for m in ev["report_media"]] == ["image", "pdf"]
    assert r["acceptes"] == ["vue.png (image)", "doc.pdf (PDF)"]
    assert r["refuses"] == [{"fichier": "passwd", "raison": "hors des dossiers permis"}]
    assert passwd not in " ".join(said)
    r = j.on_command({"cmd": "report", "text": "Sans fichier."})
    ev = [e for e in published if e["type"] == "report"][-1]
    assert "report_media" not in ev and "acceptes" not in r


def test_show_media_carte_sans_voix(astrel, published, med):
    j, said = astrel
    r = j.on_command({"cmd": "show_media", "items": [str(write(med, "a.png", _png())), str(write(med, "b.wav", WAV))],
                      "title": "Deux\x1b[31m fichiers‮", "_from_claude": True})
    ev = [e for e in published if e["type"] == "media"][-1]
    assert r["ok"] and ev["mid"] == r["mid"] and ev["title"] == "Deux [31m fichiers"
    assert [m["kind"] for m in ev["items"]] == ["image", "audio"] and said == []
    assert any(e.get("mid") == r["mid"] for e in bus.snapshot()["history"])  # rejoué à la reconnexion
    bad = j.on_command({"cmd": "show_media", "items": [dehors(med)]})
    assert not bad["ok"] and bad["refuses"][0]["raison"] == "hors des dossiers permis"
    assert not j.on_command({"cmd": "show_media", "items": [str(med / "a.png")], "channel": "inconnu"})["ok"]


def test_question_a_choix_avec_images(astrel, published, med):
    j, _ = astrel
    a, b = write(med, "a.png", _png()), write(med, "b.png", _png())
    r = j.on_command({"cmd": "ask_choice", "question": "Quel logo ?", "options": ["Bleu", "bleu", "Rouge", "Vert"],
                      "images": [str(a), str(a), str(b), str(write(med, "c.wav", WAV))]})
    ev = [e for e in published if e["type"] == "choice"][-1]
    assert r["ok"] and [o["label"] for o in ev["options"]] == ["Bleu", "Rouge", "Vert"]
    assert ev["options"][0]["image"]["kind"] == "image" and ev["options"][1]["image"]["kind"] == "image"
    assert "image" not in ev["options"][2]  # un son n'est pas une image de galerie
    assert r["images_refusees"][0] == {"fichier": "c.wav", "raison": "pas une image affichable"}  # le nom, pas la fin du chemin
    r2 = j.on_command({"cmd": "ask_choice", "question": "Sans image ?", "options": ["Oui", "Non"]})
    ev = [e for e in published if e["type"] == "choice"][-1]
    assert r2["ok"] and all("image" not in o for o in ev["options"]) and "images_refusees" not in r2


class FakeMic:
    def __init__(self):
        self.depth = 0

    def hold_mute(self):
        self.depth += 1

    def release_mute(self):
        self.depth -= 1


def test_commandes_reservees_au_hud(astrel, med, monkeypatch):
    j, _ = astrel
    j.mic = FakeMic()
    d = medias.publish(write(med, "a.png", _png()))
    for cmd in ({"cmd": "media_open", "media_id": d["id"]}, {"cmd": "media_playing", "playing": True}):
        r = j.on_command({**cmd, "_from_claude": True})
        assert not r["ok"] and "réservé au HUD" in r["error"]
    assert j.mic.depth == 0
    from astrel.tools import desktop

    opened = []
    monkeypatch.setattr(desktop, "open_path", lambda p: opened.append(p) or "Ouvert")
    assert j.on_command({"cmd": "media_open", "media_id": d["id"], "_from_claude": False})["ok"]
    assert opened == [str(medias.MEDIA_DIR / f"{d['id']}.png")]
    assert not j.on_command({"cmd": "media_open", "media_id": "../x", "_from_claude": False})["ok"]


def test_micro_retenu_pendant_la_lecture(astrel, monkeypatch):
    j, _ = astrel
    j.mic = FakeMic()
    play = lambda on, conn="c1", **k: j.on_command({"cmd": "media_playing", "playing": on, "_conn": conn,
                                                    "_from_claude": False, **k})
    assert play(True)["micro_retenu"] and j.mic.depth == 1
    play(True)  # second signal de la même page : pas de second niveau
    play(True, "c2")
    assert j.mic.depth == 2
    play(False)
    play(False)  # déjà relâché : sans effet
    assert j.mic.depth == 1
    # page fermée : le serveur relâche la connexion (`_gone`)
    play(False, "c2", _gone=True)
    assert j.mic.depth == 0
    # garde-fou : 4 h au plus
    remplacer_coeur(monkeypatch, "MEDIA_HOLD_MAX_S", 0.05)
    play(True, "c3")
    assert j.mic.depth == 1
    deadline = time.time() + 2
    while j.mic.depth and time.time() < deadline:
        time.sleep(0.02)
    assert j.mic.depth == 0 and not j._media_holds


def test_outils_mcp(monkeypatch):
    from astrel import mcp_server

    sent = []
    monkeypatch.setattr(mcp_server, "_core", lambda cmd, **k: sent.append(cmd) or {"ok": True})
    mcp_server.show_media([f"/tmp/{i}.png" for i in range(14)], "Titre", "Studio")
    assert sent[-1]["cmd"] == "show_media" and len(sent[-1]["items"]) == 10 and sent[-1]["channel"] == "Studio"
    mcp_server.show_media("/tmp/une.png")  # fiche 2 : une chaîne seule n'est pas découpée en caractères
    assert sent[-1]["items"] == ["/tmp/une.png"]
    mcp_server.ask_choice("Q ?", ["a", "b"], images=["/tmp/a.png", "", "/tmp/c.png", "/tmp/d.png", "/tmp/e.png"])
    assert sent[-1]["images"] == ["/tmp/a.png", "", "/tmp/c.png", "/tmp/d.png"]
    mcp_server.ask_choice("Q ?", ["a", "b"])
    assert "images" not in sent[-1]
    # 10 chemins de 300 caractères tiennent dans le canal de contrôle (8 Ko)
    raw = json.dumps({"cmd": "show_media", "items": ["/" + "é" * 299] * 10, "title": "t" * 200, "channel": "x"},
                     ensure_ascii=False)
    assert len(raw.encode()) <= mcp_server.CONTROL_MAX_BYTES


def test_media_dans_l_historique():
    from astrel import events

    assert "media" in events.HISTORY and "media" not in events.STICKY


def test_consigne_du_prompt():
    from astrel.brain import VOICE_SYSTEM_PROMPT as p

    assert "show_media" in p and "n'en dis jamais le chemin" in p


# ------------------------------------------------------------------ la route du serveur

@poppler
def test_route_media_du_hud(med, monkeypatch, tmp_path):
    import asyncio

    import aiohttp

    from astrel.server import HudServer

    monkeypatch.setattr(config, "HUD_TOKEN_FILE", tmp_path / "hud-token")  # piège du CLAUDE.md
    monkeypatch.setattr(config, "HUD_PORT", 47196)
    png = _png(900, 600)
    img = medias.publish(write(med, "a.png", png))
    pdf = medias.publish(write(med, "d.pdf", _pdf(1)))
    vid = medias.publish(write(med, "v.mp4", MP4 * 4))
    srv = HudServer()
    srv.start()
    try:
        async def get(path, token=True, origin=None, rng=None):
            headers = {"X-Astrel-Token": srv.token} if token else {}
            if origin:
                headers["Origin"] = origin
            if rng:
                headers["Range"] = rng
            async with aiohttp.ClientSession() as s:
                async with s.get(f"http://127.0.0.1:47196{path}", headers=headers) as r:
                    return r.status, dict(r.headers), await r.read()

        async def go():
            return {
                "img": await get(f"/api/media/{img['id']}"),
                "thumb": await get(f"/api/media/{img['id']}?v=thumb"),
                "page": await get(f"/api/media/{pdf['id']}?v=page&n=1"),
                "page_hors": await get(f"/api/media/{pdf['id']}?v=page&n=2"),
                "range": await get(f"/api/media/{vid['id']}", rng="bytes=4-11"),
                "etranger": await get(f"/api/media/{img['id']}", origin="https://site-malveillant.example"),
                "sans_jeton": await get(f"/api/media/{img['id']}", token=False),
                "inconnu": await get("/api/media/" + "0" * 24),
                "mal_forme": await get("/api/media/..%2F..%2Fconfig"),
                "json": await get(f"/api/media/{img['id']}.json"),
            }

        got = asyncio.run(go())
    finally:
        srv.stop()
    status, headers, body = got["img"]
    assert status == 200 and body == png and headers["Content-Type"] == "image/png"
    # 04/11 (lot B, M4) : le serveur ajoute `frame-ancestors 'none'` à toute CSP (jamais dans un cadre).
    assert headers["Content-Security-Policy"] == "default-src 'none'; sandbox; frame-ancestors 'none'"
    assert headers["X-Content-Type-Options"] == "nosniff" and headers["Content-Disposition"] == "inline"
    assert got["thumb"][0] == 200 and got["thumb"][1]["Content-Type"] == "image/webp"
    assert got["page"][0] == 200 and got["page"][2][:8] == PNG_HEAD
    assert got["range"][0] == 206 and got["range"][2] == b"ftypisom" and got["range"][1]["Content-Type"] == "video/mp4"
    assert got["etranger"][0] == 403 and got["sans_jeton"][0] == 401
    assert got["inconnu"][0] == got["mal_forme"][0] == got["json"][0] == got["page_hors"][0] == 404


def test_page_fermee_relache_le_micro(monkeypatch, tmp_path):
    """Le serveur relâche le micro de la connexion qui s'en va, s'il a servi."""
    import asyncio

    import aiohttp

    from astrel.server import HudServer

    monkeypatch.setattr(config, "HUD_TOKEN_FILE", tmp_path / "hud-token")
    monkeypatch.setattr(config, "HUD_PORT", 47197)
    seen = []
    old = bus._command_handler
    bus.set_command_handler(lambda cmd: seen.append(cmd) or {"ok": True})
    srv = HudServer()
    srv.start()
    try:
        async def go(play):
            async with aiohttp.ClientSession() as s:
                async with s.ws_connect(f"http://127.0.0.1:47197/ws?t={srv.token}") as ws:
                    await ws.receive()  # hello
                    if play:
                        await ws.send_json({"cmd": "media_playing", "playing": True, "_conn": "falsifie", "id": 1})
                        await ws.receive()
            await asyncio.sleep(0.3)

        asyncio.run(go(False))
        assert not [c for c in seen if c.get("cmd") == "media_playing"]
        asyncio.run(go(True))
    finally:
        srv.stop()
        bus.set_command_handler(old)
    plays = [c for c in seen if c.get("cmd") == "media_playing"]
    assert [c["playing"] for c in plays] == [True, False] and plays[1].get("_gone")
    assert plays[0]["_conn"] == plays[1]["_conn"] != "falsifie" and plays[1]["_from_claude"] is False
