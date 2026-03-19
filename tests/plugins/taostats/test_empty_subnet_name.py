"""Tests for subnet name quality handling.

Verifies that subnet names are never empty, whitespace-only, or non-alphanumeric in:
1. api_client._parse_subnet_data (data normalization)
2. api_client._sanitize_subnet_names (file cache sanitization)
3. variables._is_usable_name (name quality gate)
4. variables._fetch_subnet_name (raises on empty)
5. variables._fetch_active_subnet_ids (filters unusable names)
6. analysis._get_subnet_list (question generation)
"""

import random
from unittest.mock import patch

import pytest

import liveweb_arena.plugins.taostats.api_client as taostats_api
from liveweb_arena.plugins.taostats.templates.variables import (
    _fetch_subnet_name,
    _fetch_active_subnet_ids,
    _is_usable_name,
    _subnet_ids_cache,
    _subnet_names_cache,
)
from liveweb_arena.plugins.taostats.templates.analysis import _get_subnet_list

# Patch target for get_cached_subnets (lazy import in variables.py)
_GCS_PATCH = "liveweb_arena.plugins.taostats.api_client.get_cached_subnets"


def _reset_caches():
    """Reset module-level caches between tests."""
    import liveweb_arena.plugins.taostats.templates.variables as v
    v._subnet_ids_cache = None
    v._subnet_names_cache.clear()


# ── _is_usable_name ──


@pytest.mark.parametrize("name,expected", [
    ("Coldint", True),
    ("SN42", True),
    ("OMEGA.inc: The Awakening", True),
    ("subnet 1", True),
    ("3Com", True),
    ("", False),
    ("   ", False),
    ("⚒", False),          # hammer symbol — real bug from SN86
    ("🔥", False),          # emoji only
    ("---", False),         # punctuation only
    ("...", False),
])
def test_is_usable_name(name, expected):
    assert _is_usable_name(name) == expected, f"_is_usable_name({name!r}) should be {expected}"


# ── api_client._parse_subnet_data ──


def _make_raw_subnet(netuid, subnet_name=None, token_symbol=None):
    """Build a raw API response with controlled name fields."""
    identities = {}
    if subnet_name is not None:
        identities["subnetName"] = subnet_name
    snapshot = {"subnet_identities_v3": identities}
    if token_symbol is not None:
        snapshot["token_symbol"] = token_symbol
    return {"netuid": netuid, "latest_snapshot": snapshot}


def test_parse_subnet_name_empty_string():
    parsed = taostats_api._parse_subnet_data(_make_raw_subnet(5, subnet_name=""))
    assert parsed["name"] == "SN5"


def test_parse_subnet_name_whitespace_only():
    parsed = taostats_api._parse_subnet_data(_make_raw_subnet(7, subnet_name="  "))
    assert parsed["name"] == "SN7"


def test_parse_subnet_name_none():
    parsed = taostats_api._parse_subnet_data(_make_raw_subnet(9, subnet_name=None))
    assert parsed["name"] == "SN9"


def test_parse_subnet_name_falls_to_token_symbol():
    parsed = taostats_api._parse_subnet_data(
        _make_raw_subnet(3, subnet_name="", token_symbol="ALPHA")
    )
    assert parsed["name"] == "ALPHA"


def test_parse_subnet_name_whitespace_token_symbol():
    parsed = taostats_api._parse_subnet_data(
        _make_raw_subnet(11, subnet_name=" ", token_symbol=" ")
    )
    assert parsed["name"] == "SN11"


def test_parse_subnet_name_valid():
    parsed = taostats_api._parse_subnet_data(
        _make_raw_subnet(1, subnet_name="Coldint")
    )
    assert parsed["name"] == "Coldint"


def test_parse_subnet_name_non_string_type():
    parsed = taostats_api._parse_subnet_data(
        _make_raw_subnet(8, subnet_name=12345)
    )
    assert parsed["name"] == "12345"


def test_parse_subnet_name_special_char():
    """API returns '⚒' as name — parse preserves it (filtering is downstream)."""
    parsed = taostats_api._parse_subnet_data(
        _make_raw_subnet(86, subnet_name="⚒")
    )
    assert parsed["name"] == "⚒"


def test_parse_never_produces_empty_name():
    """No combination of inputs produces an empty name."""
    cases = [
        {},
        {"latest_snapshot": None},
        {"latest_snapshot": {}},
        {"latest_snapshot": {"subnet_identities_v3": None}},
        {"latest_snapshot": {"subnet_identities_v3": {"subnetName": ""}}},
        {"latest_snapshot": {"subnet_identities_v3": {"subnetName": " "}}},
        {"latest_snapshot": {"subnet_identities_v3": {"subnetName": None}}},
        {"latest_snapshot": {"token_symbol": ""}},
        {"latest_snapshot": {"token_symbol": " "}},
        {"latest_snapshot": {"token_symbol": None}},
    ]
    for i, raw in enumerate(cases):
        raw.setdefault("netuid", i + 100)
        parsed = taostats_api._parse_subnet_data(raw)
        assert parsed["name"].strip(), (
            f"Case {i} produced empty name: raw={raw}, parsed_name={parsed['name']!r}"
        )


# ── api_client._sanitize_subnet_names ──


def test_sanitize_fixes_empty_names():
    subnets = {
        "1": {"name": "Valid"},
        "2": {"name": ""},
        "3": {"name": "   "},
        "4": {"name": None},
    }
    result = taostats_api._sanitize_subnet_names(subnets)
    assert result["1"]["name"] == "Valid"
    assert result["2"]["name"] == "SN2"
    assert result["3"]["name"] == "SN3"
    assert result["4"]["name"] == "SN4"


# ── variables._fetch_active_subnet_ids (filters unusable names) ──


def test_fetch_active_subnet_ids_filters_special_chars():
    """Subnets with non-alphanumeric-only names must be excluded."""
    _reset_caches()
    fake_subnets = {
        "0": {"name": "Root"},      # root network, always excluded
        "1": {"name": "Alpha"},     # valid
        "2": {"name": "Beta"},      # valid
        "86": {"name": "⚒"},       # special char only — must be filtered
        "99": {"name": ""},         # empty — must be filtered
    }
    with patch(_GCS_PATCH, return_value=fake_subnets):
        ids = _fetch_active_subnet_ids()
    assert sorted(ids) == [1, 2]
    _reset_caches()


def test_fetch_active_subnet_ids_keeps_alphanumeric_with_special():
    """Names with mixed alphanumeric + special chars should be kept."""
    _reset_caches()
    fake_subnets = {
        "1": {"name": "OMEGA.inc: The Awakening"},
        "2": {"name": "SN42"},
        "3": {"name": "3-Com"},
    }
    with patch(_GCS_PATCH, return_value=fake_subnets):
        ids = _fetch_active_subnet_ids()
    assert sorted(ids) == [1, 2, 3]
    _reset_caches()


# ── variables._fetch_subnet_name ──


def test_fetch_subnet_name_valid_cached():
    _reset_caches()
    fake_subnets = {"10": {"name": "LUCID"}}
    with patch(_GCS_PATCH, return_value=fake_subnets):
        name = _fetch_subnet_name(10)
    assert name == "LUCID"
    assert _subnet_names_cache[10] == "LUCID"
    _reset_caches()


def test_fetch_subnet_name_empty_raises():
    _reset_caches()
    fake_subnets = {"42": {"name": ""}}
    with patch(_GCS_PATCH, return_value=fake_subnets):
        with pytest.raises(RuntimeError, match="empty name"):
            _fetch_subnet_name(42)
    _reset_caches()


def test_fetch_subnet_name_whitespace_raises():
    _reset_caches()
    fake_subnets = {"99": {"name": "   "}}
    with patch(_GCS_PATCH, return_value=fake_subnets):
        with pytest.raises(RuntimeError, match="empty name"):
            _fetch_subnet_name(99)
    _reset_caches()


# ── analysis._get_subnet_list ──


def test_get_subnet_list_valid_names():
    _reset_caches()
    fake_subnets = {
        "1": {"name": "Alpha"},
        "2": {"name": "Beta"},
        "3": {"name": "Gamma"},
    }
    with patch(
        "liveweb_arena.plugins.taostats.templates.analysis._fetch_active_subnet_ids",
        return_value=[1, 2, 3],
    ), patch(_GCS_PATCH, return_value=fake_subnets):
        rng = random.Random(42)
        result = _get_subnet_list(rng, 3)

    names = [name for _, name in result]
    for name in names:
        assert name.strip(), f"Got empty name in subnet list: {names}"
        assert any(c.isalnum() for c in name), f"Got non-alphanumeric name: {name!r}"
    _reset_caches()


def test_get_subnet_list_empty_name_raises():
    _reset_caches()
    fake_subnets = {
        "1": {"name": "Alpha"},
        "2": {"name": ""},
    }
    with patch(
        "liveweb_arena.plugins.taostats.templates.analysis._fetch_active_subnet_ids",
        return_value=[1, 2],
    ), patch(_GCS_PATCH, return_value=fake_subnets):
        rng = random.Random(42)
        with pytest.raises(RuntimeError, match="empty name"):
            _get_subnet_list(rng, 2)
    _reset_caches()
