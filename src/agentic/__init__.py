"""
AgentIC - AI-Driven Chip Design Framework
"""
import atexit
import os

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

try:
    from crewai.events.event_bus import crewai_event_bus

    atexit.unregister(crewai_event_bus.shutdown)
except Exception:
    pass

try:
    from crewai.telemetry.telemetry import Telemetry

    _telemetry = Telemetry()
    if hasattr(_telemetry, "_shutdown"):
        atexit.unregister(_telemetry._shutdown)
except Exception:
    pass

__version__ = "2.0.0"
