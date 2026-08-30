# Workspace Memory Auto-Management — Concise Implementation Plan

1. **Evidence model and policy**
   - Extend `memory_candidate.py` with bounded evidence and strict structured
     extraction.
   - Add pure deterministic `MemoryPolicy` tests for authentic/fabricated user,
     config, command, secret, transient, duplicate, conflict, and precedence
     paths.

2. **Automatic persistence**
   - Add shared `MemoryAutoManager` over the existing store.
   - Test ADD/UPDATE/IGNORE, identity preservation, failure isolation,
     workspace isolation, and schema compatibility.

3. **CLI and product integration**
   - Replace interactive confirmation code with the shared manager.
   - Remove TUI candidate modal/API and emit lightweight Added/Updated activity.
   - Preserve all manual memory commands.

4. **Offline E2E and regression**
   - Use `FakeModelClient`, real session/memory persistence, real ContextManager,
     restart/new-session flows, and canonical tool evidence.
   - Run targeted Memory, Context/Session, TUI/Product, complete pytest, compile,
     smoke, forbidden-dependency, and credential scans.

Self-review gate: requirements covered; interfaces share one policy; old stored
schemas/sources remain readable; no placeholders; scope stays within workspace
memory formation and maintenance.
