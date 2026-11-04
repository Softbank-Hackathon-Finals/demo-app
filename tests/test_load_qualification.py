from copy import deepcopy

import pytest

from testing.load_checks import whole_run_checks


def report():
    return {
        "verdict": "PASS",
        "warnings": ["NOT_EDGE_MEASURED"],
        "http_error_rate": 0.0,
        "contract_error_rate": 0.0,
        "requests": {
            side: {
                "attempts": 1500,
                "http_5xx": 0,
                "http_4xx": 0,
                "timeout": 0,
                "connect": 0,
            }
            for side in ("write", "read")
        },
        "windows": {
            "emitted": 3,
            "conserved": True,
            "delivery_failed": 0,
            "invalid": 0,
            "tail_requests_not_in_a_window": 0,
            "late_requests": 0,
            "continuity_problems": [],
        },
    }


def test_healthy_local_run_is_not_claimed_to_be_edge_measurement():
    value = report()
    assert all(whole_run_checks(value, duration=30).values())
    assert value["warnings"] == ["NOT_EDGE_MEASURED"]


def test_eventual_db_pass_does_not_erase_prior_http_errors():
    value = report()
    value["requests"]["write"]["http_5xx"] = 1
    assert value["verdict"] == "PASS"
    assert whole_run_checks(value, duration=30)["whole_run_http_clean"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("delivery_failed", 1),
        ("emitted", 2),
        ("conserved", False),
        ("invalid", 1),
        ("tail_requests_not_in_a_window", 2),
        ("late_requests", 1),
        ("continuity_problems", ["gap"]),
    ],
)
def test_latest_received_window_does_not_prove_full_stream(field, value):
    data = report()
    data["windows"][field] = value
    assert whole_run_checks(data, duration=30)["whole_run_windows_complete"] is False


@pytest.mark.parametrize(
    "warning", ["GENERATOR_LAGGING", "WINDOW_DELIVERY_FAILED", "HTTP_ERRORS", "unknown-warning"]
)
def test_generator_warning_is_not_silently_qualified(warning):
    value = report()
    value["warnings"].append(warning)
    assert whole_run_checks(value, duration=30)["whole_run_generator_healthy"] is False


@pytest.mark.parametrize(
    "invalid", [None, {}, {"requests": None, "windows": None}, {"requests": [], "windows": []}]
)
def test_missing_data_is_not_a_clean_trial(invalid):
    assert not all(whole_run_checks(invalid, duration=30).values())


def test_false_is_not_a_numeric_zero_counter():
    value = deepcopy(report())
    value["requests"]["read"]["timeout"] = False
    assert whole_run_checks(value, duration=30)["whole_run_http_clean"] is False
