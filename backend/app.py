import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .config_store import (load_config, load_public_config, load_secrets_status,
                          preview_public_config, save_public_config, save_secrets)
from .ktx_monitor_service import KtxMonitorService
from .monitor_service import MonitorService
from .reservation_coordinator import ReservationCoordinator
from .schemas import (MonitorStatus, PublicConfig, PublicConfigUpdate, ReservationLockResolve,
                      ReservationLockStatus, SecretsPayload, SecretsStatus)
from .stations import ALL_STATIONS, KTX_STATIONS, SRT_STATIONS
from .routes import station_route_catalog, validate_route
from .unified_monitor_service import MonitorConflict, UnifiedMonitorService, merged_events


@asynccontextmanager
async def lifespan(app: FastAPI):
    coordinator = ReservationCoordinator()
    app.state.reservation_coordinator = coordinator
    app.state.srt_service = MonitorService(coordinator)
    app.state.ktx_service = KtxMonitorService(coordinator)
    config = load_public_config()
    app.state.unified_service = UnifiedMonitorService(
        app.state.srt_service, app.state.ktx_service, coordinator, config.connection_mode)
    yield
    await asyncio.gather(app.state.srt_service.stop(), app.state.ktx_service.stop(), return_exceptions=True)


app = FastAPI(title="Rail Monitor / 고속철도 통합 모니터", lifespan=lifespan)
LOOPBACK_CLIENTS = {"127.0.0.1", "::1", "localhost"}
ROOT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT_DIR / "frontend"
app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR), check_dir=False), name="assets")


@app.middleware("http")
async def require_loopback_client(request: Request, call_next):
    if (request.client.host if request.client else "") not in LOOPBACK_CLIENTS:
        return JSONResponse(status_code=403, content={"detail": "Rail Monitor API는 이 PC의 로컬 브라우저에서만 사용할 수 있습니다."})
    return await call_next(request)


app.add_middleware(CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


def get_srt(request: Request):
    return request.app.state.srt_service


def get_ktx(request: Request):
    return request.app.state.ktx_service


def get_coordinator(request: Request):
    return request.app.state.reservation_coordinator


def get_unified(request: Request):
    return request.app.state.unified_service


SrtService = Annotated[MonitorService, Depends(get_srt)]
KtxService = Annotated[KtxMonitorService, Depends(get_ktx)]
CoordinatorDep = Annotated[ReservationCoordinator, Depends(get_coordinator)]
UnifiedDep = Annotated[UnifiedMonitorService, Depends(get_unified)]


def _merged_status(srt, ktx, coordinator):
    return UnifiedMonitorService(srt, ktx, coordinator).status()


def _merged_events(srt, ktx):
    return merged_events(srt, ktx)


@app.exception_handler(MonitorConflict)
async def handle_conflict(request, exc):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(ValidationError)
async def handle_stored_validation_error(request, exc):
    # A model-level merge error must never echo stored credentials in its input.
    errors = [{"loc": ["body", *item["loc"]], "msg": item["msg"], "type": item["type"]}
              for item in exc.errors(include_input=False, include_url=False)]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.get("/", include_in_schema=False)
def get_frontend_app():
    return FileResponse(FRONTEND_DIR / "index.html", media_type="text/html")


@app.get("/api/config/public")
def get_public_config() -> PublicConfig:
    return load_public_config()


@app.post("/api/config/public")
async def update_public_config(public: PublicConfigUpdate, monitor: UnifiedDep) -> PublicConfig:
    async with monitor.operation_lock:
        monitor.ensure_idle()
        try:
            validate_route(preview_public_config(public))
        except ValueError as exc:
            if isinstance(exc, ValidationError):
                raise
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        saved = save_public_config(public)
        monitor.connection_mode = saved.connection_mode
        return saved


@app.get("/api/config/secrets")
def get_secrets_status() -> SecretsStatus:
    return load_secrets_status()


@app.post("/api/config/secrets")
async def update_secrets(secrets: SecretsPayload, monitor: UnifiedDep) -> SecretsStatus:
    async with monitor.operation_lock:
        monitor.ensure_idle()
        return save_secrets(secrets)


@app.get("/api/status")
def get_status(monitor: UnifiedDep) -> MonitorStatus:
    return monitor.status()


@app.get("/api/stations")
def get_stations():
    return ALL_STATIONS


@app.get("/api/station-routes")
def get_station_routes():
    return station_route_catalog()


@app.get("/api/stations/srt")
def get_srt_stations():
    return SRT_STATIONS


@app.get("/api/stations/ktx")
def get_ktx_stations():
    return KTX_STATIONS


@app.get("/api/events")
def get_events(monitor: UnifiedDep):
    return monitor.recent_events()


@app.post("/api/start")
async def start_monitor(public: PublicConfigUpdate, monitor: UnifiedDep):
    async with monitor.operation_lock:
        monitor.ensure_idle()
        config = preview_public_config(public)
        try:
            monitor.validate_start(config)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        save_public_config(public)
        return await monitor.start_prepared(config)


@app.post("/api/stop")
async def stop_monitor(monitor: UnifiedDep):
    return await monitor.stop()


@app.post("/api/reservation-lock/resolve")
def resolve_reservation_lock(payload: ReservationLockResolve, coordinator: CoordinatorDep) -> ReservationLockStatus:
    coordinator.resolve_uncertain(found=payload.found)
    return ReservationLockStatus(**coordinator.public_state())


def _events_after(events, last_id):
    if last_id is None:
        return events
    positions = {item.id: index for index, item in enumerate(events)}
    position = positions.get(last_id)
    return events[position + 1:] if position is not None else events


@app.get("/api/events/stream")
async def stream_events(request: Request, srt: SrtService, ktx: KtxService, skip: int = 0):
    async def event_generator():
        events = _merged_events(srt, ktx)
        resume_id = request.headers.get("last-event-id")
        last_id = resume_id
        initial = _events_after(events, resume_id) if resume_id else events[max(0, skip):]
        # A skipped or resumed backlog must not be replayed on the next poll.
        if not initial and events:
            last_id = events[-1].id
        for item in initial:
            yield f"id: {item.id}\ndata: {json.dumps(item.model_dump(), ensure_ascii=False)}\n\n"
            last_id = item.id
        while not await request.is_disconnected():
            for item in _events_after(_merged_events(srt, ktx), last_id):
                yield f"id: {item.id}\ndata: {json.dumps(item.model_dump(), ensure_ascii=False)}\n\n"
                last_id = item.id
            yield ": keepalive\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(event_generator(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})
