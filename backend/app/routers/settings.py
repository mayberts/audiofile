from __future__ import annotations

from fastapi import APIRouter

from ..clients.slskd import SlskdClient
from ..config import get_settings, update_settings
from ..schemas import SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def read_settings():
    return get_settings().masked()


@router.put("")
def write_settings(payload: SettingsUpdate):
    settings = update_settings(payload.model_dump(exclude_unset=True))
    return settings.masked()


@router.get("/slskd-status")
def slskd_status():
    settings = get_settings()
    client = SlskdClient(settings)
    try:
        ok = client.health()
    finally:
        client.close()
    return {"connected": ok}
