"""Allowlisted HTTP metrics; no traces, logs, payloads or ambient exporter config."""

import logging
import os
from urllib.parse import urlsplit

import requests
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import AlwaysOffExemplarFilter, Histogram, MeterProvider
from opentelemetry.sdk.metrics.export import AggregationTemporality, PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource

log = logging.getLogger("demo-chat.metrics")
NAME = "http.server.request.duration"
ATTRIBUTES = {"http.request.method", "http.response.status_code", "http.route"}
ROUTES = frozenset(
    {
        "/",
        "/api/info",
        "/healthz",
        "/readyz",
        "/api/health/db",
        "/api/messages",
        "/api/attachments/{attachment_id}",
        "/admin/write-block",
        "/admin/websockets/close",
    }
)
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
BOUNDARIES = (0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 10)


class FixedSession(requests.Session):
    """No redirect, netrc, proxy or environment CA override."""

    def __init__(self):
        super().__init__()
        self.trust_env = False

    def request(self, method, url, **kwargs):
        kwargs["allow_redirects"] = False
        kwargs["stream"] = True
        response = super().request(method, url, **kwargs)
        try:
            # Collector responses are untrusted too. Do not load an unbounded
            # response or allow a remote diagnostic body to enter SDK logs.
            raw = bytearray()
            for chunk in response.iter_content(4096):
                if len(raw) + len(chunk) > 65536:
                    raise requests.RequestException("metrics_response_too_large")
                raw.extend(chunk)
            response._content = bytes(raw) if 200 <= response.status_code < 300 else b""
            response._content_consumed = True
            response.reason = "metrics_collector_response"
            return response
        finally:
            response.close()


def validate_endpoint(endpoint):
    try:
        parsed = urlsplit(endpoint)
        valid = (
            type(endpoint) is str
            and len(endpoint) <= 2048
            and endpoint == endpoint.strip()
            and not any(ord(c) < 33 or ord(c) > 126 for c in endpoint)
            and parsed.hostname
            and parsed.port != 0
            and parsed.scheme in ("https", "http")
            and (parsed.scheme == "https" or parsed.hostname in ("127.0.0.1", "::1", "localhost"))
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and parsed.path == "/v1/metrics"
            and "@" not in parsed.netloc
            and "\\" not in endpoint
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("invalid_fixed_metrics_endpoint")
    return endpoint


class HttpMetrics:
    def __init__(self, readers):
        self.provider = MeterProvider(
            metric_readers=readers,
            resource=Resource({"service.name": "demo-chat"}),
            exemplar_filter=AlwaysOffExemplarFilter(),
            shutdown_on_exit=False,
            views=[
                View(
                    instrument_name=NAME,
                    attribute_keys=ATTRIBUTES,
                    aggregation=ExplicitBucketHistogramAggregation(BOUNDARIES),
                    aggregation_cardinality_limit=2048,
                )
            ],
        )
        self.histogram = self.provider.get_meter("bravo.demo-chat.http", "1").create_histogram(
            NAME, unit="s", description="App HTTP response duration; not cutover gate evidence"
        )

    @classmethod
    def from_env(cls):
        endpoint = os.environ.get("BRAVO_METRICS_ENDPOINT")
        if not endpoint:
            return None
        endpoint = validate_endpoint(endpoint)
        # Official exporter merges generic OTEL headers/TLS/credential providers.
        # Do not silently inherit unrelated credentials or exporter behavior.
        if any(k.startswith("OTEL_EXPORTER_OTLP") for k in os.environ):
            raise ValueError("ambient_otlp_exporter_configuration_not_allowed")
        cert = os.environ.get("BRAVO_METRICS_CA_FILE") or None
        client_cert = os.environ.get("BRAVO_METRICS_CLIENT_CERT") or None
        client_key = os.environ.get("BRAVO_METRICS_CLIENT_KEY") or None
        if bool(client_cert) != bool(client_key):
            raise ValueError("metrics_client_certificate_pair_required")
        for path in (cert, client_cert, client_key):
            if path is not None and (not os.path.isabs(path) or not os.path.isfile(path)):
                raise ValueError("metrics_certificate_file_required")
        exporter = OTLPMetricExporter(
            endpoint=endpoint,
            certificate_file=cert,
            client_certificate_file=client_cert,
            client_key_file=client_key,
            session=FixedSession(),
            timeout=2,
            compression=Compression.NoCompression,
            preferred_temporality={Histogram: AggregationTemporality.CUMULATIVE},
            preferred_aggregation={Histogram: ExplicitBucketHistogramAggregation(BOUNDARIES)},
            max_export_batch_size=2048,
            max_request_size=1024 * 1024,
        )
        return cls(
            [
                PeriodicExportingMetricReader(
                    exporter, export_interval_millis=10000, export_timeout_millis=2500
                )
            ]
        )

    def record(self, scope, status, duration):
        route = getattr(scope.get("route"), "path", None)
        method = scope.get("method")
        self.histogram.record(
            max(0, duration),
            {
                "http.request.method": method if method in METHODS else "_OTHER",
                "http.response.status_code": status
                if type(status) is int and 100 <= status <= 599
                else 500,
                "http.route": route if route in ROUTES else "unmatched",
            },
        )

    def close(self):
        try:
            self.provider.shutdown(timeout_millis=3000)
        except Exception:
            log.warning("metrics_shutdown_unconfirmed")
