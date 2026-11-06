"""Pydantic schemas and contracts for Borsa Analiz REST API."""

from __future__ import annotations

import zoneinfo
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

DISCLAIMER_TEXT = "Bu sistem bilgilendirme amaclidir, yatirim tavsiyesi degildir."
EMAIL_REGEX = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"


class APIModel(BaseModel):
    """Base schema config prohibiting unmodeled attributes."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


# --- Error Schemas ---


class ErrorDetail(APIModel):
    """Standardized machine-readable error body."""

    code: str = Field(description="Error classification code")
    message: str = Field(description="Safe human-readable error description")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC event timestamp",
    )


class ErrorResponse(APIModel):
    """Unified error envelope."""

    error: ErrorDetail


# --- Pagination Schemas ---


class PaginationParams(APIModel):
    """Query parameters for pagination."""

    page: int = Field(default=1, ge=1, description="1-indexed page number")
    page_size: int = Field(default=50, ge=1, le=100, description="Items per page (max 100)")


class PagedResponse[T](APIModel):
    """Generic paginated envelope."""

    items: list[T]
    total: int = Field(ge=0, description="Total matching items count")
    page: int = Field(ge=1, description="Current page number")
    page_size: int = Field(ge=1, description="Requested page size")
    pages: int = Field(ge=0, description="Total calculated pages")


# --- System / Health Schemas ---


class HealthResponse(APIModel):
    """Service health and operational flags."""

    status: str = Field(default="healthy", description="Service status")
    version: str = Field(default="0.1.0", description="API version")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Current UTC server time",
    )
    flags: dict[str, bool] = Field(
        default_factory=dict,
        description="Active feature toggles (forecast_enabled, llm_enabled)",
    )


# --- Market Schemas ---


class SymbolItem(APIModel):
    """Brief metadata for a listed financial instrument."""

    symbol: str = Field(description="Stock ticker symbol (e.g. THYAO)")
    title: str = Field(description="Company full corporate name")
    indices: list[str] = Field(default_factory=list, description="Associated index tags")
    currency: str = Field(default="TRY", description="Trading denomination currency")


class SymbolsResponse(APIModel):
    """List of supported market instruments."""

    universe: str = Field(default="BIST_100", description="Market universe code")
    count: int = Field(ge=0, description="Returned symbols count in this page")
    total: int = Field(ge=0, description="Total universe instruments count")
    page: int = Field(default=1, ge=1, description="Page number")
    page_size: int = Field(default=50, ge=1, description="Page size")
    updated_at: datetime = Field(description="Last EOD feed timestamp")
    symbols: list[SymbolItem] = Field(description="Collection of symbol records")
    disclaimer: str = Field(
        default=DISCLAIMER_TEXT,
        description="Legal disclaimer reminder",
    )


class CandleItem(APIModel):
    """Single daily OHLCV bar."""

    date: datetime = Field(description="UTC session timestamp")
    open: float = Field(description="Session opening price")
    high: float = Field(description="Session highest price")
    low: float = Field(description="Session lowest price")
    close: float = Field(description="Session closing price")
    volume: float = Field(description="Total trade volume in shares")
    turnover_try: float | None = Field(default=None, description="Total traded volume in TRY")


class HistoryResponse(APIModel):
    """Historical OHLCV series for an instrument."""

    symbol: str = Field(description="Instrument ticker symbol")
    currency: str = Field(default="TRY", description="Quoted currency")
    timezone: str = Field(default="UTC", description="Data timestamp timezone standard")
    price_type: str = Field(
        default="adjusted",
        description="Price calculation type ('adjusted' or 'raw')",
    )
    data_source: str = Field(
        default="official_eod_feed",
        description="Underlying market data provider tier",
    )
    calculation_version: str = Field(
        default="v1.0.0",
        description="Data transformation and price processing engine version",
    )
    lineage_id: str | None = Field(
        default=None,
        description="Audit trace identifier for displayed price dataset",
    )
    is_downsampled: bool = Field(
        default=False,
        description="Indicates whether the returned candle series was downsampled",
    )
    downsample_method: str | None = Field(
        default=None,
        description="Downsampling algorithm or resolution used ('bucket', 'weekly', 'monthly')",
    )
    raw_count: int | None = Field(
        default=None,
        description="Total candle count prior to downsampling",
    )
    downsampled_count: int | None = Field(
        default=None,
        description="Total candle count after downsampling",
    )
    disclaimer: str = Field(
        default=DISCLAIMER_TEXT,
        description="Mandatory disclaimer",
    )
    candles: list[CandleItem] = Field(default_factory=list, description="Chronological price bars")


# --- Analytics & Indicators Schemas ---


class IndicatorMacd(APIModel):
    """MACD indicator components."""

    line: float
    signal: float
    histogram: float


class IndicatorsData(APIModel):
    """Set of calculated deterministic indicators."""

    rsi_14: float | None = Field(default=None, description="14-period RSI (0-100)")
    sma_20: float | None = Field(default=None, description="20-session Simple Moving Average")
    sma_50: float | None = Field(default=None, description="50-session Simple Moving Average")
    macd: IndicatorMacd | None = Field(default=None, description="MACD trend oscillator")


class IndicatorsResponse(APIModel):
    """Calculated technical indicator payload with strictly neutral commentary."""

    symbol: str
    calculated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    indicators: IndicatorsData
    neutral_summary: str = Field(
        description="Fact-based neutral description without advice or recommendations"
    )
    disclaimer: str = Field(
        default="Teknik gostergeler deterministik hesaplamadir, al-sat onerisi degildir."
    )


class ScreenerMatch(APIModel):
    """Symbol fulfilling technical screening criteria."""

    symbol: str
    close: float
    rsi_14: float | None = None
    sma_20: float | None = None
    sma_50: float | None = None


class ScreenerResponse(APIModel):
    """Results of a rule-based screener query."""

    filter_applied: dict[str, Any] = Field(description="Active filters")
    matches: list[ScreenerMatch] = Field(default_factory=list, description="Matching instruments")
    total: int = Field(default=0, ge=0, description="Toplam eslesen kayit sayisi")
    page: int = Field(default=1, ge=1, description="Mevcut sayfa numarasi")
    page_size: int = Field(default=50, ge=1, le=100, description="Sayfa basina kayit adedi")
    disclaimer: str = Field(
        default="Filtreleme sonuclari teknik kriter taramasidir; yatirim tavsiyesi degildir."
    )


# --- Forecast / Model Inference Schemas ---


class ForecastResponse(APIModel):
    """Statistical prediction interval payload with safe fallback support."""

    symbol: str
    forecast_available: bool = Field(
        default=True,
        description="Tahmin modelinin aktif ve basarili calisip calismadigi",
    )
    status: str = Field(
        default="OK",
        description="Tahmin durumu ('OK' veya 'FALLBACK_TRIGGERED')",
    )
    model_version: str | None = Field(
        default=None,
        description="Model identifier and registry version",
    )
    calculation_version: str = Field(
        default="v1.0.0",
        description="Prediction inference engine calculation version",
    )
    data_source: str = Field(
        default="official_eod_feed",
        description="Data feed tier used for model feature inputs",
    )
    lineage_id: str | None = Field(
        default=None,
        description="Audit trace identifier for displayed prediction",
    )
    evaluation_date: datetime = Field(description="Inference evaluation timestamp")
    horizon_days: int | None = Field(
        default=None,
        ge=1,
        description="Projection forecast window in calendar days",
    )
    predicted_direction: str | None = Field(
        default=None,
        description="Directional tendency ('upward', 'neutral', 'downward')",
    )
    confidence_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Model confidence level",
    )
    uncertainty_interval: list[float] | None = Field(
        default=None,
        description="[lower_bound, upper_bound] estimated price band",
    )
    baseline_comparison: str | None = Field(
        default=None,
        description="Comparison showing outperformance over naive baseline",
    )
    fallback_reason: str | None = Field(
        default=None,
        description="Model calismadigi veya basarisiz oldugu durumlarda seffaf aciklama",
    )
    alternative_indicators: list[str] | None = Field(
        default=None,
        description="Fallback durumunda onerilen klasik gostergeler (RSI, SMA vb.)",
    )
    disclaimer: str = Field(
        default=(
            "Model cikti ve tahminleri kesinlik icermez; "
            "istatistiki bir yaklasimdir, yatirim tavsiyesi degildir."
        )
    )


class ExplanationResponse(APIModel):
    """Optional LLM or deterministic rule-based commentary with strict safety guardrails."""

    symbol: str
    context_summary: str
    technical_observations: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    disclaimer: str
    llm_enabled: bool = False
    llm_used: bool = False
    cached: bool = False
    fallback_used: bool = False
    reason: str = "OK"


# --- Auth Schemas ---


class MagicLinkRequest(APIModel):
    """Request payload for passwordless magic link email dispatch."""

    email: str = Field(pattern=EMAIL_REGEX, description="Recipient user email address")


class MagicLinkResponse(APIModel):
    """Response acknowledging magic link dispatch without revealing account existence."""

    message: str = Field(
        default="Giris baglantisi e-posta adresinize gonderildi.",
        description="Generic user notification",
    )


class VerifyTokenRequest(APIModel):
    """Request payload to exchange magic link token for session access token."""

    token: str = Field(min_length=16, description="One-time validation token")


class RegisterRequest(APIModel):
    """User registration credentials."""

    email: str = Field(pattern=EMAIL_REGEX, description="User email address")
    password: str = Field(
        min_length=10,
        max_length=128,
        description="User password meeting complexity policy",
    )
    guest_token: str | None = Field(
        default=None,
        description="Optional guest session token to migrate sample watchlist and preferences",
    )


class RegisterResponse(APIModel):
    """Generic registration confirmation response preventing email enumeration."""

    message: str = Field(
        default="Kayıt talebiniz alındı. E-posta adresinize doğrulama bağlantısı gönderildi.",
        description="Generic notification message to user preventing account existence leakage",
    )


class LoginRequest(APIModel):
    """User credentials for password-based authentication."""

    email: str = Field(pattern=EMAIL_REGEX, description="User email address")
    password: str = Field(min_length=1, max_length=128, description="User password")
    mfa_code: str | None = Field(
        default=None,
        description="Optional TOTP or recovery code if MFA enabled",
    )
    guest_token: str | None = Field(
        default=None,
        description="Optional guest session token to migrate sample watchlist and preferences",
    )


class LoginResponse(APIModel):
    """Authentication response returning access token or prompting for MFA challenge."""

    access_token: str | None = Field(default=None, description="Bearer session access token")
    token_type: str = Field(default="bearer", description="Token authentication scheme")
    expires_in: int = Field(default=86400, description="Validity period in seconds")
    requires_mfa: bool = Field(default=False, description="Flag indicating MFA code required")
    message: str = Field(default="Giriş başarılı.", description="Status message")


class TokenResponse(APIModel):
    """Session access token response."""

    access_token: str = Field(description="Bearer session token")
    token_type: str = Field(default="bearer", description="Token authentication scheme")
    expires_in: int = Field(default=86400, description="Validity period in seconds")


class EmailVerificationRequest(APIModel):
    """Request payload to initiate email address verification."""

    email: str = Field(pattern=EMAIL_REGEX, description="Recipient user email address to verify")


class EmailVerificationResponse(APIModel):
    """Generic response acknowledging verification dispatch without user enumeration."""

    message: str = Field(
        default="Doğrulama bağlantısı e-posta adresinize gönderildi.",
        description="Notification message to user",
    )


class EmailVerificationConfirmRequest(APIModel):
    """Request payload containing the single-use verification token."""

    token: str = Field(min_length=16, description="Cryptographic email verification token")


class EmailVerificationConfirmResponse(APIModel):
    """Response returned upon successful email verification confirmation."""

    message: str = Field(
        default="E-posta adresiniz başarıyla doğrulandı.",
        description="Status message",
    )
    email: str = Field(description="Confirmed email address")
    is_verified: bool = Field(default=True, description="Account email verification status")


# --- Password Reset Schemas ---


class PasswordResetRequest(APIModel):
    """Request payload to initiate a secure, time-limited password reset."""

    email: str = Field(
        pattern=EMAIL_REGEX,
        description="Recipient user email address for password reset",
    )


class PasswordResetResponse(APIModel):
    """Generic response acknowledging password reset request without leaking account existence."""

    message: str = Field(
        default="Sıfırlama bağlantısı e-posta adresinize gönderildi.",
        description="Notification message to user",
    )


class PasswordResetConfirmRequest(APIModel):
    """Request payload containing the single-use reset token and the new password."""

    token: str = Field(min_length=16, description="Cryptographic password reset token")
    new_password: str = Field(
        min_length=10,
        max_length=128,
        description="New password meeting security complexity policy",
    )


class PasswordResetConfirmResponse(APIModel):
    """Response returned upon successful password reset."""

    message: str = Field(
        default="Parolanız başarıyla güncellendi.",
        description="Status message",
    )
    email: str = Field(description="Confirmed email address")


# --- Session & CSRF Schemas ---


class CsrfTokenResponse(APIModel):
    """Response containing Anti-CSRF Double-Submit token."""

    csrf_token: str = Field(description="Anti-CSRF Double Submit token")


class SessionLogoutResponse(APIModel):
    """Confirmation of session termination and cookie clearing."""

    message: str = Field(
        default="Oturumunuz başarıyla kapatıldı.",
        description="Session termination status message",
    )


# --- Multi-Factor Authentication (MFA / TOTP) Schemas ---


class MFASetupResponse(APIModel):
    """Credentials and provisioning information for MFA setup."""

    secret: str = Field(description="Base32 TOTP secret key")
    provisioning_uri: str = Field(description="Standard otpauth:// provisioning URI")
    recovery_codes: list[str] = Field(description="Single-use backup recovery codes")


class MFAEnableRequest(APIModel):
    """Request payload to verify code and permanently enable MFA."""

    secret: str = Field(min_length=16, description="Base32 TOTP secret key")
    code: str = Field(
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="6-digit TOTP code",
    )
    recovery_codes: list[str] = Field(min_length=1, description="List of generated recovery codes")


class MFAEnableResponse(APIModel):
    """Confirmation of successful MFA activation."""

    message: str = Field(
        default="İki faktörlü doğrulama (MFA) başarıyla etkinleştirildi.",
        description="Status message",
    )
    mfa_enabled: bool = Field(default=True, description="Account MFA status")


class MFAVerifyRequest(APIModel):
    """Request payload to verify an MFA challenge using TOTP or recovery code."""

    code: str = Field(min_length=6, max_length=32, description="6-digit TOTP code or recovery code")


class MFAVerifyResponse(APIModel):
    """Result of MFA challenge verification."""

    message: str = Field(default="MFA doğrulaması başarılı.", description="Status message")
    verified: bool = Field(default=True, description="Verification success flag")
    remaining_recovery_codes: int | None = Field(
        default=None,
        description="Count of unused recovery codes if a recovery code was used",
    )


class MFADisableRequest(APIModel):
    """Request payload to disable MFA on account."""

    code: str = Field(min_length=6, max_length=32, description="6-digit TOTP code or recovery code")


class MFADisableResponse(APIModel):
    """Confirmation of MFA deactivation."""

    message: str = Field(
        default="İki faktörlü doğrulama başarıyla devre dışı bırakıldı.",
        description="Status message",
    )
    mfa_enabled: bool = Field(default=False, description="Account MFA status")


# --- User Active Session Management Schemas ---


class UserSessionItem(APIModel):
    """Active user session representation."""

    id: int = Field(description="Unique session identifier")
    created_at: datetime = Field(description="Timestamp when session was created")
    expires_at: datetime = Field(description="Timestamp when session expires")
    user_agent: str | None = Field(default=None, description="Client device / browser user-agent")
    ip_hash: str | None = Field(default=None, description="Hashed client IP address")
    is_current: bool = Field(
        default=False,
        description="Flag indicating if this is the active session",
    )


class UserSessionsListResponse(APIModel):
    """List of active sessions for the authenticated user."""

    sessions: list[UserSessionItem] = Field(
        default_factory=list,
        description="List of active user sessions",
    )
    total: int = Field(ge=0, description="Count of active sessions")


class TerminateSessionsResponse(APIModel):
    """Confirmation of session termination."""

    message: str = Field(description="Termination confirmation status message")
    terminated_count: int = Field(ge=0, description="Count of closed sessions")


# --- User & Watchlist Schemas ---
# Note: In accordance with SPK and compliance rules, watchlists ONLY store symbol strings.
# Absolutely NO portfolio fields, buy price, lot, profit/loss are stored or returned.


class WatchlistResponse(APIModel):
    """Curated symbol watchlist for user tracking."""

    watchlist: list[str] = Field(
        default_factory=list,
        description="Watched ticker symbols (strictly no portfolio, cost or lot fields)",
    )


class WatchlistActionResponse(APIModel):
    """Confirmation of watchlist symbol insertion or removal."""

    message: str
    symbol: str


# --- Alert Schemas ---


class AlertCreateRequest(APIModel):
    """Specification for a threshold alert."""

    symbol: str = Field(min_length=1, max_length=16, description="Ticker symbol")
    condition_type: str = Field(
        description="Condition type (e.g. 'PRICE_ABOVE', 'PRICE_BELOW', 'RSI_ABOVE')"
    )
    target_value: float = Field(description="Numeric trigger threshold")
    channel: str = Field(default="EMAIL", description="Notification delivery channel")


class AlertItem(APIModel):
    """Configured alert rule."""

    alert_id: str
    symbol: str
    condition_type: str
    target_value: float
    channel: str = "EMAIL"
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AlertsListResponse(APIModel):
    """Active user alert rules."""

    alerts: list[AlertItem] = Field(
        default_factory=list,
        description="Active alerts (max 5 active per user)",
    )
    total: int = Field(ge=0, description="Total active rules count")


class AlertCreatedResponse(APIModel):
    """Created alert confirmation."""

    alert_id: str
    status: str = "active"


class UnsubscribeResponse(APIModel):
    """RFC 8058 one-click unsubscribe response."""

    success: bool
    scope: str
    masked_email: str
    message: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConsentUpdateRequest(APIModel):
    """Request to grant or revoke notification consent."""

    channel: str = Field(default="email", description="Notification channel (email, in_app, etc.)")
    is_granted: bool = Field(description="Consent status (True=granted, False=revoked)")
    scope: str = Field(default="all", description="Consent scope ('all' or specific alert)")
    reason: str | None = Field(default=None, description="Reason for consent update")


class ConsentResponse(APIModel):
    """Notification consent status response."""

    user_id: int
    channel: str
    is_granted: bool
    scope: str
    message: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# --- KVKK Data Management Schemas ---


class UserExportResponse(APIModel):
    """Full KVKK portable data export for the authenticated user."""

    user_id: int
    email: str
    created_at: datetime
    preferences: UserPreferencesResponse | None = None
    watchlists: list[str]
    alerts: list[AlertItem]
    disclaimer: str = Field(
        default=DISCLAIMER_TEXT,
        description="Legal reminder attached to personal export",
    )


class DeleteAccountResponse(APIModel):
    """Confirmation of irreversible account and data deletion."""

    message: str = Field(
        default="Hesabiniz ve tum verileriniz kalici olarak silindi.",
        description="KVKK right to erasure confirmation",
    )


# --- User Preferences Schemas ---

VALID_THEMES = frozenset({"system", "light", "dark"})
VALID_LANGUAGES = frozenset({"tr", "en"})
VALID_ALERT_FREQUENCIES = frozenset({"immediate", "daily_digest", "hourly"})


class UserPreferencesResponse(APIModel):
    """User account settings and personalization options."""

    email_notifications: bool = Field(
        default=True,
        description="E-posta bildirimleri açık mı?",
    )
    timezone: str = Field(
        default="Europe/Istanbul",
        description="Kullanıcı saat dilimi (IANA formatı, örn: Europe/Istanbul)",
    )
    theme: str = Field(
        default="system",
        description="Arayüz teması ('system', 'light', 'dark')",
    )
    language: str = Field(
        default="tr",
        description="Arayüz dili ('tr', 'en')",
    )
    alert_frequency: str = Field(
        default="immediate",
        description="Fiyat ve kural uyarı sıklığı ('immediate', 'daily_digest', 'hourly')",
    )
    weekly_digest: bool = Field(
        default=False,
        description="Haftalık piyasa özeti bülteni alımı",
    )


class UserPreferencesUpdateRequest(APIModel):
    """Payload for updating user preferences."""

    email_notifications: bool | None = Field(
        default=None,
        description="E-posta bildirimleri açık mı?",
    )
    timezone: str | None = Field(
        default=None,
        description="Kullanıcı saat dilimi (IANA formatı, örn: Europe/Istanbul)",
    )
    theme: str | None = Field(
        default=None,
        description="Arayüz teması ('system', 'light', 'dark')",
    )
    language: str | None = Field(
        default=None,
        description="Arayüz dili ('tr', 'en')",
    )
    alert_frequency: str | None = Field(
        default=None,
        description="Fiyat ve kural uyarı sıklığı ('immediate', 'daily_digest', 'hourly')",
    )
    weekly_digest: bool | None = Field(
        default=None,
        description="Haftalık piyasa özeti bülteni alımı",
    )

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str | None) -> str | None:
        if v is not None:
            v_clean = v.strip()
            try:
                zoneinfo.ZoneInfo(v_clean)
            except Exception:
                msg = f"Geçersiz saat dilimi: '{v}'. IANA saat dilimi formatı beklenir."
                raise ValueError(msg) from None
            return v_clean
        return v

    @field_validator("theme")
    @classmethod
    def validate_theme(cls, v: str | None) -> str | None:
        if v is not None:
            v_clean = v.strip().lower()
            if v_clean not in VALID_THEMES:
                msg = f"Geçersiz tema: '{v}'. Desteklenen temalar: {sorted(VALID_THEMES)}"
                raise ValueError(msg)
            return v_clean
        return v

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str | None) -> str | None:
        if v is not None:
            v_clean = v.strip().lower()
            if v_clean not in VALID_LANGUAGES:
                msg = f"Geçersiz dil: '{v}'. Desteklenen diller: {sorted(VALID_LANGUAGES)}"
                raise ValueError(msg)
            return v_clean
        return v

    @field_validator("alert_frequency")
    @classmethod
    def validate_alert_frequency(cls, v: str | None) -> str | None:
        if v is not None:
            v_clean = v.strip().lower()
            if v_clean not in VALID_ALERT_FREQUENCIES:
                msg = (
                    f"Geçersiz uyarı sıklığı: '{v}'. "
                    f"Desteklenen değerler: {sorted(VALID_ALERT_FREQUENCIES)}"
                )
                raise ValueError(msg)
            return v_clean
        return v


# --- Guest Session & Migration Schemas ---


class GuestSessionResponse(APIModel):
    """Anonymous guest session with initial sample value (BIST sample watchlist)."""

    guest_token: str = Field(description="Opaque crypto-safe guest session identifier")
    watchlist: list[str] = Field(description="Sample initial ticker symbols (e.g. THYAO, ASELS)")
    preferences: dict[str, str] = Field(description="Sample or customized UI preferences")
    expires_in: int = Field(description="Remaining guest session duration in seconds")
    message: str = Field(
        default="Anonim misafir oturumu oluşturuldu. İlk değer hazır.",
        description="Informational status message",
    )


class GuestWatchlistUpdateRequest(APIModel):
    """Update ticker symbols in the guest's temporary watchlist before registration."""

    symbol: str = Field(min_length=1, max_length=10, description="Ticker symbol (e.g. THYAO)")
    action: str = Field(
        default="add",
        pattern="^(add|remove)$",
        description="Action to perform: 'add' or 'remove'",
    )


class GuestMigrationRequest(APIModel):
    """Request payload to migrate an anonymous guest session to current user account."""

    guest_token: str = Field(min_length=16, description="Guest session token to migrate")


class GuestMigrationResponse(APIModel):
    """Confirmation of guest session data migration to registered user account."""

    migrated_symbols: list[str] = Field(
        description="List of ticker symbols added to user watchlist"
    )
    preferences_migrated: bool = Field(description="Whether UI preferences were applied")
    message: str = Field(
        default="Misafir oturumu tercihleri ve izleme listesi kullanıcı hesabına aktarıldı.",
        description="Confirmation message",
    )


# --- Form Accessibility & Password Manager Metadata Schemas ---


class FormFieldDescriptor(APIModel):
    """HTML and accessibility attributes tailored for browser password managers."""

    name: str = Field(description="Input field name attribute")
    id: str = Field(description="Unique DOM element id")
    type: str = Field(description="HTML input type (email, password, text)")
    autocomplete: str = Field(
        description=(
            "Standard W3C autofill token "
            "(e.g. username, current-password, new-password, one-time-code, webauthn)"
        )
    )
    inputmode: str | None = Field(default=None, description="Mobile keyboard inputmode hint")
    pattern: str | None = Field(default=None, description="Optional regex pattern hint")
    required: bool = Field(default=True, description="Whether field is required")
    label: str = Field(description="Localized label text")
    placeholder: str | None = Field(default=None, description="Input placeholder text")


class FormMetadataResponse(APIModel):
    """Configuration and accessibility schema for authentication forms."""

    form_id: str = Field(description="Form container DOM identifier")
    action: str = Field(description="Submission target API endpoint")
    method: str = Field(default="POST", description="HTTP submission method")
    fields: dict[str, FormFieldDescriptor] = Field(description="Dictionary of field specifications")
    passkey_supported: bool = Field(default=True, description="Whether passkey login is available")
    passkey_autocomplete: str = Field(
        default="username webauthn",
        description="HTML autocomplete attribute for conditional passkey autofill",
    )
    supported_password_managers: list[str] = Field(
        default_factory=lambda: [
            "1Password",
            "Bitwarden",
            "Apple Keychain",
            "Google Password Manager",
            "KeePassXC",
        ],
        description="Officially compatible and tested password managers",
    )
    tips: list[str] = Field(
        default_factory=lambda: [
            "Şifre yöneticiniz otomatik doldurmayı destekler.",
            (
                "Daha güvenli ve hızlı giriş için Passkey "
                "(güvenlik anahtarı / biyometri) kullanabilirsiniz."
            ),
            "Giriş bilgileri güvenli TLS bağlantısı üzerinden iletilir.",
        ],
        description="Localized usability and security guidance",
    )


# --- Passkey / WebAuthn Schemas ---


class PasskeyRegisterOptionsResponse(APIModel):
    """PublicKeyCredentialCreationOptions for passkey registration."""

    options: dict[str, Any] = Field(description="W3C WebAuthn creation options dictionary")


class PasskeyRegisterVerifyRequest(APIModel):
    """Attestation and public key payload from client authenticator."""

    challenge: str = Field(min_length=16, description="Cryptographic registration challenge")
    credential_id: str = Field(min_length=16, description="Base64url credential identifier")
    public_key: str = Field(min_length=16, description="Public key representation or PEM")
    device_name: str | None = Field(
        default="Güvenlik Anahtarı",
        max_length=128,
        description="Friendly user-assigned device name",
    )
    aaguid: str | None = Field(default=None, max_length=64, description="Authenticator AAGUID")
    transports: list[str] | None = Field(
        default=None, description="Supported transports (e.g. ['internal', 'hybrid', 'usb'])"
    )


class PasskeyRegisterVerifyResponse(APIModel):
    """Confirmation of successful passkey registration."""

    credential_id: str = Field(description="Registered credential identifier")
    device_name: str = Field(description="Assigned device name")
    message: str = Field(
        default="Güvenlik anahtarınız (Passkey) başarıyla kaydedildi.",
        description="Status message",
    )


class PasskeyLoginOptionsRequest(APIModel):
    """Optional payload to generate tailored authentication options."""

    email: str | None = Field(
        default=None,
        description="Optional user email if not using discoverable credentials autofill",
    )


class PasskeyLoginOptionsResponse(APIModel):
    """PublicKeyCredentialRequestOptions for passkey assertion."""

    options: dict[str, Any] = Field(description="W3C WebAuthn request options dictionary")


class PasskeyLoginVerifyRequest(APIModel):
    """Assertion signature and credential ID payload from client authenticator."""

    challenge: str = Field(min_length=16, description="Cryptographic assertion challenge")
    credential_id: str = Field(min_length=16, description="Base64url credential identifier")
    client_sign_count: int = Field(
        default=0, ge=0, description="Authenticator monotonic sign counter"
    )
    guest_token: str | None = Field(
        default=None,
        description="Optional guest token to migrate preferences during passkey login",
    )


class PasskeyItemResponse(APIModel):
    """Registered passkey device summary."""

    id: int = Field(description="Passkey primary key id")
    credential_id: str = Field(description="Unique credential ID")
    device_name: str = Field(description="Friendly device name")
    transports: list[str] = Field(default_factory=list, description="Supported transport modes")
    created_at: datetime = Field(description="Registration timestamp")
    last_used_at: datetime | None = Field(default=None, description="Last authentication timestamp")


class PasskeyListResponse(APIModel):
    """List of registered passkeys for authenticated user."""

    passkeys: list[PasskeyItemResponse] = Field(
        default_factory=list, description="Registered passkeys"
    )
    total: int = Field(ge=0, description="Total registered devices count")


class PasskeyRenameRequest(APIModel):
    """Request payload to rename an existing passkey."""

    device_name: str = Field(
        min_length=1, max_length=128, description="Updated friendly device name"
    )


class PasskeyActionResponse(APIModel):
    """Generic confirmation for passkey operations."""

    message: str = Field(description="Status confirmation message")


# --- Web Vitals Telemetry Schemas ---


class WebVitalIngestItem(APIModel):
    """Single Web Vital metric report from client browser."""

    name: str = Field(description="Web Vital identifier (LCP, INP, CLS, FCP, TTFB)")
    value: float = Field(ge=0.0, description="Observed metric value (ms or score)")
    route: str = Field(default="/", max_length=128, description="Originating client page route")
    navigation_type: str = Field(
        default="navigate", max_length=32, description="Navigation mode (e.g. navigate, reload)"
    )
    rating: str | None = Field(
        default=None, description="Client computed rating (good, needs_improvement, poor)"
    )


class WebVitalIngestRequest(APIModel):
    """Payload for submitting client Web Vitals telemetry."""

    metrics: list[WebVitalIngestItem] = Field(
        min_length=1, max_length=50, description="Batch of observed Web Vitals measurements"
    )


class WebVitalIngestResponse(APIModel):
    """Confirmation response for ingested Web Vitals."""

    status: str = Field(default="recorded", description="Ingestion status")
    ingested_count: int = Field(ge=0, description="Total metrics successfully ingested")
    timestamp: str = Field(description="Server ingestion timestamp")


class LegalDocumentMetadataResponse(APIModel):
    """Metadata summary of a versioned legal or regulatory document."""

    doc_type: str = Field(description="Identifier of document type (terms, privacy, etc.)")
    title: str = Field(description="Official title of the document")
    relative_path: str = Field(description="Filepath within repository")
    version: str = Field(description="Document semantic version")
    last_reviewed_date: str = Field(description="Date of last legal review (YYYY-MM-DD)")
    checksum_sha256: str = Field(description="Cryptographic SHA-256 integrity hash")
    is_approved: bool = Field(description="True if document meets all statutory criteria")
    mandatory_clauses_count: int = Field(ge=0, description="Count of verified mandatory clauses")
    banned_phrases_found: list[str] = Field(
        default_factory=list, description="Any detected violations"
    )
    summary: str = Field(description="Short human-readable summary of document scope")


class LegalDocumentDetailResponse(LegalDocumentMetadataResponse):
    """Full content representation of a legal document."""

    content: str = Field(description="Full raw markdown/text of the legal document")


class PackageLicenseResponse(APIModel):
    """Licensing governance details for a third-party dependency."""

    name: str = Field(description="Package or dataset name")
    version_spec: str = Field(description="Configured version constraint")
    license_name: str = Field(description="Open-source license identifier")
    classification: str = Field(description="PERMISSIVE, COPYLEFT, or PROPRIETARY")
    status: str = Field(description="APPROVED, CONDITIONAL, or PROHIBITED")
    is_dev_only: bool = Field(description="Whether restricted to development/test only")
    copyleft_risk: bool = Field(
        description="Whether license poses copyleft viral contamination risk"
    )
    notes: str = Field(default="", description="Compliance or usage notes")


class LegalComplianceGateResponse(APIModel):
    """Comprehensive pre-distribution legal and regulatory compliance report."""

    overall_gate_status: str = Field(description="PASS or FAIL")
    total_documents: int = Field(ge=0, description="Total managed legal documents")
    approved_documents: int = Field(ge=0, description="Count of fully approved documents")
    total_packages_audited: int = Field(ge=0, description="Count of audited dependencies")
    prohibited_licenses_count: int = Field(ge=0, description="Count of prohibited licenses found")
    spk_compliance_passed: bool = Field(description="SPK statutory disclaimer check passed")
    kvkk_compliance_passed: bool = Field(
        description="KVKK data minimization and rights check passed"
    )
    disclaimer_coverage_passed: bool = Field(description="Short UI disclaimer coverage passed")
    issues_found: list[str] = Field(default_factory=list, description="List of identified issues")
    audited_at: str = Field(description="ISO timestamp of evaluation")


class SpkDisclaimerResponse(APIModel):
    """Standardized SPK disclaimer snippet for widgets and headers."""

    disclaimer: str = Field(description="Statutory SPK disclaimer text")
    data_delay_minutes: int = Field(
        default=15, description="Mandatory BIST market data delay in minutes"
    )
    personalization_allowed: bool = Field(
        default=False, description="Whether personalized advice is given"
    )
    source: str = Field(default="Lisanslı Veri Sağlayıcı", description="Market data origin")


class ProviderContractResponse(APIModel):
    """Registered market data vendor contract metadata."""

    contract_id: str = Field(description="Unique contract reference ID")
    provider_name: str = Field(description="Data provider identifier")
    vendor_license_number: str = Field(
        description="Official BIST vendor distribution license number"
    )
    feed_tiers: list[str] = Field(description="Authorized feed tiers (delayed, realtime, EOD)")
    redistribution_scopes: list[str] = Field(
        description="Permitted redistribution scopes (DISPLAY, NON_DISPLAY, DERIVED_DATA)"
    )
    effective_date: str = Field(description="Start date of agreement")
    expiration_date: str = Field(description="Expiry date of agreement")
    max_cache_ttl_seconds: int = Field(description="Maximum allowed caching duration in seconds")
    commercial_use_approved: bool = Field(description="Commercial redistribution permission flag")
    disclaimer_required: bool = Field(description="Whether statutory disclaimer is required")
    signature_hash: str = Field(description="SHA-256 integrity signature")
    has_entitlement_token: bool = Field(description="Whether valid entitlement token exists")
    audit_notes: str = Field(default="", description="Auditor review notes")
    is_revoked: bool = False


class VerifyEntitlementRequest(APIModel):
    """Payload to test entitlement against a specific feed and scope."""

    provider_name: str = Field(description="Target provider name")
    requested_tier: str = Field(
        default="BIST_EQUITY_DELAYED_15MIN",
        description="Feed tier code",
    )
    scope: str = Field(
        default="DISPLAY",
        description="Usage scope (DISPLAY, NON_DISPLAY, DERIVED_DATA)",
    )
    disclaimer_acknowledged: bool = Field(
        default=True,
        description="Whether statutory SPK/BIST disclaimer has been accepted/displayed",
    )


class VerifyEntitlementResponse(APIModel):
    """Runtime market data entitlement verification report."""

    status: str = Field(description="Contract status code (VALID, EXPIRED, etc.)")
    is_authorized: bool = Field(description="True if feed access is fully authorized")
    contract_id: str = Field(description="Associated contract ID")
    provider_name: str = Field(description="Target provider name")
    requested_tier: str = Field(description="Evaluated tier")
    days_until_expiration: int = Field(description="Days remaining before contract expiry")
    errors: list[str] = Field(default_factory=list, description="Authorization errors")
    warnings: list[str] = Field(default_factory=list, description="Auditor warnings")
    checked_at: str = Field(description="ISO timestamp of evaluation")


class CriticalSuiteItemResponse(APIModel):
    """Execution status for an individual critical test suite."""

    suite_id: str = Field(description="Suite identifier")
    suite_name: str = Field(description="Human readable suite name")
    category: str = Field(description="Suite category")
    test_file: str = Field(description="Test file path")
    tests_count: int = Field(ge=0, description="Total tests evaluated")
    passed: int = Field(ge=0, description="Passed tests count")
    failed: int = Field(ge=0, description="Failed tests count")
    skipped: int = Field(default=0, ge=0, description="Skipped tests count")
    duration_seconds: float = Field(ge=0.0, description="Execution duration in seconds")
    status: str = Field(description="PASS or FAIL")
    is_successful: bool = Field(description="True if 0 failed and passed > 0")
    error_details: list[str] = Field(default_factory=list, description="Error messages if any")


class PreDeploymentVerificationResponse(APIModel):
    """Consolidated pre-deployment verification gate report."""

    gate_status: str = Field(description="Overall gate outcome: PASS or FAIL")
    timestamp: str = Field(description="Evaluation ISO timestamp")
    sign_off_ready: bool = Field(description="Whether build is certified for release")
    critical_tests_passed: bool = Field(description="True if all critical test suites passed")
    security_scans_passed: bool = Field(
        description="True if zero CRITICAL/HIGH security vulnerabilities"
    )
    tests_summary: dict[str, Any] = Field(description="Test execution summary metrics")
    security_summary: dict[str, Any] = Field(description="Security findings summary metrics")
    blocking_reasons: list[str] = Field(
        default_factory=list, description="List of blocking reasons"
    )
    test_suites: list[CriticalSuiteItemResponse] = Field(
        default_factory=list, description="Suite breakdown"
    )
    security_scans: dict[str, Any] = Field(
        description="Detailed multi-vector security scan metrics"
    )


class AccountLifecycleCheckResponse(APIModel):
    """Execution status for an individual account lifecycle check."""

    check_id: str = Field(description="Unique check identifier")
    category: str = Field(
        description="Lifecycle category (email_verification, password_reset, account_deletion)"
    )
    title: str = Field(description="Human readable check title")
    status: str = Field(description="PASS or FAIL")
    details: str = Field(description="Technical check details and verification notes")
    standard_ref: str = Field(description="Standard reference (OWASP, KVKK, Law No. 5651)")


class AccountLifecycleGateResponse(APIModel):
    """Pre-deployment verification report for email verification, reset, and account deletion."""

    gate_status: str = Field(description="Overall gate outcome: PASS or FAIL")
    is_signoff_ready: bool = Field(description="Whether build is certified for release")
    checks_evaluated: int = Field(ge=0, description="Total lifecycle checks evaluated")
    checks_passed: int = Field(ge=0, description="Passed lifecycle checks count")
    checks_failed: int = Field(ge=0, description="Failed lifecycle checks count")
    checks: list[AccountLifecycleCheckResponse] = Field(
        default_factory=list, description="Detailed checks breakdown"
    )
    blocking_reasons: list[str] = Field(
        default_factory=list, description="List of blocking reasons if any"
    )
    recommendations: list[str] = Field(
        default_factory=list, description="Post-deployment and hardening recommendations"
    )
    audited_at: str = Field(description="Evaluation ISO timestamp")


class EvaluatedModelResponse(APIModel):
    """Summary schema for an evaluated model benchmark."""

    model_name: str = Field(description="Model architecture and identifier")
    symbol: str = Field(description="Benchmark stock symbol")
    horizon_days: int = Field(description="Prediction horizon in days")
    model_mae: float = Field(description="Mean Absolute Error of evaluated model")
    best_baseline_mae: float = Field(description="Best competing naive baseline MAE")
    baseline_type: str = Field(description="Type of competing naive baseline")
    mae_improvement_pct: float = Field(description="Percentage improvement over naive baseline")
    directional_accuracy: float = Field(description="Directional prediction accuracy (0.0 - 1.0)")
    naive_directional_accuracy: float = Field(description="Naive baseline directional accuracy")
    net_return_pct: float = Field(description="Net cumulative return percentage after friction")
    net_sharpe_ratio: float = Field(description="Net Sharpe ratio after friction")
    max_drawdown_pct: float = Field(description="Maximum drawdown percentage")
    beats_baseline: bool = Field(description="Whether model strictly beats naive baseline")
    beats_friction: bool = Field(description="Whether model survives realistic execution friction")
    is_approved: bool = Field(description="Whether model meets all deployment gate criteria")
    recommended_forecast_enabled: bool = Field(description="Recommended state of FORECAST_ENABLED")
    rejection_reasons: list[str] = Field(
        default_factory=list, description="Reasons for rejection if any"
    )


class ModelBaselineGateResponse(APIModel):
    """Pre-deployment verification report for model baseline and forecast kill-switch."""

    gate_status: str = Field(
        description="Overall gate outcome (PASS, PASS_WITH_FORECAST_DISABLED, etc.)"
    )
    timestamp: str = Field(description="Evaluation ISO timestamp")
    total_models_evaluated: int = Field(ge=0, description="Total models evaluated")
    models_approved_count: int = Field(ge=0, description="Approved models count")
    models_rejected_count: int = Field(ge=0, description="Rejected models count")
    forecast_enabled: bool = Field(description="Operational status of FORECAST_ENABLED flag")
    fallback_enforced_on_failure: bool = Field(description="Whether safe fallback is enforced")
    evaluated_models: list[EvaluatedModelResponse] = Field(
        default_factory=list, description="Evaluated models details"
    )
    blocking_reasons: list[str] = Field(
        default_factory=list, description="List of blocking deployment reasons"
    )
    sign_off_ready: bool = Field(description="Whether build is certified for release")
    published_report_path: str | None = Field(default=None, description="Path to published report")
    disclaimer: str = Field(description="SPK statutory disclaimer")


class UiTransparencyCheckResponse(APIModel):
    """Individual verification check result in UI transparency gate report."""

    check_id: str = Field(description="Unique check identifier (e.g. LAT-01, UNC-01)")
    category: str = Field(description="Transparency category (e.g. DATA_LATENCY)")
    title: str = Field(description="Human readable check title")
    description: str = Field(description="Detailed verification purpose")
    target_file: str = Field(description="Target file evaluated")
    required_elements: list[str] = Field(default_factory=list, description="Required DOM elements")
    required_patterns: list[str] = Field(
        default_factory=list, description="Required text/code patterns"
    )
    passed: bool = Field(description="Whether verification passed")
    details: str = Field(description="Technical verification details")
    violations: list[str] = Field(default_factory=list, description="Detected violations if any")


class UiTransparencyGateResponse(APIModel):
    """Pre-deployment verification report for UI Data Latency and Model Uncertainty."""

    gate_status: str = Field(description="Overall gate outcome (PASS or FAIL)")
    timestamp: str = Field(description="Evaluation timestamp")
    total_checks: int = Field(ge=0, description="Total UI transparency checks performed")
    passed_checks: int = Field(ge=0, description="Passed checks count")
    failed_checks: int = Field(ge=0, description="Failed checks count")
    data_latency_verified_count: int = Field(ge=0, description="Data latency checks passed")
    model_uncertainty_verified_count: int = Field(
        ge=0, description="Model uncertainty checks passed"
    )
    fallback_safety_verified_count: int = Field(ge=0, description="Fallback safety checks passed")
    checks: list[UiTransparencyCheckResponse] = Field(
        default_factory=list, description="Detailed checks breakdown"
    )
    blocking_violations: list[str] = Field(
        default_factory=list, description="Blocking violations that prevent deployment"
    )
    sign_off_ready: bool = Field(description="Whether UI transparency is certified for release")
    published_report_path: str | None = Field(
        default=None, description="Path to published audit report"
    )
    regulatory_framework: str = Field(description="Applicable statutory regulatory framework")


class BackupRestoreCheckResponse(APIModel):
    """Individual verification check result in backup and restoration drill report."""

    check_id: str = Field(description="Unique check identifier (e.g. BKUP-ENC-01)")
    category: str = Field(description="Category (e.g. ENCRYPTION_AND_KEY_DERIVATION)")
    title: str = Field(description="Human readable check title")
    description: str = Field(description="Detailed verification purpose")
    passed: bool = Field(description="Whether verification passed")
    details: str = Field(description="Technical verification details")
    violations: list[str] = Field(default_factory=list, description="Detected violations if any")


class BackupRestoreGateResponse(APIModel):
    """Pre-deployment verification report for database backup and restoration drills."""

    gate_status: str = Field(description="Overall gate outcome (PASS or FAIL)")
    timestamp: str = Field(description="Evaluation ISO timestamp")
    total_checks: int = Field(ge=0, description="Total backup/restoration checks performed")
    passed_checks: int = Field(ge=0, description="Passed checks count")
    failed_checks: int = Field(ge=0, description="Failed checks count")
    rto_seconds: float = Field(ge=0.0, description="Measured RTO restoration time in seconds")
    rto_limit_seconds: float = Field(ge=0.0, description="RTO limit in seconds (3600s)")
    rto_compliant: bool = Field(description="Whether measured RTO meets statutory SLA (< 1 hour)")
    rpo_compliant: bool = Field(description="Whether RPO point-in-time recovery target is met")
    tables_verified: int = Field(ge=0, description="Number of database tables verified")
    rows_verified: int = Field(ge=0, description="Total records verified with 1:1 parity")
    checks: list[BackupRestoreCheckResponse] = Field(
        default_factory=list, description="Detailed checks breakdown"
    )
    blocking_violations: list[str] = Field(
        default_factory=list, description="Blocking violations that prevent deployment"
    )
    sign_off_ready: bool = Field(
        description="Whether database backup/restore is certified for release"
    )
    published_report_path: str | None = Field(
        default=None, description="Path to published audit report"
    )
    regulatory_framework: str = Field(description="Applicable statutory regulatory framework")


class OperationsReadinessCheckResponse(APIModel):
    """Individual verification check result for operations readiness."""

    check_id: str = Field(description="Unique check identifier")
    category: str = Field(description="Check category")
    title: str = Field(description="Check title")
    description: str = Field(description="Detailed verification purpose")
    passed: bool = Field(description="Whether verification passed")
    details: str = Field(description="Technical verification details")
    violations: list[str] = Field(default_factory=list, description="Detected violations if any")


class OperationsReadinessGateResponse(APIModel):
    """Pre-deployment verification report for operations, alarms, IR and rollback readiness."""

    gate_status: str = Field(description="Overall gate outcome (PASS or FAIL)")
    timestamp: str = Field(description="Evaluation ISO timestamp")
    total_checks: int = Field(ge=0, description="Total operations checks performed")
    passed_checks: int = Field(ge=0, description="Passed checks count")
    failed_checks: int = Field(ge=0, description="Failed checks count")
    rollback_rto_seconds: float = Field(ge=0.0, description="Measured rollback duration in seconds")
    checks: list[OperationsReadinessCheckResponse] = Field(
        default_factory=list, description="Detailed checks breakdown"
    )
    audit_notes: list[str] = Field(
        default_factory=list, description="Audit and regulatory compliance notes"
    )


# --- Support and Feedback Schemas ---


class SupportTicketCreateRequest(APIModel):
    """Payload to create a new user support ticket or feedback submission."""

    email: str = Field(
        min_length=5,
        max_length=120,
        pattern=EMAIL_REGEX,
        description="User contact email for support resolution",
    )
    category: str = Field(
        description=(
            "Ticket category: bug, feature_request, data_issue, account, legal_privacy, general"
        )
    )
    subject: str = Field(
        min_length=3,
        max_length=120,
        description="Brief subject line summarizing the issue or request",
    )
    message: str = Field(
        min_length=10,
        max_length=3000,
        description="Detailed description of the issue, observation, or feedback",
    )
    client_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional diagnostic context (e.g. app version, browser, OS)",
    )


class SupportTicketResponse(APIModel):
    """Response confirming creation or retrieval of a support ticket."""

    ticket_id: str = Field(description="Unique tracking ticket reference code")
    email: str = Field(description="Submitted contact email")
    category: str = Field(description="Classified category")
    subject: str = Field(description="Ticket subject")
    message: str = Field(description="Ticket body")
    priority: str = Field(description="Assigned priority level (low, normal, high, urgent)")
    status: str = Field(description="Current ticket lifecycle status")
    created_at: str = Field(description="Creation UTC ISO timestamp")
    sla_response_hours: int = Field(description="Estimated response SLA in hours")
    advice_disclaimer_triggered: bool = Field(
        description="Whether query triggered investment advice advisory warning"
    )
    notes: list[str] = Field(default_factory=list, description="System advisory or routing notes")
    client_metadata: dict[str, Any] = Field(
        default_factory=dict, description="Diagnostic context metadata"
    )


class SupportChannelInfoResponse(APIModel):
    """Public information regarding official customer support channels and SLAs."""

    email: str = Field(description="Official support contact address")
    security_email: str = Field(description="Vulnerability and security disclosure address")
    operating_hours: str = Field(description="Service operating days and business hours")
    sla_standard_hours: int = Field(description="Standard issue response SLA in hours")
    sla_urgent_hours: int = Field(description="Urgent/security issue response SLA in hours")
    faq_url: str = Field(description="Link to frequently asked questions and user terms")
    statutory_disclaimer: str = Field(description="Mandatory SPK non-advice disclaimer")


class SupportCheckItemResponse(APIModel):
    """Readiness verification item for support infrastructure."""

    check_id: str = Field(description="Check identifier")
    name: str = Field(description="Check descriptive title")
    passed: bool = Field(description="Readiness pass status")
    details: str = Field(description="Technical configuration details")


class SupportGateResponse(APIModel):
    """Pre-deployment verification report for user support and feedback channels."""

    gate_status: str = Field(description="Overall gate outcome (PASS or FAIL)")
    total_checks: int = Field(ge=0, description="Total support checks performed")
    passed_checks: int = Field(ge=0, description="Passed checks count")
    failed_checks: int = Field(ge=0, description="Failed checks count")
    checks: list[SupportCheckItemResponse] = Field(
        default_factory=list, description="Verification checks breakdown"
    )
    timestamp: str = Field(description="Evaluation timestamp")


# --- Closed Beta & Phased Rollout Schemas ---


class BetaInviteValidateRequest(APIModel):
    """Request payload to validate a closed beta invite code."""

    code: str = Field(min_length=3, max_length=64, description="Invitation token to validate")


class BetaInviteValidateResponse(APIModel):
    """Validation response for closed beta invitation."""

    code: str = Field(description="Validated invitation code")
    is_valid: bool = Field(description="Whether the invitation code is currently usable")
    message: str = Field(description="Validation outcome message")


class BetaInviteRedeemRequest(APIModel):
    """Request payload to redeem a closed beta invitation."""

    code: str = Field(min_length=3, max_length=64, description="Invitation token to redeem")
    user_identifier: str = Field(
        min_length=3, max_length=128, description="User email or unique account ID"
    )
    agreed_to_terms: bool = Field(
        description="Mandatory user consent to KVKK policy and terms of service"
    )
    agreed_to_spk_disclaimer: bool = Field(
        description="Mandatory acknowledgment that service provides no investment advice"
    )


class BetaInviteRedeemResponse(APIModel):
    """Redemption confirmation for closed beta access."""

    success: bool = Field(description="Whether redemption succeeded")
    message: str = Field(description="Redemption details message")
    user_identifier: str = Field(description="Normalized user identifier added to allowlist")


class BetaRolloutStatusResponse(APIModel):
    """Current rollout stage and health status."""

    current_stage: str = Field(description="Active rollout stage (CLOSED_BETA, CANARY_5, etc.)")
    stage_percentage: float = Field(
        ge=0.0, le=100.0, description="Current public traffic percentage"
    )
    allowlist_size: int = Field(ge=0, description="Number of approved beta users on allowlist")
    circuit_breaker_active: bool = Field(description="Whether circuit breaker has halted rollout")
    circuit_breaker_reason: str | None = Field(
        default=None, description="Reason for emergency rollout halt, if active"
    )


class BetaCheckItemResponse(APIModel):
    """Individual verification check result for beta rollout."""

    check_id: str = Field(description="Check identifier")
    category: str = Field(description="Check category classification")
    title: str = Field(description="Descriptive check title")
    description: str = Field(description="Detailed verification purpose")
    passed: bool = Field(description="Whether check passed")
    details: str = Field(description="Technical check details")
    violations: list[str] = Field(default_factory=list, description="Detected violations if any")


class BetaRolloutGateResponse(APIModel):
    """Pre-deployment verification report for closed beta & phased rollout."""

    gate_status: str = Field(description="Overall gate outcome (PASS or FAIL)")
    timestamp: str = Field(description="Evaluation timestamp")
    total_checks: int = Field(ge=0, description="Total checks performed")
    passed_checks: int = Field(ge=0, description="Passed checks count")
    failed_checks: int = Field(ge=0, description="Failed checks count")
    current_stage: str = Field(description="Rollout stage during evaluation")
    circuit_breaker_active: bool = Field(description="Circuit breaker status")
    allowlist_size: int = Field(ge=0, description="Total allowlisted beta users")
    checks: list[BetaCheckItemResponse] = Field(
        default_factory=list, description="Verification checks breakdown"
    )
    blocking_violations: list[str] = Field(
        default_factory=list, description="Any critical violations found"
    )
    sign_off_ready: bool = Field(description="Whether release gate is ready for deployment")
    published_report_path: str | None = Field(
        default=None, description="Path where markdown audit report is saved"
    )
    regulatory_framework: str = Field(
        default="SPK Bilgi Sistemleri Tebliği VII-128.9 & KVKK m. 12",
        description="Statutory governance framework",
    )


class ComplianceCheckItemResponse(APIModel):
    """Detailed result of an individual statutory compliance check."""

    check_id: str = Field(description="Unique check identifier")
    category: str = Field(description="Statutory category (SPK, KVKK, Data License, etc.)")
    title: str = Field(description="Descriptive check title")
    status: str = Field(description="Check status: PASS, FAIL, or WARNING")
    details: str = Field(description="Technical check details and findings")
    statute_reference: str = Field(description="Underlying law, decree, or standard reference")


class LegalCounselReviewResponse(APIModel):
    """Accredited legal counsel statutory review and sign-off record."""

    review_id: str = Field(description="Official review identifier")
    counsel_name: str = Field(description="Full name and title of legal counsel")
    bar_registration: str = Field(description="Official Bar Association registration")
    firm_or_chamber: str = Field(description="Law firm or organization name")
    reviewed_at: str = Field(description="ISO timestamp of review execution")
    verdict: str = Field(description="Legal verdict (APPROVED, REJECTED, CONDITIONAL)")
    scope_reviewed: list[str] = Field(description="Statutory laws and regulations examined")
    summary: str = Field(description="Executive legal opinion summary")
    conditions_met: list[str] = Field(description="List of satisfied compliance conditions")
    signature_hash: str = Field(description="HMAC/cryptographic signature hash")
    compliance_doc_sha256: str = Field(
        default="", description="SHA-256 hash of compliance document at review"
    )
    is_valid: bool = Field(default=True, description="Whether counsel sign-off is currently valid")


class ComplianceGateResponse(APIModel):
    """Statutory pre-deployment compliance gate verification report."""

    timestamp: str = Field(description="ISO timestamp of evaluation")
    status: str = Field(description="Overall gate status (PASS or FAIL)")
    compliance_doc_checksum: str = Field(description="SHA-256 hash of docs/COMPLIANCE_TR.md")
    total_checks: int = Field(ge=0, description="Total statutory checks evaluated")
    passed_checks: int = Field(ge=0, description="Count of passed statutory checks")
    failed_checks: int = Field(ge=0, description="Count of failed statutory checks")
    counsel_review: LegalCounselReviewResponse = Field(
        description="Active legal counsel review and sign-off"
    )
    checks: list[ComplianceCheckItemResponse] = Field(
        default_factory=list, description="All itemized statutory check results"
    )
    violations: list[str] = Field(
        default_factory=list, description="Identified compliance violations if any"
    )


class AcceptanceCriterionItemResponse(APIModel):
    """Itemized evaluation result for an acceptance criterion."""

    criterion_id: str = Field(description="Unique criterion identifier")
    area: str = Field(description="Operational area (Veri, API, Web, Guvenlik, vb.)")
    criterion_name: str = Field(description="Descriptive criterion name")
    target_threshold: str = Field(description="Required acceptance threshold")
    actual_value: str = Field(description="Empirically measured benchmark or status")
    status: str = Field(description="Evaluation verdict: PASS or FAIL")
    evidence_source: str = Field(description="Test suite, benchmark, or telemetry source")
    phase_reference: str = Field(description="Correlated development phase (FAZ-XX)")
    details: str = Field(description="Detailed verification observations")


class AcceptanceCriteriaGateResponse(APIModel):
    """Pre-deployment verification report for docs/ACCEPTANCE_CRITERIA.md."""

    timestamp: str = Field(description="ISO timestamp of gate evaluation")
    gate_status: str = Field(description="Overall gate outcome (PASS or FAIL)")
    document_path: str = Field(description="Path to target acceptance criteria file")
    document_checksum: str = Field(description="SHA-256 hash of acceptance criteria file")
    total_criteria: int = Field(ge=0, description="Total criteria evaluated")
    passed_criteria: int = Field(ge=0, description="Total passing criteria count")
    failed_criteria: int = Field(ge=0, description="Total failing criteria count")
    all_passed: bool = Field(description="Whether 100% of criteria are passing")
    criteria: list[AcceptanceCriterionItemResponse] = Field(
        default_factory=list, description="List of itemized criteria evaluations"
    )
    blocking_violations: list[str] = Field(
        default_factory=list, description="Any critical failures or missing areas"
    )
    published_report_path: str | None = Field(
        default=None, description="Path where markdown audit report is saved"
    )

