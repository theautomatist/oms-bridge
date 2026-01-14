from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from app.clients import (
    LobaroClient,
    LobaroConfig,
    LobaroResponseError,
    MqttConnectError,
    MqttNotConfigured,
    MqttPublishError,
    MqttPublisher,
    MqttRuntimeConfig,
    safe_publish,
)
from app.config import get_settings
from app.store import SqliteStore
from app.models import (
    IngestResponse,
    IngestTelegramRequest,
    KeyPayload,
    MqttConfigPayload,
    MqttConfigResponse,
)

logger = logging.getLogger(__name__)
LOBARO_BASE_URL = "https://platform.lobaro.com"
DEFAULT_MQTT_URL = "mqtt://localhost:1883"
DEFAULT_MQTT_TOPIC = "oms/v1/gw/{gateway_id}/meter/{meter_id}/reading"
DEFAULT_MQTT_QOS = 1
DEFAULT_MQTT_RETAIN = False


def _utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).isoformat()
    return dt.astimezone(timezone.utc).isoformat()


def _configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).setLevel(level)


def _normalize_mqtt_url(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return ""
    if "://" not in trimmed:
        return f"mqtt://{trimmed}"
    return trimmed


def create_app() -> FastAPI:
    _configure_logging()
    settings = get_settings()

    app = FastAPI(title="OMS Parser Bridge")

    raw_mqtt_url = (settings.mqtt_url or "")
    lock_url = bool(raw_mqtt_url.strip())
    lock_username = bool((settings.mqtt_username or "").strip())
    lock_password = bool((settings.mqtt_password or "").strip())
    lock_topic = bool((settings.mqtt_topic_template or "").strip())
    app.state.mqtt_env_locks = {
        "url": lock_url,
        "username": lock_username,
        "password": lock_password,
        "topic": lock_topic,
    }
    app.state.mqtt_source = "none"

    app.state.mqtt = MqttPublisher(
        MqttRuntimeConfig(
            url=DEFAULT_MQTT_URL,
            username=None,
            password=None,
            topic_template=DEFAULT_MQTT_TOPIC,
            qos=DEFAULT_MQTT_QOS,
            retain=DEFAULT_MQTT_RETAIN,
        ),
        configured=False,
    )
    app.state.store = SqliteStore(settings.keys_db_path)
    app.state.lobaro = LobaroClient(
        LobaroConfig(
            base_url=LOBARO_BASE_URL,
            token=settings.lobaro_token,
            timeout_s=settings.lobaro_timeout_s,
        )
    )

    app.mount("/ui", StaticFiles(directory="app/static", html=True), name="ui")
    app.mount("/static", StaticFiles(directory="app/static", html=True), name="static")

    @app.middleware("http")
    async def log_unhandled_errors(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            logger.exception(
                "request_failed",
                extra={"path": request.url.path, "method": request.method},
            )
            print(f"request_failed: {request.method} {request.url.path} {exc!r}", file=sys.stderr)
            return JSONResponse(status_code=500, content={"detail": "internal_error"})

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_error",
            extra={"path": request.url.path, "method": request.method},
        )
        print(f"unhandled_error: {request.method} {request.url.path} {exc!r}", file=sys.stderr)
        return JSONResponse(status_code=500, content={"detail": "internal_error"})

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/ui/")

    @app.on_event("startup")
    async def on_startup() -> None:
        await app.state.store.init()
        stored = await app.state.store.get_mqtt_config()
        mqtt_url = _normalize_mqtt_url(raw_mqtt_url) or ""
        mqtt_user = (settings.mqtt_username or "").strip() or None
        mqtt_pass = (settings.mqtt_password or "").strip() or None
        env_topic = (settings.mqtt_topic_template or "").strip() or None

        if stored:
            stored_url = _normalize_mqtt_url(stored.url) or stored.url
            merged = MqttRuntimeConfig(
                url=mqtt_url if lock_url else stored_url,
                username=mqtt_user if lock_username else stored.username,
                password=mqtt_pass if lock_password else stored.password,
                topic_template=env_topic if lock_topic else stored.topic_template,
                qos=stored.qos,
                retain=stored.retain,
            )
            await app.state.mqtt.update_config(merged)
            if lock_url or lock_username or lock_password:
                await app.state.store.set_mqtt_config(merged)
                app.state.mqtt_source = "db+env"
                logger.info("mqtt_config_merged_with_env")
            else:
                app.state.mqtt_source = "db"
        elif mqtt_url:
            env_config = MqttRuntimeConfig(
                url=mqtt_url,
                username=mqtt_user,
                password=mqtt_pass,
                topic_template=env_topic or DEFAULT_MQTT_TOPIC,
                qos=settings.mqtt_qos,
                retain=settings.mqtt_retain,
            )
            await app.state.mqtt.update_config(env_config)
            await app.state.store.set_mqtt_config(env_config)
            app.state.mqtt_source = "env"
            logger.info("mqtt_config_loaded_from_env")
        else:
            app.state.mqtt_source = "none"
        if not app.state.lobaro.has_token:
            logger.warning("lobaro_token_missing")
        logger.info(
            "store_initialized",
            extra={
                "db_path": settings.keys_db_path,
                "mqtt_source": app.state.mqtt_source,
                "mqtt_configured": app.state.mqtt.configured,
                "mqtt_url": app.state.mqtt.config.url if app.state.mqtt.configured else "",
                "mqtt_topic": app.state.mqtt.config.topic_template if app.state.mqtt.configured else "",
            },
        )

    @app.get("/api/mqtt", response_model=MqttConfigResponse)
    async def get_mqtt_config() -> MqttConfigResponse:
        stored = await app.state.store.get_mqtt_config()
        if stored:
            config = stored
            configured = True
        elif app.state.mqtt.configured:
            config = app.state.mqtt.config
            configured = True
        else:
            config = app.state.mqtt.config
            configured = False
        locks = app.state.mqtt_env_locks
        return MqttConfigResponse(
            url=config.url,
            username=config.username,
            topic_template=config.topic_template,
            qos=config.qos,
            retain=config.retain,
            password_set=bool(config.password) if configured else False,
            configured=configured,
            locked_url=locks["url"],
            locked_username=locks["username"],
            locked_password=locks["password"],
            locked_topic=locks["topic"],
        )

    @app.put("/api/mqtt", response_model=MqttConfigResponse)
    async def update_mqtt_config(payload: MqttConfigPayload) -> MqttConfigResponse:
        current = app.state.mqtt.config
        locks = app.state.mqtt_env_locks
        normalized_url = _normalize_mqtt_url(payload.url) or payload.url
        if locks["url"] and payload.url != current.url:
            raise HTTPException(status_code=400, detail="mqtt_url_locked")
        if locks["username"] and payload.username != current.username:
            raise HTTPException(status_code=400, detail="mqtt_username_locked")
        if locks["topic"] and payload.topic_template != current.topic_template:
            raise HTTPException(status_code=400, detail="mqtt_topic_locked")
        if locks["password"]:
            if payload.password and payload.password != current.password:
                raise HTTPException(status_code=400, detail="mqtt_password_locked")
        password = payload.password
        if password is None or password == "":
            password = current.password
        new_config = MqttRuntimeConfig(
            url=normalized_url,
            username=payload.username,
            password=password,
            topic_template=payload.topic_template,
            qos=payload.qos,
            retain=payload.retain,
        )
        await app.state.store.set_mqtt_config(new_config)
        await app.state.mqtt.update_config(new_config)
        return MqttConfigResponse(
            url=new_config.url,
            username=new_config.username,
            topic_template=new_config.topic_template,
            qos=new_config.qos,
            retain=new_config.retain,
            password_set=bool(new_config.password),
            configured=True,
            locked_url=locks["url"],
            locked_username=locks["username"],
            locked_password=locks["password"],
            locked_topic=locks["topic"],
        )

    @app.post("/api/mqtt/test")
    async def test_mqtt_connection() -> dict:
        try:
            await app.state.mqtt.test_connection()
        except MqttNotConfigured as exc:
            raise HTTPException(status_code=400, detail="mqtt_not_configured") from exc
        except MqttConnectError as exc:
            logger.warning(
                "mqtt_connect_failed",
                extra={"error": str(exc), "mqtt_url": app.state.mqtt.config.url},
            )
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "connected": app.state.mqtt.connected}

    @app.post("/api/mqtt/test-message")
    async def send_test_message() -> dict:
        if not app.state.mqtt.configured:
            raise HTTPException(status_code=400, detail="mqtt_not_configured")
        topic = app.state.mqtt.config.topic_template.format(
            gateway_id="test-gateway",
            meter_id="test-meter",
        )
        payload = {
            "schema": "oms.bridge.test.v1",
            "message": "mqtt_test",
            "gateway_id": "test-gateway",
            "meter_id": "test-meter",
        }
        try:
            await safe_publish(app.state.mqtt, topic, payload)
        except MqttPublishError as exc:
            logger.warning(
                "mqtt_test_publish_failed",
                extra={"error": str(exc), "mqtt_url": app.state.mqtt.config.url},
            )
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "topic": topic}

    @app.get("/api/keys")
    async def list_keys() -> dict:
        meters = await app.state.store.list_known_meters()
        return {"meters": meters}

    @app.get("/api/meters/known")
    async def list_known_meters() -> dict:
        try:
            meters = await app.state.store.list_known_meters()
            return {"meters": meters}
        except Exception as exc:
            logger.exception("list_known_meters_failed", extra={"error": str(exc)})
            raise HTTPException(status_code=500, detail="meter_list_failed") from exc

    @app.get("/api/meters/pending")
    async def list_pending_meters() -> dict:
        try:
            meters = await app.state.store.list_pending_meters()
            return {"meters": meters}
        except Exception as exc:
            logger.exception("list_pending_meters_failed", extra={"error": str(exc)})
            raise HTTPException(status_code=500, detail="meter_list_failed") from exc

    @app.put("/api/keys/{meter_id}")
    async def put_key(meter_id: str, payload: KeyPayload) -> dict:
        await app.state.store.set_key(meter_id, payload.key_hex)
        return {"updated": True, "meter_id": meter_id}

    @app.delete("/api/keys/{meter_id}")
    async def delete_key(meter_id: str) -> dict:
        await app.state.store.delete_key(meter_id)
        return {"deleted": True, "meter_id": meter_id}

    @app.get("/api/meters/{meter_id}/telegrams")
    async def list_meter_telegrams(meter_id: str) -> dict:
        telegrams = await app.state.store.list_telegrams(meter_id)
        return {"telegrams": telegrams}

    @app.get("/api/meters/{meter_id}/telegrams/{telegram_id}")
    async def get_meter_telegram(meter_id: str, telegram_id: int) -> dict:
        detail = await app.state.store.get_telegram_detail(meter_id, telegram_id)
        if not detail:
            raise HTTPException(status_code=404, detail="telegram_not_found")
        return detail

    @app.post("/v1/telegrams", response_model=IngestResponse)
    async def ingest(payload: IngestTelegramRequest, response: Response) -> IngestResponse:
        meter_id = payload.id
        gateway_id = payload.gateway or "unknown"
        input_payload = payload.model_dump(mode="json")
        logger.info(
            "telegram_received",
            extra={
                "gateway_id": gateway_id,
                "meter_id": meter_id,
                "status": payload.status,
                "rssi_dbm": payload.rssi,
                "lqi": payload.lqi,
                "manuf": payload.manuf,
                "payload_len": payload.payload_len,
            },
        )
        try:
            key_hex = await app.state.store.get_key(meter_id)
        except Exception as exc:
            logger.exception(
                "store_get_key_failed",
                extra={"gateway_id": gateway_id, "meter_id": meter_id, "error": str(exc)},
            )
            response.status_code = 202
            return IngestResponse(status="store_error", meter_id=meter_id, mqtt_topic=None)
        if not key_hex:
            try:
                await app.state.store.mark_pending_meter(
                    meter_id=meter_id,
                    manuf=payload.manuf,
                    dev_type=payload.dev_type,
                    version=payload.version,
                    ci=payload.ci,
                )
            except Exception as exc:
                logger.warning(
                    "store_mark_pending_failed",
                    extra={"gateway_id": gateway_id, "meter_id": meter_id, "error": str(exc)},
                )
            try:
                await app.state.store.add_telegram(meter_id, gateway_id, "pending_key", input_payload, None)
            except Exception as exc:
                logger.warning("store_add_telegram_failed", extra={"error": str(exc)})
            logger.info(
                "telegram_pending_key",
                extra={"gateway_id": gateway_id, "meter_id": meter_id},
            )
            response.status_code = 202
            return IngestResponse(status="pending_key", meter_id=meter_id, mqtt_topic=None)
        if not app.state.lobaro.has_token:
            logger.warning("lobaro_token_missing", extra={"gateway_id": gateway_id, "meter_id": meter_id})
            try:
                await app.state.store.add_telegram(meter_id, gateway_id, "lobaro_token_missing", input_payload, None)
            except Exception as store_exc:
                logger.warning("store_add_telegram_failed", extra={"error": str(store_exc)})
            response.status_code = 202
            return IngestResponse(status="lobaro_token_missing", meter_id=meter_id, mqtt_topic=None)

        try:
            rx_time = payload.rx_time or datetime.now(timezone.utc)

            try:
                parsed = await app.state.lobaro.parse_meter_data(payload.logical_hex, key_hex)
            except LobaroResponseError as exc:
                body_preview = exc.body[:500] if exc.body else ""
                logger.warning(
                    "lobaro_parse_failed",
                    extra={
                        "gateway_id": gateway_id,
                        "meter_id": meter_id,
                        "status_code": exc.status_code,
                        "body_preview": body_preview,
                    },
                )
                try:
                    await app.state.store.add_telegram(meter_id, gateway_id, "lobaro_error", input_payload, None)
                except Exception as store_exc:
                    logger.warning("store_add_telegram_failed", extra={"error": str(store_exc)})
                logger.info(
                    "telegram_lobaro_error",
                    extra={"gateway_id": gateway_id, "meter_id": meter_id},
                )
                response.status_code = 202
                return IngestResponse(status="lobaro_error", meter_id=meter_id, mqtt_topic=None)
            except Exception as exc:
                logger.warning(
                    "lobaro_parse_failed",
                    exc_info=exc,
                    extra={"gateway_id": gateway_id, "meter_id": meter_id},
                )
                try:
                    await app.state.store.add_telegram(meter_id, gateway_id, "lobaro_error", input_payload, None)
                except Exception as store_exc:
                    logger.warning("store_add_telegram_failed", extra={"error": str(store_exc)})
                logger.info(
                    "telegram_lobaro_error",
                    extra={"gateway_id": gateway_id, "meter_id": meter_id},
                )
                response.status_code = 202
                return IngestResponse(status="lobaro_error", meter_id=meter_id, mqtt_topic=None)

            resolved_meter_id = parsed.get("meterId") or meter_id or "unknown"
            if not app.state.mqtt.configured:
                logger.warning(
                    "mqtt_not_configured",
                    extra={
                        "gateway_id": gateway_id,
                        "meter_id": meter_id,
                        "mqtt_source": app.state.mqtt_source,
                    },
                )
                try:
                    await app.state.store.add_telegram(meter_id, gateway_id, "mqtt_not_configured", input_payload, parsed)
                except Exception as store_exc:
                    logger.warning("store_add_telegram_failed", extra={"error": str(store_exc)})
                response.status_code = 202
                return IngestResponse(status="mqtt_not_configured", meter_id=resolved_meter_id, mqtt_topic=None)
            topic = app.state.mqtt.config.topic_template.format(
                gateway_id=gateway_id,
                meter_id=resolved_meter_id,
            )
            mqtt_payload = {
                "schema": "oms.bridge.v1",
                "gateway_id": gateway_id,
                "meter_id": resolved_meter_id,
                "rx_time": _utc_iso(rx_time),
                "status": payload.status,
                "rssi_dbm": payload.rssi,
                "lqi": payload.lqi,
                "manufacturer": payload.manuf,
                "device_type": payload.dev_type,
                "version": payload.version,
                "ci": payload.ci,
                "payload_len": payload.payload_len,
                "logical_hex": payload.logical_hex,
                "lobaro": parsed,
            }

            await safe_publish(app.state.mqtt, topic, mqtt_payload)

            try:
                await app.state.store.add_telegram(meter_id, gateway_id, "published", input_payload, parsed)
            except Exception as exc:
                logger.warning("store_add_telegram_failed", extra={"error": str(exc)})

            logger.info(
                "telegram_published",
                extra={"gateway_id": gateway_id, "meter_id": meter_id, "topic": topic},
            )
            return IngestResponse(status="ok", meter_id=resolved_meter_id, mqtt_topic=topic)
        except MqttPublishError as exc:
            logger.warning("mqtt_publish_failed", exc_info=exc)
            try:
                await app.state.store.add_telegram(meter_id, gateway_id, "mqtt_error", input_payload, None)
            except Exception as store_exc:
                logger.warning("store_add_telegram_failed", extra={"error": str(store_exc)})
            logger.info(
                "telegram_mqtt_error",
                extra={"gateway_id": gateway_id, "meter_id": meter_id},
            )
            raise HTTPException(status_code=502, detail="mqtt_error") from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception(
                "ingest_failed",
                extra={"gateway_id": gateway_id, "meter_id": meter_id},
            )
            try:
                await app.state.store.add_telegram(meter_id, gateway_id, "ingest_error", input_payload, None)
            except Exception as store_exc:
                logger.warning("store_add_telegram_failed", extra={"error": str(store_exc)})
            response.status_code = 202
            return IngestResponse(status="ingest_error", meter_id=meter_id, mqtt_topic=None)

    @app.get("/v1/telegrams")
    async def ingest_status() -> dict:
        return {"status": "ok", "mqtt_configured": app.state.mqtt.configured}

    @app.head("/v1/telegrams")
    async def ingest_head() -> Response:
        return Response(status_code=200)

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        await app.state.lobaro.close()
        await app.state.mqtt.close()

    @app.get("/healthz")
    async def health() -> dict:
        return {
            "status": "ok",
            "mqtt_connected": app.state.mqtt.connected,
            "mqtt_configured": app.state.mqtt.configured,
            "lobaro_token_set": app.state.lobaro.has_token,
        }

    return app


app = create_app()
