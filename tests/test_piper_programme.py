"""Piper en PROGRAMME SÉPARÉ (02/11, licence ; choix de l'utilisateur). Piper (`piper-tts`) est sous GPL-3.0 :
l'importer ferait d'Astrel (PolyForm Noncommercial) une œuvre combinée. Astrel lance donc la commande
`piper` et lui parle par un tube, sans jamais importer un de ses modules. Les tests remplacent piper par
un faux programme qui imite son CLI (`--output-dir`, « Wrote … » dans le journal) : aucun vrai son."""
import ast
import json
import os
import re
import shlex
import sys
import textwrap
import time
import wave
from pathlib import Path

import pytest

from astrel import config
from astrel.voice import engines
from astrel.voice import speaker as speaker_mod
from astrel.voice.engines import PiperEngine

ROOT = Path(__file__).resolve().parent.parent

FAUX_PIPER = textwrap.dedent('''
    import argparse, logging, os, sys, time, wave
    ap = argparse.ArgumentParser()
    ap.add_argument("-m", "--model", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("-s", "--speaker", type=int)
    ap.add_argument("--length-scale", type=float)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("__main__")
    with open(os.environ["FAUX_PIPER_JOURNAL"], "a", encoding="utf-8") as f:
        f.write(" ".join(sys.argv[1:]) + "\\n")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        with open(os.environ["FAUX_PIPER_JOURNAL"], "a", encoding="utf-8") as f:
            f.write("LIGNE " + line + "\\n")
        if "PLANTE" in line:
            sys.exit(3)
        if "BLOQUE" in line:
            time.sleep(30)
        path = os.path.join(a.output_dir, f"{time.monotonic_ns()}.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(b"\\x01\\x00" * (100 * len(line)))   # durée = longueur du texte
        log.info("Wrote %s", path)
''')


@pytest.fixture
def faux(tmp_path, monkeypatch):
    models = tmp_path / "models"
    (models / "piper").mkdir(parents=True)
    for voix in ("fr_FR-upmc-medium", "en_US-joe-medium"):
        (models / "piper" / f"{voix}.onnx").write_bytes(b"onnx")
        (models / "piper" / f"{voix}.onnx.json").write_text(json.dumps({"audio": {"sample_rate": 22050}}))
    script = tmp_path / "faux_piper.py"
    script.write_text(FAUX_PIPER, encoding="utf-8")
    journal = tmp_path / "journal.txt"
    journal.write_text("")
    monkeypatch.setattr(config, "MODELS", models)
    monkeypatch.setattr(config, "PIPER_CMD", f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}")
    monkeypatch.setenv("FAUX_PIPER_JOURNAL", str(journal))
    made = []

    def nouveau(voix="fr_FR-upmc-medium", **kw):
        e = PiperEngine(voix, **kw)
        made.append(e)
        return e

    nouveau.journal = journal
    yield nouveau
    for e in made:
        e.close()


def octets(text):
    return 100 * len(text) * 2


# --- aucune liaison avec Piper ---

_IMPORT_DYNAMIQUE = re.compile(r"""(import_module|__import__)\(\s*["']piper\b""")


def test_aucun_import_de_piper_dans_le_code_publie():
    """Ni `import piper`, ni `from piper…`, ni import dynamique, dans tout le code Python du dépôt
    (les venvs exceptés)."""
    fautes = []
    for tree in ("astrel", "tools", "tests", "hooks"):
        for path in (ROOT / tree).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            src = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(src, str(path))):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                fautes += [f"{path.relative_to(ROOT)}:{node.lineno}" for n in names
                           if n == "piper" or n.startswith("piper.")]
            if path.name != Path(__file__).name and _IMPORT_DYNAMIQUE.search(src):
                fautes.append(f"{path.relative_to(ROOT)} (import dynamique)")
    for path in ROOT.glob("*.py"):
        if re.search(r"^\s*(import|from)\s+piper\b", path.read_text(encoding="utf-8"), re.M):
            fautes.append(path.name)
    assert fautes == []


def test_commande_piper(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PIPER_CMD", "/opt/piper/piper --cuda")
    assert engines.piper_command() == ["/opt/piper/piper", "--cuda"]
    monkeypatch.setattr(config, "PIPER_CMD", "")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "piper").write_text("")
    monkeypatch.setattr(sys, "executable", str(bindir / "python"))
    assert engines.piper_command() == [str(bindir / "piper")]  # celui de la venv d'Astrel


def test_decoupe_en_lignes():
    assert engines._piper_lines("Bonjour.\nÇa va ?  Oui… très bien. …") == [
        "Bonjour.", "Ça va ?", "Oui…", "très bien."]
    assert engines._piper_lines(" … - ; ") == []  # le CLI planterait sur une ligne sans mot


# --- le process ---

def test_synthese_par_le_programme_persistant(faux):
    e = faux()
    e.load()
    assert e.sample_rate == 16000  # celui du WAV fait foi (la fiche .onnx.json ne sert qu'avant)
    pid = e._proc.pid
    blocs = list(e.synthesize("Bonjour. La réunion est à quinze heures."))
    assert [len(b) for b in blocs] == [octets("Bonjour."), octets("La réunion est à quinze heures.")]
    assert e.sample_rate == 16000  # celui du WAV fait foi
    assert len(list(e.synthesize("Encore."))) == 1
    assert e._proc.pid == pid  # un seul process, gardé ouvert
    args = faux.journal.read_text().splitlines()[0]
    assert "-m " in args and "fr_FR-upmc-medium.onnx" in args and "--length-scale 1.0000" in args
    assert not os.listdir(e._dir)  # chaque WAV lu est effacé


def test_lance_par_enfants_popen(faux, monkeypatch):
    """Process long, enfant du cœur : `platform.enfants.popen` (il meurt avec le cœur)."""
    from astrel.platform import enfants

    vus = []
    vrai = enfants.popen
    monkeypatch.setattr(enfants, "popen", lambda args, **kw: vus.append(args) or vrai(args, **kw))
    e = faux()
    e.load()
    assert len(vus) == 1 and "--output-dir" in vus[0]


def test_locuteur_et_vitesse(faux):
    e = faux(speaker=1, length_scale=0.8)
    e.load()
    args = faux.journal.read_text().splitlines()[0]
    assert "-s 1" in args and "--length-scale 0.8000" in args


def test_voix_anglaise(faux):
    e = faux("en_US-joe-medium")
    assert len(list(e.synthesize("Hello there."))) == 1
    assert "en_US-joe-medium.onnx" in faux.journal.read_text()


def test_parole_coupee_puis_phrase_suivante(faux):
    """Une phrase interrompue laisse des WAV en route : ils sont jetés, la suivante a SON son."""
    e = faux()
    g = e.synthesize("Une première phrase. Une deuxième phrase. Une troisième phrase.")
    next(g)
    g.close()
    assert e._stale == 2
    assert [len(b) for b in e.synthesize("Après la coupure.")] == [octets("Après la coupure.")]
    assert e._stale == 0


def test_programme_qui_plante_relance_a_la_phrase_suivante(faux):
    e = faux()
    e.load()
    pid = e._proc.pid
    with pytest.raises(RuntimeError):
        list(e.synthesize("PLANTE."))
    assert [len(b) for b in e.synthesize("Ça repart.")] == [octets("Ça repart.")]
    assert e._proc.pid != pid


def test_programme_tue_relance_sans_perdre_la_phrase(faux):
    e = faux()
    e.load()
    e._proc.kill()
    e._proc.wait()
    assert [len(b) for b in e.synthesize("Toujours là.")] == [octets("Toujours là.")]


def test_programme_bloque(faux, monkeypatch):
    monkeypatch.setattr(engines, "PIPER_TIMEOUT_S", 0.5)
    e = faux()
    e.load()
    t = time.monotonic()
    with pytest.raises(TimeoutError):
        list(e.synthesize("BLOQUE."))
    assert time.monotonic() - t < 5 and e._proc is None  # tué, relancé à la phrase suivante
    assert len(list(e.synthesize("Débloqué."))) == 1


def test_voix_ou_programme_absents(faux, monkeypatch):
    with pytest.raises(FileNotFoundError):
        faux("fr_FR-inconnue").load()
    monkeypatch.setattr(engines, "piper_command", lambda: [])
    with pytest.raises(FileNotFoundError):
        faux().load()


def test_commande_qui_s_arrete_aussitot(faux, monkeypatch):
    """`load` lève (pas de relance en boucle) : le Speaker garde l'ancienne voix de secours."""
    monkeypatch.setattr(config, "PIPER_CMD", f"{shlex.quote(sys.executable)} -c pass")
    e = faux()
    with pytest.raises(RuntimeError):
        e.load()
    assert e._proc is None


def test_fermeture(faux):
    e = faux()
    e.load()
    proc, dossier = e._proc, e._dir
    e.close()
    assert proc.poll() is not None and not os.path.exists(dossier)


# --- l'orateur ferme les voix quittées ---

class Moteur:
    sample_rate = 1000

    def __init__(self, name):
        self.name = name
        self.closed = 0

    def load(self):
        pass

    def synthesize(self, text):
        yield b"\x00\x00"

    def close(self):
        self.closed += 1


def test_secours_change_ferme_l_ancien_entre_deux_phrases(monkeypatch):
    monkeypatch.setattr(speaker_mod, "build_engine", Moteur)
    sp = speaker_mod.Speaker("edge", fallback="piper:fr")
    ancien = sp._fallback
    assert sp.set_fallback("piper:en") == "piper:en"
    assert ancien.closed == 0  # peut-être en train de parler : fermé par le fil de l'orateur
    sp._close_retired()
    assert ancien.closed == 1 and sp._fallback.closed == 0
    sp.close()
    assert sp._fallback.closed == 1 and sp._primary.closed == 1


# --- le vrai programme, s'il est installé ---

@pytest.mark.skipif(not engines.piper_command() or not (config.MODELS / "piper" / "fr_FR-upmc-medium.onnx").exists(),
                    reason="programme piper ou voix UPMC absents")
def test_vrai_programme_piper():
    e = PiperEngine("fr_FR-upmc-medium")
    try:
        pcm = b"".join(e.synthesize("Bonjour."))
        assert e.sample_rate == 22050 and len(pcm) / 2 / e.sample_rate > 0.3
    finally:
        e.close()
