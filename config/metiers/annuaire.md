# Annuaire des métiers de l'équipe d'Astrel (plan P13)

Un MÉTIER = une fiche dans ce dossier ; un AGENT = une définition courte dans
`config/agents/` qui lit sa fiche au lancement. Légende : modèle h/s/o (haiku, sonnet,
opus) ; L = lecture seule, É = écrit ; actif = agent défini, catalogue = idée seulement
(aucun coût tant qu'il n'est pas activé). Règles communes : `equipe.md` ; couverture :
`couverture.md`.

- Pilotage : chef de projet = agent `planificateur` (s, L, actif, `chef-de-projet.md`) ;
  avocat du diable (s, L, actif, `avocat-du-diable.md`) ; archiviste, bilan (actifs,
  consignes dans leur définition).
- Produit : analyste de produit concurrent (s, L, catalogue, `analyste-concurrent.md`) ;
  rédacteur de spécifications (s, L, catalogue).
- Ingénierie : codeur (o, É, actif, `codeur.md`) ; architecte (o, L, catalogue) ;
  dépanneur de bugs (o, É, catalogue).
- Qualité : testeur (s, L + tests et app sans fenêtre visible, actif, `testeur.md`) ;
  relecteur (s, L, actif, `relecteur.md`).
- Sécurité : auditeur (s, L, actif) ; sécurité applicative (s, L, actif, localhost
  seulement, `securite-applicative.md`) ; veilleur de vulnérabilités (h, L, catalogue).
- Design : designer d'interface (o, É, catalogue) ; motion (s, É, catalogue).
- Marketing : stratège marketing (s, L + web, actif, `stratege-marketing.md`) ;
  rédacteur (s, É texte, catalogue) ; SEO (s, L, catalogue) ; fiche stores (s, catalogue).
- Ventes et support : support client (h, catalogue) ; recherche clients (s, catalogue).
- Finance et droit : CFO et prix (s, L, catalogue) ; juridique et RGPD (s, L, catalogue,
  avant toute publication).
- Données : backtester (o, É, catalogue, projet Trading).
- Veille : chercheur (h, L, actif).
- Opérations : déploiement (s, É, catalogue, toujours avec l'accord de l'utilisateur).

Enchaînements (Astrel les lance, un agent à la fois) :
- Plan : planificateur → avocat-du-diable → planificateur (2 tours au plus) → l'utilisateur.
- Code : codeur → testeur → relecteur (relais automatique du cœur après chaque codeur).
