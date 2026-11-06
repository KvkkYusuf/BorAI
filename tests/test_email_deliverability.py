"""Tests for email deliverability, DNS security (SPF, DKIM, DMARC), and anti-spoofing policies."""

import pytest

from stock_analysis.auth import (
    DMARCPolicy,
    DomainAlignmentMode,
    EmailSecurityError,
    check_domain_alignment,
    generate_recommended_dns_records,
    validate_dkim_record,
    validate_dmarc_record,
    validate_outbound_email_security,
    validate_spf_record,
)
from stock_analysis.auth.email_security import mask_email
from stock_analysis.config import Settings


def test_check_domain_alignment_relaxed() -> None:
    # Exact match
    assert check_domain_alignment("noreply@borsaanaliz.com", "borsaanaliz.com") is True
    # Subdomain to parent domain
    assert check_domain_alignment("noreply@mail.borsaanaliz.com", "borsaanaliz.com") is True
    # Two-level ccTLD (.com.tr)
    assert check_domain_alignment("security@mail.borsaanaliz.com.tr", "borsaanaliz.com.tr") is True
    # Complete mismatch / spoofing
    assert check_domain_alignment("attacker@phishing.com", "borsaanaliz.com") is False
    # Empty or malformed inputs
    assert check_domain_alignment("", "borsaanaliz.com") is False
    assert check_domain_alignment("invalid", "borsaanaliz.com") is False


def test_check_domain_alignment_strict() -> None:
    # Strict mode requires exact domain match
    assert (
        check_domain_alignment(
            "noreply@borsaanaliz.com", "borsaanaliz.com", mode=DomainAlignmentMode.STRICT
        )
        is True
    )
    # Subdomain fails in strict mode
    assert (
        check_domain_alignment(
            "noreply@mail.borsaanaliz.com", "borsaanaliz.com", mode=DomainAlignmentMode.STRICT
        )
        is False
    )


def test_validate_spf_record() -> None:
    # Valid SPF with -all (hard fail)
    valid, err = validate_spf_record("v=spf1 include:_spf.emailprovider.com -all")
    assert valid is True
    assert err is None

    # Valid SPF with ~all (soft fail)
    valid, err = validate_spf_record("v=spf1 ip4:192.0.2.1 include:relay.domain.com ~all")
    assert valid is True
    assert err is None

    # Insecure SPF (+all)
    valid, err = validate_spf_record("v=spf1 include:_spf.provider.com +all")
    assert valid is False
    assert "Insecure SPF configuration" in str(err)

    # Missing v=spf1
    valid, err = validate_spf_record("include:_spf.provider.com -all")
    assert valid is False
    assert "v=spf1" in str(err)

    # Missing terminal qualifier
    valid, err = validate_spf_record("v=spf1 include:_spf.provider.com")
    assert valid is False
    assert "standard qualifier" in str(err)

    # Lookup limit exceeded (RFC 7208 > 10 lookups)
    too_many = "v=spf1 " + " ".join([f"include:inc{i}.com" for i in range(12)]) + " -all"
    valid, err = validate_spf_record(too_many)
    assert valid is False
    assert "10-lookup limit" in str(err)


def test_validate_dkim_record() -> None:
    # Valid 2048-bit RSA public key record
    valid, err = validate_dkim_record(
        "v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0r123"
    )
    assert valid is True
    assert err is None

    # Valid Ed25519 key
    ed_key = "11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwYWnVe9240="
    valid, err = validate_dkim_record(f"v=DKIM1; k=ed25519; p={ed_key}")
    assert valid is True
    assert err is None

    # Missing p tag
    valid, err = validate_dkim_record("v=DKIM1; k=rsa;")
    assert valid is False
    assert "missing mandatory 'p='" in str(err)

    # Invalid version
    valid, err = validate_dkim_record("v=DKIM2; k=rsa; p=dummy")
    assert valid is False
    assert "Invalid DKIM version" in str(err)

    # Unsupported key algorithm
    valid, err = validate_dkim_record("v=DKIM1; k=dsa; p=dummy")
    assert valid is False
    assert "Unsupported DKIM key type" in str(err)


def test_validate_dmarc_record() -> None:
    # Valid strict DMARC
    valid, err = validate_dmarc_record(
        "v=DMARC1; p=reject; sp=reject; pct=100; aspf=r; adkim=r; rua=mailto:dmarc@borsaanaliz.com"
    )
    assert valid is True
    assert err is None

    # Valid quarantine policy
    valid, err = validate_dmarc_record("v=DMARC1; p=quarantine; pct=100")
    assert valid is True

    # Missing v=DMARC1
    valid, err = validate_dmarc_record("p=reject")
    assert valid is False
    assert "v=DMARC1" in str(err)

    # Missing policy
    valid, err = validate_dmarc_record("v=DMARC1; rua=mailto:dmarc@domain.com")
    assert valid is False
    assert "missing mandatory 'p='" in str(err)

    # Invalid policy value
    valid, err = validate_dmarc_record("v=DMARC1; p=invalid_policy")
    assert valid is False
    assert "Invalid DMARC policy" in str(err)

    # Invalid pct value
    valid, err = validate_dmarc_record("v=DMARC1; p=reject; pct=120")
    assert valid is False
    assert "between 0 and 100" in str(err)

    # Invalid rua (not mailto)
    valid, err = validate_dmarc_record("v=DMARC1; p=reject; rua=https://example.com/dmarc")
    assert valid is False
    assert "must start with 'mailto:'" in str(err)

    # Invalid alignment mode
    valid, err = validate_dmarc_record("v=DMARC1; p=reject; aspf=invalid")
    assert valid is False
    assert "alignment" in str(err)


def test_generate_recommended_dns_records() -> None:
    records = generate_recommended_dns_records(
        domain="borsaanaliz.com",
        spf_provider_include="_spf.resend.com",
        dkim_selector="default",
        dkim_public_key="MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC0",
        dmarc_rua="dmarc-reports@borsaanaliz.com",
        dmarc_policy=DMARCPolicy.REJECT,
    )
    assert len(records) == 3

    # SPF record check
    spf = next(r for r in records if r.host == "@")
    assert "v=spf1 include:_spf.resend.com -all" == spf.value
    valid_spf, _ = validate_spf_record(spf.value)
    assert valid_spf is True

    # DKIM record check
    dkim = next(r for r in records if r.host == "default._domainkey")
    assert "v=DKIM1; k=rsa; p=MIGf" in dkim.value
    valid_dkim, _ = validate_dkim_record(dkim.value)
    assert valid_dkim is True

    # DMARC record check
    dmarc = next(r for r in records if r.host == "_dmarc")
    assert "v=DMARC1; p=reject" in dmarc.value
    assert "rua=mailto:dmarc-reports@borsaanaliz.com" in dmarc.value
    valid_dmarc, _ = validate_dmarc_record(dmarc.value)
    assert valid_dmarc is True


def test_validate_outbound_email_security() -> None:
    # Success scenario
    validate_outbound_email_security(
        from_address="noreply@borsaanaliz.local",
        sender_domain="borsaanaliz.local",
        require_tls=True,
    )

    # Invalid format raises EmailSecurityError without exposing full PII
    with pytest.raises(EmailSecurityError, match=r"Invalid 'From' email address format: \*\*\*"):
        validate_outbound_email_security(
            from_address="plainstringwithoutat",
            sender_domain="borsaanaliz.local",
        )

    # Domain mismatch / spoofing raises EmailSecurityError with masked email
    with pytest.raises(EmailSecurityError, match=r"Email domain alignment violation"):
        validate_outbound_email_security(
            from_address="spoofed@evildomain.com",
            sender_domain="borsaanaliz.local",
        )

    # Insecure TLS disabled raises EmailSecurityError
    with pytest.raises(EmailSecurityError, match=r"Transactional emails must enforce TLS"):
        validate_outbound_email_security(
            from_address="noreply@borsaanaliz.local",
            sender_domain="borsaanaliz.local",
            require_tls=False,
        )


def test_mask_email_pii_defense() -> None:
    assert mask_email("user@example.com") == "u***@example.com"
    assert mask_email("a@b.com") == "a***@b.com"
    assert mask_email("invalid") == "***"
    assert mask_email("@domain.com") == "***@domain.com"


def test_settings_email_deliverability_defaults() -> None:
    settings = Settings()
    assert settings.email_from_address == "noreply@borsaanaliz.local"
    assert settings.email_sender_domain == "borsaanaliz.local"
    assert settings.email_dmarc_policy == "reject"
    assert settings.email_dkim_selector == "default"

    # Verify default config achieves domain alignment
    assert check_domain_alignment(settings.email_from_address, settings.email_sender_domain) is True
