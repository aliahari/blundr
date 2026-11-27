"""Separate queues per kind of card, and reviewing past the daily limit."""
from tests.conftest import client
from tests import test_blundr_features as _features

_seed = _features.TestMixedKindQueue()._seed


def _due(h, **params):
    return client.get("/api/reviews/due", headers=h, params=params).json()


def test_kind_filter_serves_only_that_kind():
    h, ids = _seed("queue_kind", max_new=10)
    assert sorted(c["card_id"] for c in _due(h, kind="test")) == sorted(ids["X"] + ids["Y"])
    assert sorted(c["card_id"] for c in _due(h, kind="blunder")) == sorted(ids["A"] + ids["B"])
    # Several kinds at once (Learn's openings tab asks for opening and punish cards)
    assert len(_due(h, kind=["blunder", "test"])) == 8


def test_each_kind_has_its_own_daily_limit():
    h, ids = _seed("queue_quota", max_new=1)
    for card_id in ids["A"]:  # start blunder A today
        assert client.post(f"/api/reviews/{card_id}", headers=h, json={"grade": "good"}).status_code == 200
    # Today's one new blunder is used up, but the other kind still gets its own
    assert _due(h, kind="blunder") == []
    assert sorted(c["card_id"] for c in _due(h, kind="test")) == sorted(ids["X"])


def test_review_more_goes_past_the_limit():
    h, ids = _seed("queue_more", max_new=1)
    assert sorted(c["card_id"] for c in _due(h, kind="blunder")) == sorted(ids["A"])
    more = _due(h, kind="blunder", extra_new=1)
    assert sorted(c["card_id"] for c in more) == sorted(ids["A"] + ids["B"])

