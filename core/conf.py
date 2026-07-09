import csv, io, logging, os

logger = logging.getLogger("downloader")

def _read_raw(path: str) -> dict[str, str]:
    data = {}
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith('#') or '=' not in stripped:
                continue
            key, _, value = stripped.partition('=')
            data[key.strip()] = value.strip()
    return data

def _coerce(key: str, raw: str, kind):
    if kind is bool:
        return raw.strip().lower() == 'true'
    if kind is int:
        return int(raw.strip())
    if kind is list:
        return [(item.strip() or None) for item in next(csv.reader([raw]))]
    return raw

def _serialize(value) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, list):
        out = io.StringIO()
        csv.writer(out, lineterminator='').writerow(['' if v is None else v for v in value])
        return out.getvalue()
    return str(value)

def read(path: str, template_path: str, schema: dict) -> dict:
    """Reads config from `path`, using `template_path` (the shipped default) as the source of
    truth for any key that's missing or invalid in `path`. `schema` maps each key to its Python
    type (bool/int/list/str), driving how raw text is coerced."""
    base = _read_raw(template_path)
    overrides = _read_raw(path)
    result = {}
    for key, kind in schema.items():
        if key in overrides:
            try:
                result[key] = _coerce(key, overrides[key], kind)
                continue
            except (ValueError, csv.Error):
                logger.warning(f"Invalid {key}={overrides[key]!r} in {path} - using shipped default")
        result[key] = _coerce(key, base.get(key, ''), kind)
    return result

def write(path: str, template_path: str, values: dict):
    """Writes `values` into a copy of `template_path`'s text - each recognized `KEY=` line's value
    is replaced with `values[key]`, serialized per its Python type; comments, blank lines, and
    order are otherwise copied through unchanged. Overwrites `path`."""
    lines = []
    with open(template_path, 'r', encoding='utf-8') as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith('#') and '=' in stripped:
                key = stripped.partition('=')[0].strip()
                if key in values:
                    lines.append(f"{key}={_serialize(values[key])}\n")
                    continue
            lines.append(line)
    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
