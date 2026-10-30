"""
Live data from the EcoFlow IoT Open Platform over MQTT.

The REST endpoint /device/quota/all can return an old cached snapshot
(for example, it kept reporting "AC off, grid connected" during a power
outage). The station pushes its real-time values to the MQTT broker, so
the widget listens there and uses REST only as a starting point / fallback.

Broker credentials come from GET /iot-open/sign/certification.
Topics: /open/{certificateAccount}/{sn}/quota   (live values)
        /open/{certificateAccount}/{sn}/status  (online / offline)
"""

import json
import logging
import ssl
import threading
import time

log = logging.getLogger("EcoFlowWidget")

try:
    import paho.mqtt.client as mqtt
    HAVE_PAHO = True
except ImportError:  # install.bat not re-run after updating
    mqtt = None
    HAVE_PAHO = False

# message "typeCode" -> key prefix used by /device/quota/all
TYPE_PREFIX = {
    "pdStatus": "pd",
    "mpptStatus": "mppt",
    "invStatus": "inv",
    "bmsStatus": "bms_bmsStatus",
    "emsStatus": "bms_emsStatus",
    "bmsInfo": "bms_bmsInfo",
    "bmsSlaveStatus_1": "bms_slave_bmsSlaveStatus_1",
    "bmsSlaveStatus_2": "bms_slave_bmsSlaveStatus_2",
}


def flatten_message(raw):
    """Turn one MQTT quota message into the same flat keys quota/all uses ("pd.soc", "inv.cfgAcEnabled" ...)."""
    if not isinstance(raw, dict):
        return {}
    if "typeCode" in raw:
        prefix = TYPE_PREFIX.get(raw["typeCode"], "unknown_" + str(raw["typeCode"])) + "."
    elif "cmdFunc" in raw and "cmdId" in raw:
        prefix = f"{raw['cmdFunc']}_{raw['cmdId']}."
    else:
        prefix = ""
    out = {}
    for block in ("param", "params"):
        values = raw.get(block)
        if isinstance(values, dict):
            for key, value in values.items():
                out[f"{prefix}{key}"] = value
                if isinstance(value, dict):
                    for k2, v2 in value.items():
                        out[f"{prefix}{key}.{k2}"] = v2
    return out


def parse_status(raw):
    """Status topic -> True (online) / False (offline) / None (unknown)."""
    if not isinstance(raw, dict):
        return None
    for source in (raw.get("params"), raw.get("param"), raw):
        if isinstance(source, dict) and "status" in source:
            try:
                return bool(int(source["status"]))
            except (TypeError, ValueError):
                return None
    return None


class LiveFeed:
    """
    Background MQTT connection. Callbacks run on paho's network thread:
      on_quota(dict_of_flat_values), on_online(bool), on_state(str, detail)
    state is one of: "connecting", "live", "disconnected", "auth_failed", "error"
    """

    def __init__(self, host, port, username, password, sn, on_quota, on_online, on_state, use_tls=True):
        self.host, self.port = host, int(port)
        self.username, self.sn = username, sn
        self.on_quota, self.on_online, self.on_state = on_quota, on_online, on_state
        self.topic_quota = f"/open/{username}/{sn}/quota"
        self.topic_status = f"/open/{username}/{sn}/status"
        self.raw_samples = {}
        self._lock = threading.Lock()
        self.started_at = None
        self.connected_at = None
        self.last_msg_at = None
        self.msg_count = 0

        # A fixed client id: EcoFlow allows only ~10 unique ids per account per day.
        client_id = f"EcoFlowWidget-{username}"[:64]
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=True)
        self.client.username_pw_set(username, password)
        if use_tls:
            self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        self.client.reconnect_delay_set(min_delay=2, max_delay=60)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def start(self):
        self.started_at = time.time()
        self.on_state("connecting", f"{self.host}:{self.port}")
        try:
            self.client.connect_async(self.host, self.port, keepalive=30)
            self.client.loop_start()
        except Exception as err:
            log.warning("mqtt start failed: %s", err)
            self.on_state("error", str(err))

    def stop(self):
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

    # -- paho callbacks
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            text = str(reason_code)
            log.warning("mqtt connect refused: %s", text)
            if "user name" in text.lower() or "authori" in text.lower():
                self.on_state("auth_failed", text)
                client.loop_stop()  # don't hammer the broker with rejected credentials
            else:
                self.on_state("error", text)
            return
        log.info("mqtt connected to %s", self.host)
        self.connected_at = time.time()
        client.subscribe([(self.topic_quota, 1), (self.topic_status, 1)])
        self.on_state("live", "")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        log.info("mqtt disconnected: %s", reason_code)
        self.on_state("disconnected", str(reason_code))

    def _on_message(self, client, userdata, msg):
        try:
            raw = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            log.warning("mqtt: undecodable message on %s (%d bytes)", msg.topic, len(msg.payload))
            return
        if msg.topic == self.topic_status:
            online = parse_status(raw)
            if online is not None:
                self.on_online(online)
            return
        flat = flatten_message(raw)
        self.last_msg_at = time.time()
        self.msg_count += 1
        with self._lock:
            self.raw_samples[str(raw.get("typeCode") or raw.get("cmdId") or "other")] = raw
        if flat:
            self.on_quota(flat)

    def samples(self):
        with self._lock:
            return dict(self.raw_samples)
