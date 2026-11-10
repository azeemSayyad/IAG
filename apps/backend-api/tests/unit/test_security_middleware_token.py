"""?token= (the login JWT on <audio>/<video>/download links) must never be
mistaken for SQL injection — base64url tokens can contain "--"."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security_middleware import SecurityMiddleware


def _client():
    app = FastAPI()
    app.add_middleware(SecurityMiddleware)

    @app.get("/r")
    def r():
        return {"ok": True}

    return TestClient(app)


def test_jwt_with_double_dash_passes():
    tok = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.ab--cd_EF-gh"
    assert _client().get("/r", params={"token": tok}).status_code == 200


def test_other_params_and_non_jwt_tokens_still_checked():
    c = _client()
    assert c.get("/r", params={"q": "a--b"}).status_code == 400
    assert c.get("/r", params={"token": "1; DROP TABLE users"}).status_code == 400
    assert c.get("/r", params={"token": "a.b.c<script>x</script>"}).status_code == 400
