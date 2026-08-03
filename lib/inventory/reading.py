"""Safe reading of individual files and normalisation of names."""
import errno
import json
import os
import stat as stat_module
import unicodedata

SIZE_LIMIT = 1024 * 1024
MAX_DEPTH = 100


def read_safely(path):
    """Read a regular file without following symlinks.

    Returns (text, None) or (None, finding_code).

    O_NOFOLLOW also closes the TOCTOU window between the size check and the
    open, because fstat works on the already-open descriptor. followlinks=False
    during a directory walk is not enough: it only covers linked directories,
    a linked file would still be opened.

    O_NONBLOCK is required as well, otherwise opening a FIFO blocks
    indefinitely until a writer shows up, and the S_ISREG check below would
    come too late. A tool that inspects foreign plugins must not let the
    inspected plugin stall it.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            return None, "symlink"
        return None, "no-read-permission"
    try:
        info = os.fstat(fd)
        if not stat_module.S_ISREG(info.st_mode):
            return None, "not-a-regular-file"
        if info.st_size > SIZE_LIMIT:
            return None, "file-too-large"
        raw = os.read(fd, SIZE_LIMIT + 1)
    except OSError:
        return None, "no-read-permission"
    finally:
        os.close(fd)
    return raw.decode("utf-8", errors="replace"), None


def too_deep(text, max_depth=MAX_DEPTH):
    """Check bracket nesting depth without parsing.

    Tracks string state, otherwise a brace inside "a{b" would be counted.
    """
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if escaped:
            escaped = False
            continue
        if in_string:
            if char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
            if depth > max_depth:
                return True
        elif char in "}]":
            depth -= 1
    return False


def clean_name(name):
    """Repair surrogates coming from the filesystem and normalise to NFC.

    Use for the inventory and for comparison only, never to reopen the file:
    on a filesystem that stores NFD, the normalised name raises
    FileNotFoundError. Raw name for I/O, NFC name in the state.
    """
    repaired = name.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    return unicodedata.normalize("NFC", repaired)


def read_json(path):
    """Read and parse a JSON file defensively. Returns (data, finding_code)."""
    text, finding = read_safely(path)
    if finding:
        return None, finding
    if too_deep(text):
        return None, "nesting-too-deep"
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        return None, "invalid-json"
    except RecursionError:
        # Do nothing deep inside this handler, the stack is still tight here.
        return None, "recursion"


def file_digest(path, limit=64 * 1024 * 1024):
    """Content hash of a file, or None when it cannot be taken safely.

    Uses the same precautions as read_safely: no symlinks, no blocking on a
    FIFO, regular files only, and an upper bound. Taking a hash with a plain
    open() reintroduced exactly the hang that read_safely exists to prevent --
    a FIFO in bin/ stalled the run indefinitely, and a link to /dev/zero ran
    forever.
    """
    import hashlib
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat_module.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        digest = hashlib.sha256()
        read_total = 0
        while True:
            block = os.read(fd, 65536)
            if not block:
                break
            read_total += len(block)
            # The st_size check only covers the moment of opening. A file that
            # grows while being read had no bound at all: 81 GB in 25 seconds.
            if read_total > limit:
                return None
            digest.update(block)
        return "sha256:" + digest.hexdigest()[:12]
    except OSError:
        return None
    finally:
        os.close(fd)


def _is_skipped(relative_name, skip):
    """Does `skip` cover this entry? Full path from the collection root only.

    Both lists exist because they answer different questions, and one list
    answering both was a hiding place:

    - `skip` names things with an entry of their own at a KNOWN place:
      `SKILL.md`, `commands`, `.claude-plugin`. Matching those by basename at
      any depth cut `references/bin/` out of a root skill's hash, and pruned
      any *directory* called `SKILL.md` in any plugin -- so its contents could
      be swapped for their opposite while the report said "no changes".
    - `skip_names` names files that have an entry of their own WHEREVER they
      turn up, which is only ever SKILL.md below skills/: the walk that finds
      skills descends past the first hit, so a nested skill is inventoried in
      its own right and must not also land in its parent's hash.
    """
    return relative_name in skip


def tree_digest(directory, skip=(), max_depth=8, max_files=2000, skip_names=()):
    """Content hash over a whole directory, or None when it is empty.

    A skill is more than its SKILL.md: `references/` and `scripts/` are what
    the instructions send the model to. They were neither read nor hashed, so
    swapping "never print secrets" for its opposite produced the sentence
    "no changes since the last run".

    The same holds for the categories the tool only counts. Counting alone
    means an unchanged number hides a complete swap of the contents.

    Names go into the hash as well as contents, otherwise a rename between
    two equally sized files would be invisible. Unreadable entries are folded
    in as a marker rather than skipped -- a file that becomes unreadable is
    itself a change. Symlinks are recorded by their target and never
    followed, which keeps this from walking out of the plugin.
    """
    import hashlib
    parts = []
    count = 0
    for current, directories, files in os.walk(directory, followlinks=False):
        relative = os.path.relpath(current, directory)
        if relative != "." and relative.count(os.sep) + 1 > max_depth:
            directories[:] = []
            continue
        directories.sort()
        # Prune skipped directories from the walk itself, not just from the
        # hash: descending into them costs time and could still add entries
        # through the file branch.
        directories[:] = [d for d in directories
                          if not _is_skipped(
                              os.path.normpath(os.path.join(relative, d)), skip)]
        # A directory symlink never appears in `files`, so without this it
        # went into the hash neither by name nor by target -- and the whole
        # of a skill's extras could be swapped for another directory while
        # the hash stayed the same.
        for name in list(directories):
            full = os.path.join(current, name)
            if os.path.islink(full):
                directories.remove(name)
                relative_name = os.path.normpath(os.path.join(relative, name))
                if _is_skipped(relative_name, skip):
                    continue
                # Counted like a file: this branch used to append without
                # touching `count`, and a directory of symlinks costs an
                # attacker nothing in a tarball -- 200.000 of them took a
                # minute where 200.000 files stopped at the cap in seconds.
                count += 1
                if count > max_files:
                    parts.append("truncated")
                    break
                try:
                    parts.append(f"{relative_name}\0dirlink:{os.readlink(full)}")
                except OSError:
                    parts.append(f"{relative_name}\0dirlink:?")
        if count > max_files:
            break
        for name in sorted(files):
            relative_name = os.path.normpath(os.path.join(relative, name))
            if _is_skipped(relative_name, skip) or name in skip_names:
                continue
            count += 1
            if count > max_files:
                parts.append("truncated")
                break
            full = os.path.join(current, name)
            if os.path.islink(full):
                try:
                    parts.append(f"{relative_name}\0link:{os.readlink(full)}")
                except OSError:
                    parts.append(f"{relative_name}\0link:?")
                continue
            parts.append(f"{relative_name}\0{file_digest(full) or 'unreadable'}")
        if count > max_files:
            break
    if not parts:
        return None
    return "sha256:" + hashlib.sha256(
        "\n".join(parts).encode("utf-8")).hexdigest()[:12]
