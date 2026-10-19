import pytest

FORM = {"account_name": "x", "opening": "10", "date": "2026-10-01"}


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
    ],
)
def test_cross_site_post_rejected(client, headers):
    r = client.post("/setup", data=FORM, headers=headers, follow_redirects=False)
    assert r.status_code == 403
    assert client.get("/", follow_redirects=False).headers["location"] == "/setup"  # nothing saved


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "same-origin"},
        {"Sec-Fetch-Site": "none"},
        {"Origin": "http://testserver"},
        {},  # non-browser clients send neither header
    ],
)
def test_same_origin_post_allowed(client, headers):
    r = client.post("/setup", data=FORM, headers=headers, follow_redirects=False)
    assert r.status_code == 303


def test_cross_site_get_still_allowed(client):
    assert client.get("/setup", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
