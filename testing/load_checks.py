"""Local integration qualification, not T36 policy or Edge measurement.

An eventual successful DB reconciliation or a clean final Prometheus window
cannot erase HTTP failures / missing windows earlier in a long run.
"""


def _zero(value):
    return type(value) in (int, float) and value == 0


def whole_run_checks(report, *, duration):
    if not isinstance(report, dict):
        report = {}
    requests = report.get("requests")
    requests = requests if isinstance(requests, dict) else {}
    windows = report.get("windows")
    windows = windows if isinstance(windows, dict) else {}
    counters_clean = True
    for method in ("write", "read"):
        stats = requests.get(method)
        if not isinstance(stats, dict):
            counters_clean = False
            continue
        counters_clean &= type(stats.get("attempts")) is int and stats["attempts"] > 0
        counters_clean &= all(
            _zero(stats.get(k)) for k in ("http_5xx", "http_4xx", "timeout", "connect")
        )
    warnings = report.get("warnings")
    return {
        "whole_run_http_clean": counters_clean
        and _zero(report.get("http_error_rate"))
        and _zero(report.get("contract_error_rate")),
        "whole_run_windows_complete": (
            type(windows.get("emitted")) is int
            and windows["emitted"] == duration // 10
            and windows.get("conserved") is True
            and all(
                _zero(windows.get(k))
                for k in (
                    "delivery_failed",
                    "invalid",
                    "tail_requests_not_in_a_window",
                    "late_requests",
                )
            )
            and windows.get("continuity_problems") == []
        ),
        "whole_run_generator_healthy": (
            isinstance(warnings, list)
            and all(isinstance(w, str) and w == "NOT_EDGE_MEASURED" for w in warnings)
        ),
    }
