import calendar
import datetime
import os
import time as time_module

import pytest

from core.utils import get_hash_from_url, get_post_time, sanitize_filename


@pytest.fixture
def temp_timezone():
    """Sets TZ and calls time.tzset() for the duration of one test, restoring the original
    timezone afterward. Deliberately doesn't use monkeypatch.setenv for this - its automatic
    revert only touches os.environ, and a plain env change has no effect without a matching
    tzset() call immediately after, including on the way back out."""

    original = os.environ.get('TZ')

    def _set(tz_name):
        os.environ['TZ'] = tz_name
        time_module.tzset()

    yield _set

    if original is None:
        os.environ.pop('TZ', None)
    else:
        os.environ['TZ'] = original
    time_module.tzset()


# --- sanitize_filename ---

def test_sanitize_filename_replaces_invalid_characters():
    assert sanitize_filename('a<b>c:d"e/f\\g|h?i*j') == 'a_b_c_d_e_f_g_h_i_j'


def test_sanitize_filename_replaces_spaces_and_collapses_underscores():
    assert sanitize_filename('a   b  c') == 'a_b_c'


def test_sanitize_filename_strips_trailing_dots():
    assert sanitize_filename('file.name...') == 'file.name'


def test_sanitize_filename_strips_leading_and_trailing_underscores():
    assert sanitize_filename('__file__') == 'file'


def test_sanitize_filename_empty_becomes_unnamed():
    assert sanitize_filename('') == 'unnamed'
    assert sanitize_filename(None) == 'unnamed'


def test_sanitize_filename_all_invalid_characters_becomes_unnamed():
    assert sanitize_filename('///') == 'unnamed'


def test_sanitize_filename_truncates_and_cleans_up_the_cut_edge():
    # Construct a name whose max_length-th character lands mid-run of trailing dots/underscores
    # after truncation, to prove the post-truncation cleanup actually re-runs.
    name = 'a' * 10 + '.' * 5 + 'b' * 10
    result = sanitize_filename(name, max_length=12)

    assert len(result) <= 12
    assert not result.endswith('.')
    assert not result.endswith('_')


# --- get_post_time ---

def test_get_post_time_parses_expected_fields():
    result = get_post_time('2024-06-15T12:30:45.123456')

    local_dt = datetime.datetime(2024, 6, 15, 12, 30, 45)
    assert result == pytest.approx(time_module.mktime(local_dt.timetuple()))


def test_get_post_time_uses_local_timezone_not_utc(temp_timezone):
    temp_timezone('America/New_York')

    result = get_post_time('2024-06-15T12:00:00.000000')

    dt = datetime.datetime(2024, 6, 15, 12, 0, 0)
    local_interpretation = time_module.mktime(dt.timetuple())
    utc_interpretation = calendar.timegm(dt.timetuple())

    # Proves get_post_time matches a fresh local-tz mktime() call on the same wall-clock
    # fields, and is NOT the UTC interpretation of those same fields - pins this down so a
    # future "fix" to parse as UTC can't silently change behavior unnoticed.
    assert result == pytest.approx(local_interpretation, abs=1)
    assert result != pytest.approx(utc_interpretation, abs=1)


# --- get_hash_from_url ---

def test_get_hash_from_url_strips_query_string():
    assert get_hash_from_url('https://kemono.cr/data/ab/cd/abcd1234.jpg?f=name.jpg') == 'abcd1234'


def test_get_hash_from_url_strips_only_final_extension():
    assert get_hash_from_url('https://kemono.cr/data/x/file.name.with.dots.jpg') == 'file.name.with.dots'
