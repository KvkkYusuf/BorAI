"""Temel smoke testleri."""

import stock_analysis


def test_package_version() -> None:
    """Paket sürümünün tanımlı olduğunu doğrular."""
    assert stock_analysis.__version__ == "0.1.0"
