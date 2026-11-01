"""Réponse du cœur lue par le hook (22/10, plan P27, fiche 3 du relecteur, accord de l'utilisateur).

`confirm.request` faisait `bool(json.loads(…).get("allow"))` : la CHAÎNE "false" (ou "non", 1, [0])
valait un oui. Le serveur n'émet que des booléens (`ConfirmServer._handle`), mais le hook ne doit
autoriser que sur le booléen `true` lui-même ; tout le reste est un refus."""
import socket

import pytest

from astrel import confirm


def _reponse(monkeypatch, brut: bytes):
    """`confirm.request` face à un cœur qui répond `brut` (socketpair, sans thread)."""
    a, b = socket.socketpair()
    b.sendall(brut)
    b.shutdown(socket.SHUT_WR)  # le cœur a fini de répondre (réponse vide : fin de flux)

    def connect(path, timeout):
        a.settimeout(timeout)  # comme `ipc.connect`
        return a

    monkeypatch.setattr(confirm.ipc, "connect", connect)
    try:
        return confirm.request("Bash", "une publication git (push)", "git push", "k", timeout=2)
    finally:
        b.close()


@pytest.mark.parametrize("brut", [
    b'{"allow": "false"}\n',
    b'{"allow": "true"}\n',
    b'{"allow": "oui"}\n',
    b'{"allow": 1}\n',
    b'{"allow": [0]}\n',
    b'{"allow": {"x": 1}}\n',
    b'{"allow": false}\n',
    b'{"allow": null}\n',
    b'{"error": "x"}\n',
])
def test_seul_le_booleen_true_autorise(monkeypatch, brut):
    assert _reponse(monkeypatch, brut) is False


def test_booleen_true_autorise(monkeypatch):
    assert _reponse(monkeypatch, b'{"allow": true}\n') is True


@pytest.mark.parametrize("brut", [b"", b"pas du json\n", b'["allow", true]\n'])
def test_reponse_illisible_vaut_injoignable(monkeypatch, brut):
    assert _reponse(monkeypatch, brut) is None  # le hook refuse alors (« Astrel injoignable »)
