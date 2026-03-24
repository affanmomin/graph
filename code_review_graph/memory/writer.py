"""Disk writer for ``.agent-memory/`` artifacts.

Responsible for safely writing generated memory artifacts to the filesystem
under ``.agent-memory/``. This is the only module in the memory subsystem
that writes to disk (other than ``metadata.py`` for metadata files).

Design constraints:
- writes must be atomic where possible (write to temp, rename)
- must not overwrite human-authored override files in ``overrides/``
- must create parent directories as needed
- must produce stable output (same content -> same bytes) to minimise Git diff noise
- durable artifacts only; heavy graph/index state is never written here

Planned responsibilities:
- write a markdown artifact to its ``relative_path`` under ``.agent-memory/``
- write metadata JSON files (delegated from ``metadata.py``)
- manage the ``.agent-memory/`` directory structure
- skip writes when content is unchanged (compare hashes before writing)
- provide a ``WriteSummary`` result describing what was created/updated/skipped

TODO(writer): implement ``write_artifact(artifact, content, agent_memory_root)``
TODO(writer): implement ``write_metadata(manifest, agent_memory_root)``
TODO(writer): implement change detection to skip no-op writes
TODO(writer): ensure ``.agent-memory/`` is added to .gitignore's exclusions
              for the local-only metadata/cache files only
"""

from __future__ import annotations

# TODO(writer): imports will be added when implementation begins
