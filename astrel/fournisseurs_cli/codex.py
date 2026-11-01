"""Codex (OpenAI), par son CLI officiel `codex` et l'abonnement ChatGPT de l'utilisateur (plan P27).

⚠️ NON PROUVÉ (22/10) : `codex` n'est pas installé sur cette machine. Tout ce qui suit vient de
la documentation (sources du chercheur, 22/10) et attend le lot 0 (runtime/equipe/p27/
lot0-procedure.md) :
- `codex exec --json [options] -` lit la consigne sur stdin et écrit des lignes JSON :
  `thread.started {thread_id}`, `turn.started`, `item.started|updated|completed {item}`,
  `turn.completed {usage}`, `turn.failed {error}`, `error {message}` ;
  items : `agent_message {text}`, `reasoning`, `command_execution {command, aggregated_output,
  exit_code, status}`, `file_change {changes[{path, kind}], status}`, `mcp_tool_call {server,
  tool, arguments, result, error, status}`, `web_search {query}`, `todo_list`, `error {message}` ;
- `codex exec --json resume <thread_id> -` reprend une conversation ;
- `CODEX_HOME` place `config.toml`, `hooks.json` et la connexion (`auth.json`) dans
  `runtime/cli/codex/` ; `codex login status` dit si l'on est connecté ;
- hooks `PreToolUse` au format de Claude Code (`hooks.json`, drapeau `[features] codex_hooks`).
Capacités : toutes fausses tant que le lot 0 ne les a pas vues (le choix reste refusé).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .. import config
from .base import (MODE_OUTILS, MODELE_DEFAUT, Capacites, Commande, EtatTour, FournisseurCli, ev_fin,
                   ev_outil, ev_resultat_outil, ev_texte, ev_usage)


def _toml(v) -> str:
    """Valeur TOML : chaînes en guillemets doubles (échappements JSON = TOML de base), listes, tables."""
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{json.dumps(str(k))} = {_toml(x)}" for k, x in v.items()) + " }"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml(x) for x in v) + "]"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(str(v))


class Codex(FournisseurCli):
    id = "codex"
    nom = "Codex"
    binaire = "codex"
    env_dossier = "CODEX_HOME"
    capacites = Capacites()  # NON PROUVÉ : lot 0

    @property
    def commande_connexion(self) -> str:  # type: ignore[override]
        return f"CODEX_HOME={self.dossier()} codex login"

    def _connexion(self, argv: list[str], env: dict) -> bool | None:
        out = self._lancer_court(argv + ["login", "status"], env)
        if out is None:
            return None
        return out[0] == 0

    def outils_annonces(self, mode: str) -> list[str]:
        return ["Bash", "Edit", "WebSearch"] if mode == MODE_OUTILS else []

    def config_outils(self, mode: str) -> None:
        """`config.toml` (MCP seulement en mode outils, hooks activés) et `hooks.json` : le hook
        d'Astrel sur TOUS les outils. En conversation seule, le même hook refuse tout. Jamais
        `auth.json` (la connexion de l'utilisateur)."""
        lignes = ["# Écrit par Astrel (plan P27) à chaque démarrage du cerveau : ne pas modifier.",
                  "# NON PROUVÉ : clés lues dans la documentation de Codex, à vérifier (lot 0).",
                  "[features]", "codex_hooks = true", ""]
        if mode == MODE_OUTILS:
            for nom, srv in self.serveurs_mcp().items():
                lignes += [f"[mcp_servers.{nom}]", f"command = {_toml(srv['command'])}",
                           f"args = {_toml(srv['args'])}", f"env = {_toml(srv['env'])}", ""]
        self.ecrire(self.dossier() / "config.toml", "\n".join(lignes))
        hooks = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": self.commande_hook(mode), "timeout": self.delai_hook_s()}]}]}}
        self.ecrire(self.dossier() / "hooks.json", json.dumps(hooks, indent=2))

    def commande(self, texte: str, session: str, modele: str, mode: str,
                 images: list[Path] | None = None) -> Commande:
        argv = list(self.argv0() or [self.binaire]) + ["exec", "--json", "--skip-git-repo-check",
                                                       "--sandbox", "workspace-write" if mode == MODE_OUTILS
                                                       else "read-only"]
        if modele and modele != MODELE_DEFAUT:
            argv += ["-m", modele]
        cwd = str(config.WORKDIR if mode == MODE_OUTILS else self.espace())
        argv += ["-C", cwd]
        for img in images or []:
            argv += ["-i", str(img)]
        if session:
            argv += ["resume", session]
        argv.append("-")  # consigne lue sur stdin : ni dans `ps`, ni limitée en longueur
        return Commande(argv, self.env(os.environ), cwd, entree=texte)

    def traduire(self, obj: dict, etat: EtatTour) -> list[dict]:
        t = str(obj.get("type") or "")
        if t == "thread.started":
            return [{"type": "_session", "id": str(obj.get("thread_id") or "")}]
        if t in ("item.started", "item.updated", "item.completed"):
            return self._item(obj.get("item") or {}, etat, fini=(t == "item.completed"))
        if t == "turn.completed":
            u = obj.get("usage") or {}
            return ev_usage(etat, u.get("input_tokens"), u.get("cached_input_tokens"), u.get("output_tokens")) \
                + ev_fin(etat)
        if t == "turn.failed":
            err = obj.get("error") or {}
            msg = str(err.get("message") if isinstance(err, dict) else err or "échec du tour")
            return ev_fin(etat, f"Codex : {msg[:300]}")
        if t == "error":
            etat.erreur = f"Codex : {str(obj.get('message') or 'erreur')[:300]}"
        return []

    def _item(self, item: dict, etat: EtatTour, fini: bool) -> list[dict]:
        kind, iid = str(item.get("type") or ""), str(item.get("id") or "")
        status = str(item.get("status") or "")
        if kind == "agent_message":
            return ev_texte(etat, str(item.get("text") or "")) if fini else []
        if kind == "command_execution":
            out = ev_outil(etat, iid, "Bash", {"command": str(item.get("command") or "")})
            if fini:
                code = item.get("exit_code")
                bad = status in ("failed", "declined") or (isinstance(code, int) and code != 0)
                out += ev_resultat_outil(iid, str(item.get("aggregated_output") or ""), bad)
            return out
        if kind == "file_change":
            changes = [c for c in item.get("changes") or [] if isinstance(c, dict)]
            first = str(changes[0].get("path") or "") if changes else ""
            out = ev_outil(etat, iid, "Edit", {"file_path": first, "changes": changes})
            if fini:
                out += ev_resultat_outil(iid, status, status in ("failed", "declined"))
            return out
        if kind == "mcp_tool_call":
            name = f"mcp__{item.get('server') or 'inconnu'}__{item.get('tool') or 'inconnu'}"
            args = item.get("arguments")
            out = ev_outil(etat, iid, name, args if isinstance(args, dict) else {})
            if fini:
                err = item.get("error")
                out += ev_resultat_outil(iid, err or item.get("result") or "", bool(err) or status == "failed")
            return out
        if kind == "web_search":
            out = ev_outil(etat, iid, "WebSearch", {"query": str(item.get("query") or "")})
            return out + (ev_resultat_outil(iid, "", False) if fini else [])
        if kind == "error" and fini:
            etat.erreur = f"Codex : {str(item.get('message') or 'erreur')[:300]}"
        return []  # reasoning (jamais dit), todo_list, inconnu
