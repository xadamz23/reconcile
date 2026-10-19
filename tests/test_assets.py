import pytest

from app.assets import Assets


def test_url_includes_content_hash_that_changes_with_content(tmp_path):
    (tmp_path / "css").mkdir()
    css = tmp_path / "css" / "app.css"
    css.write_text("body{}")
    first = Assets(tmp_path)
    css.write_text("body{color:red}")
    second = Assets(tmp_path)
    assert first.url("css/app.css").startswith("/static/css/app.css?v=")
    assert first.url("css/app.css") != second.url("css/app.css")
    assert first.version != second.version


def test_unknown_asset_raises(tmp_path):
    with pytest.raises(KeyError):
        Assets(tmp_path).url("nope.js")


def test_hashed_static_is_immutable(client):
    url = client.app.state.assets.url("css/app.css")
    r = client.get(url)
    assert r.status_code == 200
    assert "immutable" in r.headers["cache-control"]
