from __future__ import annotations

from fastapi import APIRouter

from ..clients.plex import PlexNotConfigured, connect_plex
from ..clients.slskd import SlskdClient, SlskdError
from ..config import get_settings, update_settings
from ..schemas import ConnectionTestResult, SettingsUpdate, TestPlexRequest, TestSlskdRequest

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def read_settings():
    return get_settings().masked()


@router.put("")
def write_settings(payload: SettingsUpdate):
    settings = update_settings(payload.model_dump(exclude_unset=True))
    return settings.masked()


@router.post("/test-slskd", response_model=ConnectionTestResult)
def test_slskd(payload: TestSlskdRequest):
    """Tests connectivity against whatever URL/key is currently in the form,
    whether or not it's been saved yet."""
    settings = get_settings()
    api_key = payload.api_key or settings.slskd_api_key
    client = SlskdClient(payload.url, api_key)
    try:
        ok = client.health()
        return ConnectionTestResult(connected=ok, detail=None if ok else "no response from slskd")
    except SlskdError as exc:
        return ConnectionTestResult(connected=False, detail=str(exc))
    finally:
        client.close()


@router.post("/test-plex", response_model=ConnectionTestResult)
def test_plex(payload: TestPlexRequest):
    settings = get_settings()
    token = payload.token or settings.plex_token
    try:
        plex = connect_plex(payload.url, token)
        plex.library.sections()  # cheap call that proves auth + reachability
        return ConnectionTestResult(connected=True)
    except PlexNotConfigured as exc:
        return ConnectionTestResult(connected=False, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 — plexapi raises several distinct error types
        return ConnectionTestResult(connected=False, detail=str(exc))


@router.get("/detect-slskd", response_model=list[str])
def detect_slskd():
    """Tries a handful of common addresses for a locally-reachable slskd instance."""
    settings = get_settings()
    candidates = [
        "http://localhost:5030",
        "http://host.docker.internal:5030",
        "http://slskd:5030",
    ]
    found = []
    for url in candidates:
        client = SlskdClient(url, settings.slskd_api_key)
        try:
            if client.health():
                found.append(url)
        except SlskdError:
            pass
        finally:
            client.close()
    return found
