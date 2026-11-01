"""Chemins de la machine qui lance les tests (dépôt public) : les tests ne citent aucun chemin
réel. RACINE = ce dépôt ; PARENT = le dossier qui le contient ; PROJETS = le dossier des projets
d'Astrel pendant la suite (jetable, posé par conftest.py) ; HOME = le dossier personnel."""
from pathlib import Path

from astrel import config

RACINE = str(Path(__file__).resolve().parent.parent)
PARENT = str(Path(RACINE).parent)
PROJETS = str(config.WORKDIR)
HOME = str(Path.home())


def longueur_type(texte: str) -> int:
    """Longueur d'une invite mesurée avec un prénom (8 lettres) et un dossier d'installation (34
    caractères) de longueur FIXE : le profil et le chemin du clone ne font pas échouer un budget."""
    return len(texte.replace(config.USER_NAME, "X" * 8).replace(config.ROOT.as_posix(), "/" + "x" * 33))
