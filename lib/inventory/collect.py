"""Path resolution and per-category collectors."""
import errno
import hashlib
import json
import os
import re

from .frontmatter import body_of, frontmatter_text_of, read_frontmatter
from .reading import (clean_name, file_digest, read_json, read_safely,
                      tree_digest)

COMPONENTS = {
    "hooks": "hooks/hooks.json",
    "settings": "settings.json",
    "mcpServers": ".mcp.json",
    "monitors": "monitors/monitors.json",
    "bin": "bin",
    "commands": "commands",
    "skills": "skills",
    "agents": "agents",
    "lsp": ".lsp.json",
    "outputStyles": "output-styles",
    "workflows": "workflows",
    "themes": "themes",
}

# Per the docs only "skills" adds to the default path; commands, agents,
# workflows, outputStyles and themes REPLACE it. hooks and mcpServers have
# their own merge rules and are treated as additive here: a hook reported
# twice is harmless, a missed one is not.
ADDITIVE = frozenset({"skills", "hooks", "mcpServers"})

MAX_WALK_DEPTH = 8

RELOAD_MARKERS = ("curl", "wget", "npx", "uvx", "pip install")
_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
# A slash not preceded by a word, dot, tilde or slash character and followed by
# a letter. Catches " /usr", ":-/Applications" (bash default expansion) and
# "=/etc" alike.
# POSIX treats //etc/passwd and ///etc/passwd as /etc/passwd. Excluding a
# preceding "/" made both invisible.
# The character before the slash run must not itself be a slash. Without that
# exclusion `/+` can start inside a run of slashes, retry every length at every
# position, and turn into quadratic backtracking: 32.000 slashes took 21 s, and
# hooks.json may be a megabyte -- about seven hours for one plugin. It is also
# the more accurate reading, because `a//b` is not an absolute path.
_ABSOLUTE_PATTERN = re.compile(r"(^|[^\w.~/])/+[A-Za-z]")
# ~/ and $HOME/ point out of the plugin just as clearly as an absolute path,
# and in hook commands they are the more common spelling.
_HOME_PATTERN = re.compile(r"(^|[\s\"'=:(])(~|\$HOME|\$\{HOME\})/")
_SHELL_PATTERN = re.compile(r"!`([^`]*)`")

# Five hook types, not one. The first version only knew "command" and dropped
# the other four as "unknown" -- among them "http", which posts data to a
# foreign URL and is therefore exactly the category this tool exists to
# surface.
HOOK_TYPES = ("command", "http", "mcp_tool", "prompt", "agent")

COUNT_ONLY_CATEGORIES = ("lsp", "outputStyles", "workflows", "themes", "monitors")

# Entry kind -> component category, used for "checked and absent".
KIND_TO_CATEGORY = {
    "hook": "hooks", "mcp": "mcpServers", "settings": "settings",
    "skill": "skills", "command": "commands", "agent": "agents", "bin": "bin",
}


# --------------------------------------------------------------- helpers ----

def _short_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _raw_hash(obj):
    """Catch-all hash over the unfiltered source object.

    The tool stores a SELECTION of fields. Everywhere that selection stood
    alone, a change outside it was invisible while the report said "no
    changes" -- an all-clear the tool had no basis for. A hook's `env` and
    `cwd`, an MCP server's `env` VALUES and header values, the query part of
    a URL: all of them execute or authenticate, and none of them belong in
    the report.

    So they are hashed instead of printed. Nothing foreign is shown, and the
    diff still names the entry when something moved. sort_keys because JSON
    key order is not meaningful; default=str because a foreign file may hold
    something json cannot serialise, and a crash there would be worse than a
    coarse hash.
    """
    return "sha256:" + _short_hash(
        json.dumps(obj, sort_keys=True, ensure_ascii=True, default=str))


def as_list(value):
    """Return value if it is a list, otherwise an empty one.

    `value or []` only catches falsy values, not wrong types. A number instead
    of a list used to raise a TypeError in the middle of a run.
    """
    return value if isinstance(value, list) else []


def as_dict(value):
    return value if isinstance(value, dict) else {}


def stays_inside(root, candidate):
    """Check that candidate really lives below root.

    BOTH sides go through realpath. The first version normalised the candidate
    with normpath only, which made a symlinked directory in the path look
    harmless: `disguise/settings.json` formally sits below the root while
    `disguise` actually points elsewhere. A prepared plugin could use that to
    make the tool read foreign files and print their contents.

    commonpath raises ValueError on mixed relative/absolute paths, hence the
    guard.
    """
    real_root = os.path.realpath(root)
    real_candidate = os.path.realpath(os.path.join(real_root, candidate))
    try:
        return os.path.commonpath([real_root, real_candidate]) == real_root
    except ValueError:
        return False


def _list_dir(root, rel, findings, category=None):
    """List a directory, with symlink and permission checks."""
    if not stays_inside(root, rel):
        findings.append({"code": "symlink-outside", "path": rel,
                         "category": category,
                         "detail": "points-outside"})
        return []
    try:
        return sorted(os.listdir(os.path.join(root, rel)))
    except NotADirectoryError:
        # A regular, world-readable file declared as a directory is not a
        # permission problem, and saying so sends the reader looking in the
        # wrong place.
        findings.append({"code": "unexpected-type", "path": rel,
                         "category": category, "detail": ""})
        return []
    except OSError:
        findings.append({"code": "no-read-permission", "path": rel,
                         "category": category, "detail": ""})
        return []


def markers_for(command):
    """Mark reloading and leaving the plugin directory.

    This is not a judgement: it says what is written there, not whether it is
    bad. The difference to a traffic light is verifiability, it is literally in
    the file. ${CLAUDE_PLUGIN_ROOT} does not count as leaving, it points into
    the plugin.
    """
    markers = []
    # Replace with a placeholder WITHOUT a slash instead of deleting:
    # "${CLAUDE_PLUGIN_ROOT}/hooks/x.sh" would otherwise become "/hooks/x.sh",
    # which looks like an absolute path.
    text = command.replace("${CLAUDE_PLUGIN_ROOT}", "PLUGINROOT")

    for word in RELOAD_MARKERS:
        # Not excluding "/" and "." before the word: /usr/bin/curl and
        # ./bin/curl are the same program, and both stayed unmarked.
        if re.search(r"(?<![\w-])" + re.escape(word) + r"(?![\w.-])", text):
            markers.append("reloads")
            break

    # Neutralise URLs first, otherwise the second slash in "https://host/path"
    # counts as an absolute path. Token-wise instead of by regex: the pattern
    # \w+://\S* backtracks quadratically, and a 200 kB command took minutes.
    without_urls = " ".join(
        "URL" if "://" in token else token for token in text.split())
    if (".." in without_urls
            or _ABSOLUTE_PATTERN.search(without_urls)
            or _HOME_PATTERN.search(without_urls)):
        markers.append("leaves-plugin")
    return sorted(set(markers))


def _url_without_secret(url):
    """Strip query string and user:pass from a URL.

    Masking header values while printing a token in the query string right
    next to it would create a false sense of safety.
    """
    if not isinstance(url, str):
        # Do not hand the raw object back: the report str()s it, and a dict or
        # list carrying the URL slipped past the masking entirely.
        return _mask_secrets(url) if url is not None else url
    # Fragment and path parameters carry tokens just as often as the query
    # string does, and user:pass appears with and without a scheme.
    had_query = "?" in url
    had_fragment = "#" in url
    # Mask the authority FIRST. Cutting query and fragment before that let a
    # "#" or "?" inside the password leave the username and the beginning of
    # the secret in plain sight. rsplit, because "@" may occur in the password.
    scheme, sep, after = url.partition("://")
    if sep:
        authority, slash, tail = after.partition("/")
        if "@" in authority:
            authority = "[…]@" + authority.rsplit("@", 1)[1]
        rest = f"{scheme}://{authority}{slash}{tail}"
    else:
        rest = url
        if "@" in rest.split("/", 1)[0]:
            head, _, tail = rest.partition("/")
            rest = "[…]@" + head.rsplit("@", 1)[1] + ("/" + tail if tail else "")
    rest = rest.split("?", 1)[0].split("#", 1)[0]
    if ";" in rest:
        rest = rest.split(";", 1)[0] + ";[…]"
    return rest + ("?[…]" if had_query else "") + ("#[…]" if had_fragment else "")


def _variables(obj):
    """Collect ${VAR} and $VAR recursively from all strings."""
    found = set()
    if isinstance(obj, str):
        for braced, bare in _VAR_PATTERN.findall(obj):
            found.add(braced or bare)
    elif isinstance(obj, dict):
        for value in obj.values():
            found |= _variables(value)
    elif isinstance(obj, list):
        for value in obj:
            found |= _variables(value)
    return found


def _entry(kind, source, source_kind, fields, markers=None, findings=None):
    return {"kind": kind, "source": source, "source_kind": source_kind,
            "fields": fields, "markers": markers or [], "findings": findings or []}


def _put(entries, ident, entry, findings):
    """Store an entry, but never overwrite a different one silently.

    Two components can end up with the same ID: a NFC and a NFD spelling of
    the same name, or two additive base directories carrying the same file
    name. Overwriting would drop a real component from the report while the
    counter still claims one -- the exact failure this tool exists to prevent.
    """
    if ident in entries:
        findings.append({"code": "duplicate-id", "path": entry["source"],
                         "detail": "collides-with", "detail_arg": entries[ident]["source"]})
        suffix = 2
        while f"{ident}#{suffix}" in entries:
            suffix += 1
        ident = f"{ident}#{suffix}"
    entries[ident] = entry
    return ident


def _without_nested(bases):
    """Drop every base that lies inside another base of the same category."""
    kept = []
    for base in sorted(bases):
        if not any(base != other and base.startswith(other + "/")
                   for other in bases):
            kept.append(base)
    return kept


def _source_kind(path, default_path):
    return "convention" if path == default_path else "manifest"


# ------------------------------------------------------- path resolution ----

def _declaration(manifest, category):
    """Get the manifest entry for a category, including experimental."""
    if category == "themes" and "themes" not in manifest:
        return as_dict(manifest.get("experimental")).get("themes")
    return manifest.get(category)


def _root_skill_skip(paths):
    """What the root skill's catch-all hash must leave out: everything with
    an entry of its own.

    Built from the paths that were actually resolved, and from nothing else.
    It used to be the KEYS of COMPONENTS, four of which are not what the file
    is called on disk -- `settings` is `settings.json`, `mcpServers` is
    `.mcp.json` -- so editing settings.json moved this hash as well as its own
    entry: one change, two lines. The resolved paths also cover a declared
    path and a `Bin/` that a case-insensitive filesystem hands back.

    Only the resolved paths, not those plus the conventional names: a name
    that is not in `paths` is either absent or was rejected, and a rejected
    one has no entry, so it belongs in the hash. Listing both would be two
    mechanisms for one rule, and then neither is tested.
    """
    skip = {"SKILL.md", ".claude-plugin"}
    for found in paths.values():
        for relative in found:
            skip.add(relative.split("/")[0])
    return tuple(sorted(skip))


def resolve_paths(root, manifest):
    """Determine which paths to read per category.

    Returns ({category: [relative paths]}, [findings]). The paths are relative
    to the root and are guaranteed to exist.

    Reading only the conventional paths would report "no hooks" for a plugin
    that declares a hook path. That would not be incomplete, it would be
    misleading.
    """
    paths = {}
    findings = []

    for category, default_path in COMPONENTS.items():
        found = []
        # Check the CONVENTIONAL path too. The first version only did this for
        # declared paths, so a symlinked hooks/ still led out of the plugin
        # without anything noticing.
        full_default = os.path.join(root, default_path)
        reachable = os.path.exists(full_default)
        if not reachable:
            try:
                os.lstat(full_default)
                present = True
            except OSError as exc:
                # EACCES on the parent means we cannot even tell whether it is
                # there. Claiming absence would be a guess.
                present = exc.errno in (errno.EACCES, errno.EPERM)
            if present:
                # The entry is there but cannot be reached: a dangling link,
                # or a directory above it without search permission. Without
                # this the category silently became "checked and absent".
                findings.append({"code": "no-read-permission",
                                 "path": default_path, "category": category,
                                 "detail": "present-but-unreachable"})
        if reachable:
            if stays_inside(root, default_path):
                found.append(default_path)
            else:
                findings.append({"code": "symlink-outside", "path": default_path,
                                 "category": category,
                                 "detail": "convention-path-points-outside"})

        declared = _declaration(manifest, category)

        if isinstance(declared, dict):
            findings.append({"code": "declared-inline", "path": category,
                             "category": category,
                             "detail": "object-instead-of-path"})
            declared = None

        if declared is not None:
            candidates = declared if isinstance(declared, list) else [declared]
            resolved = []
            for raw in candidates:
                if not isinstance(raw, str):
                    findings.append({"code": "declared-inline", "path": category,
                                     "category": category,
                                     "detail": "list-entry-not-a-path"})
                    continue
                if os.path.isabs(raw):
                    findings.append({"code": "absolute-path", "path": raw,
                                     "category": category,
                                     "detail": "in-category", "detail_arg": category})
                    continue
                if not stays_inside(root, raw):
                    findings.append({"code": "path-leaves-plugin", "path": raw,
                                     "category": category,
                                     "detail": "in-category", "detail_arg": category})
                    continue
                # relpath on the RESOLVED path. normpath strips ".."
                # textually while realpath resolves symlinks first -- the two
                # can diverge, and together they allowed an escape.
                rel = os.path.relpath(
                    os.path.realpath(os.path.join(root, raw)),
                    os.path.realpath(root))
                if not os.path.exists(os.path.join(root, rel)):
                    findings.append({"code": "declared-path-missing", "path": rel,
                                     "category": category,
                                     "detail": "in-category", "detail_arg": category})
                    continue
                resolved.append(rel)

            if resolved:
                if category in ADDITIVE:
                    found = list(set(found) | set(resolved))
                else:
                    # Only replace when something valid is actually there.
                    # Otherwise a broken declaration would delete the existing
                    # conventional path from the report.
                    displaced = set(found) - set(resolved)
                    for path in sorted(displaced):
                        # Say it. Two words in a manifest were enough to make a
                        # full commands/ directory disappear from the report
                        # while it still claimed "checked and absent".
                        findings.append({"code": "displaced-by-manifest",
                                         "path": path, "category": category,
                                         "detail": "displaced-by-declaration"})
                    found = list(set(resolved))

        # Two bases of the same category where one sits inside the other
        # would walk the same file twice, under two different IDs. The report
        # then counted it twice, and removing the inner declaration later
        # looked like a component had disappeared. The outer base already
        # covers everything the inner one would find.
        paths[category] = sorted(_without_nested(set(found)))

    return paths, findings


# ------------------------------------------------------------ collectors ----

def _hook_command(hook_type, hook, mask=True):
    """The single line that says what the hook does. Differs per type.

    `mask=False` gives the same line unmasked, for marker detection. The
    markers used to be read off the masked line, and masking a key called
    `connectionScript` turned `curl … | sh` into `[…]` -- so the one hook
    shape nobody understands, an unknown type, was also the one that reported
    no markers at all.
    """
    if hook_type == "command":
        args = " ".join(str(arg) for arg in as_list(hook.get("args")))
        return (str(hook.get("command", "")) + (" " + args if args else "")).strip()
    if hook_type == "http":
        return str(_url_without_secret(hook.get("url", "")) or "")
    if hook_type == "mcp_tool":
        return f"{hook.get('server', '?')}/{hook.get('tool', '?')}"
    if hook_type in ("prompt", "agent"):
        return str(hook.get("prompt", ""))
    # Unknown type: show whatever field carries the payload, so the command
    # never disappears from the report. The url still goes through the same
    # masking as everywhere else -- a single capital letter in the type must
    # not turn into a token leak.
    for key in ("command", "url", "prompt", "tool", "script"):
        if hook.get(key):
            value = _url_without_secret(hook[key]) if key == "url" else hook[key]
            args = " ".join(str(arg) for arg in as_list(hook.get("args")))
            return (str(value) + (" " + args if args else "")).strip()
    # No known payload key: show the whole object rather than an empty line.
    # An unknown field name must not make the hook invisible.
    rest = {key: value for key, value in hook.items() if key != "type"}
    if rest:
        # Not repr(): that puts Python quoting into a German report, which is
        # exactly what the report's _readable() prevents everywhere else.
        shown = _mask_secrets(rest) if mask else rest
        return ", ".join(f"{key}: {shown[key]}" for key in sorted(shown))
    return ""


def _hook_fields(event, matcher, hook_type, command, hook):
    fields = {
        "event": event,
        "matcher": matcher,
        "hook_type": hook_type,
        "command": command,
        "timeout": hook.get("timeout"),
        "condition": hook.get("if"),
        "status_message": hook.get("statusMessage"),
        "run_once": bool(hook.get("once", False)),
    }
    if hook_type == "command":
        fields["args"] = [str(arg) for arg in as_list(hook.get("args"))]
        fields["shell"] = hook.get("shell")
        fields["run_async"] = bool(hook.get("async", False))
        fields["async_rewake"] = bool(hook.get("asyncRewake", False))
    elif hook_type == "http":
        # Never print header VALUES, they carry tokens.
        fields["header_names"] = sorted(as_dict(hook.get("headers")).keys())
        fields["allowed_env"] = sorted(
            str(name) for name in as_list(hook.get("allowedEnvVars")))
    elif hook_type == "mcp_tool":
        fields["server"] = hook.get("server")
        fields["tool"] = hook.get("tool")
    elif hook_type in ("prompt", "agent"):
        fields["model"] = hook.get("model")
    # Everything the selection above does not cover -- cwd, env, body,
    # headers, and any field a future release adds.
    fields["raw_hash"] = _raw_hash(hook)
    return fields


def collect_hooks(root, paths):
    """Read hooks.

    ID rule, two-stage. When a combination of event and matcher has exactly ONE
    hook (the normal case), the ID is `hook:<event>:m<matcher>:0` and does NOT
    contain the command. Only that way a command change stays a change instead
    of becoming a pair of removed and added.

    With several hooks a discriminator is needed. The position is unsuitable
    because an inserted hook would shift all following ones, so the command
    hash is used. The price: in that constellation a command change shows up as
    a pair. That is the deliberate trade-off, and it beats the first version in
    which one of the two hooks simply vanished.
    """
    entries, findings = {}, []
    raw_hooks = []

    for rel in paths.get("hooks", []):
        data, finding = read_json(os.path.join(root, rel))
        if finding:
            findings.append({"code": finding, "path": rel, "category": "hooks",
                             "detail": ""})
            continue
        groups = data.get("hooks") if isinstance(data, dict) else None
        if not isinstance(groups, dict):
            findings.append({"code": "invalid-json", "path": rel,
                             "category": "hooks",
                             "detail": "hooks-not-an-object"})
            continue
        for event in sorted(groups):
            group_list = groups[event]
            if not isinstance(group_list, list):
                findings.append({"code": "unexpected-type", "path": rel,
                                 "category": "hooks",
                                 "detail": "event-not-a-list", "detail_arg": event})
                continue
            for group in group_list:
                if not isinstance(group, dict):
                    findings.append({"code": "unexpected-type", "path": rel,
                                     "category": "hooks",
                                     "detail": "group-not-an-object", "detail_arg": event})
                    continue
                matcher = str(group.get("matcher", ""))
                for hook in as_list(group.get("hooks")):
                    if not isinstance(hook, dict):
                        findings.append({"code": "unexpected-type", "path": rel,
                                         "category": "hooks",
                                         "detail": "hook-not-an-object", "detail_arg": event})
                        continue
                    hook_type = hook.get("type")
                    if isinstance(hook_type, str):
                        hook_type = hook_type.strip().lower()
                    if hook_type not in HOOK_TYPES:
                        # Still record it. Dropping the entry would hide a hook
                        # that Claude Code may well execute, and a new hook type
                        # in a future version would tear the same hole open. A
                        # tool that promises visibility must not go silent when
                        # it does not recognise something.
                        findings.append({"code": "unknown-hook-type", "path": rel,
                                         "category": "hooks",
                                         "detail": "hook-type-in-event", "detail_arg": f"{event}: {hook_type}"})
                        hook_type = f"unknown:{hook_type}"
                    command = _hook_command(hook_type, hook)
                    raw_hooks.append((f"hook:{event}:m{_short_hash(matcher)}", rel,
                                      event, matcher, hook_type, command, hook))

    frequency = {}
    for base, *_ in raw_hooks:
        frequency[base] = frequency.get(base, 0) + 1

    taken = set()
    for base, rel, event, matcher, hook_type, command, hook in raw_hooks:
        if frequency[base] == 1:
            ident = f"{base}:0"
        else:
            ident = f"{base}:k{_short_hash(command)[:8]}"
            counter = 0
            while ident in taken:  # identical commands, real duplicates
                counter += 1
                ident = f"{base}:k{_short_hash(command)[:8]}-{counter}"
        taken.add(ident)
        entries[ident] = _entry(
            "hook", rel, _source_kind(rel, COMPONENTS["hooks"]),
            _hook_fields(event, matcher, hook_type, command, hook),
            markers=markers_for(_hook_command(hook_type, hook, mask=False)))

    if os.path.isdir(os.path.join(root, "hooks")):
        for name in _list_dir(root, "hooks", findings, "hooks"):
            rel = f"hooks/{name}"
            # Every file, not only *.json. The one worth seeing most is a
            # `hooks.json.bak` beside the real one: it looks like a hook
            # configuration, it is not loaded, and it is the copy someone
            # left behind. Restricting this to .json hid exactly that.
            if rel not in paths.get("hooks", []) and not os.path.isdir(
                    os.path.join(root, rel)):
                entries[f"file:{rel}"] = _entry(
                    "unused_file", rel, "convention", {},
                    findings=["present-but-not-loaded"])
    return entries, findings


def collect_mcp(root, paths):
    """Read MCP servers. Header VALUES are never printed, only the names."""
    entries, findings = {}, []
    for rel in paths.get("mcpServers", []):
        data, finding = read_json(os.path.join(root, rel))
        if finding:
            findings.append({"code": finding, "path": rel,
                             "category": "mcpServers", "detail": ""})
            continue
        # as_dict turns anything that is not a dict into {}, and the check
        # below then finds a perfectly good empty dict. A .mcp.json holding a
        # JSON list produced no finding at all -- and the report went on to
        # claim "checked and absent: MCP-Server" with the file lying there.
        if not isinstance(data, dict):
            findings.append({"code": "invalid-json", "path": rel,
                             "category": "mcpServers",
                             "detail": "mcp-not-an-object"})
            continue
        servers = data.get("mcpServers", data)
        if not isinstance(servers, dict):
            findings.append({"code": "invalid-json", "path": rel,
                             "category": "mcpServers",
                             "detail": "mcp-not-an-object"})
            continue
        for name in sorted(servers):
            config = servers[name]
            if not isinstance(config, dict):
                findings.append({"code": "unexpected-type", "path": rel,
                                 "category": "mcpServers",
                                 "detail": "server-not-an-object", "detail_arg": name})
                continue
            url = config.get("url")
            transport = config.get("type") or ("http" if url else "stdio")
            if transport == "streamable-http":
                transport = "http"
            command = config.get("command")
            fields = {
                "transport": transport,
                "command": "[…]" if _looks_secret(command) else command,
                # args carry tokens just as often as the url does.
                "args": [str(_mask_secrets(arg)) for arg in as_list(config.get("args"))],
                "url": _url_without_secret(url),
                "header_names": sorted(as_dict(config.get("headers")).keys()),
                "env_variables": sorted(
                    _variables(config) | set(as_dict(config.get("env")).keys())),
                "always_load": bool(config.get("alwaysLoad", False)),
                "uses_oauth": bool(config.get("oauth")),
                "uses_headers_helper": bool(config.get("headersHelper")),
                "timeout": config.get("timeout"),
                # env VALUES, header values, cwd and the query part of the url
                # are deliberately not printed -- but they decide what runs
                # and with which credentials, so they have to be compared.
                "raw_hash": _raw_hash(config),
            }
            line = " ".join([str(command or "")] + fields["args"])
            _put(entries, f"mcp:{name}", _entry(
                "mcp", rel, _source_kind(rel, COMPONENTS["mcpServers"]), fields,
                markers=markers_for(line)), findings)
    return entries, findings


def collect_settings(root, paths):
    """Report every top-level key. Full value only for `agent`.

    Reporting every key is deliberate: otherwise the next key that gets
    introduced would be invisible, and that is exactly the class of mistake
    this tool exists to avoid.
    """
    entries, findings = {}, []
    for rel in paths.get("settings", []):
        data, finding = read_json(os.path.join(root, rel))
        if finding:
            findings.append({"code": finding, "path": rel,
                             "category": "settings", "detail": ""})
            continue
        if not isinstance(data, dict):
            findings.append({"code": "invalid-json", "path": rel,
                             "category": "settings",
                             "detail": "settings-not-an-object"})
            continue
        for key in sorted(data):
            # agent is worth showing because it can replace the session's main
            # agent. But masking header values everywhere and then dumping the
            # one key that holds model and API configuration was inconsistent.
            value = _mask_secrets(data[key]) if key == "agent" else None
            _put(entries, f"settings:{key}", _entry(
                "settings", rel, _source_kind(rel, COMPONENTS["settings"]),
                {"key": key, "value": value,
                 # Hash for every key so that a silent permission change during
                 # an update still shows up in the diff without printing the
                 # value itself.
                 "value_hash": _raw_hash(data[key])}), findings)
    return entries, findings


# ------------------------------------------------- directory collectors ----

def _accepted_names(ident_name, rel, kind):
    """Names that do not count as a deviation.

    Besides the directory name itself, a nested skill may carry the full path
    with slashes turned into hyphens -- that is the established convention
    for grouped skills (real case: zscaler 0.14.0, 42 of 42 skills). Treating
    it as a finding produced 42 identical lines and buried the real ones.
    """
    accepted = {ident_name}
    if kind == "skill":
        parts = rel.split("/")
        if parts and parts[-1] == "SKILL.md":
            parts = parts[:-1]
        # Everything from the collection root downwards, hyphen-joined.
        for start in range(len(parts)):
            accepted.add("-".join(parts[start:]))
    return accepted


def _markdown_entry(root, rel, kind, ident_name, source_kind="convention"):
    text, finding = read_safely(os.path.join(root, rel))
    if finding:
        return _entry(kind, rel, source_kind, {}, findings=[finding])
    raw, frontmatter_finding = read_frontmatter(text)
    findings = [frontmatter_finding] if frontmatter_finding else []
    fields = {
        "frontmatter_name": raw.get("name"),
        "disable_model_invocation": bool(raw.get("disable-model-invocation", False)),
        # as_list instead of "or []": frontmatter with "allowed-tools: true"
        # yields a bool, and that used to reach the output.
        "allowed_tools": as_list(raw.get("allowed-tools")),
        "disallowed_tools": as_list(raw.get("disallowed-tools")),
        # Agents declare their permissions under `tools:`, not under
        # `allowed-tools:`. Reading the field and dropping it meant an agent
        # gaining Bash and Write showed up only as a changed checksum.
        "tools": as_list(raw.get("tools")),
        "context": raw.get("context"),
        "model": raw.get("model"),
        "user_invocable": raw.get("user-invocable"),
        "declares_hooks": bool(raw.get("hooks")),
        "description_hash": "sha256:" + _short_hash(str(raw.get("description", ""))),
        # The body of a skill IS the instruction to the model. Hashing only the
        # description meant a rewritten body diffed as "no changes" -- and the
        # background update is exactly the case this tool is built for.
        "body_hash": "sha256:" + _short_hash(body_of(text)),
        # Everything in the frontmatter that is not captured by name:
        # a changed tools: list or an added hooks: block would
        # otherwise diff as "no change".
        "frontmatter_hash": "sha256:" + _short_hash(frontmatter_text_of(text)),
    }
    if raw.get("name") and raw["name"] not in _accepted_names(ident_name, rel, kind):
        findings.append("name-differs")
    markers = []
    if kind == "skill":
        # Always set, not only in the special case: otherwise a move from the
        # plugin root into skills/ would diff against None instead of False.
        fields["in_plugin_root"] = False
    if kind == "command":
        fields["shell_lines"] = [m.strip() for m in _SHELL_PATTERN.findall(text)]
        for line in fields["shell_lines"]:
            markers.extend(markers_for(line))
    return _entry(kind, rel, source_kind, fields,
                  markers=sorted(set(markers)), findings=findings)


# A cap on how many entries one category may contribute, mirroring the
# max_files of tree_digest. Depth and file size were capped, the COUNT of
# files was not: 100.000 command files (4 MB on disk) produced a 69 MB state
# file, a 137 MB pair after rotation, and 585 MB of memory on the second run,
# because save() builds the whole JSON string and load() parses it back.
MAX_ENTRIES = 2000


def _walk(root, base, findings, category, pick, depth=0, visited=None,
          budget=None):
    """Walk below `base` and yield whatever `pick` accepts.

    One walker, two callers. The skill and the command version were
    line-for-line identical except for three lines at the end, and every
    repair had to be made twice -- which is how three of this project's
    findings came about. `pick(rel, full)` returns "yield", "descend",
    "both" or None.

    Global visited set, not per-descent. A symlink that stays INSIDE the
    plugin (ln -s . commands/x) is allowed by stays_inside and explodes the
    walk: four such links produced 87381 entries, and a per-descent guard
    caught cycles but not a DAG -- four symlinks per level made 21845
    entries out of a single file, eight of them ran past a minute.

    A global set alone would go too far the other way: a directory reachable
    under two names would be reported under one name only and the other
    would vanish. That is why the second sighting is a FINDING and not a
    silent skip. Both halves of the problem need both parts.
    """
    if depth > MAX_WALK_DEPTH:
        findings.append({"code": "too-deep", "path": base,
                         "category": category, "detail": ""})
        return
    if visited is None:
        visited = set()
    if budget is None:
        budget = [MAX_ENTRIES]
    if budget[0] <= 0:
        return
    try:
        info = os.stat(os.path.join(root, base))
        marker = (info.st_dev, info.st_ino)
    except OSError:
        # Every other error path in this module appends a finding. Returning
        # silently made a whole subtree vanish without a word, and the
        # category then landed in "checked and absent".
        findings.append({"code": "no-read-permission", "path": base,
                         "category": category, "detail": ""})
        return
    if marker in visited:
        findings.append({"code": "already-visited", "path": base,
                         "category": category, "detail": ""})
        return
    visited.add(marker)
    for name in _list_dir(root, base, findings, category):
        rel = f"{base}/{name}"
        if not stays_inside(root, rel):
            findings.append({"code": "symlink-outside", "path": rel,
                             "category": category,
                             "detail": "points-outside"})
            continue
        what = pick(rel, os.path.join(root, rel))
        if what in ("yield", "both"):
            if budget[0] <= 0:
                break
            budget[0] -= 1
            if budget[0] == 0:
                findings.append({"code": "too-many", "path": base,
                                 "category": category,
                                 "detail": "entry-cap",
                                 "detail_arg": str(MAX_ENTRIES)})
            yield rel
        if what in ("descend", "both"):
            for deeper in _walk(root, rel, findings, category, pick,
                                depth + 1, visited, budget):
                yield deeper
        if budget[0] <= 0:
            break


def _skill_dirs(root, base, findings, depth=0, visited=None, category=None,
                budget=None):
    """Find directories containing SKILL.md, including nested ones."""
    def pick(rel, full):
        if not os.path.isdir(full):
            return None
        # Keep descending either way. Stopping at the first hit hid every
        # skill below a directory that carried one itself.
        return "both" if os.path.isfile(os.path.join(full, "SKILL.md")) else "descend"

    return _walk(root, base, findings, category, pick, depth, visited, budget)


def _markdown_files(root, base, findings, depth=0, visited=None, category=None,
                    budget=None):
    """Collect .md files below base, recursively, without following symlinks."""
    def pick(rel, full):
        if os.path.isdir(full):
            return "descend"
        return "yield" if rel.endswith(".md") else None

    return _walk(root, base, findings, category, pick, depth, visited, budget)


def collect_directories(root, paths, manifest_name=None):
    """Collect skills, commands, agents, bin/ and the count-only categories."""
    entries, findings = {}, []

    # A plugin with exactly one skill may put SKILL.md straight into the root
    # instead of creating a skills/ directory. The name then comes from the
    # frontmatter. Without this case the tool reports "no plugin here" for such
    # a plugin -- real case: watch 0.1.3.
    if os.path.isfile(os.path.join(root, "SKILL.md")):
        raw, _ = read_frontmatter(read_safely(os.path.join(root, "SKILL.md"))[0] or "")
        # NOT the directory name as a fallback: in the cache the version sits
        # in the path (…/<plugin>/<version>/), so every version bump would have
        # changed the ID and produced a pair of added and removed -- in exactly
        # the situation this tool is built for.
        name = clean_name(str(raw.get("name") or manifest_name or "skill"))
        entry = _markdown_entry(root, "SKILL.md", "skill", name)
        entry["fields"]["in_plugin_root"] = True
        # The skills/ branch sets this; the root case did not, so the one
        # layout that is a documented real case had no catch-all hash at all.
        # Skip the manifest and the component directories: everything below
        # them is inventoried in its own right.
        entry["fields"]["extras_hash"] = tree_digest(
            root, skip=_root_skill_skip(paths))
        _put(entries, f"skill:{name}", entry, findings)

    for base in paths.get("skills", []):
        kind = _source_kind(base, COMPONENTS["skills"])
        # Recursive, same as for commands. Skills may be grouped in
        # subdirectories (real case: zscaler 0.14.0 has 42 skills under
        # skills/<area>/<name>/SKILL.md). A flat search misses all of them and
        # then reports "not present".
        for rel in _skill_dirs(root, base, findings, category="skills"):
            short = clean_name(rel[len(base) + 1:])
            entry = _markdown_entry(
                root, f"{rel}/SKILL.md", "skill", short.rsplit("/", 1)[-1], kind)
            entry["fields"]["extras_hash"] = tree_digest(
                os.path.join(root, rel), skip_names=("SKILL.md",))
            _put(entries, f"skill:{short}", entry, findings)

    for category, kind in (("commands", "command"), ("agents", "agent")):
        for base in paths.get(category, []):
            source_kind = _source_kind(base, COMPONENTS[category])
            for rel in _markdown_files(root, base, findings, category=category):
                stem = clean_name(rel[len(base) + 1:-3])
                _put(entries, f"{kind}:{stem}", _markdown_entry(
                    root, rel, kind, stem.rsplit("/", 1)[-1], source_kind), findings)

    for base in paths.get("bin", []):
        source_kind = _source_kind(base, COMPONENTS["bin"])
        for name in _list_dir(root, base, findings, "bin"):
            rel = f"{base}/{name}"
            full = os.path.join(root, rel)
            if os.path.isdir(full) and not os.path.islink(full):
                # A directory is not an executable on the PATH. `mode & 0o111`
                # is true for 0755, so every subdirectory was reported as one,
                # and file_digest then returned None for it -- which the diff
                # later called "no longer checkable".
                findings.append({"code": "present-but-not-loaded", "path": rel,
                                 "category": "bin", "detail": ""})
                continue
            # bin/ lands in the Bash tool's PATH. A link pointing out of the
            # plugin is exactly what has to be visible here.
            inside = stays_inside(root, rel)
            if os.path.islink(full) and not inside:
                findings.append({"code": "symlink-outside", "path": rel,
                                 "category": "bin",
                                 "detail": "points-outside"})
            try:
                mode = os.lstat(full).st_mode
            except OSError:
                findings.append({"code": "no-read-permission", "path": rel,
                                 "category": "bin", "detail": ""})
                continue
            _put(entries, f"bin:{clean_name(name)}", _entry(
                "bin", rel, source_kind,
                {"executable": bool(mode & 0o111),
                 "is_symlink": os.path.islink(full),
                 "link_target": os.readlink(full) if os.path.islink(full) else None,
                 # A hash is not a code audit, it only remembers sameness.
                 # Only hash what stays inside. Reporting the symlink and
                 # then hashing the foreign file anyway would defeat the point.
                 # realpath for a link that stays inside: O_NOFOLLOW would
                 # otherwise return None for every symlink, and a content swap
                 # at the target went unnoticed -- on the PATH of all places.
                 "content_hash": file_digest(os.path.realpath(full))
                 if inside else None}), findings)

    for category in COUNT_ONLY_CATEGORIES:
        for base in paths.get(category, []):
            full = os.path.join(root, base)
            if os.path.isdir(full):
                count = len(_list_dir(root, base, findings, category))
            elif os.path.isfile(full):
                count = 1
            else:
                continue
            # The number alone said nothing about the contents: swapping
            # every file in output-styles/ for its opposite kept the count at
            # 1 and produced "no changes since the last run".
            content = (tree_digest(full) if os.path.isdir(full)
                       else file_digest(full))
            _put(entries, f"count:{category}", _entry(
                "count", base, _source_kind(base, COMPONENTS[category]),
                {"count": count, "content_hash": content}), findings)

    return entries, findings


def _merge(entries, part, findings):
    """Fold one collector's result into the whole, without silent overwrites.

    `_put` guards against that inside a collector; `dict.update` between them
    did not. Today the ID prefixes are disjoint, so nothing collides -- but
    the most obvious next step (reporting "present but not loaded" for
    commands/ and skills/ as well as hooks/) creates a second `file:` prefix,
    and then one collector would quietly win.
    """
    for ident, entry in part.items():
        _put(entries, ident, entry, findings)


def _covered_categories(entries):
    names = set()
    for ident, entry in entries.items():
        if entry["kind"] == "count":
            names.add(ident.split(":", 1)[1])
        elif entry["kind"] in KIND_TO_CATEGORY:
            names.add(KIND_TO_CATEGORY[entry["kind"]])
    return names


def build_inventory(root):
    """Build the complete, normalised inventory dict."""
    manifest_rel = ".claude-plugin/plugin.json"
    manifest_path = os.path.join(root, manifest_rel)
    manifest_present = os.path.isfile(manifest_path)
    manifest, manifest_finding = ({}, None)
    if manifest_present:
        manifest, manifest_finding = read_json(manifest_path)
        manifest = manifest if isinstance(manifest, dict) else {}

    paths, findings = resolve_paths(root, manifest)

    # No tests/ exemption any more. It never had an effect (tests/ is not a
    # collection path, so nothing below it is ever reached) and it was a
    # liability: whoever could influence a directory name could make whole
    # categories disappear from the report. Removing a filter that does
    # nothing but can be abused is the easy call.

    if manifest_finding:
        findings.append({"code": manifest_finding, "path": manifest_rel, "detail": ""})

    manifest_name = manifest.get("name")
    entries = {}
    for collector in (collect_hooks, collect_settings, collect_mcp):
        part, part_findings = collector(root, paths)
        _merge(entries, part, findings)
        findings.extend(part_findings)
    part, part_findings = collect_directories(root, paths, manifest_name)
    _merge(entries, part, findings)
    findings.extend(part_findings)

    # "Checked and absent" is the most valuable statement in the report and
    # must stay provable. A category whose file exists but could not be read
    # (broken JSON, too large, no permission) must NOT appear there -- else the
    # tool claims absence where it simply knows nothing.
    covered = _covered_categories(entries)
    # A category whose path was REJECTED (symlink out, absolute, leaves the
    # plugin, declared inline) has an empty path list -- and would therefore
    # have landed in the positive statement. The report then contradicted
    # itself two lines apart: "checked and absent: settings" right above
    # "finding symlink-outside: settings.json".
    # Read the category off the finding instead of guessing it from the
    # path. Prefix matching alone missed three of five rejection codes, and
    # those categories then landed in the positive statement -- the report
    # contradicted itself two lines apart. The prefix pass below stays as a
    # second net for findings that predate the category field.
    flagged = {finding["category"] for finding in findings if finding.get("category")}
    for finding in findings:
        path = finding.get("path", "")
        for key, default_path in COMPONENTS.items():
            if path == default_path or path.startswith(default_path.rstrip("/") + "/"):
                flagged.add(key)
    # Only what a finding names. The disjunct paths.get(key) was tried and
    # dropped: it reported a category that was read completely and is simply
    # empty as "present but unreadable", with no finding to explain it -- a
    # line that shows up on almost every plugin gets ignored in the case
    # that matters.
    unclear = {key for key in COMPONENTS if key not in covered and key in flagged}
    absent = sorted(key for key in COMPONENTS
                    if key not in covered and key not in unclear)

    name = manifest_name or os.path.basename(os.path.abspath(root))
    return {
        "identity": {
            "name": clean_name(str(name)),
            "version": manifest.get("version"),
            "manifest_present": manifest_present,
            "manifest_path": manifest_rel if manifest_present else None,
            # Hooks, MCP servers and settings all have a catch-all hash; the
            # manifest, the most central file of the plugin, had none. A
            # changed description, a changed author, a field a future
            # release starts honouring: all invisible.
            "raw_hash": _raw_hash(manifest) if manifest_present else None,
        },
        "entries": entries,
        "checked_absent": absent,
        "unreadable": sorted(unclear),
        "findings": sorted(findings, key=lambda f: (f["code"], f["path"])),
    }


# Whole words after the split below, never substrings: matching substrings
# masked "path" and "author", and a report that hides ordinary values is as
# useless as one that leaks secrets. "authorization" therefore has to be
# listed in its own right -- it is not the word "auth".
_SECRET_HINTS = ("key", "token", "secret", "password", "passwd", "pwd",
                 "passphrase", "credential", "credentials", "auth",
                 "authorization", "authentication", "cookie", "bearer",
                 "pat", "otp", "dsn", "connection", "signature")

# Four words were in this list and are gone again: "api", "session", "sig"
# and "private". None of them carries a secret by itself, and each masked a
# pile of ordinary values -- apiUrl, apiVersion, sessionTimeout, publicKey,
# keyBindings, privateNote. A report that hides the endpoint of the session
# agent can still say THAT it changed, but no longer to what, and that is a
# masking which conceals a real change instead of a secret.
#
# camelCase is the common spelling in these files. Splitting only at
# non-alphanumeric characters left apiKey, authToken, accessToken and
# sessionId as single words, so none of them matched and every one of those
# values went into the report and into the state file in the clear.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# Hints that also count as the tail of a compound written without a separator.
# Whole-word matching alone let through the most common spelling there is:
# "apikey" is one word, so is "accesskey", "authtoken", "githubtoken". The list
# is deliberately shorter than _SECRET_HINTS -- "pat" would swallow "compat",
# "auth" would swallow "oauth". What precedes the tail is still checked against
# the harmless neighbours, so "publickey" stays readable while "apikey" does not.
_COMPOUND_HINTS = ("key", "token", "secret", "password", "passwd",
                   "passphrase", "credential", "credentials")


def _key_words(key):
    """The words of a config key, for matching against the hint lists.

    Digits split words as well as case and punctuation do. Without that,
    "apiKey2" became ["api", "key2"] and the value went into the report and
    the state file in the clear -- a numbered key is the ordinary way to
    write a second one.
    """
    spaced = _CAMEL_BOUNDARY.sub("-", str(key))
    words = set()
    for part in re.split(r"[^a-z]+", spaced.lower()):
        if not part:
            continue
        words.add(part)
        for hint in _COMPOUND_HINTS:
            if len(part) > len(hint) and part.endswith(hint):
                words.add(hint)
                words.add(part[:-len(hint)])
    return words

# Words that turn a secret word into a statement ABOUT the secret rather than
# the secret itself. A public key is public by definition, a signature
# ALGORITHM is a name, and keyBindings are keyboard shortcuts. Without this
# the masking hides ordinary values, and a report that blacks out the wrong
# things gets ignored where it matters.
_HARMLESS_NEIGHBOURS = ("public", "algorithm", "algo", "bindings", "binding",
                        "type", "name", "id", "count", "timeout", "url",
                        "endpoint", "uri", "version", "format", "length",
                        "expiry", "expires", "ttl", "path", "file", "note")

# Value shapes that look like a secret regardless of the key name.
_SECRET_SHAPES = ("sk-", "ghp_", "gho_", "github_pat_", "xoxb-", "xoxp-",
                  "AKIA", "Bearer ")


def _looks_secret(value):
    return isinstance(value, str) and any(
        value.startswith(shape) or f"={shape}" in value for shape in _SECRET_SHAPES)


def _mask_secrets(value, depth=0):
    """Replace values whose key name or shape suggests a secret.

    Same rule as everywhere else in this tool: names are shown, values are
    not. The shape check catches the cases a name list always misses, such as
    a token stored under an innocuous key.
    """
    if depth > 6:
        return "[…]"
    if isinstance(value, dict):
        masked = {}
        for key, inner in value.items():
            parts = _key_words(key)
            if (any(hint in parts for hint in _SECRET_HINTS)
                    and not any(word in parts for word in _HARMLESS_NEIGHBOURS)):
                masked[key] = "[…]"
            else:
                masked[key] = _mask_secrets(inner, depth + 1)
        return masked
    if isinstance(value, list):
        return [_mask_secrets(item, depth + 1) for item in value]
    if _looks_secret(value):
        return "[…]"
    if isinstance(value, str) and "://" in value:
        return _url_without_secret(value)
    return value
