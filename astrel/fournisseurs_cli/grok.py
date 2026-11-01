"""Grok Build (xAI), par son CLI officiel `grok` et l'abonnement de l'utilisateur (plan P27).

⚠️ NON PROUVÉ (22/10) : `grok` n'est pas installé sur cette machine, et son format n'est décrit
nulle part de façon sûre. Supposé d'après la documentation (sources du chercheur, 22/10), à
vérifier au lot 0 (runtime/equipe/p27/lot0-procedure.md) :
- `grok -p <consigne> --output-format streaming-json [-m modèle] [--resume <session>]` ;
- un flux de lignes JSON : soit au format stream-json de Claude (repris tel quel), soit des
  évènements `session|init {session_id}`, `text|content|delta {text|content|delta}`,
  `tool_use|tool_call {id, name, input|arguments}`, `tool_result {tool_use_id, content,
  is_error}`, `done|result|end {usage}`, `error {message}` (lecture tolérante) ;
- `GROK_HOME` place réglages et connexion dans `runtime/cli/grok/` (nom de variable SUPPOSÉ) ;
- MCP et hooks `PreToolUse` dans `settings.json`, au format de Claude Code (SUPPOSÉ ; la capacité
  du hook à REFUSER est inconnue). Conditions d'usage tierces de xAI non trouvées.
Capacités : toutes fausses tant que le lot 0 ne les a pas vues (le choix reste refusé).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .. import config
from .base import (MODE_OUTILS, MODELE_DEFAUT, Capacites, Commande, EtatTour, FournisseurCli, ev_fin, ev_outil,
                   ev_resultat_outil, ev_texte, ev_usage)

_CLAUDE_TYPES = ("assistant", "user", "stream_event")


class Grok(FournisseurCli):
    id = "grokbuild"
    nom = "Grok Build"
    binaire = "grok"
    env_dossier = "GROK_HOME"  # SUPPOSÉ (lot 0, C2 : où le CLI écrit-il sa connexion ?)
    capacites = Capacites()  # NON PROUVÉ : lot 0

    @property
    def commande_connexion(self) -> str:  # type: ignore[override]
        return f"GROK_HOME={self.dossier()} grok   (puis la connexion proposée par le CLI)"

    def outils_annonces(self, mode: str) -> list[str]:
        return ["Bash", "Edit"] if mode == MODE_OUTILS else []

    def config_outils(self, mode: str) -> None:
        """`settings.json` : hook d'Astrel sur tous les outils (refuse tout en conversation seule),
        MCP `astrel`/`connecteurs` en mode outils seulement."""
        data: dict = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": self.commande_hook(mode), "timeout": self.delai_hook_s()}]}]}}
        if mode == MODE_OUTILS:
            data["mcpServers"] = {n: {"type": "stdio", **s} for n, s in self.serveurs_mcp().items()}
        self.ecrire(self.dossier() / "settings.json", json.dumps(data, indent=2))

    def commande(self, texte: str, session: str, modele: str, mode: str,
                 images: list[Path] | None = None) -> Commande:
        argv = list(self.argv0() or [self.binaire]) + ["-p", texte, "--output-format", "streaming-json"]
        if modele and modele != MODELE_DEFAUT:
            argv += ["-m", modele]
        if session:
            argv += ["--resume", session]
        cwd = str(config.WORKDIR if mode == MODE_OUTILS else self.espace())
        return Commande(argv, self.env(os.environ), cwd)

    def traduire(self, obj: dict, etat: EtatTour) -> list[dict]:
        t = str(obj.get("type") or "").lower()
        sid = obj.get("session_id") or obj.get("sessionId")
        out: list[dict] = [{"type": "_session", "id": str(sid)}] if sid and t in (
            "system", "init", "session", "session_start", "result", "done") else []
        if t == "system":
            return out  # init du CLI : `SessionCli` émet le sien (id de session d'Astrel)
        if t in _CLAUDE_TYPES:
            if t == "stream_event" and ((obj.get("event") or {}).get("delta") or {}).get("text"):
                etat.texte.append(obj["event"]["delta"]["text"])
            etat.message = etat.message or t != "user"
            return out + [obj]
        if t in ("session", "init", "session_start"):
            return out
        if t in ("text", "content", "delta", "message_delta", "assistant_delta"):
            chunk = obj.get("text") or obj.get("content") or obj.get("delta") or ""
            return out + ev_texte(etat, chunk if isinstance(chunk, str) else "")
        if t == "message" and obj.get("role") == "assistant" and isinstance(obj.get("content"), str):
            return out + ev_texte(etat, obj["content"])
        if t in ("tool_use", "tool_call"):
            args = obj.get("input", obj.get("arguments"))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {"arguments": args}
            return out + ev_outil(etat, str(obj.get("id") or obj.get("tool_call_id") or ""),
                                  str(obj.get("name") or "outil"), args if isinstance(args, dict) else {})
        if t == "tool_result":
            tid = str(obj.get("tool_use_id") or obj.get("tool_call_id") or obj.get("id") or "")
            return out + ev_resultat_outil(tid, obj.get("content", obj.get("output", "")),
                                           bool(obj.get("is_error") or obj.get("error")))
        if t in ("done", "result", "end", "complete", "finish"):
            u = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
            if obj.get("is_error"):
                return out + ev_fin(etat, f"Grok : {str(obj.get('result') or obj.get('error') or 'échec')[:300]}")
            if not etat.texte and isinstance(obj.get("result"), str):
                out += ev_texte(etat, obj["result"])
            return out + ev_usage(etat, u.get("input_tokens") or u.get("prompt_tokens"),
                                  u.get("cache_read_input_tokens") or u.get("cached_tokens"),
                                  u.get("output_tokens") or u.get("completion_tokens")) + ev_fin(etat)
        if t == "error":
            msg = obj.get("message") or obj.get("error") or "erreur"
            return out + ev_fin(etat, f"Grok : {str(msg)[:300]}")
        return out
