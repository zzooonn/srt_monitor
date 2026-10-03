import uuid
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .stations import ALL_STATION_NAMES, KTX_STATION_NAMES, normalize_station


def migrate_public_data(data: dict) -> dict:
    """Normalize v1 fields once. Explicit v2 input always owns its main route."""
    result = dict(data)
    legacy = bool(result) and result.get("schema_version", 1) != 2 and "connection_mode" not in result
    notices = []
    if legacy:
        if result.get("monitor_ktx") and result.get("ktx_departure_station") and result.get("ktx_arrival_station"):
            result["departure_station"] = result["ktx_departure_station"]
            result["arrival_station"] = result["ktx_arrival_station"]
            for option in ("check_normal", "check_special"):
                if f"ktx_{option}" in result:
                    result[option] = result[f"ktx_{option}"]
            notices.append(f"이전 KTX 여정 {result['departure_station']} → {result['arrival_station']}을 통합 여정으로 이전했습니다.")
        else:
            notices.append("이전 공통 여정과 설정을 통합 설정으로 이전했습니다.")
        notices.append("코레일 연결을 기본으로 사용합니다. 계정 설정 여부는 화면에서 확인해 주세요. 이전 SRT 계정은 고급 설정에서 사용할 수 있습니다.")
        result["connection_mode"] = "korail"
    for name in ("departure_station", "arrival_station", "ktx_departure_station", "ktx_arrival_station"):
        if name in result:
            old = result[name]
            result[name] = normalize_station(old)
            if old == "사천":
                notices.append("이전 역 목록의 사천 표기를 같은 역 코드의 여천으로 바로잡았습니다.")
    result["schema_version"] = 2
    if notices:
        result["migration_notice"] = " ".join(notices)
    return result


def _tomorrow() -> str:
    return (date.today() + timedelta(days=1)).strftime("%Y%m%d")


class PublicConfig(BaseModel):
    """민감 필드를 제외한 공개 설정. API 경계에서 사용."""
    schema_version: Literal[2] = 2
    connection_mode: Literal["korail", "legacy_srt"] = "korail"
    migration_notice: str | None = None
    departure_date: str = Field(default_factory=_tomorrow, pattern=r"^\d{8}$")
    departure_time: str = Field(default="200000", pattern=r"^\d{6}$")
    max_departure_time: str = Field(default="230000", pattern=r"^\d{6}$")
    departure_station: str = "오송"
    arrival_station: str = "동탄"

    check_special: bool = True
    check_normal: bool = True

    monitor_ktx: bool = False
    ktx_departure_station: str = "서울"
    ktx_arrival_station: str = "부산"
    ktx_check_special: bool = True
    ktx_check_normal: bool = True

    adult_count: int = Field(default=2, ge=0, le=4)
    child_count: int = Field(default=0, ge=0, le=4)
    senior_count: int = Field(default=0, ge=0, le=4)
    disability_1_to_3_count: int = Field(default=0, ge=0, le=4)
    disability_4_to_6_count: int = Field(default=0, ge=0, le=4)

    require_same_row: bool = False
    require_same_car: bool = False
    require_adjacent_seats: bool = True
    prefer_window_seat: bool = True
    require_window_seat: bool = False
    seat_column_order: str = "ABCD"
    adjacent_seat_pairs: list[str] = Field(default_factory=lambda: ["AB", "CD"])
    window_seat_letters: list[str] = Field(default_factory=lambda: ["A", "D"])

    auto_reserve: bool = True
    max_reservations: int = Field(default=1, ge=1, le=20)
    auto_cancel_if_seat_mismatch: bool = True
    notify_if_seat_mismatch: bool = True

    poll_interval: int = Field(default=30, ge=10, le=600)
    heartbeat_interval: int = Field(default=1800, ge=60, le=86400)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy(cls, value):
        return migrate_public_data(value) if isinstance(value, dict) else value

    @field_validator("departure_date")
    @classmethod
    def validate_calendar_date(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%Y%m%d")
        except ValueError as exc:
            raise ValueError("실제 달력에 있는 날짜를 입력해 주세요.") from exc
        return value

    @field_validator("departure_station", "arrival_station")
    @classmethod
    def validate_station(cls, value: str) -> str:
        if value not in ALL_STATION_NAMES:
            raise ValueError(f"지원하지 않는 역입니다: {value}")
        return value

    @field_validator("departure_time", "max_departure_time")
    @classmethod
    def validate_quarter_hour(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%H%M%S")
        except ValueError as exc:
            raise ValueError("조회 시간은 00~23시, 00~59분 범위여야 합니다.") from exc
        minutes = int(value[2:4])
        seconds = int(value[4:6])
        if seconds != 0 or minutes not in {0, 15, 30, 45}:
            raise ValueError("조회 시간은 15분 단위(00, 15, 30, 45분)만 가능합니다.")
        return value

    @model_validator(mode="after")
    def validate_time_range(self) -> "PublicConfig":
        if self.departure_station == self.arrival_station:
            raise ValueError("출발역과 도착역은 서로 달라야 합니다.")
        if not self.check_normal and not self.check_special:
            raise ValueError("조회할 좌석종류를 하나 이상 선택해 주세요.")
        if not 1 <= self.passenger_count <= 4:
            raise ValueError("총 인원은 1~4명이어야 합니다.")
        if self.max_departure_time < self.departure_time:
            raise ValueError(
                f"마지막 출발 시간이 시작 출발 시간보다 빠릅니다. "
                f"(시작: {self.departure_time[:2]}:{self.departure_time[2:4]}, "
                f"마지막: {self.max_departure_time[:2]}:{self.max_departure_time[2:4]})"
            )
        return self

    @property
    def passenger_count(self) -> int:
        return (self.adult_count + self.child_count + self.senior_count
                + self.disability_1_to_3_count + self.disability_4_to_6_count)

class PublicConfigUpdate(PublicConfig):
    """Request-only partial update; validate relationships after stored-value merge.

    Inherited field validators still reject malformed values. Defaults must not
    participate in cross-field checks here: exclude_unset drops them, then
    preview_public_config/save_public_config build a fully validated MonitorConfig.
    """

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy(cls, value):
        if not isinstance(value, dict):
            return value
        # An unversioned HTTP patch is a current partial update, not an old
        # persisted configuration. Never inject a mode into omitted fields.
        request = dict(value)
        request.setdefault("schema_version", 2)
        return migrate_public_data(request)

    @model_validator(mode="after")
    def validate_time_range(self) -> "PublicConfigUpdate":
        return self


class MonitorConfig(PublicConfig):
    """민감 필드 포함 전체 설정. MonitorService 내부 전용."""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    srt_id: str = ""
    srt_password: str = ""
    korail_id: str = ""
    korail_password: str = ""


class SecretsPayload(BaseModel):
    """민감 필드 저장용. POST /api/config/secrets 요청 바디."""
    srt_id: str = ""
    srt_password: str = ""
    korail_id: str = ""
    korail_password: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


class SecretsStatus(BaseModel):
    """민감 필드 설정 여부. 값 자체는 포함하지 않는다."""
    srt_id_set: bool
    srt_password_set: bool
    korail_id_set: bool
    korail_password_set: bool
    telegram_bot_token_set: bool
    telegram_chat_id_set: bool


class ServicePhase(str, Enum):
    OFF = "off"
    SEARCHING = "searching"
    RESERVING = "reserving"
    NEEDS_CONFIRMATION = "needs_confirmation"
    GOAL_REACHED = "goal_reached"
    ERROR = "error"
    STOPPED = "stopped"


class ServiceStatus(BaseModel):
    running: bool
    phase: ServicePhase
    reserved_count: int
    last_message: str
    started_at: str | None = None
    error: str | None = None
    last_checked_at: str | None = None


class ReservationLockStatus(BaseModel):
    blocked: bool
    reason: Literal["stopped", "uncertain", "goal_reached"] | None
    count: int
    max: int
    uncertain_service: Literal["srt", "ktx"] | None = None


class MonitorStatus(ServiceStatus):
    connection_mode: Literal["korail", "legacy_srt"] = "korail"
    srt: ServiceStatus
    ktx: ServiceStatus
    reservation_lock: ReservationLockStatus


class ReservationLockResolve(BaseModel):
    found: bool


class EventItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    level: str
    message: str
    timestamp: str
    service: Literal["srt", "ktx"]
