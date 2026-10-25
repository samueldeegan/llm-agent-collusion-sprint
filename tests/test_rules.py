from src.procurement.rules import (cover_inflate, cover_shadow, cover_roles,
                                   rotation_roles, markup_bid)


def test_cover_inflate_rule():
    assert cover_inflate(997_500, 945_000, 1_000_000) == 1_097_500
    assert cover_inflate(800_000, 945_000, 1_000_000) == 955_000


def test_cover_shadow_rule():
    assert cover_shadow(945_000) == 982_800


def test_cover_roles_by_cost():
    assert cover_roles(100.0, 200.0, 2, 5) == (2, 5)
    assert cover_roles(300.0, 200.0, 2, 5) == (5, 2)
    assert cover_roles(200.0, 200.0, 2, 5) == (2, 5)


def test_rotation_rule():
    assert rotation_roles(1, 2, 5) == (2, 5)
    assert rotation_roles(2, 2, 5) == (5, 2)


def test_markup_rule():
    assert markup_bid(945_000, 1_000_000) == 1_045_000
