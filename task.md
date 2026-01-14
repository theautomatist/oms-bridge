# Ziel

Eine **FastAPI Web-App** bereitstellen, die von deinen Gateways empfangene **wM-Bus/OMS Telegramme** entgegennimmt, an den **Lobaro wMbus Parser** weiterleitet, das Ergebnis normalisiert/validiert und anschließend **via MQTT** publiziert.

Referenz (API-Doku): [https://confluence.lobaro.com/display/PUB/wMbus+Parser#wMbusParser-APIEndpoint](https://confluence.lobaro.com/display/PUB/wMbus+Parser#wMbusParser-APIEndpoint)

---

# Annahmen & harte Anforderungen

* Gateways senden **Raw-Telegramme als Hex** (ohne `0x`), plus Metadaten (RSSI, Kanal, Empfangszeit, Gateway-ID, optional Meter-ID).
* Parsing erfolgt über Lobaro Endpoint **`POST https://platform.lobaro.com/api/meterData`**

  * Query: `raw=<HEX>`, `key=<HEX_AES128>`
  * Header: `Authorization: Bearer <LOBARO_TOKEN>`
  * Body: `application/json` mit **Mapping Configuration** (domainMappings)
* Ausgabe: MQTT (QoS konfigurierbar), Topics deterministisch und versionsfähig.
* Keine UI-Pflicht. Optional: Admin-Endpoints für Keys/Mappings.

---

# High-Level Architektur

**Ingress API (FastAPI)** → **Job Queue (in-memory)** → **Parser Worker (HTTP→Lobaro)** → **Publisher (MQTT)**

Warum Queue?

* Gateway-Requests bleiben schnell (ACK innerhalb <100ms möglich).
* Retries/Backoff isoliert vom HTTP-Threadpool.
* MQTT-/HTTP-Ausfälle blockieren nicht die Ingress-API.

---

# Projektstruktur (Vorschlag)

```
oms-parser-bridge/
  app/
    main.py
    api/
      routes_ingest.py
      routes_admin.py
      routes_health.py
    core/
      config.py
      logging.py
      security.py
    models/
      ingest.py
      parsed.py
      admin.py
    services/
      lobaro_client.py
      key_resolver.py
      mapping_store.py
      pipeline.py
      mqtt_publisher.py
      dedup.py
    storage/
      db.py
      repos.py
      migrations/
    workers/
      queue.py
      worker.py
  tests/
  docker/
    Dockerfile
    docker-compose.yml
  pyproject.toml
  README.md
```

---

# Konfiguration (ENV)

Minimal:

* `APP_ENV=dev|prod`
* `INGEST_API_KEYS=gw1:...;gw2:...` (oder JWT)
* `LOBARO_TOKEN=...` (Bearer)
* `LOBARO_TIMEOUT_S=10`
* `MQTT_URL=mqtt://host:1883`
* `MQTT_USERNAME=...` / `MQTT_PASSWORD=...`
* `MQTT_TOPIC_TEMPLATE=oms/v1/gw/{gateway_id}/meter/{meter_id}`
* `MQTT_QOS=1`
* `MQTT_RETAIN=false`

Optional:

* `DATABASE_URL=sqlite:///./data.db` oder Postgres
* `ENABLE_DEDUP=true`
* `DEDUP_WINDOW_S=300`

---

# Datenmodelle

## 1) Gateway → Ingress Request (JSON)

**Endpoint:** `POST /v1/telegrams`

Beispiel:

```json
{
  "gateway_id": "gw-esp32c3-001",
  "rx_time": "2026-01-08T12:45:11.123Z",
  "rssi_dbm": -82,
  "mode": "T1",
  "raw_hex": "2e4493157856341233037a2a0020...",
  "meter_hint": {
    "meter_id": "12345678",
    "manufacturer": "ELS"
  }
}
```

Validierungen:

* `raw_hex`: nur `[0-9a-fA-F]`, min Länge z.B. 20 Bytes
* `gateway_id`: required
* `rx_time`: optional, server setzt sonst `now()`

Antwort:

* **202 Accepted**: `{ "accepted": true, "ingest_id": "..." }`
* Optional **200** synchroner Modus: `?sync=true` (nur für Debug)

## 2) Intern: Pipeline Job

* `ingest_id` (uuid)
* `raw_hex`
* `gateway_id`
* Metadaten
* `dedup_hash`

## 3) Lobaro → Parser Response

Ziel: den **Domain Model Output** (Metering Domain Model) unverändert speichern + minimal normalisieren:

* `meterId`, `manufacturer`, `type`, Zeitstempel, Werte-Liste.

---

# Key- & Mapping-Strategie

## Key Resolver (AES-128)

Lobaro erwartet `key=<HEX>`.

Implementiere `KeyResolver.resolve(job)`:

1. Wenn Request bereits `key_hex` enthält → nutzen.
2. Sonst DB lookup anhand `meter_hint.meter_id`.
3. Fallback: "no key" → je nach Policy:

   * **strict:** Job als `decrypt_key_missing` markieren (MQTT error-topic)
   * **lenient:** trotzdem an Lobaro senden (wenn Lobaro leere Keys akzeptiert; sonst skip)

Speicher:

* Tabelle `meter_keys(meter_id TEXT PK, manufacturer TEXT, key_hex TEXT, updated_at)`

## Mapping Store

Lobaro verlangt Mapping-JSON im Body (`domainMappings`).

Implementiere `MappingStore.get_mapping_config()`:

* Variante A: `domainMappings.json` als Datei (hot reload optional)
* Variante B: DB Tabelle `domain_mappings(id, json, version, active)`

Empfehlung: Start mit Datei + später DB.

---

# MQTT Topic & Payload Design

## Topic Schema

* Raw Telegram (immer):

  * `oms/v1/gw/{gateway_id}/raw/{ingest_id}`
* Parsed Domain Model:

  * `oms/v1/gw/{gateway_id}/meter/{meter_id}/reading`
* Errors:

  * `oms/v1/gw/{gateway_id}/errors/{ingest_id}`

## Payload (Parsed)

```json
{
  "schema": "oms.bridge.v1",
  "ingest_id": "...",
  "gateway_id": "...",
  "rx_time": "...",
  "rssi_dbm": -82,
  "raw_hex": "...",
  "lobaro": {
    "metering_domain_model": { /* original response */ }
  }
}
```

Regeln:

* Kein Retain für Telemetrie.
* QoS default 1.
* Payload maximal (optional) gzip/CBOR später.

---

# Implementationsplan für den Coding Agent

## Phase 0 — Setup

1. Python 3.11+; `pyproject.toml` mit:

   * `fastapi`, `uvicorn[standard]`
   * `httpx`
   * `pydantic-settings`
   * `asyncio-mqtt` (oder `paho-mqtt` + thread)
   * `sqlalchemy` + `alembic` (optional)
   * `orjson` (optional)
   * `pytest`, `pytest-asyncio`
2. `docker/Dockerfile` + `docker-compose.yml` (inkl. Mosquitto, optional Postgres)

## Phase 1 — FastAPI Grundgerüst

1. `app/main.py`:

   * Router mount: `/v1/telegrams`, `/v1/admin`, `/healthz`
   * Startup/Shutdown: MQTT connect, Worker start
2. `core/config.py`:

   * Pydantic Settings, env parsing
3. `core/security.py`:

   * API-Key Auth: `Authorization: Bearer <GW_KEY>`
   * Mapping `gateway_id -> allowed key` (oder ein globaler Key)

## Phase 2 — Ingress Endpoint

1. `models/ingest.py`: Pydantic Modell `IngestTelegramRequest`
2. `api/routes_ingest.py`:

   * POST `/v1/telegrams`
   * Validate, compute `ingest_id` (uuid)
   * Persist raw (optional) und enqueue Job
   * Return 202
3. Dedup:

   * `dedup_hash = sha256(gateway_id + raw_hex)`
   * optional: Drop duplicates in `DEDUP_WINDOW_S`

## Phase 3 — Lobaro Client

1. `services/lobaro_client.py`:

   * async httpx Client
   * Methode `parse_meter_data(raw_hex, key_hex, mapping_json) -> dict`
   * Implementiere:

     * URL: `https://platform.lobaro.com/api/mbus`
     * Query params: `raw`, `key`
     * Headers: `Authorization: Bearer {LOBARO_TOKEN}`, `Accept: application/json`
     * Body: none
     * Timeouts + retries (z.B. 3 Versuche, exponential backoff)
     * Raise klare Exceptions: `LobaroAuthError`, `LobaroRateLimitError`, `LobaroBadRequestError`, `LobaroTimeoutError`

## Phase 4 — Pipeline Worker

1. `workers/queue.py`: `asyncio.Queue[Job]`
2. `workers/worker.py`:

   * Endlosschleife: `job = await queue.get()`
   * `key = KeyResolver.resolve(job)`
   * `mapping = MappingStore.get_mapping_config()`
   * `parsed = LobaroClient.parse_meter_data(...)`
   * `MQTTPublisher.publish_parsed(job, parsed)`
   * Persist parsed (optional)
   * Error-Pfade → `publish_error(...)`

Wichtig:

* Worker concurrency: `N_WORKERS` env (z.B. 2–8)
* Backpressure: Queue max size (z.B. 5000); bei overflow 429/503 am Ingress

## Phase 5 — MQTT Publisher

1. `services/mqtt_publisher.py`:

   * Connect on startup, reconnect loop
   * `publish(topic, payload_bytes, qos, retain)`
   * Topic rendering über Template
2. Publish Regeln:

   * immer raw → `.../raw/{ingest_id}`
   * parsed → `.../meter/{meter_id}/reading`
   * wenn `meterId` fehlt: fallback `.../meter/unknown/reading`

## Phase 6 — Storage (optional, aber empfohlen)

* SQLite/Postgres für:

  * raw telegrams
  * parsing results
  * meter keys
  * mapping config versions
* Minimal-Schema:

  * `telegrams(ingest_id PK, gateway_id, rx_time, rssi, raw_hex, status, created_at)`
  * `parse_results(ingest_id PK/FK, parsed_json, published_at)`

## Phase 7 — Admin API (optional)

* `GET /v1/admin/keys` / `PUT /v1/admin/keys/{meter_id}`
* `GET /v1/admin/mappings/active` / `PUT /v1/admin/mappings/active`
* absichern via separatem Admin Token

## Phase 8 — Observability

* Structured logs (JSON)
* `/metrics` (Prometheus) optional
* `/healthz`:

  * MQTT connected?
  * Lobaro reachable? (optional, cached check)
  * Queue depth

## Phase 9 — Tests

* Unit:

  * raw_hex validation
  * topic rendering
  * key resolution
* Integration:

  * httpx mock Lobaro
  * MQTT test broker (docker mosquitto)

---

# Akzeptanzkriterien (Definition of Done)

* `POST /v1/telegrams` akzeptiert Telegramme, antwortet 202.
* Worker ruft Lobaro `/api/meterData` korrekt auf (Bearer, query params, mapping body).
* Parsed Output wird auf MQTT publiziert (Topic-Schema stabil).
* Fehlerfälle publizieren auf Error-Topic + loggen strukturiert.
* Docker Compose startet: App + Mosquitto (optional DB).

---

# Edge Cases (muss der Agent explizit behandeln)

* Lobaro 401/403: Token falsch/abgelaufen → Circuit breaker (z.B. 60s Pause) + Alarm-Log
* 429 Rate Limit: Backoff + Queue growth kontrollieren
* Timeouts: Retries max 3, danach Error publish
* Ungültiges `raw_hex`: 400 am Ingress
* Kein Key vorhanden: je nach Policy publish error oder skip parsing
* MQTT offline: Buffering begrenzen; nicht endlos RAM füllen

---

# README Inhalte (minimal)

* "Wie starte ich lokal" (docker-compose)
* Beispiel curl für Ingress
* Beispiel MQTT topics
* Konfiguration via ENV

---

# Bonus: Gateway-Integration (praktisch)

Gateway sendet direkt:

* HTTP POST zu `https://<bridge>/v1/telegrams` mit `Authorization: Bearer <GW_KEY>`
* Payload wie oben
* Retry Policy im Gateway: nur bei 5xx/timeout; bei 4xx keine Retries
