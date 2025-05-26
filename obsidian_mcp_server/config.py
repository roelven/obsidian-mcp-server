"""Configuration management for Obsidian MCP Server."""

from pydantic import Field
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
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8" 