"""
Encryption Service (Step 12.1)

Encrypts:
- PII (Personally Identifiable Information)
- Phone numbers
- Email addresses
- Notes
- SSN (if collected)

Uses Fernet symmetric encryption from cryptography library.
"""

import base64
import hashlib
from typing import Optional
from cryptography.fernet import Fernet
from app.core.config import settings


# Generate a key from JWT_SECRET for consistency
def _get_encryption_key() -> bytes:
    """Derive an encryption key from the application secret."""
    secret = settings.JWT_SECRET.encode()
    key = hashlib.sha256(secret).digest()
    return base64.urlsafe_b64encode(key)


# Global Fernet instance
_fernet = None


def get_fernet() -> Fernet:
    """Get or create Fernet instance."""
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_get_encryption_key())
    return _fernet


def encrypt_value(value: str) -> str:
    """
    Encrypt a string value.

    Args:
        value: Plain text string to encrypt

    Returns:
        Encrypted string (base64 encoded)
    """
    if not value:
        return value

    f = get_fernet()
    encrypted = f.encrypt(value.encode())
    return encrypted.decode()


def decrypt_value(encrypted_value: str) -> str:
    """
    Decrypt an encrypted string value.

    Args:
        encrypted_value: Encrypted string to decrypt

    Returns:
        Decrypted plain text string
    """
    if not encrypted_value:
        return encrypted_value

    try:
        f = get_fernet()
        decrypted = f.decrypt(encrypted_value.encode())
        return decrypted.decode()
    except Exception:
        # If decryption fails, return as-is (might be unencrypted legacy data)
        return encrypted_value


def encrypt_pii(data: dict, fields: list) -> dict:
    """
    Encrypt specific PII fields in a dictionary.

    Args:
        data: Dictionary containing data
        fields: List of field names to encrypt

    Returns:
        Dictionary with encrypted fields
    """
    result = data.copy()
    for field in fields:
        if field in result and result[field]:
            result[field] = encrypt_value(str(result[field]))
    return result


def decrypt_pii(data: dict, fields: list) -> dict:
    """
    Decrypt specific PII fields in a dictionary.

    Args:
        data: Dictionary containing encrypted data
        fields: List of field names to decrypt

    Returns:
        Dictionary with decrypted fields
    """
    result = data.copy()
    for field in fields:
        if field in result and result[field]:
            result[field] = decrypt_value(result[field])
    return result


def mask_phone(phone: str) -> str:
    """
    Mask a phone number for display.

    Example: +15551234567 -> +1555***4567
    """
    if not phone or len(phone) < 7:
        return phone

    # Try to decrypt first
    decrypted = decrypt_value(phone)

    if len(decrypted) >= 10:
        return decrypted[:4] + "***" + decrypted[-4:]
    return "***"


def mask_email(email: str) -> str:
    """
    Mask an email for display.

    Example: john@example.com -> j***n@example.com
    """
    if not email or "@" not in email:
        return email

    # Try to decrypt first
    decrypted = decrypt_value(email)

    local, domain = decrypted.split("@", 1)
    if len(local) <= 2:
        return local[0] + "***@" + domain
    return local[0] + "***" + local[-1] + "@" + domain


def hash_value(value: str) -> str:
    """
    Create a one-way hash of a value for lookups.

    Used for searching encrypted fields without decrypting all records.
    """
    if not value:
        return value

    return hashlib.sha256(value.encode()).hexdigest()


def encrypt_lead_pii(lead_data: dict) -> dict:
    """
    Encrypt PII fields in lead data.
    """
    pii_fields = ["phone", "email", "first_name", "last_name"]
    return encrypt_pii(lead_data, pii_fields)


def decrypt_lead_pii(lead_data: dict) -> dict:
    """
    Decrypt PII fields in lead data.
    """
    pii_fields = ["phone", "email", "first_name", "last_name"]
    return decrypt_pii(lead_data, pii_fields)
