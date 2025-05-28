"""Configuration management for Obsidian MCP Server."""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration settings for the Obsidian MCP Server."""
    
    # CouchDB connection settings
    couchdb_base_url: str = Field(
        ...,
        description="Base URL for CouchDB instance (e.g., https://vault.w22.io/e=_)"
    )
    couchdb_database_name: str = Field(
        ...,
        description="Name of the CouchDB database used by LiveSync"
    )
    couchdb_user: str = Field(
        ...,
        description="Username for CouchDB authentication"
    )
    couchdb_password: str = Field(
        ...,
        description="Password for CouchDB authentication"
    )
    
    # Server settings
    server_port: int = Field(
        default=8000,
        description="Port for the MCP server to listen on"
    )
    api_key: str = Field(
        ...,
        description="API key for authenticating MCP clients"
    )
    
    # LiveSync settings
    use_path_obfuscation: bool = Field(
        default=False,
        description="Whether LiveSync uses path obfuscation"
    )
    
    # Optional vault identifier for URI construction
    vault_id: str = Field(
        default="default",
        description="Identifier for the vault (used in URI construction)"
    )
    
    # Rate limiting settings
    rate_limit_requests_per_minute: int = Field(
        default=60,
        description="Maximum requests per minute per client"
    )
    rate_limit_burst_size: int = Field(
        default=10,
        description="Maximum burst requests allowed"
    )
    
    # Encryption settings
    vault_passphrase: str = Field(
        default="",
        description="Passphrase for encrypted vault (if applicable)"
    )

    @field_validator("vault_passphrase")
    @classmethod
    def strip_vault_passphrase(cls, v: str) -> str:
        if v:
            return v.strip()
        return v
    
    couchdb_list_limit_for_path_search: int = Field(
        default=500,
        description="Maximum number of recent notes to scan when direct path lookup fails or path obfuscation is on."
    )
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8" 