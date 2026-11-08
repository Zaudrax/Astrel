"""Pannes de Windows corrigées avant le premier essai sur un vrai PC (07/11).

Whisper hors Linux : processeur d'office. Avec une carte NVIDIA mais sans cuBLAS ni cuDNN (jamais installés hors Linux, requirements.txt),
`WhisperModel(device="cuda")` se charge et c'est la transcription qui casse : le repli de `load` ne la
rattrapait pas. Linux garde la carte graphique (réglage de config.py)."""
import sys
import types

from astrel import config, fournisseurs, listener


def test_processeur_hors_linux_sauf_demande_expresse():
    assert listener.whisper_device("windows", env={}) == ("cpu", "int8")
    assert listener.whisper_device("macos", env={}) == ("cpu", "int8")
    assert listener.whisper_device("linux", env={}) == (config.STT_DEVICE, config.STT_COMPUTE)
    # CUDA installé à la main : ASTREL_STT_DEVICE posé exprès, il est suivi (valeur lue par config.py)
    assert listener.whisper_device("windows", env={"ASTREL_STT_DEVICE": "cuda"}) == (
        config.STT_DEVICE, config.STT_COMPUTE)


def test_les_deux_whisper_chargent_sur_le_processeur_sous_windows(monkeypatch):
    charges = []

    class FauxWhisper:
        def __init__(self, nom, device, compute_type):
            charges.append((nom, device, compute_type))

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FauxWhisper))
    monkeypatch.setattr(listener, "_preload_cuda_libs", lambda: None)
    vrai = listener.whisper_device
    monkeypatch.setattr(listener, "whisper_device", lambda: vrai("windows", env={}))
    t = listener.Transcriber()
    t.load()
    listener._WakeWhisper().load()
    assert t.device == "cpu"
    assert charges == [(config.STT_MODEL, "cpu", "int8"), (config.WAKE_TEXT_MODEL, "cpu", "int8")]


def test_hooks_du_cli_en_utf8_sous_windows():
    """Les hooks sans `-I` (rappel, routage, Stop) héritent de l'environnement du cerveau : en cp1252 sous
    Windows, « é » arrivait « Ã© » et « → » les faisait planter. Linux : rien de changé."""
    base = {"PATH": "x", "ANTHROPIC_API_KEY": "k"}
    env = fournisseurs.env_cerveau(base, "abonnement", windows=True)
    assert env == {"PATH": "x", "PYTHONUTF8": "1"}
    assert fournisseurs.env_cerveau(base, "abonnement", windows=False) == {"PATH": "x"}


def test_dossier_des_transcriptions_nomme_comme_claude_code():
    # Claude Code : tout caractère hors [A-Za-z0-9] devient « - » (vu dans ~/.claude/projects : un « _ »
    # de tempfile y devient « - »). Linux inchangé ; Windows, espace et accent : avant, history_search vide.
    assert config.claude_project_name("/home/lea/Projets") == "-home-lea-Projets"
    assert config.claude_project_name("/home/lea/Projets/") == "-home-lea-Projets"
    assert config.claude_project_name(r"C:\Users\Léa Martin\Projets") == "C--Users-L-a-Martin-Projets"
    assert config.claude_project_name("/tmp/banc_x1") == "-tmp-banc-x1"


def test_hooks_du_cli_en_mode_utf8_sous_windows(monkeypatch):
    # Hooks lancés sans -I (rappel, routage, Stop) : `-X utf8` sous Windows, comme le garde-fou
    monkeypatch.setattr(config, "UTF8_ARGS", ["-X", "utf8"])
    ligne = config.python_command(config.ROOT / "hooks" / "rappel.py")
    assert " -X utf8 " in ligne and ligne.endswith("hooks/rappel.py")
    monkeypatch.setattr(config, "UTF8_ARGS", [])
    assert config.python_command(config.ROOT / "hooks" / "rappel.py") == " ".join(
        [config.VENV_PYTHON.as_posix(), (config.ROOT / "hooks" / "rappel.py").as_posix()])


def test_git_bash_trouve_comme_claude_code(tmp_path):
    # Sans Git Bash, Claude Code lance les hooks par PowerShell : le garde-fou refuserait tout
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_install", config.ROOT / "tools" / "check_install.py")
    ci = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci)
    git = tmp_path / "Git" / "cmd" / "git.exe"
    bash = tmp_path / "Git" / "bin" / "bash.exe"
    git.parent.mkdir(parents=True)
    bash.parent.mkdir(parents=True)
    git.touch()
    assert ci.git_bash_windows(env={}, which=lambda _: str(git)) is None
    bash.touch()
    assert ci.git_bash_windows(env={}, which=lambda _: str(git)) == bash.resolve()
    assert ci.git_bash_windows(env={"ProgramFiles": str(tmp_path)}, which=lambda _: None) == bash
    autre = tmp_path / "bash.exe"
    autre.touch()
    assert ci.git_bash_windows(env={"CLAUDE_CODE_GIT_BASH_PATH": str(autre)}, which=lambda _: None) == autre
