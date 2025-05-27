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

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


# Constants from LiveSync
SALT_OF_PASSPHRASE = "rHGMPtr6oWw7VSa3W3wpa8fT8U"
EDEN_ENCRYPTED_KEY = "h:++encrypted"


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
    try:
        iv_bytes: bytes
        salt_bytes: bytes
        encrypted_content_bytes: bytes
        iterations = 100000 # octagonal-wheels uses 100000 if autoCalculateIterations is false

        if encrypted_data.startswith("|%|"):
            # This is the V2 format with embedded IV and Salt (both 32 bytes raw)
            # Parsed from a single base64 block after the |%|
            iv_bytes, salt_bytes, encrypted_content_bytes = _parse_encrypted_data(encrypted_data)
        elif encrypted_data.startswith("%"):
            # This is the V2 format variant used by encrypt() in octagonal-wheels
            # %<IV_hex(32chars)><Salt_hex(32chars)><Ciphertext_base64>
            # IV is 16 bytes, Salt is 16 bytes.
            if len(encrypted_data) < 1 + 32 + 32 + 1: # % + hex_iv + hex_salt + min_1_char_base64
                raise ValueError("Encrypted data string (starting with '%') too short.")

            iv_hex = encrypted_data[1:33]
            salt_hex = encrypted_data[33:65]
            ciphertext_b64 = encrypted_data[65:]

            try:
                iv_bytes = bytes.fromhex(iv_hex) # 16 bytes
                salt_bytes = bytes.fromhex(salt_hex) # 16 bytes
            except ValueError as e_hex:
                raise ValueError(f"Failed to decode hex IV/Salt for '%' prefixed data: {e_hex}")

            if len(iv_bytes) != 16:
                raise ValueError(f"Decoded IV for '%' prefixed data is not 16 bytes (got {len(iv_bytes)})")
            if len(salt_bytes) != 16:
                raise ValueError(f"Decoded Salt for '%' prefixed data is not 16 bytes (got {len(salt_bytes)})")

            try:
                encrypted_content_bytes = base64.b64decode(ciphertext_b64)
            except base64.binascii.Error as e_b64:
                # If base64 decoding fails, it might be v1 JSON that coincidentally starts with %
                # and also failed the previous hex/length checks.
                # Fallback to v1 parsing.
                try:
                    iv_bytes, salt_bytes, encrypted_content_bytes = _parse_encrypted_data_v1(encrypted_data)
                except ValueError as ve_v1:
                    raise ValueError(f"Data starting with '%' failed hex/base64 parsing (IV/Salt/Ciphertext) and also failed v1 JSON parsing. Hex/B64 error: {e_b64}. V1 error: {ve_v1}")

        else:
            # Try v1 format (JSON array)
            iv_bytes, salt_bytes, encrypted_content_bytes = _parse_encrypted_data_v1(encrypted_data)
        
        # Derive the key
        # Note: _derive_key expects salt to be bytes, iterations defaults to 100000 in its own definition
        # but we pass it explicitly here for clarity from octagonal-wheels.
        key = _derive_key(passphrase, salt_bytes, iterations=iterations)
        
        aesgcm = AESGCM(key)
        
        # Nonce for AES-GCM:
        # octagonal-wheels TS passes the full 16-byte IV to WebCrypto's decrypt.
        # Let's try passing the full 16-byte IV to python-cryptography's AESGCM decrypt as the nonce.
        # While 12 bytes is typical for GCM, the library might support other lengths if consistent with encryption.
        nonce = iv_bytes # Use the full 16-byte IV as the nonce
        if len(nonce) not in [12, 16]: # Common GCM nonces are 12B, but WebCrypto might effectively use 16B if provided
             # Re-checking documentation, python-cryptography AESGCM nonce can be any length, but 12 is recommended for performance/security.
             # However, we must match what was used for encryption.
             pass # Allow it if it's 16 bytes, as per TS WebCrypto usage.

        first_try_error = None
        try:
            decrypted = aesgcm.decrypt(nonce, encrypted_content_bytes, None)
            return decrypted.decode('utf-8')
        except Exception as e_first:
            first_try_error = e_first
            pass 

        if first_try_error:
            # If the V2 style from octagonal-wheels encrypt() was `%<hexIV(16B)><hexSalt(16B)><Base64Cipher>`,
            # and it still failed with InvalidTag, the IV interpretation for nonce might be the issue.
            # WebCrypto's AES-GCM takes the full IV. Python's cryptography might need something specific if the 16B IV isn't just a 12B nonce + 4B counter.
            # However, 12-byte nonce is standard. The error is more likely key or data.
            raise ValueError(f"Decryption failed. Error: {repr(first_try_error)}")
        else:
            raise ValueError("Decryption failed due to an unknown issue within the decryption attempts.")
            
    except ValueError as ve: 
        raise ve 
    except Exception as e:
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
    except:
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