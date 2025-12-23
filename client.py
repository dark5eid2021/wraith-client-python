"""
Wraith Client - Fire-and-forget telemetry client.

Sends events to the Wraith daemon over a Unix socket.
All operations are non-blocking and fail silently.
"""

import atexit
import json
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any


class Level(Enum):
    """Log level / severity of an event."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    FATAL = "FATAL"


class WraithClient:
    """
    Client for sending events to the Wraith telemetry daemon.
    
    All operations are fire-and-forget - they never block or raise exceptions.
    If Wraith is not running, events are silently dropped.
    
    Usage:
        client = WraithClient()
        client.tool_invoked("migrateiq", "scan")
        client.tool_succeeded("migrateiq", "scan", duration_ms=1234)
    """
    
    # Class-level singleton
    _instance: Optional["WraithClient"] = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        """Singleton pattern - one client per process."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(
        self,
        socket_path: Optional[Path] = None,
        tool_version: Optional[str] = None,
        auto_spawn: bool = True,
        enabled: bool = True,
    ):
        """
        Initialize the Wraith client.
        
        Args:
            socket_path: Custom socket path. Default: ~/.infraiq/wraith.sock
            tool_version: InfraIQ version string. Default: reads from package.
            auto_spawn: Whether to spawn Wraith if not running. Default: True
            enabled: Whether telemetry is enabled. Default: True
        """
        # Only initialize once
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        
        self._enabled = enabled and self._check_consent()
        self._socket_path = socket_path or self._default_socket_path()
        self._tool_version = tool_version or self._get_tool_version()
        self._installation_id = self._get_or_create_installation_id()
        self._auto_spawn = auto_spawn
        self._socket: Optional[socket.socket] = None
        self._connect_lock = threading.Lock()
        
        # Register cleanup
        atexit.register(self._cleanup)
    
    @staticmethod
    def _default_socket_path() -> Path:
        """Get default socket path."""
        return Path.home() / ".infraiq" / "wraith.sock"
    
    @staticmethod
    def _default_infraiq_dir() -> Path:
        """Get InfraIQ directory."""
        return Path.home() / ".infraiq"
    
    def _check_consent(self) -> bool:
        """Check if user has opted out of telemetry."""
        # Check environment variable
        if os.environ.get("INFRAIQ_TELEMETRY", "").lower() in ("0", "false", "no", "off"):
            return False
        
        # Check config file
        config_file = self._default_infraiq_dir() / "config.json"
        if config_file.exists():
            try:
                with open(config_file) as f:
                    config = json.load(f)
                    if config.get("telemetry") is False:
                        return False
            except Exception:
                pass
        
        return True
    
    def _get_tool_version(self) -> str:
        """Get InfraIQ version."""
        try:
            from importlib.metadata import version
            return version("infraiq-suite")
        except Exception:
            return "unknown"
    
    def _get_or_create_installation_id(self) -> str:
        """Get or create persistent installation ID."""
        id_file = self._default_infraiq_dir() / "installation_id"
        
        # Try to read existing
        if id_file.exists():
            try:
                installation_id = id_file.read_text().strip()
                if installation_id:
                    return installation_id
            except Exception:
                pass
        
        # Generate new
        installation_id = str(uuid.uuid4())
        
        # Save it
        try:
            id_file.parent.mkdir(parents=True, exist_ok=True)
            id_file.write_text(installation_id)
        except Exception:
            pass
        
        return installation_id
    
    def _build_context(self) -> Dict[str, Any]:
        """Build event context."""
        return {
            "installation_id": self._installation_id,
            "tool_version": self._tool_version,
            "python_version": platform.python_version(),
            "os": sys.platform,
            "os_version": platform.release(),
        }
    
    def _spawn_wraith(self) -> bool:
        """Spawn Wraith daemon if not running."""
        try:
            # Check if wraith binary is available
            wraith_path = self._find_wraith_binary()
            if not wraith_path:
                return False
            
            # Spawn as detached process
            pid = os.getpid()
            subprocess.Popen(
                [str(wraith_path), "--parent-pid", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            
            # Wait briefly for socket to be created
            for _ in range(10):
                time.sleep(0.1)
                if self._socket_path.exists():
                    return True
            
            return False
        except Exception:
            return False
    
    def _find_wraith_binary(self) -> Optional[Path]:
        """Find the wraith binary."""
        # Check common locations
        candidates = [
            Path.home() / ".infraiq" / "bin" / "wraith",
            Path("/usr/local/bin/wraith"),
            Path("/usr/bin/wraith"),
        ]
        
        # Also check PATH
        try:
            result = subprocess.run(
                ["which", "wraith"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                candidates.insert(0, Path(result.stdout.strip()))
        except Exception:
            pass
        
        for path in candidates:
            if path.exists() and os.access(path, os.X_OK):
                return path
        
        return None
    
    def _connect(self) -> bool:
        """Connect to Wraith socket."""
        with self._connect_lock:
            if self._socket is not None:
                return True
            
            # Check if socket exists
            if not self._socket_path.exists():
                if self._auto_spawn:
                    if not self._spawn_wraith():
                        return False
                else:
                    return False
            
            try:
                self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._socket.settimeout(1.0)
                self._socket.connect(str(self._socket_path))
                return True
            except Exception:
                self._socket = None
                return False
    
    def _send(self, message: Dict[str, Any]) -> bool:
        """Send a message to Wraith."""
        if not self._enabled:
            return False
        
        if not self._connect():
            return False
        
        try:
            data = json.dumps(message) + "\n"
            self._socket.sendall(data.encode())
            return True
        except Exception:
            # Connection lost, reset socket
            self._socket = None
            return False
    
    def _cleanup(self):
        """Cleanup on exit."""
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
    
    # =========================================================================
    # Public API - Event methods
    # =========================================================================
    
    def tool_invoked(self, tool: str, command: str, level: Level = Level.INFO) -> bool:
        """
        Record that a tool was invoked.
        
        Args:
            tool: Tool name (e.g., "migrateiq")
            command: Command name (e.g., "scan")
            level: Log level. Default: INFO
        
        Returns:
            True if event was sent, False otherwise (never raises).
        """
        return self._send({
            "level": level.value,
            "event_type": "tool_invoked",
            "tool": tool,
            "command": command,
            "context": self._build_context(),
        })
    
    def tool_succeeded(
        self,
        tool: str,
        command: str,
        duration_ms: int,
        level: Level = Level.INFO,
    ) -> bool:
        """
        Record that a tool completed successfully.
        
        Args:
            tool: Tool name
            command: Command name
            duration_ms: Duration in milliseconds
            level: Log level. Default: INFO
        
        Returns:
            True if event was sent.
        """
        return self._send({
            "level": level.value,
            "event_type": "tool_succeeded",
            "tool": tool,
            "command": command,
            "duration_ms": duration_ms,
            "context": self._build_context(),
        })
    
    def tool_failed(
        self,
        tool: str,
        command: str,
        error_type: str,
        duration_ms: int,
        level: Level = Level.ERROR,
    ) -> bool:
        """
        Record that a tool failed.
        
        Args:
            tool: Tool name
            command: Command name
            error_type: Type of error (class name, not message)
            duration_ms: Duration in milliseconds
            level: Log level. Default: ERROR
        
        Returns:
            True if event was sent.
        """
        return self._send({
            "level": level.value,
            "event_type": "tool_failed",
            "tool": tool,
            "command": command,
            "error_type": error_type,
            "duration_ms": duration_ms,
            "context": self._build_context(),
        })
    
    def exception_unhandled(
        self,
        tool: str,
        exception_type: str,
        traceback: Optional[str] = None,
        level: Level = Level.FATAL,
    ) -> bool:
        """
        Record an unhandled exception.
        
        Args:
            tool: Tool name
            exception_type: Exception class name
            traceback: Sanitized traceback (optional, should not contain sensitive data)
            level: Log level. Default: FATAL
        
        Returns:
            True if event was sent.
        """
        message = {
            "level": level.value,
            "event_type": "exception_unhandled",
            "tool": tool,
            "exception_type": exception_type,
            "context": self._build_context(),
        }
        if traceback:
            message["traceback"] = traceback
        
        return self._send(message)
    
    def validation_failed(
        self,
        tool: str,
        validation_type: str,
        details: Optional[str] = None,
        level: Level = Level.WARNING,
    ) -> bool:
        """
        Record that output validation failed.
        
        Args:
            tool: Tool name
            validation_type: Type of validation (e.g., "terraform_validate")
            details: Additional details (optional)
            level: Log level. Default: WARNING
        
        Returns:
            True if event was sent.
        """
        message = {
            "level": level.value,
            "event_type": "validation_failed",
            "tool": tool,
            "validation_type": validation_type,
            "context": self._build_context(),
        }
        if details:
            message["details"] = details
        
        return self._send(message)
    
    @contextmanager
    def track_command(self, tool: str, command: str):
        """
        Context manager for tracking command execution.
        
        Usage:
            with client.track_command("migrateiq", "scan"):
                # do work
                pass
        
        Automatically records invoked, succeeded/failed, and duration.
        """
        self.tool_invoked(tool, command)
        start_time = time.time()
        
        try:
            yield
            duration_ms = int((time.time() - start_time) * 1000)
            self.tool_succeeded(tool, command, duration_ms)
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            self.tool_failed(tool, command, type(e).__name__, duration_ms)
            raise


# Convenience function for global client
def get_client() -> WraithClient:
    """Get the global Wraith client instance."""
    return WraithClient()
