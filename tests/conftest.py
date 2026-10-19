import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import create_app


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "test.db")
    with TestClient(app) as c:
        yield c
