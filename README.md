# oms-hub
oms-hub is a Dockerized backend hub for OMS / Wireless M-Bus gateways: it ingests raw meter telegrams, buffers them reliably, manages device keys and configuration via a web UI, decodes/normalizes data using wmbusmeters, and exposes the results through APIs and export targets (e.g., MQTT/DB) for dashboards and downstream services.
