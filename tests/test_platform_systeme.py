"""Aides du système pour Windows (03/11/2026, portage : Windows natif doit démarrer),
`astrel/platform/systeme.py`, et faux Claude de la CI qui tient une session minimale."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from astrel.platform import systeme

ROOT = Path(__file__).resolve().parent.parent
# Liens symboliques et bits de mode : Unix (créer un lien demande des droits sous Windows).
unix = pytest.mark.skipif(os.name == "nt", reason="liens et bits de mode Unix")


@unix
def test_proprietaire_seul_unix(tmp_path):
    f = tmp_path / "cle"
    f.write_text("x")
    f.chmod(0o600)
    assert systeme.proprietaire_seul(os.lstat(f))
    f.chmod(0o644)
    assert not systeme.proprietaire_seul(os.lstat(f))
    assert not systeme.proprietaire_seul(os.lstat(tmp_path))


@unix
def test_proprietaire_seul_sans_uid_comme_sous_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(systeme, "UNIX", False)
    f = tmp_path / "cle"
    f.write_text("x")
    f.chmod(0o644)  # bits de mode sans valeur sous Windows : fichier ordinaire suffit
    assert systeme.proprietaire_seul(os.lstat(f))
    assert not systeme.proprietaire_seul(os.lstat(tmp_path))


@unix
def test_ouvrir_sans_lien_sans_o_nofollow(tmp_path, monkeypatch):
    monkeypatch.setattr(systeme, "NOFOLLOW", 0)
    vrai = tmp_path / "vrai"
    vrai.write_text("secret")
    lien = tmp_path / "lien"
    lien.symlink_to(vrai)
    with pytest.raises(OSError, match="lien refusé"):
        systeme.ouvrir_sans_lien(lien, os.O_RDONLY)
    fd = systeme.ouvrir_sans_lien(vrai, os.O_RDONLY)
    os.close(fd)
    neuf = tmp_path / "neuf"
    os.close(systeme.ouvrir_sans_lien(neuf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    assert neuf.exists()


@unix
def test_ouvrir_sans_lien_unix(tmp_path):
    vrai = tmp_path / "vrai"
    vrai.write_text("x")
    (tmp_path / "lien").symlink_to(vrai)
    with pytest.raises(OSError):
        systeme.ouvrir_sans_lien(tmp_path / "lien", os.O_RDONLY)


def test_tuer_arbre_sous_windows_par_taskkill(monkeypatch):
    appels = []
    monkeypatch.setattr(systeme, "UNIX", False)
    monkeypatch.setattr(systeme.subprocess, "run", lambda cmd, **k: appels.append(cmd))
    systeme.tuer_arbre(1234)
    assert appels == [["taskkill", "/PID", "1234", "/T", "/F"]]


def test_tuer_arbre_process_deja_parti():
    p = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    p.wait()
    systeme.tuer_arbre(p.pid, systeme.SIGKILL)  # aucune exception
    assert not systeme.groupe_vivant(p.pid)


def test_nom_machine_et_processus():
    assert systeme.nom_machine()
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "--marque-astrel-ci"])
    try:
        assert p.pid in systeme.processus_avec("--marque-astrel-ci")
        assert systeme.pid_vivant(p.pid)
    finally:
        p.kill()
        p.wait()
    assert not systeme.pid_vivant(p.pid)


def test_faux_claude_tient_une_session():
    faux = ROOT / "tools" / "ci" / "faux-claude" / "claude"
    entree = json.dumps({"type": "user", "message": {"role": "user", "content": "bonjour"}}) + "\n"
    r = subprocess.run([sys.executable, str(faux), "-p", "--input-format", "stream-json", "--output-format",
                        "stream-json"], input=entree, capture_output=True, text=True, timeout=30)
    evts = [json.loads(ligne) for ligne in r.stdout.splitlines()]
    assert [e["type"] for e in evts] == ["system", "assistant", "result"]
    assert evts[0]["subtype"] == "init" and evts[2]["is_error"] is False
    assert subprocess.run([sys.executable, str(faux), "inconnu"], capture_output=True).returncode == 2


# ------------------------------------------------------------------ Job Object du cerveau (lot 3)

def test_job_object_absent_hors_windows():
    from astrel.platform import proc
    if sys.platform == "win32":
        pytest.skip("Windows : voir le test suivant")
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert proc.rattacher_au_cerveau(p) is False and proc.in_brain_job(os.getpid()) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object : Windows seulement")
def test_job_object_reconnait_un_petit_enfant_detache_sans_marqueur():
    from astrel import state
    from astrel.platform import proc
    script = ("import subprocess, sys, time\n"
              "sys.stdin.readline()\n"  # attend d'être rattaché avant de lancer quoi que ce soit
              "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], env={'SYSTEMROOT': "
              "__import__('os').environ.get('SYSTEMROOT', '')}, creationflags=0x00000008)\n"  # DETACHED_PROCESS
              "print(p.pid, flush=True)\n"
              "time.sleep(60)\n")
    cerveau = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert proc.rattacher_au_cerveau(cerveau)
        cerveau.stdin.write("go\n")
        cerveau.stdin.flush()
        petit = int(cerveau.stdout.readline())
        assert proc.in_brain_job(petit) is True
        assert proc.brain_marker(petit) is not True  # environnement vidé : le marqueur ne le voit plus
        assert state.from_claude(petit, claude_pid=None) is True  # le Job Object, lui, le voit
        assert proc.in_brain_job(os.getpid()) is False
    finally:
        systeme.tuer_arbre(cerveau.pid, systeme.SIGKILL)
        cerveau.wait(timeout=10)
