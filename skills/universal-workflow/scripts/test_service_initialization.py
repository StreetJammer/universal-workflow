#!/usr/bin/env python3
"""Cold initialization and its interaction with the absolute HTTP deadline."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import uw_service as service


class InitializationTests(unittest.TestCase):
    def test_cold_initialization_deadline_disconnects_then_initialized_store_works(self):
        with tempfile.TemporaryDirectory(prefix='uw-service-cold-') as temporary:
            directory = Path(temporary) / 'service'
            service.initialize(directory)
            token = service.administer(directory, 'provision', 'alice', org='alpha')['token']
            server = service.ServiceHTTPServer(directory, port=0)
            entered, release, initialized = (threading.Event() for _ in range(3))
            timers, initialization_errors = [], []
            original_init = service.ServiceStore.__init__

            class ControlledTimer:
                def __init__(self, interval, callback):
                    self.interval, self.callback = interval, callback
                    timers.append(self)

                def start(self):
                    pass

                def cancel(self):
                    pass

                def join(self, timeout=None):
                    pass

            def held_initialization(store, *args, **kwargs):
                entered.set()
                try:
                    if not release.wait(10):
                        raise AssertionError('Cold-initialization fixture was not released')
                    original_init(store, *args, **kwargs)
                except BaseException as error:
                    initialization_errors.append(error)
                    raise
                finally:
                    initialized.set()

            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
            thread.start()
            headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            try:
                self.assertFalse((directory / 'state/individual/alpha/alice').exists())
                # Trigger the configured timer deterministically, without
                # sleeping or changing the production five-second deadline.
                with patch.object(service.threading, 'Timer', ControlledTimer), patch.object(service.ServiceStore, '__init__', held_initialization):
                    connection.request('POST', '/v1/individual/status', '{}', headers)
                    self.assertTrue(entered.wait(10))
                    self.assertEqual(timers[0].interval, 5)
                    timers[0].callback()
                    with self.assertRaises(http.client.RemoteDisconnected):
                        connection.getresponse()
                    release.set()
                    self.assertTrue(initialized.wait(30))
                self.assertEqual(initialization_errors, [])
                connection.close()
                # No retry: this separate request verifies the completed cold
                # initialization with the ordinary production timer restored.
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
                connection.request('POST', '/v1/individual/status', '{}', headers)
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read())['result']['memories'], 0)
            finally:
                release.set()
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=10)
            self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main(verbosity=2)
