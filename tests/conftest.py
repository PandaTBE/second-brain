"""Synthetic API fixtures: no user Keychain or external network is touched."""

from copy import deepcopy
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from brain_settings.app import create_app

ORIGIN = "http://127.0.0.1:8765"
OPENROUTER = "https://openrouter.ai/api/v1"
DEEPSEEK = "https://api.deepseek.com"
FAKE_TOKEN = "test-token-synthetic-not-a-real-key"


class MemoryVault:
    available = True

    def __init__(self):
        self.entries = {}

    def get(self, scope):
        return self.entries.get(scope)

    def set(self, scope, token):
        self.entries = {**self.entries, scope: token}

    def delete(self, scope):
        self.entries = {key: value for key, value in self.entries.items() if key != scope}


class FakeTransport:
    def __init__(self, models=None, error=None):
        self.models = models if models is not None else {
            "data": [
                {
                    "id": "vendor/low-cost",
                    "name": "Low-cost text",
                    "pricing": {"prompt": "0.0000001", "completion": "0.0000003"},
                    "context_length": 8192,
                    "architecture": {"output_modalities": ["text"]},
                },
                {
                    "id": "vendor/free",
                    "name": "Free text",
                    "pricing": {"prompt": "0", "completion": "0"},
                    "architecture": {"output_modalities": ["text"]},
                },
                {"id": "vendor/unknown", "name": "Unknown price"},
                {
                    "id": "vendor/image-only",
                    "architecture": {"output_modalities": ["image"]},
                },
            ]
        }
        self.error = error
        self.calls = ()

    def request(self, base_url, path, token=None):
        self.calls = (*self.calls, (base_url, path, token))
        if self.error is not None:
            raise self.error
        if path == "/key":
            return {"data": {"label": "Synthetic test key"}}
        return deepcopy(self.models)


@dataclass(frozen=True)
class Harness:
    client: TestClient
    vault: MemoryVault
    transport: FakeTransport
    data_dir: object

    @property
    def headers(self):
        bootstrap = self.client.get("/api/bootstrap").json()["data"]
        return {"X-Brain-CSRF": bootstrap["csrf_token"], "Origin": ORIGIN}

    def post(self, path, payload):
        return self.client.post(path, json=payload, headers=self.headers)


@pytest.fixture
def harness(tmp_path):
    vault = MemoryVault()
    transport = FakeTransport()
    app = create_app(data_dir=tmp_path, vault=vault, transport=transport)
    with TestClient(app, base_url=ORIGIN) as client:
        yield Harness(client, vault, transport, tmp_path)


@pytest.fixture
def selection():
    return {
        "provider": "openrouter",
        "base_url": OPENROUTER,
        "model_id": "vendor/low-cost",
        "model_name": "Low-cost text",
        "token": FAKE_TOKEN,
    }
