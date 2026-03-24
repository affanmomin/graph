"""Human override loader and applicator.

Allows developers to correct and guide the generated memory — making the
system more trustworthy over time without requiring a full re-scan.

Override files live under ``.agent-memory/overrides/`` as YAML files and are
committed to Git so they are shared across teammates and machines. They are
the primary human-trust mechanism in the memory system.

Override file schema (planned YAML structure):

    # .agent-memory/overrides/rules.yaml
    always_include:
      - src/auth/middleware.py     # always surface this in context packs
      - docs/architecture.md

    never_edit:
      - src/vendor/               # never suggest changes here
      - migrations/               # treat as append-only

    notes:
      - "The auth module uses a custom JWT library, not python-jose."
      - "Database migrations are managed by the infra team, not app devs."

    task_hints:
      - pattern: "add endpoint"
        hint: "New endpoints go in src/api/routes/. Register in src/api/__init__.py."
      - pattern: "fix auth"
        hint: "Check src/auth/middleware.py first. JWT secret is env-var only."

Planned responsibilities:
- load all ``*.yaml`` files from ``overrides/`` directory
- merge into a single ``Overrides`` object
- apply ``always_include`` to every ``TaskContextPack``
- apply ``never_edit`` as warnings in every ``TaskContextPack``
- match ``task_hints`` against task descriptions and inject relevant hints
- preserve human-authored content; never auto-overwrite override files

TODO(overrides): define ``Overrides`` dataclass
TODO(overrides): implement ``load_overrides(agent_memory_root)`` -> Overrides
TODO(overrides): implement ``apply_overrides(pack, overrides)`` -> TaskContextPack
TODO(overrides): implement ``task_hint_match(task, overrides)`` -> list[str]
"""

from __future__ import annotations

# TODO(overrides): imports will be added when implementation begins
