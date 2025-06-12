"""Configure pytest to only use asyncio backend."""
import pytest

def pytest_configure(config):
    """Configure pytest to only use asyncio backend."""
    config.option.anyio_backend = "asyncio" 