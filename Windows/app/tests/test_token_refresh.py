import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import urllib.error

from agy_native import Backend, PROD, DAILY, ReauthenticationRequired


def response(value):
    return io.BytesIO(json.dumps(value).encode())


def rejection(status):
    return urllib.error.HTTPError('https://example.invalid', status, 'test rejection', {},
                                   response({'error': {'code': status}}))


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.token_path = Path(self.temp.name) / 'test-token.json'
        self.token_path.write_text(json.dumps({'client_id': 'test-client', 'client_secret': 'fake',
                                               'refresh_token': 'test-refresh'}))
        self.backend = Backend(self.token_path, {'http_proxy': 'http://127.0.0.1:10809'})
        self.backend.catalog = {'test-model': {}}
        self.refreshes = 0
        self.calls = []
        self.reject_first = False
        self.always_reject = None
        self.barrier = None
        self.fail_refresh = False
        self.unauthorized_responses = []
        self.backend.post = self.post
        self.seed()

    def tearDown(self):
        self.temp.cleanup()

    def seed(self, token='old-access', project='old-project'):
        self.backend.access_token = token
        self.backend.project = project
        self.backend.endpoint = DAILY
        self.backend.expires = time.monotonic() + 3600
        self.backend.expires_at = time.time() + 3600

    def post(self, url, data, token=None, form=False):
        if url == 'https://oauth2.googleapis.com/token':
            self.refreshes += 1
            if self.fail_refresh:
                raise urllib.error.HTTPError(url, 400, 'Bad Request', {},
                                               response({'error': 'invalid_grant'}))
            return response({'access_token': 'fresh-access', 'expires_in': 3600})
        if url == PROD + '/v1internal:loadCodeAssist':
            return response({'currentTier': {'id': 'standard-tier'},
                             'cloudaicompanionProject': 'new-project'})
        self.calls.append((url, token, data))
        if self.always_reject or (token == 'old-access' and self.reject_first):
            if self.barrier is not None:
                self.barrier.wait(timeout=5)
            error = rejection(self.always_reject or 401)
            self.unauthorized_responses.append(error.fp)
            raise error
        if 'fetchAvailableModels' in url:
            return response({'models': {'test-model': {}}})
        return response({'response': {'candidates': []}})

    def test_valid_cache_does_not_refresh(self):
        with self.backend.generate('test-model', {'contents': []}, False):
            pass
        self.assertEqual(self.refreshes, 0)
        self.assertEqual(self.calls[0][1], 'old-access')

    def test_401_refreshes_once_and_rebuilds_project_for_same_body(self):
        self.reject_first = True
        body = {'contents': [], 'tools': [{'functionDeclarations': [{'name': 'read'}]}]}
        with self.backend.generate('test-model', body, False):
            pass
        self.assertEqual(self.refreshes, 1)
        self.assertEqual([call[1] for call in self.calls], ['old-access', 'fresh-access'])
        self.assertEqual(self.calls[-1][2], {'project': 'new-project', 'model': 'test-model', 'request': body})
        self.assertTrue(all(stream.closed for stream in self.unauthorized_responses))

    def test_model_catalog_refresh_is_used_by_generation(self):
        self.backend.catalog = None
        self.reject_first = True
        with self.backend.generate('test-model', {}, False):
            pass
        self.assertEqual(self.refreshes, 1)
        self.assertEqual([call[1] for call in self.calls], ['old-access', 'fresh-access', 'fresh-access'])
        self.assertIn('generateContent', self.calls[-1][0])

    def test_streaming_401_retries_only_before_returning_stream(self):
        self.reject_first = True
        with self.backend.generate('test-model', {}, True):
            pass
        self.assertTrue(all('streamGenerateContent?alt=sse' in call[0] for call in self.calls))
        self.assertEqual(self.refreshes, 1)
        self.assertEqual(len(self.calls), 2)

    def test_second_401_is_propagated_without_loop(self):
        self.always_reject = 401
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.backend.generate('test-model', {}, False)
        self.assertEqual(caught.exception.code, 401)
        caught.exception.close()
        self.assertEqual(self.refreshes, 1)
        self.assertEqual(len(self.calls), 2)

    def test_403_is_not_refreshed_or_retried(self):
        self.always_reject = 403
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.backend.models()
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()
        self.assertEqual(self.refreshes, 0)
        self.assertEqual(len(self.calls), 1)

    def test_expiry_after_sleep_uses_wall_clock_even_if_monotonic_is_paused(self):
        with patch('agy_native.time.monotonic', return_value=100), patch('agy_native.time.time', return_value=10000):
            self.backend.expires = 1000
            self.backend.expires_at = 9000
            self.assertEqual(self.backend.credentials()[0], 'fresh-access')
        self.assertEqual(self.refreshes, 1)

    def test_monotonic_expiry_still_wins_after_wall_clock_moves_back(self):
        with patch('agy_native.time.monotonic', return_value=1000), patch('agy_native.time.time', return_value=100):
            self.backend.expires = 900
            self.backend.expires_at = 200
            self.assertEqual(self.backend.credentials()[0], 'fresh-access')
        self.assertEqual(self.refreshes, 1)

    def test_parallel_401s_share_one_refresh(self):
        self.reject_first = True
        self.barrier = threading.Barrier(2)
        def generate():
            with self.backend.generate('test-model', {}, False):
                return True
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(generate) for _ in range(2)]
            self.assertTrue(all(f.result(timeout=10) for f in futures))
        self.assertEqual(self.refreshes, 1)
        self.assertEqual(sum(call[1] == 'fresh-access' for call in self.calls), 2)

    def test_revoked_refresh_token_requires_login_without_retry_loop(self):
        self.reject_first = True
        self.fail_refresh = True
        with self.assertRaisesRegex(ReauthenticationRequired, 'Login'):
            self.backend.generate('test-model', {}, False)
        self.assertEqual(self.refreshes, 1)
        self.assertEqual(len(self.calls), 1)

    def test_other_oauth_error_body_is_preserved(self):
        self.backend.expires = 0
        payload = {'error': 'invalid_client', 'error_description': 'test invalid client'}
        with patch.object(self.backend, 'post', side_effect=urllib.error.HTTPError(
                'https://oauth2.googleapis.com/token', 400, 'Bad Request', {}, response(payload))):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.backend.credentials()
        self.assertEqual(json.load(caught.exception), payload)
        caught.exception.close()

    def test_network_error_does_not_refresh_or_replay_generation(self):
        with patch.object(self.backend, 'post', side_effect=urllib.error.URLError('test disconnect')) as post:
            with self.assertRaises(urllib.error.URLError):
                self.backend.generate('test-model', {}, True)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.refreshes, 0)


if __name__ == '__main__':
    unittest.main()
