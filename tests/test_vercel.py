from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from api import run as vercel_run
from api.run import StateStoreError, load_remote_state, save_remote_state


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class TestRemoteState(unittest.TestCase):
    def setUp(self):
        self.env = {
            "UPSTASH_REDIS_REST_URL": "https://example.upstash.io",
            "UPSTASH_REDIS_REST_TOKEN": "test-token",
        }

    def test_load_parses_stored_json(self):
        document = {"assets": {"wti": {"action": "BUY"}}}
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch(
            "api.run.urlopen", return_value=FakeResponse({"result": json.dumps(document)})
        ) as urlopen:
            loaded = load_remote_state()
        self.assertEqual(loaded, document)
        request = urlopen.call_args[0][0]
        self.assertTrue(request.full_url.endswith("/get/commodity%3Aalert_state%3Av1"))
        self.assertEqual(request.headers["Authorization"], "Bearer test-token")

    def test_save_posts_pipeline_set(self):
        document = {"assets": {"wti": {"action": "BUY"}}}
        with mock.patch.dict(os.environ, self.env, clear=False), mock.patch(
            "api.run.urlopen", return_value=FakeResponse([{"result": "OK"}])
        ) as urlopen:
            save_remote_state(document)
        request = urlopen.call_args[0][0]
        self.assertTrue(request.full_url.endswith("/pipeline"))
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload[0][0], "SET")
        self.assertEqual(payload[0][1], "commodity:alert_state:v1")
        self.assertEqual(json.loads(payload[0][2]), document)

    def test_missing_store_on_vercel_is_a_config_error(self):
        with mock.patch.dict(os.environ, {"VERCEL": "1"}, clear=True):
            with self.assertRaises(StateStoreError):
                load_remote_state()

    def test_missing_store_locally_returns_none(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(load_remote_state())


if __name__ == "__main__":
    unittest.main()
