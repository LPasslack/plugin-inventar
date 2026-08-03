"""Plain text report, with escaping of foreign values.

Code and comments are English, the printed text is German on purpose: the
report is what viewers see, the code is what developers read.
"""
import unicodedata

# C0 control characters, DEL, the C1 range and the bidi overrides. Everything
# that could manipulate how a terminal renders the line.
_ESCAPES = {code: "\\x%02x" % code for code in range(0x20)}
_ESCAPES[0x7F] = "\\x7f"
_ESCAPES.update({code: "\\x%02x" % code for code in range(0x80, 0xA0)})
_ESCAPES.update({code: "\\u%04x" % code for code in (
    0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
    0x2066, 0x2067, 0x2068, 0x2069, 0x0085, 0x2028, 0x2029)})
_TRANSLATION = str.maketrans(_ESCAPES)

# Unicode categories that render as nothing: other-control, other-format,
# private use, surrogate. A positive list always misses one of them.
_INVISIBLE = frozenset({"Cc", "Cf", "Co", "Cs"})

# Characters that render as nothing but are NOT in those categories. A blanket
# rule over "Mn" would mangle legitimate diacritics, so this stays a named
# list: variation selectors, Hangul fillers, Braille blank, and every space
# except the plain one.
_INVISIBLE_EXTRA = frozenset(
    list(range(0xFE00, 0xFE10)) + list(range(0xE0100, 0xE01F0))
    + [0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800, 0x00A0, 0x2007, 0x202F]
    + [0x034F, 0x17B4, 0x17B5]
    + list(range(0x2000, 0x200B)) + [0x205F, 0x3000, 0x1680])

# Order by consequence. This IS a judgement, and it is this one: whatever runs
# without your involvement comes first.
SECTIONS = [
    ("hook", "Hooks", "Hook"),
    ("settings", "Settings", "Setting"),
    ("mcp", "MCP servers", "MCP server"),
    ("bin", "bin/", "bin/"),
    ("command", "Commands", "Command"),
    ("skill", "Skills", "Skill"),
    ("agent", "Agents", "Agent"),
    ("count", "Other", "Other"),
    ("unused_file", "files not loaded", "file not loaded"),
]

# Codes, markers and field names are English internally and in the report
# alike. The tables stay because they separate the wire format from what a
# reader sees: renaming a code must not change the report, and rewording the
# report must not change the state file.
MARKER_LEGEND = {
    "reloads": "\u201cloads at runtime\u201d: the command contains curl, npx or "
               "similar, so what actually runs is fetched only when it runs",
    "leaves-plugin": "\u201cleaves the plugin\u201d: the path points outside the "
                     "plugin directory",
}

MARKER_TEXT = {
    "reloads": "loads at runtime",
    "leaves-plugin": "leaves the plugin",
}

FINDING_TEXT = {
    "invalid-json": "invalid JSON",
    "file-too-large": "file too large",
    "nesting-too-deep": "nested too deeply",
    "recursion": "recursion limit reached",
    "no-read-permission": "no read permission",
    "symlink": "Symlink",
    "symlink-outside": "symlink points outside the plugin",
    "not-a-regular-file": "not a regular file",
    "path-leaves-plugin": "path leaves the plugin",
    "absolute-path": "absolute path",
    # The opposite case, and it needs its own word: in a manifest an absolute
    # path is rejected, in the registry a relative one is.
    "relative-path": "path is not absolute",
    "declared-path-missing": "declared path missing",
    "declared-inline": "declared inline in the manifest",
    "present-but-not-loaded": "present but not loaded",
    "unparsable-frontmatter": "frontmatter not readable",
    "name-differs": "name differs",
    "unknown-hook-type": "unknown hook type",
    "unexpected-type": "unexpected type",
    "duplicate-id": "duplicate id",
    "too-deep": "nested too deeply, not inspected further",
    "too-many": "too many entries, stopped here",
    "already-visited": "reachable under a second name",
    "displaced-by-manifest": "present but displaced by the manifest",
}

FIELD_TEXT = {
    "command": "command", "source": "source", "source_kind": "source kind",
    "event": "Event", "matcher": "Matcher", "hook_type": "hook type",
    "timeout": "timeout", "condition": "condition",
    "status_message": "status message", "run_once": "once per session",
    "args": "arguments", "shell": "Shell", "run_async": "runs in background",
    "async_rewake": "wakes back", "header_names": "headers",
    "allowed_env": "passes variables", "server": "Server", "tool": "tool",
    "model": "model", "transport": "Transport", "url": "url",
    "env_variables": "expected variables", "always_load": "always loaded",
    "uses_oauth": "OAuth", "uses_headers_helper": "headers from external command",
    "name": "Name", "version": "Version", "manifest_present": "manifest present",
    "key": "key", "value": "value", "value_hash": "value as a whole",
    "count": "count", "executable": "executable", "is_symlink": "symlink",
    "link_target": "symlink target", "content_hash": "content",
    "body_hash": "instruction body", "frontmatter_name": "name in frontmatter",
    "description_hash": "description", "context": "context",
    "allowed_tools": "may use", "disallowed_tools": "may not use",
    "tools": "tools",
    "disable_model_invocation": "on request only", "user_invocable": "user invocable",
    "declares_hooks": "declares hooks", "in_plugin_root": "in plugin root",
    "shell_lines": "shell lines",
    "frontmatter_hash": "Frontmatter",
    "raw_hash": "entry as a whole", "manifest_path": "manifest path",
    "extras_hash": "files without their own entry",
}

# Entry ID prefixes show up in the diff and have to be readable there.
KIND_TEXT = {
    "hook": "Hook", "mcp": "MCP server", "settings": "Setting",
    "skill": "Skill", "command": "Command", "agent": "Agent", "bin": "bin/",
    "count": "count", "file": "File",
}


def finding_text(code):
    """Public: bin/plugin-inventar prints findings before the report exists."""
    return FINDING_TEXT.get(code, code)


def _marker_text(code):
    return MARKER_TEXT.get(code, code)


def _field_text(name):
    return FIELD_TEXT.get(name, name)


def _ident_text(ident, matcher=None):
    """Name an entry for the diff, e.g. `Hook Stop (matcher: Bash)`.

    For hooks the raw ID is useless to a reader: the matcher is in it as a
    hash, and the trailing counter is an implementation detail. When the
    matcher is known, the event plus the matcher takes its place -- the same
    wording the inventory above already uses.
    """
    prefix, _, rest = ident.partition(":")
    if not rest:
        return ident
    if prefix == "count":
        return f"{KIND_TEXT.get(prefix, prefix)} {_category_text(rest)}"
    if prefix == "hook" and matcher is not None:
        event = rest.split(":", 1)[0]
        where = f" (matcher: {matcher})" if matcher else " (all)"
        return f"{KIND_TEXT.get(prefix, prefix)} {event}{where}"
    return f"{KIND_TEXT.get(prefix, prefix)} {rest}"


def visible(text):
    """Defuse control and invisible characters coming from foreign files.

    Without this a \\r inside a hook command hides the actual command: the tool
    reports it correctly and the human never sees it.

    The table above is a fast path for the common cases. The category check
    behind it catches what a positive list always misses: tag characters
    (U+E0000..U+E007F) are invisible in a terminal but ordinary text to any
    model reading this report, and zero-width characters can break a word so
    that "cu<zwsp>rl" reads as curl without being it. Since the report is meant
    to be read by both, both have to be safe.
    """
    escaped = str(text).translate(_TRANSLATION)

    def hidden(char):
        return (unicodedata.category(char) in _INVISIBLE
                or ord(char) in _INVISIBLE_EXTRA)

    if not any(hidden(char) for char in escaped):
        return escaped
    return "".join(
        ("\\U%08x" if ord(char) > 0xFFFF else "\\u%04x") % ord(char)
        if hidden(char) else char for char in escaped)


def shorten(text, limit=500):
    """Cut to raw codepoints. ALWAYS call before visible().

    The other way round would slice an escape sequence in half.
    """
    text = str(text)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… [shortened, {len(text)} characters]"


# Python literals must not surface in a German report.
_LITERALS = {True: "yes", False: "no", None: "not set"}


def _readable(value):
    """Render a value for German output instead of leaking a Python repr."""
    if isinstance(value, bool) or value is None:
        return _LITERALS[value]
    if isinstance(value, (list, tuple)):
        return ", ".join(_readable(item) for item in value) if value else "leer"
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_readable(v)}" for k, v in sorted(value.items())) \
            if value else "leer"
    return str(value)


def _safe(text):
    """Shorten, then escape. Every foreign value goes through here."""
    return visible(shorten(_readable(text)))


def _header_line(identity, counts):
    name = _safe(identity.get("name", "?"))
    version = _safe(identity.get("version")) if identity.get("version") \
        else "(no version)"
    parts = [f"{name} {version}"]
    for kind, plural, singular in SECTIONS:
        count = counts.get(kind)
        if count:
            parts.append(f"{count} {singular if count == 1 else plural}")
    return " · ".join(parts)


def _entry_lines(kind, ident, entry):
    fields = entry["fields"]
    markers = (f"  [{', '.join(_marker_text(m) for m in entry['markers'])}]"
               if entry["markers"] else "")
    # The part after the colon comes from foreign data (server name, file name,
    # directory name) and needs the same treatment as any other foreign value.
    name = _safe(ident.split(":", 1)[1])
    lines = []

    if kind == "hook":
        # An empty matcher means "applies to everything", which reads better
        # spelled out than as an empty pair of brackets.
        raw_matcher = fields.get("matcher") or ""
        matcher = f" (matcher: {_safe(raw_matcher)})" if raw_matcher else " (all)"
        # Only name the type when it is not the usual one. An http hook sends
        # data outwards, that has to be visible.
        hook_type = fields.get("hook_type", "command")
        if isinstance(hook_type, str) and hook_type.startswith("unknown:"):
            # Internally the value stays English; the report says it in German.
            shown = "unbekannter Typ: " + hook_type.split(":", 1)[1]
        else:
            shown = hook_type
        type_note = "" if hook_type == "command" else f"  [{_safe(shown)}]"
        lines.append(f"  {_safe(fields.get('event'))}{matcher}{type_note}{markers}")
        lines.append(f"      {_safe(fields.get('command', ''))}")
        if fields.get("header_names"):
            names = ", ".join(_safe(header) for header in fields["header_names"])
            lines.append(f"      Headers: {names} (values not shown)")
        if fields.get("allowed_env"):
            names = ", ".join(_safe(var) for var in fields["allowed_env"])
            lines.append(f"      gibt weiter: {names}")
        if fields.get("condition"):
            lines.append(f"      only if: {_safe(fields['condition'])}")
        if fields.get("run_async") or fields.get("async_rewake"):
            lines.append("      runs in the background")
        if fields.get("run_once"):
            lines.append("      once per session only")
        if fields.get("model"):
            lines.append(f"      Modell: {_safe(fields['model'])}")
        if fields.get("shell"):
            lines.append("      runs through a shell")
        if fields.get("timeout") is not None:
            lines.append(f"      timeout: {_safe(fields['timeout'])}")
        if fields.get("status_message"):
            lines.append(f"      meldet: {_safe(fields['status_message'])}")
    elif kind == "mcp":
        target = fields.get("url") or " ".join(
            [str(fields.get("command") or "")] + list(fields.get("args") or []))
        lines.append(f"  {name}  [{_safe(fields.get('transport'))}]{markers}")
        lines.append(f"      {_safe(target)}")
        if fields.get("header_names"):
            names = ", ".join(_safe(header) for header in fields["header_names"])
            lines.append(f"      Headers: {names} (values not shown)")
        if fields.get("env_variables"):
            names = ", ".join(_safe(var) for var in fields["env_variables"])
            lines.append(f"      expects: {names}")
        extras = []
        if fields.get("always_load"):
            extras.append("always loaded")
        if fields.get("uses_oauth"):
            extras.append("OAuth")
        if fields.get("uses_headers_helper"):
            extras.append("headers from external command")
        if extras:
            lines.append(f"      {', '.join(extras)}")
    elif kind == "settings":
        value = f" = {_safe(fields['value'])}" if fields.get("value") is not None else ""
        lines.append(f"  {_safe(fields.get('key'))}{value}")
    elif kind == "count":
        lines.append(f"  {_safe(_category_text(ident.split(':', 1)[1]))}: "
                     f"{fields.get('count')}")
    elif kind == "bin":
        note = "" if fields.get("executable") else "  (not executable)"
        lines.append(f"  {name}{note}{markers}")
        if fields.get("is_symlink"):
            lines.append(f"      Symlink auf {_safe(fields.get('link_target'))}")
    elif kind == "unused_file":
        lines.append(f"  {_safe(entry['source'])}")
    else:
        note = " (on request only)" if fields.get("disable_model_invocation") else ""
        lines.append(f"  {name}{note}{markers}")
        for line in fields.get("shell_lines", []):
            lines.append(f"      {_safe(line)}")
        # Tool permissions are exactly the kind of information this tool is
        # meant to surface, and reading them without showing them was the most
        # embarrassing mistake of the build -- twice. Agents declare theirs
        # under `tools:`, and a gain of Bash and Write was visible only as a
        # changed checksum.
        if fields.get("tools"):
            granted = ", ".join(_safe(tool) for tool in fields["tools"])
            lines.append(f"      Werkzeuge: {granted}")
        if fields.get("allowed_tools"):
            tools = ", ".join(_safe(tool) for tool in fields["allowed_tools"])
            lines.append(f"      may use: {tools}")
        if fields.get("disallowed_tools"):
            tools = ", ".join(_safe(tool) for tool in fields["disallowed_tools"])
            lines.append(f"      may not use: {tools}")
        if fields.get("model"):
            lines.append(f"      Modell: {_safe(fields['model'])}")
        if fields.get("context"):
            lines.append(f"      Kontext: {_safe(fields['context'])}")
        if fields.get("declares_hooks"):
            lines.append("      declares its own hooks")

    if entry["findings"]:
        texts = []
        for code in entry["findings"]:
            text = finding_text(code)
            # Without the value, 42 identical "name differs" lines teach the
            # reader to skip the word "Finding" -- in the very report where the
            # other findings matter.
            if code == "name-differs" and fields.get("frontmatter_name"):
                text += f": {_safe(fields['frontmatter_name'])}"
            texts.append(_safe(text))
        lines.append(f"      Finding: {', '.join(texts)}")
    return lines


def _finding_line(finding, sign):
    where = finding.get("path")
    if where == finding.get("category"):
        where = _category_text(where)
    line = f"{sign} Finding {_safe(finding_text(finding.get('code')))}: {_safe(where)}"
    detail = detail_text(finding)
    if detail:
        line += f" ({_safe(detail)})"
    return line


def _diff_lines(differences):
    identity = differences.get("identity") or {}
    matchers = differences.get("matchers") or {}
    commands = differences.get("commands") or {}
    findings = differences.get("findings") or {"added": [], "removed": []}
    categories = differences.get("categories") or {}
    # One predicate, not two. This decision and _has_changes said the same
    # thing in two places, and the next comparison dimension would have had to
    # be added to both -- the n-1 pattern that has hit this project four times.
    if not _has_changes(differences):
        return ["No changes since the last run."]

    # The IDs contain file and directory names from foreign plugins. A newline
    # or a \r inside one would tear apart the very line that reports a change.
    lines = ["Changes since the last run"]
    # The version first: without it "hook unchanged" reads like "nothing
    # happened", when in truth a whole new release was installed.
    for key, (before, now) in identity.items():
        if key == "raw_hash":
            # At identity level this is the manifest, not an entry.
            lines.append("~ Manifest: changed")
            continue
        lines.append(f"~ {_field_text(key)}  before: {_safe(before)}  "
                     f"now: {_safe(now)}")
    for ident in differences["changed"]:
        lines.append(f"~ {_safe(_ident_text(ident, matchers.get(ident)))}")
        for field, (before, now) in differences["changed"][ident].items():
            if field == "source_kind":
                before = SOURCE_KIND_TEXT.get(before, before)
                now = SOURCE_KIND_TEXT.get(now, now)
            elif field == "hook_type":
                before, now = _unknown_type_text(before), _unknown_type_text(now)
            label = _safe(_field_text(field))
            # A checksum against a checksum fills two lines and says one
            # thing: it moved. Nobody compares the digits by eye, and the
            # values are in --json for anyone who wants them. Half the diff
            # of a real update used to be these pairs.
            if field.endswith("_hash"):
                # Two different meanings of None, and one wording for both was
                # wrong for one of them. file_digest returns None when a file
                # cannot be hashed (too large, a FIFO, not regular) -- there
                # "entfallen" would be a false statement about a file that is
                # still there. tree_digest returns None when the tree is
                # EMPTY, and there "no longer checkable" is just as false.
                empty_means_gone = field in ("extras_hash",)
                if before is None:
                    lines.append(f"    {label}: "
                                 + ("neu" if empty_means_gone else "now checkable"))
                elif now is None:
                    lines.append(f"    {label}: "
                                 + ("entfallen" if empty_means_gone
                                    else "no longer checkable"))
                else:
                    lines.append(f"    {label}: changed")
                continue
            padding = " " * len(label)
            lines.append(f"    {label}  before: {_safe(before)}")
            lines.append(f"    {padding}  now:    {_safe(now)}")
    # Two hooks under the same event and matcher differ only by their
    # command, and that is precisely what the ID hides. Without it three
    # identical-looking lines appear and none of them says which hook is now
    # in place.
    # A category that moves between present, absent and not-evaluable says
    # something the entry list cannot: that the tool's knowledge about it
    # changed, not its content.
    for key in categories:
        before, now = categories[key]
        lines.append(f"~ Kategorie {_safe(_category_text(key))}  "
                     f"before: {CATEGORY_STATE_TEXT.get(before, before)}  "
                     f"now: {CATEGORY_STATE_TEXT.get(now, now)}")
    for finding in findings["added"]:
        lines.append(_finding_line(finding, "+"))
    for finding in findings["removed"]:
        lines.append(_finding_line(finding, "-"))
    for sign, key in (("+", "added"), ("-", "removed")):
        for ident in differences[key]:
            what = _safe(_ident_text(ident, matchers.get(ident)))
            if ident in commands:
                what += f"  {_safe(commands[ident])}"
            lines.append(f"{sign} {what}")
    return lines


def render_sweep(results, findings=None, unsaved=None):
    """Report of a run across every installed plugin.

    Deliberately NOT eight single reports stacked on top of each other. The
    first run of a memory has nothing to say about any of them, and printing
    "this is the first run" eighteen times buries the one sentence that
    matters: from now on there is something to compare against.
    """
    lines = []
    # A state that exists but cannot be used is NOT a first run. render()
    # refuses to call it one for exactly this reason; the sweep used to do it
    # anyway, and "das ist ab jetzt dein Vergleichsstand" was then a lie about
    # a plugin whose state had just been rejected.
    fresh = [r for r in results if r["differences"] is None and not r.get("note")]
    blocked = [r for r in results if r["differences"] is None and r.get("note")]
    moved = [r for r in results if r["differences"] is not None
             and _has_changes(r["differences"])]
    still = [r for r in results if r["differences"] is not None
             and not _has_changes(r["differences"])]

    count = len(results)
    word = "Plugin" if count == 1 else "Plugins"
    # "nichts gespeichert" only when nothing was. One failure out of eighteen
    # used to produce that sentence although seventeen states were written,
    # and it named unsaved[0] as if that were the whole story. The failures
    # get their own lines below, so they can no longer be swallowed by the
    # blocked branch either -- which is the separation the single run makes.
    nothing_saved = bool(unsaved) and len(unsaved) >= count
    if blocked and not moved and not still and not fresh:
        lines.append(f"Read {count} {word}, "
                     f"{'but it' if count == 1 else 'but none of them'} could be "
                     f"compared.")
    elif nothing_saved:
        # Saying "this is your baseline from now on" while nothing was
        # written is the same false reassurance the tool exists against.
        lines.append(f"Read {count} {word}, but saved nothing. "
                     f"There is no baseline to compare against.")
    elif fresh and not moved and not still:
        lines.append(f"Recorded {count} {word}. That is your baseline "
                     f"from now on.")
    else:
        lines.append(f"Checked {count} {word}.")
    lines.append("")

    if moved:
        lines.append("Changed")
        for result in moved:
            lines.append(f"  {_safe(result['key'])}{_state_note(result)}")
            # The single run warns when a state was written for a different
            # directory; the sweep, the mode the shared keys exist for, did
            # not. Without it "changed" can be a statement about the other
            # directory, and the key is an opaque hash that does not say which.
            previous_path = result.get("previous_path")
            if previous_path and previous_path != result.get("path"):
                lines.append(f"    Note: this baseline came from a different "
                             f"directory ({_safe(previous_path)})")
            for line in _diff_lines(result["differences"]):
                # Only the headline is dropped; "No changes" cannot
                # occur here because moved is prefiltered by _has_changes,
                # and "Verglichen mit" is produced by render(), not here.
                if line.startswith("Changes since"):
                    continue
                lines.append("    " + line)
        lines.append("")

    if blocked:
        lines.append("No comparison possible")
        for result in blocked:
            lines.append(f"  {_safe(result['key'])}{_state_note(result)}"
                         f"  ({_safe(result['note'])})")
        lines.append("")

    if fresh:
        # Always list them, also on the very first run. Not printing "this is
        # the first run" per plugin was the point; withholding WHICH ones
        # were recorded is a different thing, and it is the moment where
        # someone spots a plugin they had forgotten about.
        lines.append("Newly recorded")
        for result in fresh:
            lines.append(f"  {_safe(result['key'])}{_state_note(result)}")
        lines.append("")

    if still:
        number = len(still)
        lines.append(f"{number} unchanged.")

    off = [r for r in results if not r["enabled"]]
    if off:
        # A disabled plugin does not run, but it keeps updating, and whoever
        # switches it back on switches on the newer version, not the one they
        # once looked at.
        # Name them only when they are not already listed above. A plugin
        # that appears under "Changed" or "Newly recorded" carries the
        # marker there; repeating it here would be noise.
        listed = ({r["key"] for r in moved} | {r["key"] for r in fresh}
                  | {r["key"] for r in blocked})
        unnamed = [r for r in off if r["key"] not in listed]
        # The count has to match the list beside it, not some other set. It
        # used to say "3 currently disabled" next to two names, and "Of those"
        # referred to the sentence "N unchanged" one line up, where only
        # some of them were.
        shown = unnamed or off
        one = len(shown) == 1
        tail = (" It does not run, but it still updates itself." if one
                else " They do not run, but they still update themselves.")
        if unnamed:
            lines.extend(_wrapped(
                "", [f"{'One plugin is' if one else f'{len(shown)} plugins are'} "
                     f"currently disabled: "
                     + ", ".join(_safe(r["key"]) for r in shown) + "." + tail]))
        else:
            lines.extend(_wrapped(
                "", [f"{'One of them is' if one else f'{len(shown)} of them are'} "
                     f"disabled." + tail]))

    # Every failed save, on its own line and by name. Naming only the first
    # one hid the other sixteen, and when the headline went to the blocked
    # branch instead, the failure did not reach stdout at all -- the state
    # silently did not move on while the report said the run was fine.
    if unsaved and not nothing_saved:
        for problem in unsaved:
            lines.extend(_wrapped("", [f"Nicht gespeichert: {_safe(problem)}"]))
    elif unsaved:
        for problem in unsaved:
            lines.extend(_wrapped("", [f"  {_safe(problem)}"]))

    for finding in findings or []:
        lines.extend(_wrapped("", [_finding_line(finding, "").lstrip()]))
    return "\n".join(lines)


def _has_changes(differences):
    """Is there anything to report? The single predicate for both reports."""
    findings = differences.get("findings") or {"added": [], "removed": []}
    return bool(differences["added"] or differences["removed"]
                or differences["changed"] or differences.get("identity")
                or findings["added"] or findings["removed"]
                or differences.get("categories"))


def _state_note(result):
    parts = []
    if result.get("scope") and result["scope"] != "user":
        parts.append(_safe(SCOPE_TEXT.get(result["scope"], result["scope"])))
    if not result["enabled"]:
        parts.append("disabled")
    return f"  [{', '.join(parts)}]" if parts else ""


def render(inventory, differences, note=None, since=None):
    """Build the complete plain text report.

    `differences` is None when there is no previous state to compare against.
    """
    entries = inventory.get("entries", {})
    counts = {}
    for entry in entries.values():
        # A count entry stands for however many files it counted. Adding one
        # per entry made the header say "1 Weiteres" above a line reading
        # "Themes: 2".
        weight = (entry["fields"].get("count", 1)
                  if entry["kind"] == "count" else 1)
        counts[entry["kind"]] = counts.get(entry["kind"], 0) + weight

    lines = [_header_line(inventory.get("identity", {}), counts), ""]

    for kind, plural, _singular in SECTIONS:
        matching = {i: e for i, e in entries.items() if e["kind"] == kind}
        if not matching:
            continue
        lines.append(HEADINGS.get(kind, plural))
        for ident in sorted(matching):
            lines.extend(_entry_lines(kind, ident, matching[ident]))
        lines.append("")

    hook_count = counts.get("hook")
    if hook_count:
        word = "Hook" if hook_count == 1 else "Hooks"
        script = "the script it calls was" if hook_count == 1 \
            else "the scripts they call were"
        lines.append(f"{hook_count} {word}; {script} not read.")
    if inventory.get("checked_absent"):
        lines.extend(_wrapped("Checked and not present: ",
                              sorted((_safe(_category_text(k))
                                      for k in inventory["checked_absent"]),
                                     key=str.lower)))
    if inventory.get("unreadable"):
        # A third class next to "found" and "absent": something is there that
        # could not be evaluated. Without this line the tool would claim
        # absence where it knows nothing.
        lines.extend(_wrapped("Present, but could not be evaluated: ",
                              sorted((_safe(_category_text(k))
                                      for k in inventory["unreadable"]),
                                     key=str.lower)))
    # A marker nobody can read is worse than no marker: the reader cannot
    # tell whether it means anything. The legend states the fact behind it and
    # stops there -- the tool does not judge.
    shown = sorted({marker for entry in entries.values()
                    for marker in entry.get("markers", [])})
    for marker in shown:
        if marker in MARKER_LEGEND:
            lines.extend(_wrapped("", [MARKER_LEGEND[marker]]))

    for finding in inventory.get("findings", []):
        # For findings about the manifest declaration itself there is no file
        # to point at, so `path` carries the category key. Printing it raw put
        # `mcpServers` into a report that says `MCP-Server` everywhere else.
        lines.extend(_wrapped("", [_finding_line(finding, "").lstrip()]))

    lines.append("")
    if differences is None:
        # The note carries the reason when there IS a stored state that could
        # not be used. Claiming "first run" over it would be a lie, and the
        # next save overwrites it for good.
        lines.append(_safe(note) if note
                     else "No baseline yet, this is the first run.")
    else:
        if since:
            read_at, key, previous_path, current_path = since
            lines.append(f"Compared against the baseline from {_safe(read_at)} "
                         f"(key: {_safe(key)})")
            # Two directories can legitimately share a key -- that is what
            # makes a comparison survive an update. But then "no changes"
            # would be a statement about the OTHER directory, and the key
            # alone is an opaque hash that says nothing about which.
            if previous_path and current_path and previous_path != current_path:
                lines.append(f"Note: this baseline came from a different "
                             f"directory ({_safe(previous_path)})")
        lines.extend(_diff_lines(differences))

    return "\n".join(lines)


CATEGORY_STATE_TEXT = {
    "present": "present",
    "absent": "not present",
    "unreadable": "present, but could not be evaluated",
}

CATEGORY_TEXT = {
    "hooks": "Hooks", "settings": "Settings", "mcpServers": "MCP servers",
    "monitors": "Monitors", "bin": "bin/", "commands": "Commands",
    "skills": "Skills", "agents": "Agents", "lsp": "LSP servers",
    "outputStyles": "Output styles", "workflows": "Workflows", "themes": "Themes",
}


# The plural of a section doubles as a heading and as a counter inside a
# running line. For every kind but one the same word works in both places.
HEADINGS = {"unused_file": "Files not loaded"}

SOURCE_KIND_TEXT = {"convention": "convention", "manifest": "manifest"}

# Scopes are a closed English vocabulary like source_kind, and they show up in
# the sweep report beside every non-user installation.
SCOPE_TEXT = {"user": "personal", "project": "project", "local": "local only",
              "managed": "managed"}

# The detail of a finding used to be a German sentence built in collect.py.
# That put German into the stored state, which is a data format and not a
# report: a reworded sentence would have rewritten every saved state, and a
# second report language could not have rendered an old one. The detail is
# now an English code plus at most one argument, and the wording lives here
# with the rest of the wording.
DETAIL_TEXT = {
    "points-outside": "zeigt aus dem Plugin heraus",
    "convention-path-points-outside": "Konventionspfad zeigt aus dem Plugin heraus",
    "collides-with": "collides with {}",
    "present-but-unreachable": "present, but not reachable",
    "object-instead-of-path": "als Objekt statt als Pfad deklariert",
    "list-entry-not-a-path": "list entry is not a path",
    "in-category": "category {}",
    "displaced-by-declaration": "displaced by the manifest entry",
    "hooks-not-an-object": "hooks is not an object",
    "event-not-a-list": "{} is not a list",
    "group-not-an-object": "{}: group is not an object",
    "hook-not-an-object": "{}: hook is not an object",
    "hook-type-in-event": "{}",
    "mcp-not-an-object": "mcpServers is not an object",
    "server-not-an-object": "{} is not an object",
    "settings-not-an-object": "settings is not an object",
    "enabled-state-unknown": "active state could not be determined",
    "entry-cap": "from entry {} on",
}


def detail_text(finding):
    """Render a finding's detail, or "" when it has none."""
    code = finding.get("detail")
    if not code:
        return ""
    template = DETAIL_TEXT.get(code)
    if template is None:
        # An unknown code is better shown raw than swallowed -- a swallowed
        # detail is exactly the kind of silence this tool exists against.
        return code
    argument = finding.get("detail_arg")
    if "{}" in template:
        # A category key gets the same name it has everywhere else.
        if code == "in-category":
            argument = _category_text(argument)
        return template.format("" if argument is None else argument)
    return template


def _unknown_type_text(value):
    """Spell out an unknown hook type the same way the inventory does.

    The inventory already says "unbekannter Typ: websocket". Leaving the raw
    `unknown:websocket` in the diff gives the same thing two names, one of
    them English.
    """
    if isinstance(value, str) and value.startswith("unknown:"):
        return "unbekannter Typ: " + value.split(":", 1)[1]
    return value


def _wrapped(prefix, items, width=88):
    """Wrap a collected line, continuation lines aligned under the first item.

    The longest line of every report was the one listing what is absent --
    141 columns, wrapped by the terminal at an arbitrary place.

    Deliberately without break_long_words: a single path that is longer than
    the width stays on one line and overflows. Splitting it would make it
    unreadable and uncopyable, and a path is exactly the value a reader wants
    to take away from the report. The terminal wraps it either way; the point
    of this function is that a LIST does not get torn at an arbitrary comma.
    """
    import textwrap
    return textwrap.wrap(prefix + ", ".join(items), width=width,
                         subsequent_indent=" " * len(prefix),
                         break_long_words=False, break_on_hyphens=False) or [prefix]


def _category_text(key):
    return CATEGORY_TEXT.get(key, key)
