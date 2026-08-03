"""Minimal frontmatter scanner.

Deliberately not a YAML parser: skill and command frontmatter only ever
contains key/value pairs, inline lists and block lists. A dependency would be
harder to justify in an inspection tool than these few lines.

List values are always returned sorted, so that "B, A" and "[A, B]" do not
show up as a change in the diff.
"""

TRUE_WORDS = ("true", "yes", "on")
FALSE_WORDS = ("false", "no", "off")
LIST_FIELDS = ("allowed-tools", "disallowed-tools", "tools", "skills")
BLOCK_MARKERS = (">", "|", ">-", "|-", ">+", "|+")


def _unquote(raw):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        raw = raw[1:-1]
    return raw


def _value(raw):
    raw = _unquote(raw)
    lowered = raw.lower()
    if lowered in TRUE_WORDS:
        return True
    if lowered in FALSE_WORDS:
        return False
    if raw.startswith("[") and raw.endswith("]"):
        parts = [_unquote(part) for part in raw[1:-1].split(",")]
        return sorted(part for part in parts if part)
    return raw


def body_of(text):
    """Return everything after the frontmatter block.

    Splitting on "---" blindly is wrong: in a file without frontmatter the
    first two "---" are ordinary horizontal rules, and hashing only what
    follows them missed changes to the actual instructions.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "".join(lines[index + 1:])
    return text


def read_frontmatter(text):
    """Read the block between the --- lines. Returns (fields, finding)."""
    # A BOM before the opening --- would otherwise hide the whole block, and
    # with it every declared permission.
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, None

    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        return {}, "unparsable-frontmatter"

    fields = {}
    open_list = None
    block_key = None
    block_lines = []

    def close_block():
        """Fold a collected block scalar into a single string."""
        nonlocal block_key, block_lines
        if block_key is not None:
            fields[block_key] = " ".join(
                line.strip() for line in block_lines if line.strip())
        block_key, block_lines = None, []

    for line in lines[1:end]:
        indented = line[:1].isspace()

        # Indented lines always belong to the previous key, never to a new one.
        # Without this rule "  TRIGGER THIS SKILL WHEN:" inside a block scalar
        # becomes a key of its own (real case, see tests).
        if indented:
            if block_key is not None:
                block_lines.append(line)
            elif open_list and line.lstrip().startswith("- "):
                fields[open_list].append(_unquote(line.lstrip()[2:]))
            continue

        if not line.strip() or line.lstrip().startswith("#"):
            if block_key is not None:
                block_lines.append("")
            continue

        if line.startswith("- ") and open_list:
            fields[open_list].append(_unquote(line[2:]))
            continue

        if ":" not in line:
            continue

        close_block()
        key, _, rest = line.partition(":")
        key = key.strip()
        rest_stripped = rest.strip()

        if rest_stripped in BLOCK_MARKERS:
            block_key = key
            block_lines = []
            open_list = None
            continue

        if not rest_stripped:
            fields[key] = []
            open_list = key
            continue

        open_list = None
        value = _value(rest)
        if key in LIST_FIELDS and isinstance(value, str):
            value = sorted(part.strip() for part in value.split(",") if part.strip())
        fields[key] = value

    close_block()

    for key, value in fields.items():
        if isinstance(value, list):
            fields[key] = sorted(value)
    return fields, None


def frontmatter_text_of(text):
    """Return the raw frontmatter block, or an empty string.

    Hashing the PARSED fields missed everything the scanner drops: an indented
    hooks: block, comments, duplicate keys. The raw text catches all of it.
    """
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index])
    return ""
