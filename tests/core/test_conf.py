import pytest

from core import conf

SCHEMA = {'NAME': str, 'ENABLED': bool, 'COUNT': int, 'ITEMS': list}

TEMPLATE = """\
# A comment
NAME=default-name
# Another comment
ENABLED=false
COUNT=5
ITEMS=""
"""


@pytest.fixture
def template_path(tmp_path):
    path = tmp_path / "template.conf"
    path.write_text(TEMPLATE)
    return str(path)


def test_read_falls_back_to_template_when_config_missing(tmp_path, template_path):
    path = str(tmp_path / "missing.conf")
    # config.load() is what creates the file from the template before ever calling read() -
    # read() itself has no such fallback and must raise.
    with pytest.raises(OSError):
        conf.read(path, template_path, SCHEMA)


def test_read_uses_template_defaults_when_config_has_no_overrides(tmp_path, template_path):
    path = tmp_path / "config.conf"
    path.write_text("")

    result = conf.read(str(path), template_path, SCHEMA)

    assert result == {'NAME': 'default-name', 'ENABLED': False, 'COUNT': 5, 'ITEMS': [None]}


def test_read_applies_valid_overrides(tmp_path, template_path):
    path = tmp_path / "config.conf"
    path.write_text("NAME=custom\nENABLED=true\nCOUNT=42\nITEMS=a,b,c\n")

    result = conf.read(str(path), template_path, SCHEMA)

    assert result == {'NAME': 'custom', 'ENABLED': True, 'COUNT': 42, 'ITEMS': ['a', 'b', 'c']}


def test_read_falls_back_to_template_on_invalid_override(tmp_path, template_path, caplog):
    path = tmp_path / "config.conf"
    path.write_text("COUNT=not-a-number\n")

    result = conf.read(str(path), template_path, SCHEMA)

    assert result['COUNT'] == 5  # template default, not a crash
    assert "Invalid COUNT" in caplog.text


def test_read_ignores_comments_and_blank_lines(tmp_path, template_path):
    path = tmp_path / "config.conf"
    path.write_text("# NAME=ignored-because-commented\n\nNAME=real-value\n")

    result = conf.read(str(path), template_path, SCHEMA)

    assert result['NAME'] == 'real-value'


def test_list_parsing_handles_quoted_comma(tmp_path, template_path):
    path = tmp_path / "config.conf"
    path.write_text('ITEMS="pass,word",other\n')

    result = conf.read(str(path), template_path, SCHEMA)

    assert result['ITEMS'] == ['pass,word', 'other']


def test_shipped_empty_list_entry_coerces_to_none_not_empty_string(tmp_path, template_path):
    # Mirrors core/creator.conf.default's ARCHIVE_PASSWORDS="" - Creator.unpack() relies on this
    # producing [None] (an explicit "try no password" entry), not [''].
    path = tmp_path / "config.conf"
    path.write_text('ITEMS=""\n')

    result = conf.read(str(path), template_path, SCHEMA)

    assert result['ITEMS'] == [None]


def test_write_replaces_only_recognized_keys_preserving_comments_and_order(tmp_path, template_path):
    path = tmp_path / "config.conf"

    conf.write(str(path), template_path, {'NAME': 'new-name', 'ENABLED': True, 'COUNT': 9, 'ITEMS': ['x', 'y']})

    written = path.read_text()
    lines = written.splitlines()
    assert lines[0] == "# A comment"
    assert lines[1] == "NAME=new-name"
    assert lines[2] == "# Another comment"
    assert lines[3] == "ENABLED=true"
    assert lines[4] == "COUNT=9"
    assert lines[5] == "ITEMS=x,y"


def test_write_then_read_round_trips_every_schema_type(tmp_path, template_path):
    path = tmp_path / "config.conf"
    values = {'NAME': 'roundtrip', 'ENABLED': True, 'COUNT': 123, 'ITEMS': ['one', None, 'three']}

    conf.write(str(path), template_path, values)
    result = conf.read(str(path), template_path, SCHEMA)

    assert result == values


def test_serialize_is_inverse_of_coerce_for_lists(tmp_path, template_path):
    path = tmp_path / "config.conf"
    conf.write(str(path), template_path, {'NAME': 'x', 'ENABLED': False, 'COUNT': 1, 'ITEMS': [None]})

    result = conf.read(str(path), template_path, SCHEMA)

    assert result['ITEMS'] == [None]
