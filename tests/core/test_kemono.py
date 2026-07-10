import json
import logging

import pytest

import core.kemono as kemono


# --- simple wrappers: happy path + failure mode ---

def test_add_favorite_creator_delegates_to_call_api_action(monkeypatch):
    calls = []
    monkeypatch.setattr(kemono, "call_api_action", lambda *a, **k: calls.append((a, k)) or True)

    result = kemono.add_favorite_creator('patreon', '123')

    assert result is True
    assert calls[0][0] == ('favorites/creator/patreon/123',)
    assert calls[0][1] == {'method': 'POST'}


def test_get_favorite_creators_happy_path(monkeypatch):
    monkeypatch.setattr(kemono, "call_api", lambda *a, **k: json.dumps([{'id': '1'}, {'id': '2'}]))

    result = kemono.get_favorite_creators()

    assert result == [{'id': '1'}, {'id': '2'}]


def test_get_favorite_creators_returns_empty_on_no_response(monkeypatch):
    monkeypatch.setattr(kemono, "call_api", lambda *a, **k: None)

    assert kemono.get_favorite_creators() == []


def test_get_favorite_creators_returns_empty_on_malformed_json(monkeypatch, caplog):
    monkeypatch.setattr(kemono, "call_api", lambda *a, **k: "not json")

    with caplog.at_level(logging.WARNING, logger="downloader"):
        result = kemono.get_favorite_creators()

    assert result == []
    assert "Failed decoding JSON" in caplog.text


def test_get_creator_data_happy_path(monkeypatch):
    monkeypatch.setattr(kemono, "call_api", lambda *a, **k: json.dumps({'name': 'Someone'}))

    assert kemono.get_creator_data('patreon', '123') == {'name': 'Someone'}


def test_get_creator_data_returns_empty_dict_on_failure(monkeypatch):
    monkeypatch.setattr(kemono, "call_api", lambda *a, **k: None)

    assert kemono.get_creator_data('patreon', '123') == {}


def test_get_post_data_happy_path(monkeypatch):
    monkeypatch.setattr(kemono, "call_api", lambda *a, **k: json.dumps({'post': {'content': 'x'}}))

    assert kemono.get_post_data('patreon', '123', 'p1') == {'post': {'content': 'x'}}


def test_get_post_by_file_hash_uses_search_hash_endpoint(monkeypatch):
    calls = []
    monkeypatch.setattr(kemono, "call_api", lambda *a, **k: calls.append(a) or json.dumps({'id': 'p1'}))

    result = kemono.get_post_by_file_hash('abc123')

    assert result == {'id': 'p1'}
    assert calls[0][0] == 'search_hash/abc123'


def test_get_file_data_uses_file_endpoint(monkeypatch):
    calls = []
    monkeypatch.setattr(kemono, "call_api", lambda *a, **k: calls.append(a) or json.dumps({'password': 'secret'}))

    result = kemono.get_file_data('abc123')

    assert result == {'password': 'secret'}
    assert calls[0][0] == 'file/abc123'


def test_get_file_data_logs_no_warning_on_missing_response(monkeypatch, caplog):
    # Deliberately silent (see the comment in core/kemono.py) - most files have no known
    # password, so this 404s constantly and a warning per file would be noise.
    monkeypatch.setattr(kemono, "call_api", lambda *a, **k: None)

    with caplog.at_level(logging.WARNING, logger="downloader"):
        result = kemono.get_file_data('abc123')

    assert result == {}
    assert caplog.text == ""


# --- get_all_posts_from_creator: URL-variant fallback, response shapes, pagination ---

def test_get_all_posts_tries_posts_suffix_first_and_stops_on_success(monkeypatch):
    calls = []

    def fake_call_api(url, *a, **k):
        calls.append(url)
        if url == "patreon/user/123/posts?o=0":
            return json.dumps([{'id': '1'}])
        return json.dumps([{'id': 'wrong'}])

    monkeypatch.setattr(kemono, "call_api", fake_call_api)

    result = kemono.get_all_posts_from_creator('patreon', '123', page_size=50)

    assert result == [{'id': '1'}]
    assert calls == ["patreon/user/123/posts?o=0"]  # only the first variant was tried


def test_get_all_posts_falls_back_through_url_variants_in_order(monkeypatch):
    calls = []

    def fake_call_api(url, *a, **k):
        calls.append(url)
        if url == "patreon/user/123?offset=0&limit=50":
            return json.dumps([{'id': '1'}])
        return None

    monkeypatch.setattr(kemono, "call_api", fake_call_api)

    result = kemono.get_all_posts_from_creator('patreon', '123', page_size=50)

    assert result == [{'id': '1'}]
    assert calls == [
        "patreon/user/123/posts?o=0",
        "patreon/user/123?o=0",
        "patreon/user/123?offset=0&limit=50",
    ]


def test_get_all_posts_stops_when_all_variants_fail(monkeypatch):
    monkeypatch.setattr(kemono, "call_api", lambda *a, **k: None)

    result = kemono.get_all_posts_from_creator('patreon', '123', page_size=50)

    assert result == []


@pytest.mark.parametrize("payload,expected", [
    ([{'id': '1'}], [{'id': '1'}]),
    ({'posts': [{'id': '1'}]}, [{'id': '1'}]),
    ({'data': [{'id': '1'}]}, [{'id': '1'}]),
])
def test_get_all_posts_handles_recognized_response_shapes(monkeypatch, payload, expected):
    monkeypatch.setattr(kemono, "call_api", lambda *a, **k: json.dumps(payload))

    result = kemono.get_all_posts_from_creator('patreon', '123', page_size=50)

    assert result == expected


def test_get_all_posts_stops_on_unrecognized_dict_shape(monkeypatch):
    monkeypatch.setattr(kemono, "call_api", lambda *a, **k: json.dumps({'unexpected': 'shape'}))

    result = kemono.get_all_posts_from_creator('patreon', '123', page_size=50)

    assert result == []


def test_get_all_posts_paginates_until_a_short_page(monkeypatch):
    monkeypatch.setattr(kemono.time, "sleep", lambda s: None)
    pages = {
        0: [{'id': '1'}, {'id': '2'}],
        2: [{'id': '3'}],  # shorter than page_size - should stop after this page
    }

    def fake_call_api(url, *a, **k):
        if not url.endswith('/posts?o=0') and not url.endswith('/posts?o=2'):
            return None
        offset = int(url.rsplit('=', 1)[1])
        return json.dumps(pages[offset])

    monkeypatch.setattr(kemono, "call_api", fake_call_api)

    result = kemono.get_all_posts_from_creator('patreon', '123', page_size=2)

    assert result == [{'id': '1'}, {'id': '2'}, {'id': '3'}]


def test_get_all_posts_stops_at_max_pages(monkeypatch):
    monkeypatch.setattr(kemono.time, "sleep", lambda s: None)
    call_count = {'n': 0}

    def fake_call_api(url, *a, **k):
        if '/posts?o=' not in url:
            return None
        call_count['n'] += 1
        return json.dumps([{'id': str(call_count['n'])}, {'id': 'pad'}])  # always a full page

    monkeypatch.setattr(kemono, "call_api", fake_call_api)

    result = kemono.get_all_posts_from_creator('patreon', '123', page_size=2, max_pages=3)

    assert len(result) == 6  # 3 pages * 2 posts, then stopped by max_pages
