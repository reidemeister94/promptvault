### Core Pillars

1. **Maximize simplicity, minimize complexity.** Weigh complexity cost against improvement magnitude.
2. **All signal, zero noise.** Everything must earn its place — if it doesn't add value, remove it.
3. **Document every discovery.** Write insights immediately (CLAUDE.md, chronicles, plans).
4. **Comments explain why, not what.** Comment non-obvious business logic, flows, and workarounds only.


## MANDATORY: Read Before Any Task

USE ALWAYS THE PLUGIN "development-skills" FOR EVERY TASK ON THIS PROJECT (BRAINSTORMING, DEVELOPMENT, BUG FIXING, NEW FEATURE, ...)

**Treat documentation as a first-class output of every task.**

### What to do during every task

1. **Document as you go.** Write patterns, gotchas, data shapes, naming conventions, workarounds immediately. Default: CLAUDE.md for project-wide, MEMORY.md for cross-session.
1a. **Code is documentation.** Comments explain *why*, not *what*. See Pillar #4.
2. **Remove ambiguity for your future self.** If you investigated something, write the answer where you'll find it next time.
3. **Use the right document:**

   | Document | Purpose | When to update |
   |----------|---------|----------------|
   | **CLAUDE.md** | Project-wide knowledge, conventions, rules. Loaded every conversation. | Architectural patterns, API shapes, DB schemas, testing conventions, gotchas |
   | **MEMORY.md** | Cross-session memory. Confirmed facts, user preferences. | Confirmed patterns, user corrections, expressed preferences |
   | **docs/plans/** | Implementation plans with checklists. Convention: `NNNN__YYYY-MM-DD__implementation_plan__slug.md` | Start (create), during (update checklist + log), completion (mark status) |
   | **docs/chronicles/** | Discoveries, debugging sessions, design decisions. | Surprising or non-obvious findings |
   | **Other docs/** | Domain docs | Business domain, data model, external systems |

4. **Make CLAUDE.md a cheat sheet, not a novel.** Tables, code snippets, direct statements. See Pillar #2.
5. **Keep documents aligned.** No duplication — each document has its own purpose.

### What NOT to do

- Don't defer documentation — it gets lost when context compresses
- Don't write unverified facts
- Don't bloat CLAUDE.md with session-specific details (use plans/chronicles)
- Don't duplicate — link instead
