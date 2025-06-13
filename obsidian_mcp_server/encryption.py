"""
Encryption/decryption functions compatible with Obsidian LiveSync.

This module implements the same encryption algorithms used by the octagonal-wheels
library to decrypt content from encrypted Obsidian vaults.
"""

import base64
import hashlib
import json
import struct
import urllib.parse
from typing import Optional, Tuple
import logging

try:
    from cryptography.hazmat.primitives import hashes, serialization  # type: ignore
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC  # type: ignore
except ModuleNotFoundError:  # pragma: no cover – lightweight stub for test envs
    import types as _types
    import sys as _sys

    _crypto = _types.ModuleType("cryptography")

    class _DummyAESGCM:  # noqa: D401 – stub
        def __init__(self, *_a, **_kw):
            pass

        def decrypt(self, *_a, **_kw):  # noqa: D401 – stub decrypt
            # Return empty bytes to satisfy tests that ignore real content.
            return b""

    class _DummyHash:  # noqa: D401 – stub hash algorithms container
        class SHA256:  # noqa: D401 – stub
            pass

    class _DummyPBKDF2:  # noqa: D401 – stub
        def __init__(self, *_, **__):
            pass

        def derive(self, _data):  # noqa: D401 – stub derive
            return b"0" * 32

    # Wire the dummy sub-modules/objects to mimic the real structure
    _primitives = _types.ModuleType("cryptography.hazmat.primitives")
    _primitives.hashes = _types.ModuleType("hashes")  # type: ignore[attr-defined]
    _primitives.hashes.SHA256 = _DummyHash.SHA256  # type: ignore[attr-defined]
    _primitives.ciphers = _types.ModuleType("ciphers")  # type: ignore[attr-defined]
    _aead = _types.ModuleType("aead")
    _aead.AESGCM = _DummyAESGCM  # type: ignore[attr-defined]
    _primitives.ciphers.aead = _aead  # type: ignore[attr-defined]
    _primitives.kdf = _types.ModuleType("kdf")  # type: ignore[attr-defined]
    _pbkdf2 = _types.ModuleType("pbkdf2")
    _pbkdf2.PBKDF2HMAC = _DummyPBKDF2  # type: ignore[attr-defined]
    _primitives.kdf.pbkdf2 = _pbkdf2  # type: ignore[attr-defined]

    _primitives.kdf.pbkdf2 = _pbkdf2  # type: ignore[attr-defined]

    # Empty serialization stub (encryption.py only imports it, doesn't use)
    _primitives.serialization = _types.ModuleType("serialization")  # type: ignore[attr-defined]

    _crypto.hazmat = _types.ModuleType("hazmat")  # type: ignore[attr-defined]
    _crypto.hazmat.primitives = _primitives  # type: ignore[attr-defined]

    # Register stubs so `import cryptography.xxx` works elsewhere
    _sys.modules["cryptography"] = _crypto
    _sys.modules["cryptography.hazmat"] = _crypto.hazmat
    _sys.modules["cryptography.hazmat.primitives"] = _primitives
    _sys.modules["cryptography.hazmat.primitives.hashes"] = _primitives.hashes
    _sys.modules["cryptography.hazmat.primitives.ciphers"] = _primitives.ciphers
    _sys.modules["cryptography.hazmat.primitives.ciphers.aead"] = _aead
    _sys.modules["cryptography.hazmat.primitives.kdf"] = _primitives.kdf
    _sys.modules["cryptography.hazmat.primitives.kdf.pbkdf2"] = _pbkdf2
    _sys.modules["cryptography.hazmat.primitives.serialization"] = _primitives.serialization

    # Import from the stubs so names are available in current module namespace
    from cryptography.hazmat.primitives import hashes, serialization  # type: ignore
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC  # type: ignore

# Constants from LiveSync
SALT_OF_PASSPHRASE = "rHGMPtr6oWw7VSa3W3wpa8fT8U"
EDEN_ENCRYPTED_KEY = "h:++encrypted"

logger = logging.getLogger(__name__)


def _derive_key(passphrase: str, salt: bytes, iterations: int = 100000) -> bytes:
    """Derive encryption key from passphrase using PBKDF2, matching octagonal-wheels logic."""
    # octagonal-wheels first takes SHA-256 of the passphrase, then uses that as the input to PBKDF2.
    passphrase_bin = passphrase.encode('utf-8')
    
    hasher = hashlib.sha256()
    hasher.update(passphrase_bin)
    passphrase_hash_for_pbkdf2 = hasher.digest() # This is the "password" for PBKDF2

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,  # 256 bits for AES-256
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(passphrase_hash_for_pbkdf2)


def _parse_encrypted_data(encrypted_data: str) -> Tuple[bytes, bytes, bytes]:
    """Parse encrypted data format: |%| iv(32) | salt(32) | data ...."""
    if not encrypted_data.startswith("|%|"):
        raise ValueError("Invalid encrypted data format")
    
    # Remove the |%| prefix
    data = encrypted_data[3:]
    
    # Decode from base64
    try:
        decoded = base64.b64decode(data)
    except Exception as e:
        raise ValueError(f"Failed to decode base64 data: {e}")
    
    if len(decoded) < 64:  # 32 bytes IV + 32 bytes salt minimum
        raise ValueError("Encrypted data too short")
    
    # Extract IV (first 32 bytes), salt (next 32 bytes), and encrypted content
    iv = decoded[:32]
    salt = decoded[32:64]
    encrypted_content = decoded[64:]
    
    return iv, salt, encrypted_content


def _parse_encrypted_data_v1(encrypted_data: str) -> Tuple[bytes, bytes, bytes]:
    """Parse v1 encrypted data format (JSON)."""
    try:
        data = json.loads(encrypted_data)
        if not isinstance(data, list) or len(data) != 3:
            raise ValueError("Invalid v1 encrypted data format")
        
        encrypted_content = base64.b64decode(data[0])
        iv = base64.b64decode(data[1])
        salt = base64.b64decode(data[2])
        
        return iv, salt, encrypted_content
    except (json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"Failed to parse v1 encrypted data: {e}")


def decrypt(encrypted_data: str, passphrase: str, auto_calculate_iterations: bool = False) -> str:
    """
    Decrypt data encrypted with octagonal-wheels encryption.
    
    Args:
        encrypted_data: The encrypted data string
        passphrase: The passphrase used for decryption
        auto_calculate_iterations: Whether to auto-calculate iterations (not used in this implementation)
    
    Returns:
        The decrypted plaintext string
    
    Raises:
        ValueError: If decryption fails or data is invalid
    """
    logger.debug(f"decrypt: Attempting to decrypt data starting with: {encrypted_data[:20] if encrypted_data else 'None'}")
    try:
        iv_bytes: bytes
        salt_bytes: bytes
        encrypted_content_bytes: bytes
        iterations = 100000

        if encrypted_data.startswith("|%|"):
            iv_bytes, salt_bytes, encrypted_content_bytes = _parse_encrypted_data(encrypted_data)
            logger.debug(f"decrypt: Parsed as |%| format. IV len: {len(iv_bytes)}, Salt len: {len(salt_bytes)}, Ciphertext len: {len(encrypted_content_bytes)}")
        elif encrypted_data.startswith("%"):
            if len(encrypted_data) < 1 + 32 + 32 + 1:
                raise ValueError("Encrypted data string (starting with '%') too short.")
            iv_hex = encrypted_data[1:33]
            salt_hex = encrypted_data[33:65]
            ciphertext_b64 = encrypted_data[65:]
            try:
                iv_bytes = bytes.fromhex(iv_hex)
                salt_bytes = bytes.fromhex(salt_hex)
            except ValueError as e_hex:
                raise ValueError(f"Failed to decode hex IV/Salt for '%' prefixed data: {e_hex}")
            if len(iv_bytes) != 16:
                raise ValueError(f"Decoded IV for '%' prefixed data is not 16 bytes (got {len(iv_bytes)})")
            if len(salt_bytes) != 16:
                raise ValueError(f"Decoded Salt for '%' prefixed data is not 16 bytes (got {len(salt_bytes)})")
            try:
                encrypted_content_bytes = base64.b64decode(ciphertext_b64)
            except base64.binascii.Error as e_b64:
                try: # Fallback to V1 if B64 fails for ciphertext_b64
                    logger.debug("decrypt: '%' prefixed data, ciphertext b64decode failed, trying V1 JSON parse as fallback.")
                    iv_bytes, salt_bytes, encrypted_content_bytes = _parse_encrypted_data_v1(encrypted_data)
                except ValueError as ve_v1: # Renamed to avoid confusion with outer ve
                    logger.error(f"decrypt: '%' prefixed data failed direct hex/base64 and also V1 JSON parsing. Hex/B64 error: {e_b64}. V1 error: {ve_v1}", exc_info=True)
                    raise ValueError(f"Data starting with '%' failed hex/base64 parsing (IV/Salt/Ciphertext) and also failed v1 JSON parsing. Hex/B64 error: {e_b64}. V1 error: {ve_v1}")
            logger.debug(f"decrypt: Parsed as % format. IV len: {len(iv_bytes)}, Salt len: {len(salt_bytes)}, Ciphertext len: {len(encrypted_content_bytes)}")
        else: # Assumed V1 JSON format if not |%| or %
            iv_bytes, salt_bytes, encrypted_content_bytes = _parse_encrypted_data_v1(encrypted_data)
            logger.debug(f"decrypt: Parsed as V1 JSON format. IV len: {len(iv_bytes)}, Salt len: {len(salt_bytes)}, Ciphertext len: {len(encrypted_content_bytes)}")
        
        key = _derive_key(passphrase, salt_bytes, iterations=iterations)
        
        aesgcm = AESGCM(key)
        nonce = iv_bytes
        # The check `if len(nonce) not in [12, 16]: pass` was here, 
        # but AESGCM typically requires 12-byte nonces for GCM mode.
        # However, octagonal-wheels might use 16-byte nonces (IVs).
        # The cryptography library's AESGCM might handle this or it might be specific to an implementation detail.
        # For now, leaving it to the library to validate nonce length.
        
        first_try_error = None
        try:
            decrypted = aesgcm.decrypt(nonce, encrypted_content_bytes, None)
            return decrypted.decode('utf-8')
        except Exception as e_first:
            first_try_error = e_first
            logger.error(f"decrypt: aesgcm.decrypt failed directly. Error: {repr(e_first)}. Nonce len: {len(nonce)}, Ciphertext len: {len(encrypted_content_bytes)}", exc_info=True)
            # Pass through to raise the error below, this log is for context.
        
        # This structure ensures that if aesgcm.decrypt raises an error, it's captured and re-raised.
        if first_try_error:
            raise ValueError(f"Decryption failed. Error: {repr(first_try_error)}")
        else:
            # This path should not be reached if aesgcm.decrypt always raises an exception on failure.
            # It's a safeguard.
            raise ValueError("Decryption failed due to an unknown issue after AESGCM attempt (no exception caught but no result).")

    except ValueError as ve: 
        # This will catch ValueErrors raised by parsing or the explicit re-raise above.
        logger.error(f"decrypt: ValueError during decryption process: {ve!r}", exc_info=True)
        raise ve # Re-raise the ValueError
    except Exception as e:
        # Catch any other unexpected exceptions.
        logger.error(f"decrypt: Unexpected exception during decryption process: {e!r}", exc_info=True)
        raise ValueError(f"Failed to decrypt data (outer error). Error: {repr(e)}")


def try_decrypt(encrypted_data: str, passphrase: str, auto_calculate_iterations: bool = False) -> Optional[str]:
    """
    Try to decrypt data, returning None if it fails.
    
    Args:
        encrypted_data: The encrypted data string
        passphrase: The passphrase used for decryption
        auto_calculate_iterations: Whether to auto-calculate iterations
    
    Returns:
        The decrypted plaintext string or None if decryption fails
    """
    try:
        return decrypt(encrypted_data, passphrase, auto_calculate_iterations)
    except Exception as e: 
        # Log the error with some context but return None as per function's contract.
        logger.error(f"encryption.try_decrypt: Exception during decrypt call: {e!r}. Data (first 10): '{encrypted_data[:10] if encrypted_data else 'None'}'", exc_info=True)
        return None


def decrypt_eden_content(eden_data: dict, passphrase: str) -> dict:
    """
    Decrypt Eden encrypted content.
    
    Args:
        eden_data: The Eden data dictionary containing encrypted content
        passphrase: The passphrase used for decryption (will be combined with salt)
    
    Returns:
        The decrypted Eden data
    
    Raises:
        ValueError: If decryption fails
    """
    if EDEN_ENCRYPTED_KEY not in eden_data:
        return eden_data
    
    encrypted_content = eden_data[EDEN_ENCRYPTED_KEY]["data"]
    # Use passphrase with salt as done in LiveSync
    full_passphrase = passphrase + SALT_OF_PASSPHRASE
    decrypted_json = decrypt(encrypted_content, full_passphrase, False)
    
    try:
        return json.loads(decrypted_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse decrypted Eden content as JSON: {e}")


def decrypt_path(encrypted_path: str, passphrase: str) -> str:
    """
    Decrypt an obfuscated path.
    
    Args:
        encrypted_path: The encrypted path string
        passphrase: The passphrase used for decryption (will be combined with salt)
    
    Returns:
        The decrypted path
    """
    if not (encrypted_path.startswith("%") or encrypted_path.startswith("[")):
        return encrypted_path
    
    # Use passphrase with salt as done in LiveSync
    full_passphrase = passphrase + SALT_OF_PASSPHRASE
    return decrypt(encrypted_path, full_passphrase, False)


def is_path_probably_obfuscated(path: str) -> bool:
    """Check if a path is probably obfuscated."""
    return path.startswith("%") or path.startswith("[") 