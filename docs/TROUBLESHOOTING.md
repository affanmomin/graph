# Troubleshooting

## Database lock errors
The graph uses SQLite with WAL mode. If you see lock errors:
- Ensure only one build process runs at a time
- The database auto-recovers; just retry
- Delete `.repomind/graph.db-wal` and `.repomind/graph.db-shm` if corrupt

## Large repositories (>10k files)
- First build may take 30-60 seconds
- Subsequent incremental updates are fast (<2s)
- Add more ignore patterns to `.repomindignore`:
  ```
  generated/**
  vendor/**
  *.min.js
  ```

## Missing nodes after build
- Check that the file's language is supported (see [FEATURES.md](FEATURES.md))
- Check that the file isn't matched by an ignore pattern
- Run with `full_rebuild=True` to force a complete re-parse

## Graph seems stale
- Hooks auto-update on edit/commit
- If stale, run `/repomind:build-graph` manually
- Check that hooks are configured in `hooks/hooks.json` (see [hooks documentation](../hooks/hooks.json))

## Embeddings not working
- Install with: `pip install repomind[embeddings]`
- Run `embed_graph_tool` to compute vectors
- First embedding run downloads the model (~90MB, one time)

## MCP server won't start
- Verify `uv` is installed (`uv --version`; install with `pip install uv` or `brew install uv`)
- Check that `uvx repomind serve` runs without errors
- If using a custom `.mcp.json`, ensure it uses `"command": "uvx"` with `"args": ["repomind", "serve"]`
- Re-run `repomind install` to regenerate the config
