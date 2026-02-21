---
name: write-article
description: Write a technical article about a ShiftingCodes pass or related topic
---

Write a technical article about $ARGUMENTS and save it to `articles/` with a descriptive kebab-case filename.

## Workflow

### 1. Read before writing
- Read the actual pass implementation(s) the article covers — never describe what code does from memory
- Read the test files and `tests/conftest.py` helper functions to understand what "before" IR looks like
- If the article covers IR examples, trace the exact instruction sequences the pass emits

### 2. Frontmatter
Every article starts with:
```yaml
---
title: "Title Here"
date: "YYYY-MM-DD"
description: "One sentence describing what the article covers and why it matters"
tags: ["relevant", "tags", "here"]
---
```

### 3. Structure
1. **Hook** — one paragraph on the real-world problem (reverse engineering, protection, etc.)
2. **What is X** — introduce the upstream project(s) with accurate history and maintenance status
3. **The problem** — why the original doesn't work today (version freeze, API churn, etc.)
4. **The solution** — introduce ShiftingCodes and llvm-nanobind; credit authors specifically
5. **Before/after IR** — concrete examples grounded in actual pass output
6. **How to use** — setup, Python driver script, selective obfuscation, UI
7. **Credits** — name individuals, not just projects

### 4. IR example rules
- Add this note at the top of the before/after section:
  > *IR samples are lightly simplified for readability — actual output uses random constants and generated names — but the instruction sequences and structure match the Python implementation exactly.*
- **Before IR**: must match what the `conftest.py` helper functions actually build
- **After IR**: must reflect the real algorithm — read the pass source, trace the instruction sequence
- Use `<placeholder>` for random constants (e.g. `<a>`, `<m>`, `<loop_state>`) rather than inventing specific numbers
- Show real variable names from the pass (`bcf.var`, `cff.state`, `sub.ar`, etc.)
- Comments in IR should explain the invariant, not just label blocks
- If a block is dead/unreachable, say so explicitly in a comment

### 5. Tone and accuracy
- No superlatives ("brilliant", "amazing") — describe what things do, not how impressive they are
- State clearly when upstream projects are unmaintained/archived
- Legal disclaimer (end of article):
  > *[Project] is provided for legitimate use cases including software protection, security research, CTF challenge authoring, and compiler education. The authors make no representations regarding fitness for any particular purpose and accept no liability for any misuse or damages arising from the use of this software. Use is entirely at your own risk and responsibility.*
- Credit individual contributors by name with GitHub links (e.g. mrexodia for llvm-nanobind)
- Tease future articles for related projects rather than covering them inline

### 6. Scope discipline
- Be precise about what the article covers — if it's about Pluto, don't claim 17 passes when Pluto has 6
- Don't mention Polaris techniques as Pluto techniques
- If an IR example uses a Polaris-era algorithm, describe the algorithm accurately, not the simpler Pluto version

### 7. Verification checklist before finishing
- [ ] All URLs match the canonical list in CLAUDE.md
- [ ] Each "before" IR matches what conftest helpers actually build
- [ ] Each "after" IR matches the actual pass algorithm (read the source)
- [ ] Pass count / feature claims are accurate
- [ ] Maintenance status of upstream projects is stated correctly
- [ ] Article scoped to what it claims to cover
- [ ] Frontmatter complete with today's date
- [ ] Saved to `articles/<descriptive-kebab-name>.md`
