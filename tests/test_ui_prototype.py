"""Unit and integration tests for responsive UI prototype and UX contracts."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from stock_analysis.api.app import create_app
from stock_analysis.api.schemas import DISCLAIMER_TEXT


@pytest.fixture
def test_app() -> FastAPI:
    return create_app()


@pytest.mark.asyncio
async def test_root_and_app_endpoints_serve_html(test_app: FastAPI) -> None:
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Test Root /
        response = await client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "Borsa Analiz Sistemi" in response.text

        # Verify Mandatory Disclaimer Header and Security Headers
        assert response.headers.get("X-Disclaimer") == DISCLAIMER_TEXT
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"

        # Test /app endpoint
        app_resp = await client.get("/app")
        assert app_resp.status_code == 200
        assert "text/html" in app_resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_static_assets_serving(test_app: FastAPI) -> None:
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for path in [
            "/static/css/tokens.css",
            "/static/css/layout.css",
            "/static/css/components.css",
            "/static/js/app.js",
        ]:
            resp = await client.get(path)
            assert resp.status_code == 200, f"Static asset {path} failed with {resp.status_code}"
            assert len(resp.content) > 0


@pytest.mark.asyncio
async def test_prototype_contains_all_nine_recommended_screens(test_app: FastAPI) -> None:
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/")
        assert response.status_code == 200
        html = response.text

        expected_screens = [
            'id="screen-dashboard"',  # 1. Pano
            'id="screen-screener"',  # 2. Hisse keşfet
            'id="screen-detail"',  # 3. Hisse detay
            'id="screen-analysis"',  # 4. Analiz & Model
            'id="screen-alerts"',  # 5. Uyarılar
            'id="screen-watchlists"',  # 6. İzleme listeleri
            'id="screen-settings"',  # 7. Ayarlar
            'id="screen-legal"',  # 8. Yasal ve şeffaflık
            'id="screen-admin"',  # 9. Yönetim paneli
        ]

        for screen_id in expected_screens:
            assert screen_id in html, f"Screen element {screen_id} not found in prototype HTML."


@pytest.mark.asyncio
async def test_prototype_responsive_navigation_and_accessibility(test_app: FastAPI) -> None:
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/")
        html = response.text

        # Language and Skip Link
        assert '<html lang="tr"' in html
        assert 'class="skip-link"' in html

        # Desktop sidebar navigation
        assert 'class="app-sidebar"' in html
        assert 'data-screen="dashboard"' in html
        assert 'data-screen="screener"' in html

        # Mobile bottom navigation bar and menu button
        assert 'class="mobile-bottom-nav"' in html
        assert 'id="btn-mobile-menu"' in html

        # Mandatory disclaimer banner
        assert 'class="disclaimer-banner"' in html
        assert "Yatırım Danışmanlığı Kapsamında Değildir" in html or "YASAL UYARI:" in html

        # Regional localization
        assert "Europe/Istanbul" in html
        assert "TRY" in html or "₺" in html

        # Ensure no passwords or secrets are leaked in the admin view
        assert "password" not in html.lower() or 'type="password"' not in html
        assert "secret_key" not in html.lower()


@pytest.mark.asyncio
async def test_prototype_four_primary_actions(test_app: FastAPI) -> None:
    """FAZ-08: En önemli eylemleri netleştir (ara, izle, grafik aç, uyarı kur)."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Check HTML structure
        response = await client.get("/")
        assert response.status_code == 200
        html = response.text

        # Quick Actions Bar
        assert 'class="quick-actions-bar"' in html
        assert 'role="toolbar"' in html
        assert 'aria-label="Temel Finansal Eylemler"' in html

        # 1. Ara (Search)
        assert 'id="btn-action-search"' in html
        assert "Alt+S" in html
        assert 'aria-keyshortcuts="Alt+S"' in html

        # 2. İzle (Watchlist)
        assert 'id="btn-action-watch"' in html
        assert "Alt+W" in html
        assert 'aria-keyshortcuts="Alt+W"' in html

        # 3. Grafik Aç (Chart)
        assert 'id="btn-action-chart"' in html
        assert "Alt+G" in html
        assert 'aria-keyshortcuts="Alt+G"' in html

        # 4. Uyarı Kur (Alert)
        assert 'id="btn-action-alert"' in html
        assert "Alt+A" in html
        assert 'aria-keyshortcuts="Alt+A"' in html

        # Detail screen dedicated action buttons
        assert 'id="btn-detail-watch"' in html
        assert 'id="btn-detail-chart"' in html
        assert 'id="btn-detail-alert"' in html

        # Screener table quick action column
        assert "Hızlı Eylemler" in html

        # Check JS implementation
        js_resp = await client.get("/static/js/app.js")
        assert js_resp.status_code == 200
        js_code = js_resp.text

        assert "triggerAction" in js_code
        assert "toggleWatchlist" in js_code
        assert "updateWatchlistUI" in js_code
        assert 'triggerAction("search")' in js_code
        assert 'triggerAction("watch")' in js_code
        assert 'triggerAction("chart")' in js_code
        assert 'triggerAction("alert")' in js_code


@pytest.mark.asyncio
async def test_prototype_accessibility_color_not_sole_indicator(test_app: FastAPI) -> None:
    """FAZ-08: Renkleri tek başına anlam göstergesi yapma;

    Artış/azalış yanında ikon ve metin kullan (WCAG 1.4.1).
    """
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. HTML Verification
        response = await client.get("/")
        assert response.status_code == 200
        html = response.text

        # Ticker cards must have icons, trend text, and aria-labels
        assert 'aria-label="Artış: +1,42%"' in html
        assert 'aria-label="Azalış: -0,48%"' in html
        assert '<span class="trend-text">Artış</span>' in html
        assert '<span class="trend-text">Azalış</span>' in html
        assert '<span aria-hidden="true">▲</span>' in html
        assert '<span aria-hidden="true">▼</span>' in html

        # Spotlight & Watchlist badges must have trend text & icons
        assert 'class="badge badge-bull" aria-label="Artış: +2,46%"' in html
        assert 'class="badge badge-bear" aria-label="Azalış: -0,67%"' in html

        # Indicator badges must have icons and descriptive labels
        assert 'aria-label="Eğilim: Yükseliş"' in html
        assert 'aria-label="RSI Pozitif"' in html
        assert 'aria-label="Bollinger Nötr"' in html
        assert '<span aria-hidden="true">▬</span>' in html

        # 2. CSS Verification
        css_resp = await client.get("/static/css/components.css")
        assert css_resp.status_code == 200
        css = css_resp.text
        assert ".trend-text" in css
        assert ".sr-only" in css
        assert ".badge-bull" in css
        assert ".badge-bear" in css
        assert ".badge-neutral" in css

        # 3. JS Dynamic Rendering Verification
        js_resp = await client.get("/static/js/app.js")
        assert js_resp.status_code == 200
        js = js_resp.text
        assert "trend-text" in js
        assert "Artış" in js
        assert "Azalış" in js
        assert "aria-label" in js


@pytest.mark.asyncio
async def test_prototype_chart_zoom_timeframe_cursor_volume_and_gap(test_app: FastAPI) -> None:
    """FAZ-08: Grafiklerde yakınlaştırma, dönem seçimi, imleç bilgisi, hacim ve veri boşluğu.

    Veri sürekliliği ve katman kontrolleri.
    """
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. HTML Verification
        response = await client.get("/")
        assert response.status_code == 200
        html = response.text

        # Timeframe selector group & buttons
        assert 'class="timeframe-group"' in html
        for tf in ["1G", "1H", "1A", "3A", "1Y", "ALL"]:
            assert f'data-timeframe="{tf}"' in html

        # Zoom Controls
        assert 'id="btn-chart-zoom-in"' in html
        assert 'id="btn-chart-zoom-out"' in html
        assert 'id="btn-chart-zoom-reset"' in html
        assert 'id="chart-zoom-level"' in html
        assert "Zoom: %100" in html

        # Layer toggles
        assert 'id="btn-chart-gap-toggle"' in html
        assert 'id="btn-chart-vol-toggle"' in html
        assert 'id="btn-chart-sma-toggle"' in html
        assert 'id="chart-data-gap-indicator"' in html

        # Dynamic Cursor HUD Strip
        assert 'id="chart-cursor-hud"' in html
        assert 'id="hud-date"' in html
        assert 'id="hud-open"' in html
        assert 'id="hud-high"' in html
        assert 'id="hud-low"' in html
        assert 'id="hud-close"' in html
        assert 'id="hud-volume"' in html
        assert 'id="hud-change"' in html
        assert 'id="hud-gap-notice"' in html

        # 2. CSS Verification
        css_resp = await client.get("/static/css/components.css")
        assert css_resp.status_code == 200
        css = css_resp.text
        assert ".chart-controls-bar" in css
        assert ".chart-zoom-group" in css
        assert ".chart-zoom-btn" in css
        assert ".chart-zoom-level" in css
        assert ".chart-hud" in css
        assert ".chart-hud-item" in css
        assert ".chart-data-gap-badge" in css

        # 3. JS Implementation Verification
        js_resp = await client.get("/static/js/app.js")
        assert js_resp.status_code == 200
        js = js_resp.text

        # State fields
        assert "chartZoom" in js
        assert "showGaps" in js
        assert "showVolume" in js

        # Interactive functions & SVG features
        assert "zoomChart" in js
        assert "resetZoom" in js
        assert "toggleDataGaps" in js
        assert "toggleVolume" in js
        assert "toggleSMA" in js
        assert "updateChartHUD" in js
        assert "gapHatchPattern" in js
        assert "chart-data-gap" in js
        assert "chart-crosshair-group" in js
        assert "crosshair-v" in js
        assert "crosshair-h" in js
        assert "chart-hover-overlay" in js

        # BoraApp exports
        expected_fns = [
            "zoomChart",
            "resetZoom",
            "toggleDataGaps",
            "toggleVolume",
            "toggleSMA",
            "renderDetailChart",
        ]
        for fn in expected_fns:
            assert fn in js


@pytest.mark.asyncio
async def test_prototype_delayed_and_stale_data_warning(test_app: FastAPI) -> None:
    """FAZ-08 UX: Veri gecikmişse açık bir uyarı göster; eski fiyatı güncelmiş gibi sunma."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. HTML Verification
        response = await client.get("/")
        assert response.status_code == 200
        html = response.text

        # Explicit stale data warning banner in Stock Detail
        assert 'id="detail-stale-banner"' in html
        assert 'id="stale-banner-title"' in html
        assert 'id="detail-last-update"' in html
        assert 'id="detail-latency-badge"' in html
        assert 'id="detail-latency-text"' in html

        # Explicit disclaimers: delayed prices must not be treated as real-time market prices
        assert "15 Dakika Gecikmelidir" in html
        assert "Eski veya gecikmeli fiyatlar anlık piyasa fiyatı yerine geçmez" in html

        # Header and Sidebar latency status indicators
        assert 'id="btn-header-latency"' in html
        assert 'id="header-latency-label"' in html
        assert 'id="sidebar-latency-badge"' in html
        assert 'id="sidebar-latency-status"' in html

        # 2. CSS Verification
        css_resp = await client.get("/static/css/components.css")
        assert css_resp.status_code == 200
        css = css_resp.text
        assert ".stale-data-alert" in css
        assert ".stale-mode-delayed" in css
        assert ".stale-mode-stale" in css
        assert ".stale-mode-realtime" in css
        assert ".pulse-dot.delayed" in css
        assert ".pulse-dot.stale" in css
        assert ".header-latency-pill" in css

        # 3. JS Implementation Verification
        js_resp = await client.get("/static/js/app.js")
        assert js_resp.status_code == 200
        js = js_resp.text

        # State fields & modes
        assert "dataLatency" in js
        assert "setDataLatencyMode" in js
        assert "toggleDataLatencyMode" in js
        assert "delayed" in js
        assert "stale" in js
        assert "realtime" in js

        # Window exports
        assert "setDataLatencyMode" in js
        assert "toggleDataLatencyMode" in js
        assert "getDataLatency" in js


@pytest.mark.asyncio
async def test_prototype_empty_loading_error_and_retry_states(test_app: FastAPI) -> None:
    """FAZ-08 UX: Bos durum, yukleniyor durumu, hata ve tekrar deneme davranislari."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. HTML Verification
        response = await client.get("/")
        assert response.status_code == 200
        html = response.text

        # Chart loading overlay and error/retry containers
        assert 'id="chart-loading-state"' in html
        assert 'id="chart-error-state"' in html
        assert 'id="btn-chart-retry"' in html
        assert 'id="chart-retry-spinner"' in html
        assert "Piyasa Verisi Alınamadı" in html

        # Watchlist dynamic & empty-state container wrappers
        assert 'id="watchlist-table-container"' in html
        assert 'id="watchlist-empty-state-wrap"' in html
        assert 'id="watchlist-table-body"' in html

        # 2. CSS Design System Verification (tokens, skeleton, spinner, error, retry)
        css_resp = await client.get("/static/css/components.css")
        assert css_resp.status_code == 200
        css = css_resp.text

        assert ".empty-state" in css
        assert ".empty-state-icon" in css
        assert ".empty-state-title" in css
        assert ".empty-state-desc" in css
        assert ".loading-spinner" in css
        assert ".loading-overlay" in css
        assert ".skeleton" in css
        assert ".skeleton-card" in css
        assert ".error-state" in css
        assert ".error-state-title" in css
        assert ".btn-retry" in css
        assert ".btn-retry.is-retrying" in css

        # 3. JS State Handlers & Interactions Verification
        js_resp = await client.get("/static/js/app.js")
        assert js_resp.status_code == 200
        js = js_resp.text

        # State fields & UX handlers
        assert "chartStatus" in js
        assert "renderWatchlistTable" in js
        assert "clearAllWatchlist" in js
        assert "resetSampleWatchlist" in js
        assert "clearScreenerFilters" in js
        assert "addSampleAlert" in js
        assert "retryChartData" in js
        assert "simulateChartError" in js
        assert "simulateChartLoading" in js

        # Window exports
        expected_exports = [
            "retryChartData",
            "simulateChartError",
            "simulateChartLoading",
            "setChartStatus",
            "getChartStatus",
            "renderWatchlistTable",
            "clearAllWatchlist",
            "resetSampleWatchlist",
            "clearScreenerFilters",
            "addSampleAlert",
            "clearAlerts",
        ]
        for item in expected_exports:
            assert item in js, f"Export {item} missing from window.BoraApp"


@pytest.mark.asyncio
async def test_prototype_keyboard_navigation_screen_readers_contrast_and_a11y(
    test_app: FastAPI,
) -> None:
    """FAZ-08 UX: Klavye ile kullanim, ekran okuyucu etiketleri,
    kontrast ve erisilebilirlik kontrolleri.
    """
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. HTML Verification
        response = await client.get("/")
        assert response.status_code == 200
        html = response.text

        # Skip link and screen reader announcer live region
        assert 'class="skip-link"' in html
        assert 'id="a11y-announcer"' in html
        assert 'role="status"' in html
        assert 'aria-live="polite"' in html
        assert 'aria-atomic="true"' in html
        assert 'class="sr-only"' in html

        # Mobile drawer button accessible attributes
        assert 'id="btn-mobile-menu"' in html
        assert 'aria-expanded="false"' in html
        assert 'aria-controls="app-sidebar"' in html
        assert 'aria-haspopup="true"' in html

        # High Contrast UI controls
        assert 'id="btn-contrast-toggle"' in html
        assert 'id="setting-contrast"' in html
        assert "Yüksek Kontrast" in html

        # Accessible interactive table rows and scope attributes
        assert 'scope="col"' in html
        assert 'tabindex="0"' in html
        assert 'role="button"' in html
        assert "hisse detayını görüntüle" in html

        # 2. CSS Design System & Token Verification
        # Tokens CSS: Contrast and Reduced Motion media queries
        tokens_resp = await client.get("/static/css/tokens.css")
        assert tokens_resp.status_code == 200
        tokens_css = tokens_resp.text
        assert 'data-contrast="high"' in tokens_css
        assert "@media (prefers-contrast: more)" in tokens_css
        assert "@media (prefers-reduced-motion: reduce)" in tokens_css

        # Components CSS: focus-visible, sr-only, high-contrast overrides
        components_resp = await client.get("/static/css/components.css")
        assert components_resp.status_code == 200
        comp_css = components_resp.text
        assert ".sr-only" in comp_css
        assert ":focus-visible" in comp_css
        assert '.data-table tbody tr[tabindex="0"]:focus-visible' in comp_css
        assert '[data-contrast="high"]' in comp_css

        # 3. JS Implementation Verification
        js_resp = await client.get("/static/js/app.js")
        assert js_resp.status_code == 200
        js = js_resp.text

        # A11y & Contrast handlers
        assert "announceA11y" in js
        assert "setContrastMode" in js
        assert "toggleContrastMode" in js
        assert "highContrast" in js
        assert "Escape" in js

        # Window exports
        expected_a11y_exports = [
            "announceA11y",
            "setContrastMode",
            "toggleContrastMode",
            "getContrastMode",
        ]
        for exp in expected_a11y_exports:
            assert exp in js, f"Accessibility export {exp} missing from window.BoraApp"


@pytest.mark.asyncio
async def test_mobile_responsive_table_cards_and_horizontal_scroll(test_app: FastAPI) -> None:
    """FAZ-08 UX: Telefon ekranında tablo yerine okunabilir kartlar/yatay kaydırma."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. HTML Verification: Structure, Containers & Attributes
        response = await client.get("/")
        assert response.status_code == 200
        html = response.text

        # Responsive card table containers
        assert "table-cards-mobile" in html
        assert "table-scroll-hint" in html
        assert "table-responsive" in html

        # Data-label attributes for mobile cards
        assert 'data-label="Sembol"' in html
        assert 'data-label="Son Fiyat"' in html
        assert 'data-label="Fark %"' in html
        assert 'data-label="Hacim"' in html

        # Mobile view toggle buttons
        assert 'id="btn-toggle-table-view-screener"' in html
        assert 'id="btn-toggle-table-view-watchlist"' in html
        assert "btn-table-view-toggle" in html
        assert "Kart Görünümü" in html

        # 2. CSS Design System Verification (components.css)
        comp_resp = await client.get("/static/css/components.css")
        assert comp_resp.status_code == 200
        comp_css = comp_resp.text

        # Horizontal touch scrolling rules
        assert "-webkit-overflow-scrolling: touch" in comp_css
        assert ".table-scroll-hint" in comp_css

        # Responsive media query and table-to-card rules
        assert "@media (max-width: 640px)" in comp_css
        assert ".table-cards-mobile" in comp_css
        assert ".table-cards-mobile .data-table thead" in comp_css
        assert "display: none;" in comp_css
        assert "content: attr(data-label);" in comp_css
        assert ".btn-table-view-toggle" in comp_css

        # 3. JS Implementation Verification (app.js)
        js_resp = await client.get("/static/js/app.js")
        assert js_resp.status_code == 200
        js = js_resp.text

        # Mobile table view state and controllers
        assert "mobileTableView" in js
        assert "setMobileTableView" in js
        assert "toggleMobileTableView" in js
        assert "data-label=" in js
        assert "bora_mobile_table_view" in js

        # Window exports
        expected_exports = [
            "setMobileTableView",
            "toggleMobileTableView",
            "getMobileTableView",
        ]
        for exp in expected_exports:
            assert exp in js, f"Export {exp} missing from window.BoraApp"


@pytest.mark.asyncio
async def test_prediction_screen_avoids_target_price_and_shows_horizon_uncertainty_version(
    test_app: FastAPI,
) -> None:
    """FAZ-08 UX & Finans-Hukuk: Tahmin ekranında kesin hedef fiyat yerine istatistiki bant."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. HTML Verification: Analysis Screen, Model Card, Horizon and Uncertainty
        response = await client.get("/")
        assert response.status_code == 200
        html = response.text

        # Ensure prohibited promising terms are strictly absent from model prediction
        assert "hedef fiyat:" not in html.lower()
        assert "garanti kazanç" not in html.lower()
        assert "kesin getiri" not in html.lower()

        # Verify Structure and Components
        assert 'id="screen-analysis"' in html
        assert 'id="prediction-main-card"' in html
        assert "prediction-horizon-nav" in html
        assert 'data-horizon="1D"' in html
        assert 'data-horizon="3D"' in html
        assert 'data-horizon="5D"' in html
        assert 'data-horizon="10D"' in html

        # Verify Confidence Band and Limits
        assert 'id="prediction-band-text"' in html
        assert 'id="prediction-band-min"' in html
        assert 'id="prediction-band-max"' in html
        assert "Tahmin Bandı (%90 Güven Aralığı)" in html

        # Verify Uncertainty Meter
        assert 'id="prediction-uncertainty-score"' in html
        assert 'id="prediction-uncertainty-bar"' in html
        assert "Model Belirsizlik Skoru" in html

        # Verify Model Metadata and Baseline Comparison
        assert 'id="prediction-meta-version"' in html
        assert 'id="prediction-meta-horizon"' in html
        assert 'id="prediction-meta-baseline"' in html
        assert "Model v1.4.2" in html
        assert "Baseline Üstü" in html

        # Verify Fallback State (Model below baseline or disabled)
        assert 'id="prediction-model-fallback"' in html
        assert "Tahmin Özelliği Şu Anda Kullanılamıyor" in html

        # Verify Legal Disclaimer Text (DISCLAIMER_TR.md)
        assert 'id="prediction-disclaimer-text"' in html
        assert "Deneysel model çıktısı" in html
        assert "Kesin sonuç veya hedef fiyat vaadi teşkil etmez" in html
        assert "Belirsizlik aralığıyla birlikte değerlendirin" in html
        assert "basit bir kıyas yöntemine (naive baseline)" in html

        # 2. CSS Verification (components.css)
        comp_resp = await client.get("/static/css/components.css")
        assert comp_resp.status_code == 200
        comp_css = comp_resp.text

        assert ".prediction-card" in comp_css
        assert ".prediction-horizon-nav" in comp_css
        assert ".horizon-btn" in comp_css
        assert ".prediction-band-box" in comp_css
        assert ".uncertainty-meter" in comp_css
        assert ".uncertainty-bar-track" in comp_css
        assert ".uncertainty-bar-fill" in comp_css
        assert ".model-meta-grid" in comp_css
        assert ".prediction-disclaimer-box" in comp_css
        assert ".model-disabled-state" in comp_css

        # 3. JS Controller Verification (app.js)
        js_resp = await client.get("/static/js/app.js")
        assert js_resp.status_code == 200
        js = js_resp.text

        assert "PREDICTION_HORIZONS" in js
        assert "setPredictionHorizon" in js
        assert "setModelStatus" in js
        assert "toggleModelStatus" in js
        assert "getPredictionState" in js

        expected_prediction_exports = [
            "setPredictionHorizon",
            "setModelStatus",
            "toggleModelStatus",
            "getPredictionState",
        ]
        for exp in expected_prediction_exports:
            assert exp in js, f"Export {exp} missing from window.BoraApp"


@pytest.mark.asyncio
async def test_ux_usability_testing_and_unassisted_task_completion(test_app: FastAPI) -> None:
    """FAZ-08 UX: Kullanıcı testleri ve yeni kullanıcıların 4 temel görevi yardımsız tamamlaması."""
    from pathlib import Path

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. HTML Verification: UX Tasks Widget and 4 Core Tasks Checklist
        response = await client.get("/")
        assert response.status_code == 200
        html = response.text

        assert 'id="ux-tasks-widget"' in html
        assert 'id="ux-widget-title"' in html
        assert 'id="ux-progressbar"' in html
        assert 'id="ux-progress-fill"' in html
        assert 'id="ux-widget-score"' in html
        assert 'id="btn-ux-run-benchmark"' in html
        assert 'id="btn-ux-reset"' in html
        assert 'id="btn-ux-toggle"' in html

        # 4 Core Tasks Items in checklist
        assert 'id="ux-task-search"' in html
        assert 'id="ux-task-watch"' in html
        assert 'id="ux-task-chart"' in html
        assert 'id="ux-task-alert"' in html
        assert 'id="ux-benchmark-summary"' in html

        # 2. CSS Verification: UX Widget styles in components.css
        comp_resp = await client.get("/static/css/components.css")
        assert comp_resp.status_code == 200
        comp_css = comp_resp.text

        assert ".ux-tasks-widget" in comp_css
        assert ".ux-widget-header" in comp_css
        assert ".ux-progress-track" in comp_css
        assert ".ux-progress-fill" in comp_css
        assert ".ux-task-list" in comp_css
        assert ".ux-task-item" in comp_css
        assert ".ux-task-item.completed" in comp_css
        assert ".ux-benchmark-summary" in comp_css

        # 3. JS Telemetry & Controller Verification: app.js
        js_resp = await client.get("/static/js/app.js")
        assert js_resp.status_code == 200
        js = js_resp.text

        assert "uxTracker" in js
        assert "completeUXTask" in js
        assert "updateUXWidgetUI" in js
        assert "getUXTestReport" in js
        assert "resetUXTasks" in js
        assert "toggleUXWidget" in js
        assert "runAutomatedUXSession" in js

        expected_ux_exports = [
            "completeUXTask",
            "updateUXWidgetUI",
            "getUXTestReport",
            "resetUXTasks",
            "toggleUXWidget",
            "runAutomatedUXSession",
        ]
        for exp in expected_ux_exports:
            assert exp in js, f"Export {exp} missing from window.BoraApp"

    # 4. Verification of UX Test Documentation (docs/UX_TEST_NOTES.md)
    notes_path = Path("docs/UX_TEST_NOTES.md")
    assert notes_path.exists(), "docs/UX_TEST_NOTES.md must exist for FAZ-08 acceptance."
    notes_content = notes_path.read_text(encoding="utf-8")

    # Verify participant profiles (P1 to P5)
    assert "P1" in notes_content
    assert "P2" in notes_content
    assert "P3" in notes_content
    assert "P4" in notes_content
    assert "P5" in notes_content

    # Verify key metrics
    assert "Görev Tamamlama Oranı" in notes_content or "TCR" in notes_content
    assert "%100" in notes_content
    assert "Yardımsız Tamamlama" in notes_content
    assert "SUS" in notes_content
    assert "89.5" in notes_content

    # Verify 4 Core Tasks and < 60s rule
    assert "Hisse Ara" in notes_content
    assert "İzleme Listesine Ekle" in notes_content
    assert "Fiyat Grafiğini İncele" in notes_content
    assert "Fiyat Uyarısı Kur" in notes_content
    assert "60 saniyede ilk grafik" in notes_content or "< 60 sn" in notes_content

    # Verify friction point resolutions
    assert "F1" in notes_content
    assert "F2" in notes_content
    assert "F3" in notes_content
    assert "F4" in notes_content


@pytest.mark.asyncio
async def test_forecast_enabled_flag_and_ui_disabled_fallback(test_app: FastAPI) -> None:
    """FAZ-11: FORECAST_ENABLED bayrağı; baseline geçilmezse 'tahmin kapalı' durumu."""
    from stock_analysis.config import Settings, get_settings

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Varsayılan ayar: forecast_enabled=False olmalıdır
        health_resp = await client.get("/api/v1/health")
        assert health_resp.status_code == 200
        health_data = health_resp.json()
        assert health_data["flags"]["forecast_enabled"] is False

        # 2. Bayrak kapalıyken /api/v1/inference/forecast/{symbol} 503 dönmelidir
        forecast_resp = await client.get("/api/v1/inference/forecast/THYAO")
        assert forecast_resp.status_code == 503
        data = forecast_resp.json()
        code_in_err = data.get("error", {}).get("code") == "FORECAST_DISABLED"
        code_in_det = data.get("detail", {}).get("code") == "FORECAST_DISABLED"
        assert code_in_err or code_in_det

        # 3. HTML'de sıfır-güven (zero-trust) gereği varsayılan görünüm "Tahmin Kapalı" olmalıdır
        html_resp = await client.get("/")
        assert html_resp.status_code == 200
        html = html_resp.text
        assert 'id="prediction-model-fallback"' in html
        assert 'class="model-disabled-state active"' in html
        assert "Tahmin Özelliği Şu Anda Kullanılamıyor" in html
        assert "Klasik teknik analiz göstergeleri kullanılabilir" in html
        assert 'id="prediction-status-badge"' in html
        assert "Tahmin Kapalı" in html

        # 4. app.js içinde bayrak senkronizasyonu ve model kontrol fonksiyonları bulunmalıdır
        js_resp = await client.get("/static/js/app.js")
        assert js_resp.status_code == 200
        js = js_resp.text
        assert "syncModelFeatureFlag" in js
        assert "getModelStatus" in js
        assert "setModelStatus" in js
        assert 'modelStatus: "disabled"' in js

    # 5. Bayrak aktif yapıldığında (ör. baseline geçilmiş onaylı model ortamı) API 200 dönmelidir
    app_enabled = create_app()

    def override_enabled() -> Settings:
        return Settings(FORECAST_ENABLED=True)

    app_enabled.dependency_overrides[get_settings] = override_enabled
    transport_enabled = ASGITransport(app=app_enabled)
    async with AsyncClient(
        transport=transport_enabled, base_url="http://testserver"
    ) as client_enabled:
        resp_enabled = await client_enabled.get("/api/v1/health")
        assert resp_enabled.status_code == 200
        assert resp_enabled.json()["flags"]["forecast_enabled"] is True

        api_forecast = await client_enabled.get("/api/v1/inference/forecast/THYAO")
        assert api_forecast.status_code == 200
        assert api_forecast.json()["forecast_available"] is True
