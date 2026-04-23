"""Pure-Python helpers for turning a pdfgen template-data shape into a flat
list of placeholder rows, and for walking an Odoo record tree driven by a
mapping to produce the payload sent to `/documents/generate`.

Kept free of Odoo imports so it can be unit-tested on the host.
"""


def flatten_placeholders(data, prefix=""):
    """Walk a template-data dict and yield placeholder descriptors.

    Each yielded tuple is `(path, kind)` where kind is:
      - "scalar" for leaf values (strings, numbers, booleans, None)
      - "list"   for arrays of dicts (repeated sections in the template)

    List-item descriptors carry the child schema in a nested call so the caller
    can build a second level of mapping lines whose paths are relative to each
    item. The caller is responsible for that recursion.

    Nested dicts are flattened with dot-joined paths. Empty dicts and empty lists
    are treated as scalars (the template hasn't bound anything concrete to them).
    """
    if not isinstance(data, dict):
        return
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            yield from flatten_placeholders(value, prefix=f"{path}.")
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            yield (path, "list", value[0])
        else:
            yield (path, "scalar", None)


def set_nested(target, dotted_path, value):
    """Assign `value` into `target` at a dotted path, creating intermediate dicts.

    Example: `set_nested({}, "a.b.c", 1)` → `{"a": {"b": {"c": 1}}}`.
    """
    keys = dotted_path.split(".")
    cursor = target
    for key in keys[:-1]:
        if not isinstance(cursor.get(key), dict):
            cursor[key] = {}
        cursor = cursor[key]
    cursor[keys[-1]] = value


def walk(record, dotted_path):
    """Follow a dotted attribute path on an Odoo record.

    Empty path → return the record itself. `record` may be a recordset, a plain
    dict, or any object whose attributes resolve via getattr. Missing attributes
    return None rather than raising, so templates tolerate partially-populated
    records.
    """
    if not dotted_path:
        return record
    cursor = record
    for segment in dotted_path.split("."):
        if cursor is None or cursor is False:
            return None
        if isinstance(cursor, dict):
            cursor = cursor.get(segment)
            continue
        cursor = getattr(cursor, segment, None)
    return _normalize(cursor)


def _normalize(value):
    """Coerce Odoo False/empty-recordset sentinels into JSON-friendly values."""
    if value is False:
        return ""
    try:
        if hasattr(value, "_name") and hasattr(value, "ids") and not value.ids:
            return ""
    except Exception:  # pragma: no cover - defensive; exotic __getattr__ objects
        pass
    return value


def render_expression(record, template_string):
    """Substitute `{dotted.path}` tokens in `template_string` with walked values.

    - Missing fields → empty string (no exceptions raised).
    - Unmatched/orphan braces render literally so a stray `{` in user text
      doesn't destabilise generation.
    - Non-string field values go through `_jsonable` so dates / recordsets
      coerce the same way as in scalar rows.
    """
    if not template_string:
        return ""
    out = []
    i = 0
    n = len(template_string)
    while i < n:
        ch = template_string[i]
        if ch == "{":
            close = template_string.find("}", i + 1)
            if close == -1:
                out.append(template_string[i:])
                break
            token = template_string[i + 1 : close].strip()
            if not token:
                # `{}` with nothing inside: emit literally.
                out.append(template_string[i : close + 1])
            else:
                value = walk(record, token)
                out.append(_stringify(_jsonable(value)))
            i = close + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _stringify(value):
    if value is None or value == "":
        return ""
    return str(value)


def _row_value(record, line):
    """Scalar row value — expression wins over plain path."""
    expr = getattr(line, "expression", "") or ""
    if expr:
        return render_expression(record, expr)
    if line.odoo_field_path:
        return _jsonable(walk(record, line.odoo_field_path))
    return ""


def resolve(record, lines):
    """Build the payload dict for a single record from mapping lines.

    `lines` is an iterable of objects exposing:
      - `placeholder_path`: string
      - `odoo_field_path`: string
      - `is_list`: bool
      - `child_lines`: iterable of the same shape (used when is_list is True)
      - `expression`: optional string — if set, beats `odoo_field_path`

    Scalar rows walk the path from `record` (or render the expression) and set
    the result into the output dict at `placeholder_path`. List rows walk the
    path to a recordset, iterate it, and resolve each child line relative to
    the iteration's current record. Unresolved paths map to empty string so
    template rendering stays stable.
    """
    payload = {}
    for line in lines:
        if line.is_list:
            items = []
            rs = walk(record, line.odoo_field_path)
            iterable = rs if rs and not isinstance(rs, str) else []
            try:
                iterator = list(iterable)
            except TypeError:  # pragma: no cover - defensive; non-iterable walked value
                iterator = []
            for sub in iterator:
                item = resolve(sub, list(line.child_lines))
                items.append(item)
            set_nested(payload, line.placeholder_path, items)
        else:
            set_nested(payload, line.placeholder_path, _row_value(record, line))
    return payload


def _jsonable(value):
    """Make a walked value safe to drop into a JSON payload."""
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "_name") and hasattr(value, "display_name"):
        return value.display_name or ""
    return value
