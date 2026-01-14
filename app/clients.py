from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import httpx
from aiomqtt import Client, MqttError


@dataclass(slots=True)
class LobaroConfig:
    base_url: str
    token: str
    timeout_s: float


class LobaroClient:
    def __init__(self, config: LobaroConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(base_url=config.base_url, timeout=config.timeout_s)

    @property
    def has_token(self) -> bool:
        return bool(self._config.token)

    async def close(self) -> None:
        await self._client.aclose()

    async def parse_meter_data(self, raw_hex: str, key_hex: str) -> dict:
        params = {"raw": raw_hex, "key": key_hex}
        headers = {
            "Authorization": f"Bearer {self._config.token}",
            "Accept": "application/json",
        }
        response = await self._client.post(
            "/api/mbus",
            params=params,
            headers=headers,
        )
        if response.status_code >= 400:
            raise LobaroResponseError(response.status_code, response.text)
        return response.json()


@dataclass(slots=True)
class MqttRuntimeConfig:
    url: str
    username: Optional[str]
    password: Optional[str]
    topic_template: str
    qos: int
    retain: bool


class MqttPublisher:
    def __init__(self, config: MqttRuntimeConfig, configured: bool = False) -> None:
        self._config = config
        self._lock = asyncio.Lock()
        self._last_ok = False
        self._configured = configured

    @property
    def config(self) -> MqttRuntimeConfig:
        return self._config

    @property
    def connected(self) -> bool:
        return self._last_ok

    @property
    def configured(self) -> bool:
        return self._configured

    def _build_client(self, config: MqttRuntimeConfig) -> Client:
        parsed = urlparse(config.url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 1883
        return Client(hostname=host, port=port, username=config.username, password=config.password)

    async def close(self) -> None:
        self._last_ok = False

    async def update_config(self, config: MqttRuntimeConfig) -> None:
        async with self._lock:
            self._config = config
            self._last_ok = False
            self._configured = True

    async def test_connection(self) -> None:
        async with self._lock:
            if not self._configured:
                raise MqttNotConfigured("mqtt_not_configured")
            try:
                async with self._build_client(self._config):
                    pass
                self._last_ok = True
            except MqttError as exc:
                self._last_ok = False
                raise MqttConnectError(f"{exc} (url={self._config.url})") from exc

    async def publish_json(self, topic: str, payload: dict) -> None:
        data = json.dumps(payload, separators=(",", ":"))
        await self.publish(topic, data)

    async def publish(self, topic: str, payload: str) -> None:
        async with self._lock:
            if not self._configured:
                raise MqttNotConfigured("mqtt_not_configured")
            try:
                async with self._build_client(self._config) as client:
                    await client.publish(topic, payload, qos=self._config.qos, retain=self._config.retain)
                self._last_ok = True
            except MqttError as exc:
                self._last_ok = False
                raise MqttPublishError(f"{exc} (url={self._config.url})") from exc


class MqttPublishError(Exception):
    pass


class MqttConnectError(Exception):
    pass


class MqttNotConfigured(Exception):
    pass


class LobaroResponseError(Exception):
    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"lobaro_http_{status_code}")
        self.status_code = status_code
        self.body = body


async def safe_publish(publisher: MqttPublisher, topic: str, payload: dict) -> None:
    await publisher.publish_json(topic, payload)
