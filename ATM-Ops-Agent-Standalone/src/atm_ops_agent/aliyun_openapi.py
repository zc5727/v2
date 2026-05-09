from __future__ import annotations

from base64 import b64encode
from datetime import UTC, datetime
import hashlib
import hmac
import json
from urllib.parse import quote
from urllib.request import Request, urlopen
import uuid


def percent_encode(value: str) -> str:
    return quote(value, safe="~")


def sign_parameters(parameters: dict[str, str], access_key_secret: str) -> str:
    sorted_pairs = sorted(parameters.items(), key=lambda item: item[0])
    canonicalized = "&".join(f"{percent_encode(key)}={percent_encode(value)}" for key, value in sorted_pairs)
    string_to_sign = f"GET&%2F&{percent_encode(canonicalized)}"
    digest = hmac.new(
        f"{access_key_secret}&".encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return b64encode(digest).decode("utf-8")


class AliyunRPCClient:
    def __init__(self, endpoint: str, access_key_id: str, access_key_secret: str) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret

    def call(self, *, action: str, version: str, extra_params: dict[str, str], timeout: int = 20) -> dict:
        params = {
            "Format": "JSON",
            "Version": version,
            "AccessKeyId": self.access_key_id,
            "SignatureMethod": "HMAC-SHA1",
            "Timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "SignatureVersion": "1.0",
            "SignatureNonce": str(uuid.uuid4()),
            "Action": action,
        }
        params.update(extra_params)
        params["Signature"] = sign_parameters(params, self.access_key_secret)
        query = "&".join(f"{percent_encode(key)}={percent_encode(value)}" for key, value in sorted(params.items()))
        url = f"{self.endpoint}/?{query}"
        req = Request(url, method="GET")
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body)
