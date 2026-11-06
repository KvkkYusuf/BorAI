"""Email deliverability, domain authentication (SPF, DKIM, DMARC), and anti-spoofing security."""

import re
from dataclasses import dataclass
from enum import StrEnum


class DomainAlignmentMode(StrEnum):
    """Alignment mode for SPF and DKIM per RFC 7489 (DMARC)."""

    STRICT = "strict"  # Exact domain match required
    RELAXED = "relaxed"  # Subdomains of organizational domain allowed


class DMARCPolicy(StrEnum):
    """DMARC receiver handling policies."""

    NONE = "none"  # Monitor only, no enforcement
    QUARANTINE = "quarantine"  # Treat as spam / quarantine
    REJECT = "reject"  # Hard rejection at SMTP layer


class EmailSecurityError(ValueError):
    """Raised when email security, domain alignment, or deliverability validation fails."""


@dataclass(frozen=True)
class EmailDeliverabilityRecord:
    """DNS record representation for email authentication."""

    record_type: str  # e.g., "TXT", "CNAME"
    host: str  # e.g., "@", "default._domainkey", "_dmarc"
    value: str  # record payload
    description: str


def mask_email(email: str) -> str:
    """Mask email address for logs or error messages (e.g. a***@x.com) to prevent PII exposure."""
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if not local:
        return f"***@{domain}"
    masked_local = local[0] + "***" if len(local) > 1 else local + "***"
    return f"{masked_local}@{domain}"


def extract_domain(email_or_domain: str) -> str:
    """Extract and normalize lowercase domain from an email or domain string."""
    clean = email_or_domain.strip().lower()
    if "@" in clean:
        return clean.split("@", 1)[1]
    return clean


def get_organizational_domain(domain: str) -> str:
    """Extract base/organizational domain from a domain string.

    Simple multi-part domain handling for common 2-level TLDs (e.g. .com.tr, .co.uk).
    """
    parts = domain.strip().lower().split(".")
    if len(parts) <= 2:
        return ".".join(parts)

    # Common multi-part country codes (e.g., com.tr, co.uk, net.tr)
    second_level_tlds = {"com.tr", "co.uk", "net.tr", "gov.tr", "org.tr", "edu.tr"}
    last_two = f"{parts[-2]}.{parts[-1]}"
    if last_two in second_level_tlds and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def check_domain_alignment(
    from_address: str,
    target_domain: str,
    mode: DomainAlignmentMode = DomainAlignmentMode.RELAXED,
) -> bool:
    """Verify alignment between RFC 5322 From address and SPF/DKIM target domain.

    - Strict mode: Exact domain match (e.g. 'borsaanaliz.com' == 'borsaanaliz.com').
    - Relaxed mode: Organizational match (e.g. 'mail.borsaanaliz.com' with 'borsaanaliz.com').
    """
    from_domain = extract_domain(from_address)
    target_domain_clean = extract_domain(target_domain)

    if not from_domain or not target_domain_clean:
        return False

    if mode == DomainAlignmentMode.STRICT:
        return from_domain == target_domain_clean

    # Relaxed mode: check organizational domain equality
    org_from = get_organizational_domain(from_domain)
    org_target = get_organizational_domain(target_domain_clean)
    return org_from == org_target


def validate_spf_record(spf_record: str) -> tuple[bool, str | None]:
    """Validate SPF record syntax and security compliance per RFC 7208.

    Returns (is_valid, error_reason).
    """
    record = spf_record.strip()
    if not record.startswith("v=spf1"):
        return False, "SPF record must start with 'v=spf1'"

    # Dangerous +all qualifier check
    if re.search(r"(^|\s)\+all(\s|$)", record):
        return False, "Insecure SPF configuration: '+all' permits unauthorized spoofing"

    tokens = record.split()

    # Must specify a terminal qualifier: -all, ~all, ?all
    terminals = {"-all", "~all", "?all"}
    has_terminal = any(t in terminals for t in tokens)
    if not has_terminal:
        return False, "SPF record must end with a standard qualifier (e.g. -all or ~all)"

    # RFC 7208 lookup limit: maximum 10 DNS-querying mechanisms
    lookup_mechanisms = {"include:", "a", "mx", "ptr", "exists:", "redirect="}
    lookup_count = 0
    for token in tokens[1:]:
        clean_token = token.lstrip("+-~?")
        for mech in lookup_mechanisms:
            if clean_token.startswith(mech) or clean_token == mech:
                lookup_count += 1
                break

    if lookup_count > 10:
        return (
            False,
            f"SPF record exceeds RFC 7208 10-lookup limit (found {lookup_count} lookups)",
        )

    return True, None


def validate_dkim_record(dkim_record: str) -> tuple[bool, str | None]:
    """Validate DKIM public key DNS TXT record syntax per RFC 6376.

    Returns (is_valid, error_reason).
    """
    record = dkim_record.strip()
    tags: dict[str, str] = {}
    for item in record.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        k, v = item.split("=", 1)
        tags[k.strip()] = v.strip()

    # v tag is optional in some implementations, but if present must be DKIM1
    if "v" in tags and tags["v"] != "DKIM1":
        return False, f"Invalid DKIM version '{tags['v']}', expected 'DKIM1'"

    # p tag (public key) is mandatory
    if "p" not in tags or not tags["p"]:
        return False, "DKIM record missing mandatory 'p=' public key tag"

    # Key type check (default is rsa)
    key_type = tags.get("k", "rsa").lower()
    if key_type not in {"rsa", "ed25519"}:
        return False, f"Unsupported DKIM key type '{key_type}' (expected rsa or ed25519)"

    return True, None


def validate_dmarc_record(dmarc_record: str) -> tuple[bool, str | None]:
    """Validate DMARC DNS TXT record syntax per RFC 7489.

    Returns (is_valid, error_reason).
    """
    record = dmarc_record.strip()
    if not record.startswith("v=DMARC1"):
        return False, "DMARC record must start with 'v=DMARC1'"

    tags: dict[str, str] = {}
    for item in record.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        k, v = item.split("=", 1)
        tags[k.strip()] = v.strip()

    # p tag (policy) is mandatory
    if "p" not in tags:
        return False, "DMARC record missing mandatory 'p=' policy tag"

    valid_policies = {"none", "quarantine", "reject"}
    if tags["p"].lower() not in valid_policies:
        return False, f"Invalid DMARC policy '{tags['p']}', must be one of {valid_policies}"

    # sp tag (subdomain policy) if present
    if "sp" in tags and tags["sp"].lower() not in valid_policies:
        return False, f"Invalid DMARC subdomain policy '{tags['sp']}'"

    # pct tag (percentage) must be 0..100
    if "pct" in tags:
        try:
            pct_val = int(tags["pct"])
            if not 0 <= pct_val <= 100:
                return False, f"DMARC pct tag must be between 0 and 100 (got {pct_val})"
        except ValueError:
            return False, f"Invalid DMARC pct value '{tags['pct']}'"

    # rua tag (aggregate reporting) if present should be mailto:
    if "rua" in tags:
        uris = [u.strip() for u in tags["rua"].split(",")]
        for uri in uris:
            if not uri.startswith("mailto:"):
                return False, f"DMARC rua URI '{uri}' must start with 'mailto:'"

    # Alignment modes (aspf, adkim) if present must be 'r' (relaxed) or 's' (strict)
    for align_tag in ["aspf", "adkim"]:
        if align_tag in tags and tags[align_tag].lower() not in {"r", "s"}:
            mode = tags[align_tag]
            return False, f"Invalid DMARC {align_tag} alignment '{mode}', must be 'r' or 's'"

    return True, None


def generate_recommended_dns_records(
    domain: str,
    spf_provider_include: str,
    dkim_selector: str,
    dkim_public_key: str,
    dmarc_rua: str,
    dmarc_policy: DMARCPolicy = DMARCPolicy.REJECT,
) -> list[EmailDeliverabilityRecord]:
    """Generate the standard trio of DNS records (SPF, DKIM, DMARC) for a given domain."""
    extract_domain(domain)
    rua_uri = dmarc_rua if dmarc_rua.startswith("mailto:") else f"mailto:{dmarc_rua}"
    dmarc_val = (
        f"v=DMARC1; p={dmarc_policy.value}; sp={dmarc_policy.value}; "
        f"pct=100; aspf=r; adkim=r; rua={rua_uri}"
    )

    return [
        EmailDeliverabilityRecord(
            record_type="TXT",
            host="@",
            value=f"v=spf1 include:{spf_provider_include} -all",
            description="SPF: Authorizes only specified email provider; hard fail for unauthorized",
        ),
        EmailDeliverabilityRecord(
            record_type="TXT",
            host=f"{dkim_selector}._domainkey",
            value=f"v=DKIM1; k=rsa; p={dkim_public_key}",
            description=f"DKIM: 2048-bit cryptographic key for selector '{dkim_selector}'",
        ),
        EmailDeliverabilityRecord(
            record_type="TXT",
            host="_dmarc",
            value=dmarc_val,
            description="DMARC: Rejects spoofed emails and enforces 100% alignment",
        ),
    ]


def validate_outbound_email_security(
    from_address: str,
    sender_domain: str,
    require_tls: bool = True,
    alignment_mode: DomainAlignmentMode = DomainAlignmentMode.RELAXED,
) -> None:
    """Validate outbound email security parameters before dispatching transactional email.

    Ensures sender alignment and security compliance without logging plain PII.
    """
    if "@" not in from_address:
        msg = f"Invalid 'From' email address format: {mask_email(from_address)}"
        raise EmailSecurityError(msg)

    if not check_domain_alignment(from_address, sender_domain, mode=alignment_mode):
        masked_from = mask_email(from_address)
        msg = (
            f"Email domain alignment violation: From address '{masked_from}' "
            f"does not align with configured sender domain '{sender_domain}' "
            f"({alignment_mode.value} mode)"
        )
        raise EmailSecurityError(msg)

    if not require_tls:
        msg = "Insecure transmission: Transactional emails must enforce TLS 1.2+"
        raise EmailSecurityError(msg)
