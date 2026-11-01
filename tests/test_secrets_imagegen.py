"""Profils Chrome des comptes d'images IA (27/10, plan P40 A0, accord de l'utilisateur).

`runtime/imagegen/profils/<alias>/` garde les cookies de session des comptes Google et
OpenAI : les lire, c'est pouvoir se connecter au compte. Ils rejoignent les secrets
(`guard.SECRET_PATHS`, `SECRET_BASH`) : lire, copier, parcourir ou écrire = action sur le
garde-fou (ni Total, ni « oui pour 30 min », ni levée). Le reste de `runtime/imagegen`
(état des quotas, captures de débogage) reste lisible, et `medias` ne montre jamais un profil."""
from __future__ import annotations

import pytest

from astrel import config, guard, medias, totalgate

R = config.ROOT
P = config.IMAGES_PROFILS


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "MODE_FILE", tmp_path / "autonomy.json")
    monkeypatch.setattr(guard, "GRANTS_FILE", tmp_path / "grants.json")


def _assess(tool: str, tool_input: dict):
    return guard.assess(tool, tool_input, mode="autonome", cwd=str(R))


def _garde_fou(v) -> None:
    assert v.needs_confirm and v.guard is True, v.desc
    assert not v.keys and not v.lift
    assert not totalgate.total_grants(True, v.guard, False)


def test_profils_dans_les_secrets():
    assert P.resolve() in guard.SECRET_PATHS


@pytest.mark.parametrize("cmd", [
    "cat runtime/imagegen/profils/amw/Default/Cookies",
    "sqlite3 runtime/imagegen/profils/amw/Default/Cookies .dump",
    "cp -r runtime/imagegen /tmp/copie",
    "grep -r SID runtime/imagegen",
    "tar czf /tmp/p.tgz runtime/imagegen/profils",
    f"cat {P}/amw/Default/Cookies",
])
def test_bash_qui_lit_un_profil(cmd):
    _garde_fou(_assess("Bash", {"command": cmd}))


@pytest.mark.parametrize("tool, champ, cible", [
    ("Read", "file_path", P / "amw" / "Default" / "Cookies"),
    ("Grep", "path", P),
    ("Grep", "path", config.IMAGES_RUNTIME),   # dossier qui contient le secret
    ("Write", "file_path", P / "amw" / "x"),
])
def test_outils_sur_un_profil(tool, champ, cible):
    entree = {champ: str(cible), "pattern": "x", "content": "x"}
    _garde_fou(_assess(tool, entree))


@pytest.mark.parametrize("tool, entree", [
    ("Bash", {"command": "cat runtime/imagegen/etat.json"}),
    ("Bash", {"command": "ls runtime/imagegen"}),
    ("Read", {"file_path": str(config.IMAGES_RUNTIME / "etat.json")}),
    ("Read", {"file_path": str(config.IMAGES_RUNTIME / "debug" / "page.png")}),
])
def test_le_reste_de_imagegen_reste_lisible(tool, entree):
    assert not _assess(tool, entree).guard


@pytest.mark.parametrize("cmd", [
    "ln -s runtime/imagegen /tmp/zz",              # relecture fiche 10 : puis cat /tmp/zz/profils/…
    "ln -s runtime /tmp/zy",
    "cd runtime && ln -s imagegen /tmp/zz",
    "cp -rs runtime/imagegen /tmp/zz",
    f"ln -s {config.IMAGES_RUNTIME} /tmp/zz",
])
def test_lien_vers_un_dossier_qui_contient_les_profils(cmd):
    _garde_fou(_assess("Bash", {"command": cmd}))


@pytest.mark.parametrize("cmd", ["ln -s runtime/imagegen/debug /tmp/d", "ln -s runtime/attachments /tmp/a",
                                 "ln -s astrel /tmp/j"])
def test_lien_vers_un_dossier_sans_secret_libre(cmd):
    assert not _assess("Bash", {"command": cmd}).guard


def test_medias_ne_montre_jamais_un_profil():
    # 30/10 : profils lus À L'APPEL (le filet de session du conftest les met hors du vrai runtime/) ;
    # le vrai dossier, `P` pris à l'import, reste refusé lui aussi.
    profils = config.IMAGES_PROFILS
    assert "privé" in medias.refusal(profils.resolve() / "amw" / "Default" / "Cookies")
    assert "privé" in medias.refusal(profils.resolve())
    assert medias.refusal(P.resolve() / "amw" / "Default" / "Cookies") and medias.refusal(P.resolve())
