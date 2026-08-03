"""Find the plugins this user actually has, across all scopes.

A memory is worth nothing until it has something to remember. The tool used
to inventory ONE directory, which meant the first useful run required
knowing the cache layout and typing eighteen paths. Whoever forgot one
created exactly the blind spot the tool exists against.

The registry is the right source here, and it is the one thing the design
document deliberately did not read. That was correct for deriving the state
KEY (the path already carries it) and wrong for DISCOVERY: walking the cache
finds stale versions side by side -- `watch` in two, `zscaler` in four --
and cannot say which of them is the one that runs.
"""
import os

from .reading import read_json

def _config_dir():
    """Claude Code's configuration directory."""
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def _enabled_map(config_dir):
    """Read `enabledPlugins` from the user settings. Returns (map, finding).

    Project and local settings can override this per directory. They are not
    read here: the sweep is a statement about the installation, and a run
    from a random working directory must not silently produce a different
    answer than the same run one directory over.

    An unreadable settings file used to be swallowed, and then every disabled
    plugin quietly counted as enabled -- a wrong statement about the
    environment, made without a word.
    """
    path = os.path.join(config_dir, "settings.json")
    if not os.path.exists(path):
        return {}, None
    data, finding = read_json(path)
    if finding:
        return {}, {"code": finding, "path": path, "category": None,
                    "detail": "enabled-state-unknown"}
    if not isinstance(data, dict):
        return {}, {"code": "unexpected-type", "path": path,
                    "category": None, "detail": "enabled-state-unknown"}
    enabled = data.get("enabledPlugins")
    if enabled is not None and not isinstance(enabled, dict):
        return {}, {"code": "unexpected-type", "path": path,
                    "category": None, "detail": "enabled-state-unknown"}
    return enabled or {}, None


def installed_plugins(config_dir=None):
    """Return one record per installed plugin, sorted, plus findings.

    A registry key maps to a LIST, because the same plugin can be installed
    at several scopes at once. Each of those is its own installation with its
    own directory, so each gets its own record and its own comparison.

    Returns (records, findings). A record is a dict with `key`, `path`,
    `scope`, `version` and `enabled`.
    """
    config_dir = config_dir or _config_dir()
    path = os.path.join(config_dir, "plugins", "installed_plugins.json")
    findings = []
    if not os.path.isfile(path):
        return [], findings

    data, finding = read_json(path)
    if finding:
        findings.append({"code": finding, "path": path, "category": None,
                         "detail": ""})
        return [], findings
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        findings.append({"code": "unexpected-type", "path": path,
                         "category": None, "detail": ""})
        return [], findings

    enabled, enabled_finding = _enabled_map(config_dir)
    if enabled_finding:
        findings.append(enabled_finding)
    records = []
    for key in sorted(plugins):
        entries = plugins[key]
        # A single object instead of a list is not the documented shape, but
        # accepting it costs one line and refusing it would drop a plugin.
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            findings.append({"code": "unexpected-type", "path": path,
                             "category": None, "detail": ""})
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                findings.append({"code": "unexpected-type", "path": path,
                                 "category": None, "detail": ""})
                continue
            install_path = entry.get("installPath")
            if not isinstance(install_path, str) or not install_path:
                findings.append({"code": "declared-path-missing", "path": key,
                                 "category": None, "detail": ""})
                continue
            install_path = os.path.expanduser(install_path)
            if not os.path.isabs(install_path):
                # A relative path would resolve against the working directory,
                # so the same run would answer differently one directory over
                # -- exactly what the docstring above promises it will not do.
                # Two different plugins under one name, depending on where you
                # stood when you typed it.
                findings.append({"code": "relative-path", "path": key,
                                 "category": None, "detail": ""})
                continue
            # Only these three fields are read. Everything else a future
            # release adds is ignored on purpose, so a new field cannot break
            # the sweep.
            records.append({
                "key": key,
                "path": install_path,
                "scope": entry.get("scope"),
                "version": entry.get("version"),
                # Missing means enabled: `enabledPlugins` only carries an entry
                # once someone has flipped it, and the default is on.
                "enabled": bool(enabled.get(key, True)),
            })
    records.sort(key=lambda record: (record["key"], record["path"]))
    return records, findings
