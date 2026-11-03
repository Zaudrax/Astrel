"""Chemins de la machine qui lance les tests (dépôt public) : les tests ne citent aucun chemin
réel. RACINE = ce dépôt ; PARENT = le dossier qui le contient ; PROJETS = le dossier des projets
d'Astrel pendant la suite (jetable, posé par conftest.py) ; HOME = le dossier personnel."""
from pathlib import Path

from astrel import config

# En « / » (03/11, CI Windows) : ces chemins entrent dans des commandes Bash, où Git Bash mangerait les
# « \ » d'un chemin Windows ; Python et Git Bash comprennent tous deux « C:/… ». Identique sous Linux.
RACINE = Path(__file__).resolve().parent.parent.as_posix()
PARENT = Path(RACINE).parent.as_posix()
PROJETS = Path(config.WORKDIR).as_posix()
HOME = Path.home().as_posix()


def longueur_type(texte: str) -> int:
    """Longueur d'une invite mesurée avec un prénom (8 lettres) et un dossier d'installation (34
    caractères) de longueur FIXE : le profil et le chemin du clone ne font pas échouer un budget."""
    return len(texte.replace(config.USER_NAME, "X" * 8).replace(config.ROOT.as_posix(), "/" + "x" * 33))
