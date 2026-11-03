"""Garde-fou sous Windows (03/11/2026, portage, lot 3, autorisé par l'utilisateur) : machine Windows de la CI
seulement. Le Bash de Claude Code y est Git Bash : un même fichier s'écrit « C:\\… » (entre guillemets,
sinon Bash mange les « \\ »), « C:/… » ou « /c/… ». Sous toutes ces formes, écrire un fichier du
garde-fou ou lire un secret doit être DEMANDÉ ; les lire (code) reste libre."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from astrel import config, guard

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="garde-fou sous Windows : Windows seulement")


def formes(p: Path) -> list[str]:
    p = p.resolve()
    posix = p.as_posix()
    return [f"'{p}'", posix, "/" + posix[0].lower() + posix[2:], "/cygdrive/" + posix[0].lower() + posix[2:]]


PROTEGES = [config.ROOT / "astrel" / "guard.py", config.ROOT / "astrel" / "ipc.py",
            config.ROOT / "astrel" / "platform" / "proc.py", config.ROOT / "hooks" / "permission_hook.py"]
SECRETS = [config.HUD_TOKEN_FILE, config.SECRETS_FILE]
ECRITURES = ["sed -i s/a/b/ {}", "cp README.md {}", "mv {} sauvegarde", "rm -f {}", "echo x > {}",
             "echo x | tee {}", "truncate -s 0 {}", "python -c 'open(\"x\")' && cp x {}"]


@pytest.mark.parametrize("cible", PROTEGES, ids=lambda p: p.name)
def test_outils_ecriture_proteges(cible):
    for forme in (str(cible), cible.as_posix()):
        for outil, entree in (("Edit", {"file_path": forme, "old_string": "a", "new_string": "b"}),
                              ("Write", {"file_path": forme, "content": "x"})):
            v = guard.assess(outil, entree, mode="total")
            assert v.needs_confirm and v.guard, (outil, forme)


@pytest.mark.parametrize("cmd", ECRITURES)
@pytest.mark.parametrize("cible", PROTEGES, ids=lambda p: p.name)
def test_bash_ecriture_protegee_sous_toutes_les_formes(cible, cmd):
    for forme in formes(cible):
        v = guard.assess("Bash", {"command": cmd.format(forme)}, mode="autonome")
        assert v.needs_confirm, cmd.format(forme)


@pytest.mark.parametrize("cible", SECRETS, ids=lambda p: p.name)
def test_lecture_d_un_secret_demandee(cible):
    for forme in formes(cible):
        assert guard.assess("Bash", {"command": f"cat {forme}"}, mode="autonome").needs_confirm, forme
    assert guard.assess("Read", {"file_path": str(cible)}, mode="autonome").needs_confirm


@pytest.mark.parametrize("cible", PROTEGES, ids=lambda p: p.name)
def test_lire_le_code_reste_libre(cible):
    for forme in formes(cible):
        assert not guard.assess("Bash", {"command": f"grep -n def {forme}"}, mode="autonome").needs_confirm, forme


def test_ecriture_systeme_windows_critique():
    """03/11 : une écriture dans le système Windows valait « sensible » (libre en autonome) ; même verdict que /etc."""
    import os

    hosts = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"
    for outil, entree, cwd in (("Write", {"file_path": str(hosts), "content": "x"}, None),
                               ("Write", {"file_path": "/etc/hosts", "content": "x"}, None),
                               ("Write", {"file_path": "hosts", "content": "x"}, "/etc"),
                               ("Bash", {"command": f"echo x >> '{hosts}'"}, None),
                               ("Bash", {"command": f"echo x >> {hosts.as_posix()}"}, None)):
        v = guard.assess(outil, entree, mode="autonome", cwd=cwd)
        assert v.needs_confirm and v.level == guard.CRITICAL, (outil, entree, cwd)
    demarrage = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    assert guard.assess("Write", {"file_path": str(demarrage / "x.bat"), "content": "x"}, mode="autonome").needs_confirm


@pytest.mark.parametrize("cible", SECRETS, ids=lambda p: p.name)
def test_secret_aux_antislashs_non_cites_ou_en_majuscules(cible):
    """03/11 : `cat C:\\…\\hud-token` sans guillemets (Git Bash mange les « \\ », un autre interpréteur non) et
    `cat RUNTIME/HUD-TOKEN` (NTFS ignore la casse) sont des lectures du secret."""
    assert guard.assess("Bash", {"command": f"cat {cible.resolve()}"}, mode="autonome").needs_confirm
    rel = cible.resolve().relative_to(config.ROOT.resolve()).as_posix().upper()
    assert guard.assess("Bash", {"command": f"cat {rel}"}, mode="autonome", cwd=str(config.ROOT)).needs_confirm
