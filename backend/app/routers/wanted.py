from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session, select

from ..clients.slskd import SlskdClient
from ..config import get_settings
from ..database import get_session
from ..models import WantedItem
from ..schemas import WantedCreate, WantedOut
from ..services.wanted import process_all_wanted, process_wanted_item

router = APIRouter(prefix="/api/wanted", tags=["wanted"])


@router.get("", response_model=list[WantedOut])
def list_wanted(session: Session = Depends(get_session)):
    return session.exec(select(WantedItem).order_by(WantedItem.created_at.desc())).all()


@router.post("", response_model=WantedOut)
def create_wanted(payload: WantedCreate, session: Session = Depends(get_session)):
    item = WantedItem(artist=payload.artist, album=payload.album, track=payload.track)
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@router.delete("/{wanted_id}", status_code=204)
def delete_wanted(wanted_id: int, session: Session = Depends(get_session)):
    item = session.get(WantedItem, wanted_id)
    if not item:
        raise HTTPException(status_code=404, detail="wanted item not found")
    session.delete(item)
    session.commit()


@router.post("/{wanted_id}/scan-now", response_model=WantedOut)
def scan_now(wanted_id: int, session: Session = Depends(get_session)):
    item = session.get(WantedItem, wanted_id)
    if not item:
        raise HTTPException(status_code=404, detail="wanted item not found")

    settings = get_settings()
    slskd = SlskdClient.from_settings(settings)
    try:
        process_wanted_item(session, item, slskd, settings)
    finally:
        slskd.close()
    session.refresh(item)
    return item


@router.post("/scan-all")
def scan_all(background_tasks: BackgroundTasks):
    def _run():
        settings = get_settings()
        from sqlmodel import Session as _Session

        from ..database import engine

        slskd = SlskdClient.from_settings(settings)
        try:
            with _Session(engine) as session:
                process_all_wanted(session, slskd, settings)
        finally:
            slskd.close()

    background_tasks.add_task(_run)
    return {"status": "scan started"}
