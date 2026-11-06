"""Authentication, password security, and identity provider module."""

from stock_analysis.auth.brute_force import BruteForceDefender, brute_force_defender
from stock_analysis.auth.email_security import (
    DMARCPolicy,
    DomainAlignmentMode,
    EmailDeliverabilityRecord,
    EmailSecurityError,
    check_domain_alignment,
    generate_recommended_dns_records,
    validate_dkim_record,
    validate_dmarc_record,
    validate_outbound_email_security,
    validate_spf_record,
)
from stock_analysis.auth.email_verification import (
    EmailVerificationError,
    EmailVerificationManager,
    EmailVerificationTokenPayload,
    email_verification_manager,
)
from stock_analysis.auth.hasher import PasswordHasher, PasswordPolicy
from stock_analysis.auth.passkey import (
    PasskeyChallengeError,
    PasskeyError,
    PasskeyManager,
    PasskeyNotFoundError,
    PasskeyVerificationError,
    passkey_manager,
)
from stock_analysis.auth.provider import ExternalIdentity, IdentityProviderAdapter
from stock_analysis.auth.reset_flow import (
    PasswordResetManager,
    ResetFlowError,
    ResetTokenPayload,
    password_reset_manager,
)
from stock_analysis.auth.session import SessionSecurityPolicy, session_security_policy
from stock_analysis.auth.totp import (
    MFAError,
    MFASetupResult,
    TOTPManager,
    totp_manager,
)

__all__ = [
    "BruteForceDefender",
    "DMARCPolicy",
    "DomainAlignmentMode",
    "EmailDeliverabilityRecord",
    "EmailSecurityError",
    "EmailVerificationError",
    "EmailVerificationManager",
    "EmailVerificationTokenPayload",
    "ExternalIdentity",
    "IdentityProviderAdapter",
    "MFAError",
    "MFASetupResult",
    "PasswordHasher",
    "PasswordPolicy",
    "PasswordResetManager",
    "PasskeyChallengeError",
    "PasskeyError",
    "PasskeyManager",
    "PasskeyNotFoundError",
    "PasskeyVerificationError",
    "passkey_manager",
    "ResetFlowError",
    "ResetTokenPayload",
    "SessionSecurityPolicy",
    "TOTPManager",
    "brute_force_defender",
    "check_domain_alignment",
    "email_verification_manager",
    "generate_recommended_dns_records",
    "password_reset_manager",
    "session_security_policy",
    "totp_manager",
    "validate_dkim_record",
    "validate_dmarc_record",
    "validate_outbound_email_security",
    "validate_spf_record",
]
