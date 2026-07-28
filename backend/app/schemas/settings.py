"""User-settings + device-token schemas (preference sync + APNs registration).

Preferences are stored as one JSONB blob so the ~20 heterogeneous client keys
(appearance + notification toggles + general prefs, currently @AppStorage on iOS)
can evolve without a schema change. The push service reads the `notify_*` keys.
"""

from pydantic import BaseModel
from typing import Any, Dict, Optional


class UserSettingsResponse(BaseModel):
    preferences: Dict[str, Any] = {}


class UpdateUserSettingsRequest(BaseModel):
    preferences: Dict[str, Any]


class DeviceRegisterRequest(BaseModel):
    token: str                       # hex-encoded APNs device token
    platform: str = "ios"
    environment: Optional[str] = None  # sandbox | production


class DeviceRegisterResponse(BaseModel):
    registered: bool
