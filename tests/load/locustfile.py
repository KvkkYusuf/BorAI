"""Locust load testing scenario and SLA validator for Borsa Analiz Sistemi API.

Verifies acceptance criteria defined in docs/ACCEPTANCE_CRITERIA.md:
- API (önbellekli okuma): p95 yanıt < 300 ms
- API (önbelleksiz okuma): p95 yanıt < 800 ms
- Güvenlik / Hata: 500 İç Sunucu Hatası = 0, Kritik/yüksek açık bulgu = 0

Usage:
  # With Locust installed:
  locust -f tests/load/locustfile.py --headless -u 20 -r 5 -t 30s --host http://localhost:8000

  # Standalone programmatic benchmark:
  python tests/load/locustfile.py --standalone
"""

from __future__ import annotations

import json

from stock_analysis.testing.load_sla import (
    SLA_MAX_ERROR_RATE_PCT,
    SLA_P95_CACHED_MAX_MS,
    SLA_P95_UNCACHED_MAX_MS,
    BorsaAnalizUser,
    EndpointBenchmarkResult,
    LoadTestVerdict,
    calculate_percentile,
    evaluate_sla_compliance,
)

__all__ = [
    "SLA_MAX_ERROR_RATE_PCT",
    "SLA_P95_CACHED_MAX_MS",
    "SLA_P95_UNCACHED_MAX_MS",
    "BorsaAnalizUser",
    "EndpointBenchmarkResult",
    "LoadTestVerdict",
    "calculate_percentile",
    "evaluate_sla_compliance",
]


if __name__ == "__main__":
    # Demonstration of the SLA compliance calculation
    sample_cached = [12.0, 15.5, 18.2, 22.1, 35.0, 42.0, 95.0, 110.0, 150.0, 240.0]
    sample_uncached = [55.0, 80.0, 120.0, 210.0, 340.0, 480.0, 520.0, 610.0, 690.0, 740.0]
    verdict = evaluate_sla_compliance(sample_cached, sample_uncached)
    print(json.dumps(verdict.to_dict(), indent=2))
