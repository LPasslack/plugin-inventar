"""Loading, atomic writing and comparison of the stored state."""
import hashlib
import json
import os
import re
import tempfile

# 3: fields that are COMPARED have been added since 2, so an old state passes
# the guard and then produces a pseudo-change for every skill -- exactly the
# avalanche of false alarms the guard exists to prevent. Bumping costs one
# "no comparison" run and buys back the guard's whole purpose.
#
# 2: all field names switched from German to English, plus body_hash,
# content_hash and value_hash.
# 3: frontmatter_hash, raw_hash (hooks, MCP, manifest), extras_hash, tools,
#    and findings plus the category states became part of the comparison.
# 4: the same reason as 3, from the other side -- the VALUES changed, not the
#    field names. extras_hash now covers what a basename skip used to prune
#    (a `references/bin/`, a directory called SKILL.md), and identical
#    findings no longer collapse into one. An old state compares field for
#    field and reports a change in files nobody touched.
SCHEMA = 4

# Compared fields that live on the entry itself rather than inside "fields".
# A skill moving from the plugin root into a skills/ directory, or from the
# convention into a declared path, is a change to the same thing -- without
# `source` and `source_kind` the move would be invisible.
#
# `markers` and `findings` were stored and not compared. Every one of them is
# derivable from a compared field today, so nothing was actually missed -- but
# that invariant is written nowhere, and the next marker that does not follow
# from a field (say "world-writable" on a bin/ entry, where only `executable`
# and `content_hash` are compared) would be a silent all-clear. Comparing them
# costs nothing and removes the trap.
ENTRY_LEVEL_KEYS = ("source", "source_kind", "markers", "findings")


def _base_dir():
    return os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
        "plugin-inventar")


def state_path(key):
    """Map a key to a file.

    The slug is restricted to [a-z0-9-] so it works on any filesystem, and it
    is capped: a key handed in via --als is arbitrarily long and would
    otherwise blow past the filename limit, and only while writing, after the report was already
    printed. Uniqueness is carried by the hash anyway, which is built over the
    ORIGINAL value -- otherwise "Foo" and "foo" would collide on a filesystem
    that ignores case.
    """
    slug = re.sub(r"[^a-z0-9-]+", "-", key.lower()).strip("-") or "unnamed"
    slug = slug[:60].rstrip("-")
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    return os.path.join(_base_dir(), f"{slug}-{digest}.json")


def load(key):
    """Load the previous state. Returns (data, reason_when_unusable).

    Checks the SHAPE as well, not just the syntax. `[]` is valid JSON and would
    have ended the following run with an AttributeError. An unusable state file
    is treated like "no previous state", which is the right behaviour.
    """
    path = state_path(key)
    if not os.path.isfile(path):
        # Something that is not a regular file is not "no previous state".
        # Saying "first run" over it would be a lie, and the next save would
        # fail at the very end of the run.
        if os.path.exists(path) or os.path.islink(path):
            return None, "not a regular file at the state path"
        return None, None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        return None, f"nicht lesbar ({exc.strerror})"
    except json.JSONDecodeError:
        return None, "not valid JSON"
    if not isinstance(data, dict) or not isinstance(data.get("meta"), dict):
        return None, "unerwarteter Aufbau"
    inventory = data.get("inventory")
    if not isinstance(inventory, dict) or not isinstance(inventory.get("entries"), dict):
        return None, "unerwarteter Aufbau"
    if not all(isinstance(entry, dict) for entry in inventory["entries"].values()):
        return None, "unerwarteter Aufbau"
    # fields and identity are read directly in diff(); a wrong type there
    # ended the run with a traceback instead of skipping the comparison.
    if not all(isinstance(entry.get("fields", {}), dict)
               for entry in inventory["entries"].values()):
        return None, "unerwarteter Aufbau"
    if not isinstance(inventory.get("identity", {}), dict):
        return None, "unerwarteter Aufbau"
    # findings is read directly in diff() since findings became part of the
    # comparison. Without this check a single damaged state file ended the
    # WHOLE sweep in a traceback instead of costing one plugin its
    # comparison -- the only place where this tool crashes at all.
    findings = inventory.get("findings", [])
    if not isinstance(findings, list) or not all(
            isinstance(finding, dict) for finding in findings):
        return None, "unerwarteter Aufbau"
    # Not `key`: that is this function's parameter, and shadowing it here is a
    # trap for whoever adds a line below that still expects it.
    for field in ("checked_absent", "unreadable"):
        if not isinstance(inventory.get(field, []), list):
            return None, "unerwarteter Aufbau"
    return data, None


def save(key, state):
    """Write atomically and rotate the previous file.

    Three classic pitfalls, all covered here: the temporary file must sit in
    the SAME directory, otherwise os.replace fails across filesystem
    boundaries. flush alone is not enough. And without fsync on the directory
    the rename does not survive a power loss.
    """
    path = state_path(key)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)

    if os.path.isfile(path):
        try:
            os.replace(path, path[:-5] + ".1.json")
        except OSError:
            pass

    text = json.dumps(state, sort_keys=True, indent=2, ensure_ascii=True)
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _finding_key(finding):
    """Comparable identity of a finding, ignoring nothing that matters."""
    return (finding.get("code"), finding.get("path"), finding.get("category"),
            finding.get("detail"), finding.get("detail_arg"))


def _category_states(inventory):
    """Map every category the run had an opinion about to that opinion.

    Three states, and the difference between them is the whole point of this
    tool: present, checked and absent, present but not evaluable. A category
    moving between them is a change, and it used to be invisible.
    """
    states = {}
    for key in inventory.get("checked_absent") or []:
        states[key] = "absent"
    for key in inventory.get("unreadable") or []:
        states[key] = "unreadable"
    return states


def diff(old, new):
    """Compare two inventory dicts at the level of entry IDs.

    Returns eight keys: `added`, `removed` and `changed` for the entries,
    `identity` for the plugin itself, `findings` and `categories` for what
    the run could and could not read, plus `matchers` and `commands` as the
    readable names the report needs for hook IDs.
    """
    old_entries = old.get("entries", {})
    new_entries = new.get("entries", {})
    added = sorted(set(new_entries) - set(old_entries))
    removed = sorted(set(old_entries) - set(new_entries))
    changed = {}

    for ident in sorted(set(old_entries) & set(new_entries)):
        differences = {}

        for key in ENTRY_LEVEL_KEYS:
            before, now = old_entries[ident].get(key), new_entries[ident].get(key)
            if before != now:
                differences[key] = (before, now)

        old_fields = old_entries[ident].get("fields", {})
        new_fields = new_entries[ident].get("fields", {})
        for field in sorted(set(old_fields) | set(new_fields)):
            before, now = old_fields.get(field), new_fields.get(field)
            if before != now:
                differences[field] = (before, now)

        if differences:
            changed[ident] = differences

    identity_changes = {}
    for key in ("name", "version", "manifest_present", "raw_hash"):
        before = old.get("identity", {}).get(key)
        now = new.get("identity", {}).get(key)
        if before != now:
            identity_changes[key] = (before, now)

    # A hook ID carries the matcher only as a hash, because the ID has to be
    # stable. That is right for comparison and unreadable in a report: nobody
    # recognises their own hook by "m8e7e3ff9c84f". The matcher value travels
    # alongside so the report can name it. Taken from whichever side has the
    # entry -- a removed hook exists only in the old state.
    matchers, commands = {}, {}
    for ident in added + removed + list(changed):
        entry = new_entries.get(ident) or old_entries.get(ident) or {}
        fields = entry.get("fields") or {}
        if "matcher" in fields:
            matchers[ident] = fields["matcher"]
        if fields.get("command"):
            commands[ident] = fields["command"]

    # Findings and category states are part of the inventory and were stored
    # but never compared. A plugin could gain "path leaves the plugin" and
    # "absolute path" between two runs, move two categories from absent to
    # not-evaluable, and the report still said "no changes since the last
    # run" -- the same false all-clear the catch-all hashes were built
    # against, one level up.
    # load() rejects a malformed findings list, so this is defence in depth --
    # but diff() is importable on its own, and a crash here would end a whole
    # sweep instead of costing one plugin its comparison.
    def as_findings(inventory):
        raw = inventory.get("findings")
        if not isinstance(raw, list):
            return {}
        # Ordinal in the key, so identical findings do not collapse into one.
        # Findings carry no unique path -- five broken hook groups in one
        # hooks.json produce five identical dicts -- and as plain dict keys
        # they became a single entry. Going from five to one, or from one to
        # five, then reported no change at all.
        seen, out = {}, {}
        for finding in raw:
            if not isinstance(finding, dict):
                continue
            base = _finding_key(finding)
            seen[base] = seen.get(base, 0) + 1
            out[base + (seen[base],)] = finding
        return out

    old_findings, new_findings = as_findings(old), as_findings(new)
    findings = {
        "added": [new_findings[k] for k in new_findings if k not in old_findings],
        "removed": [old_findings[k] for k in old_findings if k not in new_findings],
    }

    old_states, new_states = _category_states(old), _category_states(new)
    categories = {}
    for key in sorted(set(old_states) | set(new_states)):
        before = old_states.get(key, "present")
        now = new_states.get(key, "present")
        if before != now:
            categories[key] = (before, now)

    return {"added": added, "removed": removed, "changed": changed,
            "identity": identity_changes, "matchers": matchers,
            "commands": commands, "findings": findings,
            "categories": categories}
