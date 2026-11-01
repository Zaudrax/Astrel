"""Fichier des clés API (20/10, P11, accord de l'utilisateur) : `config/secrets.json` rejoint les
secrets du garde-fou. Sa lecture par Bash, Read ou Grep est une action sur le garde-fou :
jamais accordée par Total, ni par « oui pour 30 min », ni par la levée."""
import pytest

from astrel import config, guard, totalgate

R = config.ROOT
SECRETS = str(config.SECRETS_FILE)


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "MODE_FILE", tmp_path / "autonomy.json")
    monkeypatch.setattr(guard, "GRANTS_FILE", tmp_path / "grants.json")


def _garde_fou(v) -> None:
    assert v.needs_confirm and v.guard is True, v.desc
    assert not v.keys and not v.lift
    assert not totalgate.total_grants(True, v.guard, False)


@pytest.mark.parametrize("cmd", [
    "cat config/secrets.json",
    f"cat {SECRETS}",
    "grep CLE_API config/secrets.json",
    "cp config/secrets.json /tmp/s.json",
    "jq . config/secrets.json",
    "cd config && cat secrets.json",
    "base64 -w0 config/secrets.json",
])
def test_lecture_du_fichier_des_cles_demande(cmd):
    v = guard.assess("Bash", {"command": cmd}, mode="autonome", cwd=str(R))
    _garde_fou(v)
    assert v.desc == guard.L_SECRET


@pytest.mark.parametrize("tool, inp", [
    ("Read", {"file_path": SECRETS}),
    ("Grep", {"pattern": "CLE_API", "path": SECRETS}),
])
def test_outils_de_lecture_du_fichier_des_cles(tool, inp):
    _garde_fou(guard.assess(tool, inp, mode="autonome", cwd="/tmp"))


def test_voisins_inoffensifs_restent_libres():
    for cmd in ("ls config/", "cat config/mic_profiles.json"):
        v = guard.assess("Bash", {"command": cmd}, mode="autonome", cwd=str(R))
        assert not v.needs_confirm, (cmd, v.desc)


# 23/10 (plan P29, fiche 3 du relecteur) : ÉCRIRE dans le fichier des clés était libre
# (Write/Edit), car la racine du projet est une racine d'écriture permise.
@pytest.mark.parametrize("tool, path", [
    ("Write", SECRETS), ("Edit", "config/secrets.json"), ("MultiEdit", SECRETS),
    ("Write", str(config.WHATSAPP_MEDIA_DIR / "out-1.pdf")),
])
def test_ecriture_du_fichier_des_cles_au_plafond(tool, path):
    v = guard.assess(tool, {"file_path": path}, mode="autonome", cwd=str(R))
    _garde_fou(v)
    assert v.cats == ("plafond", "secrets")  # 24/10 (P29 lot B) : marqueur + case « secrets »


@pytest.mark.parametrize("cmd", [
    "echo '{}' > config/secrets.json", "cp /tmp/s.json config/secrets.json", "echo x > config/secr*.json",
    "echo x > config/$NOM", "cp /tmp/a.pdf runtime/whatsapp-media/out-1.pdf",
])
def test_ecriture_bash_du_fichier_des_cles_au_plafond(cmd):
    v = guard.assess("Bash", {"command": cmd}, mode="autonome", cwd=str(R))
    _garde_fou(v)


def test_ecriture_des_voisins_inoffensifs_inchangee():
    for tool, path in (("Write", "config/mic_profiles.json"), ("Edit", "config/channels.json"),
                       ("Write", "runtime/equipe/note.md"), ("Write", "/tmp/secrets.json")):
        assert not guard.assess(tool, {"file_path": path}, mode="autonome", cwd=str(R)).needs_confirm, path
    assert not guard.assess("Bash", {"command": "echo x > config/mic_profiles.json"}, mode="autonome",
                            cwd=str(R)).needs_confirm
