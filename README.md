# Wraith Client (Python)

Python client for the Wraith telemetry daemon.

## Installation

```bash
pip install wraith-client
```

Or install from source:

```bash
cd wraith-client-python
pip install -e .
```

## Usage

```python
from wraith_client import WraithClient

# Get the singleton client
client = WraithClient()

# Track a command invocation
client.tool_invoked("migrateiq", "scan")

# Track success with duration
client.tool_succeeded("migrateiq", "scan", duration_ms=1234)

# Track failure
client.tool_failed("migrateiq", "scan", error_type="HerokuAPIError", duration_ms=567)

# Track unhandled exception
client.exception_unhandled("migrateiq", "RuntimeError", traceback="...")

# Track validation failure
client.validation_failed("migrateiq", "terraform_validate", details="Invalid resource")
```

### Context Manager

The easiest way to track commands:

```python
from wraith_client import WraithClient

client = WraithClient()

with client.track_command("migrateiq", "scan"):
    # Your code here
    do_scan()
    # Automatically tracks invoked, succeeded/failed, and duration
```

## Configuration

### Opt-out

Users can opt out of telemetry:

```bash
# Environment variable
export INFRAIQ_TELEMETRY=false

# Or config file (~/.infraiq/config.json)
{
  "telemetry": false
}
```

### Custom Settings

```python
client = WraithClient(
    socket_path=Path("/custom/path/wraith.sock"),
    tool_version="1.0.0",
    auto_spawn=False,  # Don't spawn Wraith if not running
    enabled=True,      # Can disable programmatically
)
```

## Behavior

- **Fire-and-forget**: All operations are non-blocking and never raise exceptions
- **Silent failure**: If Wraith is not running, events are silently dropped
- **Auto-spawn**: By default, spawns Wraith daemon if not running
- **Singleton**: One client instance per process
- **Consent-aware**: Respects opt-out settings

## Events

| Method | When to use |
|--------|-------------|
| `tool_invoked` | At command start |
| `tool_succeeded` | On successful completion |
| `tool_failed` | On handled error |
| `exception_unhandled` | On unhandled crash |
| `validation_failed` | When output validation fails |

## License

MIT License
