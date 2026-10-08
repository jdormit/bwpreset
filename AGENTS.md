# bwpreset

Build Bitwig Poly/FX/Note Grid patches as code. Scope and acceptance requirements are in GRID-GAPS.md and docs/completion-status.md.

## Collaboration

Parallel agents share this checkout. Edit only the files assigned in your prompt. Report any needed changes to another agent's files to the coordinator. Do not commit, stage, publish, alter user presets, or change Bitwig's running project unless your assignment explicitly requests it.

## Verification

- Use uv run pytest for tests and uvx ruff check / uvx ruff format for lint/format.
- Prefer synthetic fixtures and temporary assets so tests work without Jeremy's personal library.
- Ground format mappings in installed Bitwig definitions, application code or controlled preset differences. Keep unresolved guesses labeled.
- Preserve unknown fields when editing existing graphs. Do not mistake a parser round trip for live Bitwig verification.
- No proprietary definitions, audio, application dumps or absolute user paths in new committed fixtures.
