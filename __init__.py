"""
Wraith Client - Python client for Wraith telemetry daemon.

Usage:
    from wraith_client import WraithClient
    
    client = WraithClient()
    client.tool_invoked("migrateiq", "scan")
    client.tool_succeeded("migrateiq", "scan", duration_ms=1234)
"""

from wraith_client.client import WraithClient, Level

__all__ = ["WraithClient", "Level"]
__version__ = "0.1.0"
