"""
Minimal client for the official EcoFlow IoT Developer (Open) API.

Docs: https://developer-eu.ecoflow.com  (Document section)
Every request is signed with HMAC-SHA256 using your Secret Key.
"""

import hashlib
import hmac
import json
import random
import time

import requests

# EU host first (Ukraine accounts live there), then global, then Americas.
HOSTS = ("api-e.ecoflow.com", "api.ecoflow.com", "api-a.ecoflow.com")


class EcoFlowError(Exception):
    def __init__(self, code, message):
        super().__init__(f"{message} (code {code})")
        self.code = str(code)
        self.message = message


class AuthError(EcoFlowError):
    """Wrong / disabled keys or a bad signature."""


def _fmt(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def flatten(obj, prefix=""):
    """{"a": {"b": 1}, "c": [5, 6]} -> {"a.b": 1, "c[0]": 5, "c[1]": 6}"""
    out = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            out.update(flatten(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(obj, (list, tuple)):
        for i, value in enumerate(obj):
            out.update(flatten(value, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out


def build_sign_string(params, access_key, nonce, timestamp):
    flat = flatten(params or {})
    parts = [f"{k}={_fmt(flat[k])}" for k in sorted(flat)]
    parts += [f"accessKey={access_key}", f"nonce={nonce}", f"timestamp={timestamp}"]
    return "&".join(parts)


def make_sign(secret_key, sign_string):
    return hmac.new(secret_key.encode("utf-8"), sign_string.encode("utf-8"), hashlib.sha256).hexdigest()


class EcoFlowClient:
    def __init__(self, access_key, secret_key, host=None, timeout=12):
        self.access_key = access_key.strip()
        self.secret_key = secret_key.strip()
        self.host = host or HOSTS[0]
        self.timeout = timeout
        self.session = requests.Session()

    # ---- low level -------------------------------------------------------
    def _request(self, method, path, query=None, body=None, host=None):
        nonce = str(random.randint(100000, 999999))
        timestamp = str(int(time.time() * 1000))
        sign_params = query if query is not None else body
        headers = {
            "accessKey": self.access_key,
            "nonce": nonce,
            "timestamp": timestamp,
            "sign": make_sign(self.secret_key, build_sign_string(sign_params, self.access_key, nonce, timestamp)),
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json;charset=UTF-8"
            data = json.dumps(body)

        url = f"https://{host or self.host}/iot-open/sign{path}"
        resp = self.session.request(method, url, params=query, data=data, headers=headers, timeout=self.timeout)
        try:
            payload = resp.json()
        except ValueError:
            raise EcoFlowError(resp.status_code, f"HTTP {resp.status_code} from EcoFlow")

        code = str(payload.get("code", ""))
        if code != "0":
            message = payload.get("message") or "EcoFlow API error"
            low = message.lower()
            if code in ("8513", "8521", "8524") or "sign" in low or "accesskey" in low or "access key" in low:
                raise AuthError(code, message)
            raise EcoFlowError(code, message)
        return payload.get("data")

    # ---- high level ------------------------------------------------------
    def list_devices(self, host=None):
        """[{"sn": "...", "deviceName": "...", "online": 1, "productName": "..."}]"""
        return self._request("GET", "/device/list", host=host) or []

    def detect_host(self):
        """Find the regional server that knows this account. Returns its device list."""
        first_ok, last_err = None, None
        for host in HOSTS:
            try:
                devices = self.list_devices(host=host)
            except (EcoFlowError, requests.RequestException) as err:
                last_err = err
                continue
            if devices:
                self.host = host
                return devices
            if first_ok is None:
                first_ok = host
        if first_ok:
            self.host = first_ok
            return []
        raise last_err

    def quota_all(self, sn):
        return self._request("GET", "/device/quota/all", query={"sn": sn}) or {}

    def set_quota(self, sn, module_type, operate_type, params, module_sn=None):
        body = {
            "id": int(time.time() * 1000) % 1_000_000_000,
            "version": "1.0",
            "sn": sn,
            "moduleType": module_type,
            "operateType": operate_type,
            "params": params,
        }
        if module_sn:
            body["moduleSn"] = module_sn
        return self._request("PUT", "/device/quota", body=body)


# Port switches for DELTA 2 Max: state key in quota/all + command to send.
PORTS = {
    "ac": {
        "label": "AC",
        "state_key": "inv.cfgAcEnabled",
        "command": lambda sn, on: dict(
            module_type=3, operate_type="acOutCfg", module_sn=sn,
            params={"enabled": on, "out_voltage": -1, "out_freq": 255, "xboost": 255},
        ),
    },
    "dc": {
        "label": "DC 12V",
        "state_key": "pd.carState",
        "command": lambda sn, on: dict(module_type=5, operate_type="mpptCar", params={"enabled": on}),
    },
    "usb": {
        "label": "USB",
        "state_key": "pd.dcOutState",
        "command": lambda sn, on: dict(module_type=1, operate_type="dcOutCfg", module_sn=sn, params={"enabled": on}),
    },
}
