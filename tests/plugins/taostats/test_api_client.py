import asyncio
import json
import time
from typing import Any, Dict, List

import pytest

import liveweb_arena.plugins.taostats.api_client as taostats_api
from liveweb_arena.plugins.base_client import APIFetchError


def _make_subnet_result(netuid: int, **overrides: Any) -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {
        "subnet_identities_v3": {"subnetName": f"SN{netuid}"},
        "token_symbol": f"SN{netuid}",
        "subnet_tao": str(5 * taostats_api.RAO_TO_TAO),
        "subnet_alpha_in": str(10 * taostats_api.RAO_TO_TAO),
        "subnet_volume": str(2 * taostats_api.RAO_TO_TAO),
        "subnet_tao_in_emission": str(1 * taostats_api.RAO_TO_TAO),
        "subnet_alpha_out": str(20 * taostats_api.RAO_TO_TAO),
        "price": "0.5",
        "subnet_owner": "owner-address",
        "dtao": {
            "taoLiquidity": str(3 * taostats_api.RAO_TO_TAO),
            "price_diff_hour": "0.1",
            "price_diff_day": "1.0",
            "price_diff_week": "5.0",
            "price_diff_month": "10.0",
        },
    }
    snapshot.update(overrides)
    return {"netuid": netuid, "latest_snapshot": snapshot}


def test_safe_float_edge_cases():
    assert taostats_api._safe_float(None) is None
    assert taostats_api._safe_float("") is None
    assert taostats_api._safe_float("not-a-number") is None
    assert taostats_api._safe_float(object()) is None
    assert taostats_api._safe_float(1) == 1.0
    assert taostats_api._safe_float("3.14") == pytest.approx(3.14)


def test_parse_subnet_data_full_snapshot():
    subnet = _make_subnet_result(27)
    parsed = taostats_api._parse_subnet_data(subnet)

    assert parsed["netuid"] == 27
    assert parsed["name"] == "SN27"
    assert parsed["owner"] == "owner-address"

    # RAO -> TAO conversions
    assert parsed["tao_in"] == pytest.approx(5.0)
    assert parsed["alpha_in"] == pytest.approx(10.0)
    assert parsed["volume_24h"] == pytest.approx(2.0)
    assert parsed["emission"] == pytest.approx(1.0)
    assert parsed["liquidity"] == pytest.approx(3.0)

    # Price and market cap (price * alpha_out)
    assert parsed["price"] == pytest.approx(0.5)
    assert parsed["market_cap"] == pytest.approx(0.5 * 20.0)

    # Price change fields come from dtao
    assert parsed["price_change_1h"] == pytest.approx(0.1)
    assert parsed["price_change_24h"] == pytest.approx(1.0)
    assert parsed["price_change_1w"] == pytest.approx(5.0)
    assert parsed["price_change_1m"] == pytest.approx(10.0)


def test_parse_subnet_data_missing_optional_fields():
    # Missing identities, dtao, and numeric snapshot entries
    subnet: Dict[str, Any] = {
        "netuid": 10,
        "latest_snapshot": {
            "token_symbol": "GT10",
            "subnet_tao": None,
            "subnet_alpha_in": "",
            "subnet_tao_in_emission": None,
            "subnet_alpha_out": None,
            "price": None,
        },
    }

    parsed = taostats_api._parse_subnet_data(subnet)
    assert parsed["name"] == "GT10"  # falls back to token_symbol
    assert parsed["tao_in"] is None
    assert parsed["alpha_in"] is None
    assert parsed["emission"] is None
    assert parsed["market_cap"] is None


def test_normalize_emission_converts_small_totals_to_percentages():
    original = {
        "1": {"emission": 1.0},
        "2": {"emission": 2.0},
        "3": {"emission": None},
    }

    result = taostats_api._normalize_emission(original)
    # Original object must not be mutated
    assert original["1"]["emission"] == 1.0
    assert original["2"]["emission"] == 2.0

    total = result["1"]["emission"] + result["2"]["emission"]
    assert pytest.approx(total, rel=1e-3) == 100.0
    assert result["3"]["emission"] is None


def test_normalize_emission_skips_zero_and_large_totals():
    # Empty input
    assert taostats_api._normalize_emission({}) == {}

    # All None should be returned as-is
    subnets_zero = {"1": {"emission": None}}
    assert taostats_api._normalize_emission(subnets_zero) is subnets_zero

    # Totals >= 50 are treated as already-percentages
    subnets_pct = {"1": {"emission": 60.0}, "2": {"emission": 40.0}}
    assert taostats_api._normalize_emission(subnets_pct) is subnets_pct


def test_filter_by_emission_keeps_top_half_and_handles_missing():
    subnets = {
        "1": {"emission": 10.0},
        "2": {"emission": None},  # treated as -1
        "3": {"emission": 5.0},
        "4": {"emission": 1.0},
    }

    filtered = taostats_api._filter_by_emission(subnets)
    # Top half of 4 entries -> 2
    assert set(filtered.keys()) == {"1", "3"}


def test_file_cache_round_trip_valid(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache-root"
    monkeypatch.setenv("LIVEWEB_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("LIVEWEB_CACHE_TTL", "3600")

    cache_file = taostats_api._get_file_cache_path()
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "subnets": {"1": {"name": "SN1"}},
        "_fetched_at": time.time(),
    }
    cache_file.write_text(json.dumps(payload))

    assert taostats_api._is_file_cache_valid() is True
    loaded = taostats_api._load_file_cache()
    assert loaded == payload["subnets"]


def test_file_cache_invalid_when_expired_or_corrupt(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache-expired"
    monkeypatch.setenv("LIVEWEB_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("LIVEWEB_CACHE_TTL", "1")

    # Expired cache
    cache_file = taostats_api._get_file_cache_path()
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    expired_payload = {
        "subnets": {"1": {"name": "SN1"}},
        "_fetched_at": time.time() - 10,
    }
    cache_file.write_text(json.dumps(expired_payload))
    assert taostats_api._is_file_cache_valid() is False
    assert taostats_api._load_file_cache() is None

    # Corrupt cache
    cache_file.write_text("{not valid json")
    assert taostats_api._is_file_cache_valid() is False
    assert taostats_api._load_file_cache() is None


def test_initialize_cache_prefers_existing_file_cache(tmp_path, monkeypatch):
    # Reset context cache
    taostats_api._subnet_cache.set(None)

    cache_dir = tmp_path / "cache-existing"
    monkeypatch.setenv("LIVEWEB_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("LIVEWEB_CACHE_TTL", "3600")

    cache_file = taostats_api._get_file_cache_path()
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    subnets = {
        "1": {"emission": 10.0},
        "2": {"emission": 5.0},
        "3": {"emission": 1.0},
    }
    cache_file.write_text(
        json.dumps({"subnets": subnets, "_fetched_at": time.time()})
    )

    # If initialize_cache hits the network, fail the test
    async def _fail_fetch_all_subnets() -> Dict[str, Any]:
        raise AssertionError("fetch_all_subnets should not be called when cache exists")

    monkeypatch.setattr(taostats_api, "fetch_all_subnets", _fail_fetch_all_subnets)

    taostats_api.initialize_cache()

    cached = taostats_api.get_cached_subnets()
    # After filtering, top half by emission should remain ("1" and "2")
    assert set(cached.keys()) == {"1", "2"}


def test_initialize_cache_fetches_and_writes_cache(tmp_path, monkeypatch):
    # Reset context cache
    taostats_api._subnet_cache.set(None)

    cache_dir = tmp_path / "cache-fetch"
    monkeypatch.setenv("LIVEWEB_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("LIVEWEB_CACHE_TTL", "3600")

    cache_file = taostats_api._get_file_cache_path()
    if cache_file.exists():
        cache_file.unlink()

    async def _fake_fetch_all_subnets() -> Dict[str, Any]:
        # Use 4 subnets so "top half" filtering keeps 2.
        return {
            "subnets": {
                "10": {"emission": 3.0},
                "20": {"emission": 7.0},
                "30": {"emission": 5.0},
                "40": {"emission": 1.0},
            }
        }

    monkeypatch.setattr(taostats_api, "fetch_all_subnets", _fake_fetch_all_subnets)

    taostats_api.initialize_cache()

    cached = taostats_api.get_cached_subnets()
    # Top half of 4 -> keep 2 highest emission: 20 (7.0) and 30 (5.0)
    assert set(cached.keys()) == {"20", "30"}

    assert cache_file.exists()
    raw = json.loads(cache_file.read_text())
    assert "subnets" in raw and raw["subnets"]


@pytest.mark.asyncio
async def test_fetch_all_subnets_retries_on_server_error_then_succeeds(monkeypatch):
    class _FakeResponse:
        def __init__(self, status: int, payload: Dict[str, Any]):
            self.status = status
            self._payload = payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return self._payload

        async def text(self):
            return json.dumps(self._payload)

    class _FakeSession:
        def __init__(self, responses: List[_FakeResponse]):
            self._responses = responses

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, *_args, **_kwargs):
            if not self._responses:
                raise AssertionError("No more fake responses")
            return self._responses.pop(0)

    # First call: 500 error; second call: success with one subnet
    responses = [
        _FakeResponse(500, {"results": []}),
        _FakeResponse(200, {"results": [_make_subnet_result(42)]}),
    ]

    def _fake_client_session(*_args, **_kwargs):
        return _FakeSession(responses)

    monkeypatch.setattr(taostats_api.aiohttp, "ClientSession", _fake_client_session)

    async def _noop_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(taostats_api.asyncio, "sleep", _noop_sleep)

    data = await taostats_api.fetch_all_subnets()
    assert "subnets" in data
    assert data["subnets"]["42"]["netuid"] == 42


@pytest.mark.asyncio
async def test_fetch_all_subnets_raises_when_no_results(monkeypatch):
    class _FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {"results": []}

        async def text(self):
            return ""

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, *_args, **_kwargs):
            return _FakeResponse()

    def _fake_client_session(*_args, **_kwargs):
        return _FakeSession()

    monkeypatch.setattr(taostats_api.aiohttp, "ClientSession", _fake_client_session)

    with pytest.raises(APIFetchError):
        await taostats_api.fetch_all_subnets()
