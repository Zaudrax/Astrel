"""Le cœur d'Astrel découpé par domaine (29/10/2026) : chaque module est un MIXIN de `Astrel`
(`astrel/core.py`), qui en hérite ; les méthodes y ont été déplacées telles quelles.

Les mixins importent les aides du module `core` (`from ..core import …`), et `core` les importe juste
avant de définir `Astrel`, une fois ces aides définies. Charger ce paquet charge donc `core` d'abord :
importer `astrel.coeur.<module>` avant `astrel.core` fonctionne aussi.
"""
from .. import core  # noqa: F401  (ordre d'import : voir ci-dessus)
