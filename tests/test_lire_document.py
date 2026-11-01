"""Lire un document avec markitdown (20/10, plan P12, lot 1) : script `tools/lire-document.sh`,
venv à part `.venv-markitdown`, indice dans la note de pièce jointe, aucun mot du prompt."""
import base64
import subprocess
import zipfile

import pytest

from astrel import attachments, config, guard

R = config.ROOT
SCRIPT = R / "tools" / "lire-document.sh"
VENV_OK = (R / ".venv-markitdown" / "bin" / "python").exists()


def _run(*args, **env):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=120,
                          env={"PATH": "/usr/bin:/bin", **env})


@pytest.mark.parametrize("url", ["https://example.com/a.pdf", "http://x/y", "file:///etc/passwd", "www.site.fr"])
def test_une_url_est_refusee(url):
    r = _run(url)
    assert r.returncode == 2 and "URL" in r.stderr and not r.stdout


def test_chemin_inexistant_ou_dossier_refuse(tmp_path):
    for target in (tmp_path / "absent.pdf", tmp_path):
        r = _run(str(target))
        assert r.returncode == 2 and "n'existe pas" in r.stderr and not r.stdout


def test_sans_argument_affiche_l_usage():
    assert _run().returncode == 2


def _docx(path):
    ns = "http://schemas.openxmlformats.org/"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   f'<Types xmlns="{ns}package/2006/content-types"><Default Extension="rels" '
                   f'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", f'<Relationships xmlns="{ns}package/2006/relationships"><Relationship Id="rId1" '
                   f'Type="{ns}officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", f'<w:document xmlns:w="{ns}wordprocessingml/2006/main"><w:body><w:p><w:r>'
                   '<w:t>Devis du chantier</w:t></w:r></w:p></w:body></w:document>')


@pytest.mark.skipif(not VENV_OK, reason=".venv-markitdown absente (tools/lire-document.sh --installer)")
def test_word_et_excel_en_markdown(tmp_path):
    _docx(tmp_path / "devis.docx")
    r = _run(str(tmp_path / "devis.docx"))
    assert r.returncode == 0 and "Devis du chantier" in r.stdout
    # openpyxl vient de la venv à part : le classeur est écrit par elle
    subprocess.run([str(R / ".venv-markitdown" / "bin" / "python"), "-c",
                    "import openpyxl,sys;w=openpyxl.Workbook();w.active.append(['Produit','Prix']);"
                    "w.active.append(['Pomme',3]);w.save(sys.argv[1])", str(tmp_path / "t.xlsx")], check=True)
    r = _run(str(tmp_path / "t.xlsx"))
    assert r.returncode == 0 and "| Produit | Prix |" in r.stdout and "| Pomme | 3 |" in r.stdout


@pytest.mark.skipif(not VENV_OK, reason=".venv-markitdown absente")
def test_sortie_tronquee(tmp_path):
    (tmp_path / "gros.txt").write_text("x" * 5000, encoding="utf-8")
    r = _run(str(tmp_path / "gros.txt"), ASTREL_LIRE_DOCUMENT_MAX="1000")
    assert r.returncode == 0 and "tronqué à 1000 caractères sur 5000" in r.stdout
    assert len(r.stdout) < 1200


def test_versions_fixees():
    reqs = (R / "tools" / "lire-document.requirements.txt").read_text(encoding="utf-8")
    lines = [ln for ln in reqs.splitlines() if ln and not ln.startswith("#")]
    assert "markitdown==0.1.8" in lines and all("==" in ln for ln in lines)
    assert ".venv-markitdown/" in (R / ".gitignore").read_text(encoding="utf-8").splitlines()


@pytest.fixture
def isolated_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "MODE_FILE", tmp_path / "autonomy.json")
    monkeypatch.setattr(guard, "GRANTS_FILE", tmp_path / "grants.json")


@pytest.mark.parametrize("cmd", [
    "tools/lire-document.sh config/secrets.json",
    f"{SCRIPT} {config.SECRETS_FILE}",
    "cd config && ../tools/lire-document.sh secrets.json",
])
def test_lire_un_secret_reste_bloque(isolated_guard, cmd):
    v = guard.assess("Bash", {"command": cmd}, mode="autonome", cwd=str(R))
    assert v.needs_confirm and v.guard is True, v.desc


def test_lire_un_document_ordinaire_est_libre(isolated_guard):
    v = guard.assess("Bash", {"command": "tools/lire-document.sh /tmp/devis.docx"}, mode="autonome", cwd=str(R))
    assert not v.needs_confirm, v.desc


def test_note_de_piece_jointe(tmp_path, monkeypatch):
    monkeypatch.setattr(attachments, "ATTACH_DIR", tmp_path / "attachments")
    data = base64.b64encode(b"PK\x03\x04 faux").decode()
    p = attachments.prepare([{"name": "devis.docx", "data": data}, {"name": "tableau.XLSX", "data": data},
                             {"name": "notes.txt", "type": "text/plain", "data": base64.b64encode(b"a").decode()}])
    assert "tools/lire-document.sh " + str(tmp_path / "attachments") in p.notes[0]
    assert "tools/lire-document.sh" in p.notes[1]
    assert "Lis-le avec Read" in p.notes[2]
