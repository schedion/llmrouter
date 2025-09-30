"""Prometheus metrics instrumentation and local dashboard."""

from __future__ import annotations

import html
from collections import defaultdict
from time import perf_counter
from typing import Dict, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

# Track request totals per method/path/status for Prometheus scraping.
REQUEST_COUNTER = Counter(
    "llmrouter_requests_total",
    "Total number of HTTP requests processed",
    labelnames=("method", "path", "status"),
)

# Observe request latency in seconds per method/path.
REQUEST_LATENCY = Histogram(
    "llmrouter_request_latency_seconds",
    "HTTP request latency in seconds",
    labelnames=("method", "path"),
)

PROVIDER_REQUEST_COUNTER = Counter(
    "llmrouter_provider_requests_total",
    "Total number of upstream provider calls",
    labelnames=("provider", "status"),
)

PROVIDER_LATENCY = Histogram(
    "llmrouter_provider_latency_seconds",
    "Latency of upstream provider calls",
    labelnames=("provider",),
)

EXCLUDED_PATHS = {"/metrics", "/metrics/dashboard"}


async def metrics_middleware(request: Request, call_next):
    """Middleware that records request metrics and latency."""

    if request.url.path in EXCLUDED_PATHS:
        return await call_next(request)

    method = request.method
    path = request.url.path
    start = perf_counter()
    response = None
    status_code = 500

    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception:
        status_code = 500
        raise
    finally:
        duration = perf_counter() - start
        REQUEST_COUNTER.labels(method=method, path=path, status=str(status_code)).inc()
        REQUEST_LATENCY.labels(method=method, path=path).observe(duration)


router = APIRouter()


def record_provider_http(provider: str, status_code: int, duration: float) -> None:
    """Record the outcome of an upstream provider HTTP request."""

    PROVIDER_REQUEST_COUNTER.labels(provider=provider, status=str(status_code)).inc()
    PROVIDER_LATENCY.labels(provider=provider).observe(duration)


def record_provider_error(provider: str, error: str) -> None:
    """Track provider errors that occur before an HTTP response is received."""

    PROVIDER_REQUEST_COUNTER.labels(provider=provider, status="error").inc()


@router.get("/metrics")
async def metrics_endpoint() -> Response:
    """Expose Prometheus metrics for scraping."""

    data = generate_latest()  # type: ignore[arg-type]
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


@router.get("/metrics/dashboard", response_class=HTMLResponse)
async def metrics_dashboard() -> HTMLResponse:
    """Simple HTML dashboard summarising API and provider metrics."""

    stats: Dict[Tuple[str, str], Dict[str, float]] = defaultdict(lambda: {
        "total": 0.0,
        "success": 0.0,
        "client_error": 0.0,
        "server_error": 0.0,
        "latency_sum": 0.0,
        "latency_count": 0.0,
    })

    provider_stats: Dict[str, Dict[str, float]] = defaultdict(lambda: {
        "total": 0.0,
        "success": 0.0,
        "client_error": 0.0,
        "server_error": 0.0,
        "error": 0.0,
        "latency_sum": 0.0,
        "latency_count": 0.0,
    })

    # Aggregate counts grouped by status class.
    for metric in REQUEST_COUNTER.collect():
        for sample in metric.samples:
            if sample.name != "llmrouter_requests_total":
                continue
            method = sample.labels.get("method", "")
            path = sample.labels.get("path", "")
            status = sample.labels.get("status", "0")
            entry = stats[(method, path)]
            entry["total"] += sample.value
            status_code = int(status)
            if 200 <= status_code < 300:
                entry["success"] += sample.value
            elif 400 <= status_code < 500:
                entry["client_error"] += sample.value
            elif status_code >= 500:
                    entry["server_error"] += sample.value

    # Merge latency data.
    for metric in REQUEST_LATENCY.collect():
        for sample in metric.samples:
            name = sample.name
            if name.endswith("_sum"):
                method = sample.labels.get("method", "")
                path = sample.labels.get("path", "")
                stats[(method, path)]["latency_sum"] = sample.value
            elif name.endswith("_count"):
                method = sample.labels.get("method", "")
                path = sample.labels.get("path", "")
                stats[(method, path)]["latency_count"] = sample.value

    # Provider counters by status.
    for metric in PROVIDER_REQUEST_COUNTER.collect():
        for sample in metric.samples:
            if sample.name != "llmrouter_provider_requests_total":
                continue
            provider = sample.labels.get("provider", "")
            status = sample.labels.get("status", "")
            entry = provider_stats[provider]
            entry["total"] += sample.value
            if status == "error":
                entry["error"] += sample.value
            else:
                try:
                    status_code = int(status)
                except ValueError:
                    continue
                if 200 <= status_code < 300:
                    entry["success"] += sample.value
                elif 400 <= status_code < 500:
                    entry["client_error"] += sample.value
                elif status_code >= 500:
                    entry["server_error"] += sample.value

    for metric in PROVIDER_LATENCY.collect():
        for sample in metric.samples:
            name = sample.name
            provider = sample.labels.get("provider", "")
            if name.endswith("_sum"):
                provider_stats[provider]["latency_sum"] = sample.value
            elif name.endswith("_count"):
                provider_stats[provider]["latency_count"] = sample.value

    rows = []
    for (method, path), entry in sorted(stats.items()):
        count = entry["total"]
        avg_latency_ms = (
            (entry["latency_sum"] / entry["latency_count"] * 1000.0)
            if entry["latency_count"]
            else 0.0
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(method)}</td>"
            f"<td>{html.escape(path)}</td>"
            f"<td>{int(count)}</td>"
            f"<td>{int(entry['success'])}</td>"
            f"<td>{int(entry['client_error'])}</td>"
            f"<td>{int(entry['server_error'])}</td>"
            f"<td>{avg_latency_ms:.2f}</td>"
            "</tr>"
        )

    if not rows:
        rows.append(
            "<tr><td colspan=7 style='text-align:center;'>No requests recorded yet</td></tr>"
        )

    provider_rows = []
    for provider, entry in sorted(provider_stats.items()):
        total = entry["total"]
        avg_latency_ms = (
            (entry["latency_sum"] / entry["latency_count"] * 1000.0)
            if entry["latency_count"]
            else 0.0
        )
        provider_rows.append(
            "<tr>"
            f"<td>{html.escape(provider)}</td>"
            f"<td>{int(total)}</td>"
            f"<td>{int(entry['success'])}</td>"
            f"<td>{int(entry['client_error'])}</td>"
            f"<td>{int(entry['server_error'])}</td>"
            f"<td>{int(entry['error'])}</td>"
            f"<td>{avg_latency_ms:.2f}</td>"
            "</tr>"
        )

    if not provider_rows:
        provider_rows.append(
            "<tr><td colspan=7 style='text-align:center;'>No provider calls recorded yet</td></tr>"
        )

    html_body = f"""
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <title>llmrouter Metrics</title>
        <style>
          body {{ font-family: Arial, sans-serif; margin: 2rem; }}
          table {{ border-collapse: collapse; width: 100%; max-width: 960px; }}
          th, td {{ border: 1px solid #ddd; padding: 0.5rem; text-align: left; }}
          th {{ background-color: #f4f4f4; }}
        </style>
      </head>
      <body>
        <h1>llmrouter Request Metrics</h1>
        <p><a href="/metrics">Prometheus metrics</a></p>
        <table>
          <thead>
            <tr>
              <th>Method</th>
              <th>Path</th>
              <th>Total</th>
              <th>2xx</th>
              <th>4xx</th>
              <th>5xx</th>
              <th>Avg Latency (ms)</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows)}
          </tbody>
        </table>
        <h2>Provider Calls</h2>
        <table>
          <thead>
            <tr>
              <th>Provider</th>
              <th>Total</th>
              <th>2xx</th>
              <th>4xx</th>
              <th>5xx</th>
              <th>Errors</th>
              <th>Avg Latency (ms)</th>
            </tr>
          </thead>
          <tbody>
            {''.join(provider_rows)}
          </tbody>
        </table>
      </body>
    </html>
    """
    return HTMLResponse(content=html_body)
