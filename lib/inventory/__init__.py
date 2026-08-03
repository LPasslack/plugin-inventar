"""How a run fits together.

A run goes through four modules. `collect.py` resolves the paths (convention
plus manifest declaration) and builds a dict `ID -> entry`; those IDs are the
unit of comparison and have to survive an update. `state.py` loads the
previous run, compares set difference plus field comparison, and writes
atomically. `report.py` is the only place with German text: everything else
passes English codes through and `report.py` translates them via FINDING_TEXT,
DETAIL_TEXT, FIELD_TEXT, CATEGORY_TEXT and KIND_TEXT. `installed.py` finds the
installed plugins through Claude Code's registry.

Two edits that have to be made in more than one place, both guarded by
`tests/test_invariants.py`:

- A new component kind touches COMPONENTS, KIND_TO_CATEGORY, SECTIONS,
  KIND_TEXT and CATEGORY_TEXT. Forget one of the last two and the report
  claims "checked and absent" about something it just found.
- A new COMPARED field means raising SCHEMA in state.py, or an old state
  passes the guard and produces a pseudo-change for every entry. The same
  applies when an existing field is COMPUTED differently: the field name is
  unchanged, so nothing fails, and every user gets one run of false alarms.

Two limits that are easy to read as one. `tree_digest` takes `skip`, which is
a path from the collection root, and `skip_names`, which is a basename at any
depth -- the second is only ever SKILL.md, because only a nested skill has an
entry of its own wherever it turns up. Matching `skip` by basename as well
turned every one of those names into a hiding place.

Anything walked needs a cap on COUNT, not only on depth and file size:
`_walk` has MAX_ENTRIES, `tree_digest` has max_files. Without one, 100.000
files of 40 bytes each produce a 69 MB state file and half a gigabyte of
memory on the next run.

Tests: new tests go by topic into test_collect / test_report / test_state and
friends. Every file needs `import support`, has to pass on its own, and may
only write into temp directories (set XDG_STATE_HOME and CLAUDE_CONFIG_DIR).
Nothing may touch the user's own ~/.claude.
"""
