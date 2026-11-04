# Architecture decision records

Design-time decisions D1–D20 are in `docs/context/10-decision-log.md`. Decisions taken during the
build go here as `ADR-<NNN>-<slug>.md` (template `docs/templates/adr.md`), created with `/adr`.
Write one when a choice affects more than one module, changes an external dependency, triggers a
kill criterion, or reverses a D-decision.

| ADR | Title | Date | Status | Supersedes |
|-----|-------|------|--------|-----------|
| [ADR-001](ADR-001-evon-hosting.md) | Evon hosting: BF16 reference on Modal, own Q8_0 GGUF live on RunPod A6000 | 2026-11-05 | accepted (quant gate pending) | D6 for live turns |
