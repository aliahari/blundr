"""Separate queues per kind of card, and reviewing past the daily limit."""
from tests import test_blundr_features as _features
from tests.conftest import client

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



def test_review_more_keeps_the_limit_for_the_rest_of_the_session():
    import sqlite3

    from tests.test_blundr_features import _TEST_DB_PATH

    h, ids = _seed("queue_twice", max_new=1)
    uid = client.get("/api/auth/me", headers=h).json()["id"]
    con = sqlite3.connect(str(_TEST_DB_PATH))
    z = 990000 + uid
    con.execute(
        "INSERT INTO review_cards (user_id, kind, blunder_id, card_type, step, ease,"
        " interval_days, repetitions, lapses, due_at)"
        " VALUES (?, 'test', ?, 'line', 0, 2.5, 0.0, 0, 0, '2026-01-01')", (uid, z))
    con.commit()
    con.close()

    def review(cards):
        for c in cards:
            client.post(f"/api/reviews/{c['card_id']}", headers=h, json={"grade": "good"})

    review(_due(h, kind="test"))                     # today's 1 new item (X)
    more = _due(h, kind="test", extra_new=1)         # Review more: 1 more item (Y)
    assert len({c["card_id"] for c in more} & set(ids["Y"])) == 2
    review(more)
    # The queue empties and the app refreshes with the same allowance: nothing new comes in
    assert _due(h, kind="test", extra_new=1) == []
    # Only a second click (a bigger allowance) lets Z in
    assert len(_due(h, kind="test", extra_new=2)) == 1
