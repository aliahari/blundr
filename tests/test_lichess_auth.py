"""Lichess sign-in helpers: token encryption and PKCE (no network)."""
from urllib.parse import parse_qs, urlparse

from app.services.lichess_auth import (
    authorize_url,
    challenge_for,
    decrypt_token,
    encrypt_token,
    new_pkce,
    sign_in_configured,
)


def test_token_round_trip():
    enc = encrypt_token("lip_abc")
    assert "lip_abc" not in enc
    assert decrypt_token(enc) == "lip_abc"


def test_decrypt_garbage_is_none():
    assert decrypt_token("nope") is None


def test_pkce_challenge_matches_rfc7636():
    assert (challenge_for("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
            == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")


def test_new_pkce_pairs_match():
    verifier, challenge = new_pkce()
    assert len(verifier) == 64
    assert challenge == challenge_for(verifier)


def test_authorize_url():
    url = urlparse(authorize_url("st4te", "ch4llenge"))
    assert (url.scheme, url.netloc, url.path) == ("https", "lichess.org", "/oauth")
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert q == {
        "response_type": "code",
        "client_id": "blundr.ch",
        "redirect_uri": "http://testserver/api/auth/lichess/callback",
        "code_challenge_method": "S256",
        "code_challenge": "ch4llenge",
        "state": "st4te",
    }


def test_sign_in_configured(monkeypatch):
    from app.config import settings
    assert sign_in_configured()
    monkeypatch.setattr(settings, "LICHESS_TOKEN_KEY", "")
    assert not sign_in_configured()
    monkeypatch.setattr(settings, "LICHESS_TOKEN_KEY", "not-a-key")
    assert not sign_in_configured()
