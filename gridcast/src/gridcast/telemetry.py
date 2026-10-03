"""OpenTelemetry bootstrap.

Services export traces and metrics over OTLP/gRPC to the in-cluster collector, which fans out
to Tempo (traces) and Prometheus (metrics). Resource attributes follow the OpenTelemetry
semantic conventions (`service.*`, `k8s.*`, `deployment.environment.name`) so an external
observer can identify every signal without knowing GridCast internals.

Set `OTEL_SDK_DISABLED=true` (the default outside Kubernetes when no endpoint is configured)
to run with no-op providers, e.g. in unit tests.
"""

import logging
import os
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.sdk.resources import Resource

log = logging.getLogger(__name__)
_configured = False

# Explicit buckets for second-based latency histograms. (The SDK defaults assume milliseconds,
# which would put every sub-5-second duration into a single bucket.)
SECONDS_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0,
                   60.0, 120.0, 300.0)


def _resource(service: str, version: str, environment: str) -> Resource:
    attributes: dict[str, Any] = {
        "service.name": service,
        "service.version": version,
        "service.namespace": "gridcast",
        "deployment.environment.name": environment,
    }
    downward = {
        "k8s.pod.name": "K8S_POD_NAME",
        "k8s.namespace.name": "K8S_NAMESPACE",
        "k8s.node.name": "K8S_NODE_NAME",
        "k8s.deployment.name": "K8S_DEPLOYMENT_NAME",
        "service.instance.id": "K8S_POD_NAME",
    }
    for key, env in downward.items():
        if value := os.environ.get(env):
            attributes[key] = value
    return Resource.create(attributes)


def enabled() -> bool:
    if os.environ.get("OTEL_SDK_DISABLED", "").lower() == "true":
        return False
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))


def setup_telemetry(service: str, version: str, environment: str = "local") -> None:
    """Install global tracer/meter providers once per process."""
    global _configured
    if _configured or not enabled():
        return
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = _resource(service, version, environment)
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    interval = int(os.environ.get("OTEL_METRIC_EXPORT_INTERVAL", "15000"))
    reader = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=interval)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))

    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().instrument()
    _configured = True
    log.info("telemetry configured", extra={"otlp_endpoint": os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"]})


def instrument_fastapi(app: Any) -> None:
    if not enabled():
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")


def instrument_engine(engine: Any) -> None:
    if not enabled():
        return
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(engine=engine, enable_commenter=False)


def shutdown() -> None:
    """Flush pending spans/metrics; call before short-lived processes (jobs) exit."""
    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()
    meter_provider = metrics.get_meter_provider()
    if hasattr(meter_provider, "shutdown"):
        meter_provider.shutdown()
