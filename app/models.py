from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


class IngestTelegramRequest(BaseModel):
    gateway: str = ""
    status: int
    rssi: float
    lqi: int
    manuf: int
    id: str = Field(..., min_length=8)
    dev_type: int
    version: int
    ci: int
    payload_len: int
    logical_hex: str = Field(..., min_length=2)
    rx_time: Optional[datetime] = None

    @field_validator("logical_hex")
    @classmethod
    def validate_logical_hex(cls, value: str) -> str:
        if not _HEX_RE.match(value):
            raise ValueError("logical_hex_must_be_hex")
        if len(value) % 2 != 0:
            raise ValueError("logical_hex_must_be_even_length")
        return value

    @field_validator("id")
    @classmethod
    def validate_meter_id(cls, value: str) -> str:
        if not _HEX_RE.match(value):
            raise ValueError("meter_id_must_be_hex")
        if len(value) != 8:
            raise ValueError("meter_id_must_be_8_hex")
        return value


class IngestResponse(BaseModel):
    status: str
    meter_id: str
    mqtt_topic: Optional[str] = None


class MqttConfigPayload(BaseModel):
    url: str
    username: Optional[str] = None
    password: Optional[str] = None
    topic_template: str
    qos: int = 1
    retain: bool = False


class MqttConfigResponse(BaseModel):
    url: str
    username: Optional[str] = None
    topic_template: str
    qos: int
    retain: bool
    password_set: bool
    configured: bool
    locked_url: bool
    locked_username: bool
    locked_password: bool
    locked_topic: bool


class KeyPayload(BaseModel):
    key_hex: str

    @field_validator("key_hex")
    @classmethod
    def validate_key_hex(cls, value: str) -> str:
        if not _HEX_RE.match(value):
            raise ValueError("key_hex_must_be_hex")
        if len(value) != 32:
            raise ValueError("key_hex_must_be_32")
        return value
