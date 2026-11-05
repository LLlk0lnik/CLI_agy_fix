import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
import urllib.error

from agy_native import Backend
from launcher_common import configuration
from route_health import RoutePool, NoRedirect, NoAvailableRoute, classify_error, has_model_answer
from model_probe import probe_model, probe_name

PRIMARY = 'http://127.0.0.1:10809'
FALLBACK = 'http://127.0.0.1:20809'
REGION = {'error': {'message': 'User location is not supported for the API use.'}}


def reply(value):
    return io.BytesIO(json.dumps(value).encode())


class EvidenceTests(unittest.TestCase):
    def test_region_classification_requires_precise_status_and_message(self):
        self.assertEqual(classify_error(400, REGION), 'region')
        self.assertEqual(classify_error(400, {'error': {'message': 'FAILED_PRECONDITION: other'}}), 'http')
        self.assertEqual(classify_error(403, REGION), 'permission')
        self.assertEqual(classify_error(401, REGION), 'authentication')
        self.assertEqual(classify_error(400, b'not-json'), 'http')

    def test_model_catalog_or_response_id_does_not_prove_generation(self):
        self.assertFalse(has_model_answer({'models': {'test': {}}}))
        self.assertFalse(has_model_answer({'responseId': 'test'}))
        self.assertTrue(has_model_answer({'response': {'candidates': [{'content': {'parts': [{'text': 'OK'}]}}]}}))
        self.assertTrue(has_model_answer({'candidates': [{'content': {'parts': [{'functionCall': {'name': 'read'}}]}}]}))

    def test_region_cooldown_and_expiry(self):
        pool = RoutePool(PRIMARY, [FALLBACK, PRIMARY])
        with patch('route_health.time.monotonic', return_value=100):
            pool.reject_region(PRIMARY)
            self.assertEqual(pool.choose(), FALLBACK)
            self.assertIsNone(pool.choose([FALLBACK]))
        with patch('route_health.time.monotonic', return_value=221):
            self.assertEqual(pool.choose(), PRIMARY)

    def test_recent_real_answer_beats_unproven_primary(self):
        pool = RoutePool(PRIMARY, [FALLBACK])
        with patch('route_health.time.monotonic', return_value=100):
            pool.answer(FALLBACK)
            self.assertEqual(pool.choose(), FALLBACK)
        with patch('route_health.time.monotonic', return_value=1001):
            self.assertEqual(pool.choose(), PRIMARY)

    def test_recent_model_answer_protects_route_from_auxiliary_refusal(self):
        pool = RoutePool(PRIMARY)
        with patch('route_health.time.monotonic', return_value=100):
            pool.answer(PRIMARY)
        with patch('route_health.time.monotonic', return_value=101):
            pool.reject_region(PRIMARY)
            self.assertEqual(pool.choose(), PRIMARY)
        with patch('route_health.time.monotonic', return_value=191):
            pool.reject_region(PRIMARY)
            self.assertIsNone(pool.choose())

    def test_fallback_config_is_explicit_loopback_only_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for values in [['http://remote.example:8080'], [PRIMARY] * 4, ['http://user:secret@localhost:80']]:
                (root / 'config.json').write_text(json.dumps({'fallback_http_proxies': values}))
                with self.assertRaises(ValueError):
                    configuration(root)
            (root / 'config.json').write_text(json.dumps({'fallback_http_proxies': [FALLBACK]}))
            self.assertEqual(configuration(root)['fallback_http_proxies'], [FALLBACK])


class RouteRequestTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend(Path('/unused'), {'http_proxy': PRIMARY, 'fallback_http_proxies': [FALLBACK]})
        self.backend.access_token = 'fake-token'
        self.backend.project = 'test-project'
        self.backend.endpoint = 'https://example.invalid'
        self.backend.expires = time.monotonic() + 3600
        self.backend.expires_at = time.time() + 3600
        self.backend.catalog = {'test': {}}

    def test_regional_400_tries_only_next_configured_proxy(self):
        seen = []
        def post(url, data, token=None, form=False, route=None):
            seen.append(route)
            if route == PRIMARY:
                raise urllib.error.HTTPError(url, 400, 'region', {}, reply(REGION))
            return reply({'candidates': []})
        with patch.object(self.backend, 'post', side_effect=post):
            with self.backend.generate('test', {}, True):
                pass
        self.assertEqual(seen, [PRIMARY, FALLBACK])

    def test_other_400_is_not_replayed_and_body_survives(self):
        data = {'error': {'message': 'bad input'}}
        with patch.object(self.backend, 'post', side_effect=urllib.error.HTTPError('test', 400, 'bad', {}, reply(data))) as post:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.backend.generate('test', {}, True)
            self.assertEqual(json.load(caught.exception), data)
            caught.exception.close()
        self.assertEqual(post.call_count, 1)

    def test_all_regional_routes_stop_without_direct_connection(self):
        def post(url, data, token=None, form=False, route=None):
            self.assertIn(route, [PRIMARY, FALLBACK])
            raise urllib.error.HTTPError(url, 400, 'region', {}, reply(REGION))
        with patch.object(self.backend, 'post', side_effect=post) as request:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.backend.generate('test', {}, True)
            caught.exception.close()
        self.assertEqual(request.call_count, 2)
        self.assertIsNone(self.backend.routes.choose())
        with self.assertRaises(NoAvailableRoute):
            self.backend.generate('test', {}, True)

    def test_explicit_proxy_survives_inherited_no_proxy_star(self):
        fake = reply({})
        with patch.dict('os.environ', {'NO_PROXY': '*'}):
            with patch.object(self.backend.opener, 'open', return_value=fake) as opened:
                with self.backend.post('https://cloudcode-pa.googleapis.com/test', {}):
                    pass
        request = opened.call_args.args[0]
        self.assertEqual(request.host, '127.0.0.1:10809')
        self.assertEqual(request._tunnel_host, 'cloudcode-pa.googleapis.com')
        self.assertEqual(request.get_header('Host'), 'cloudcode-pa.googleapis.com')

    def test_redirects_cannot_remove_proxy_binding(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, 'redirect', {}, 'https://other.invalid'))


class ProbeTests(unittest.TestCase):
    def backend(self, content):
        result = Mock()
        result.catalog = {'gemini-3.8-flash-tiered': {}}
        result.routes = RoutePool(PRIMARY)
        stream = io.BytesIO(content)
        stream.agycli_route = PRIMARY
        result.generate.return_value = stream
        return result

    def test_probe_requires_actual_content_and_marks_route(self):
        value = {'response': {'candidates': [{'content': {'parts': [{'text': 'OK'}]}}]}}
        backend = self.backend(b'data: ' + json.dumps(value).encode() + b'\n\n')
        result = probe_model(backend, 'gemini-3.8-flash-high')
        self.assertEqual(result['status'], 'model_answer')
        self.assertTrue(backend.routes.state()[0]['model_answer_seen'])
        self.assertEqual(backend.generate.call_args.args[0], 'gemini-3.8-flash')
        self.assertNotIn('tools', backend.generate.call_args.args[1])

    def test_empty_stream_is_not_success(self):
        self.assertEqual(probe_model(self.backend(b'data: {}\n\n'), 'test')['status'], 'no_model_answer')

    def test_region_probe_report_contains_no_raw_backend_response(self):
        backend = self.backend(b'')
        body = {'error': {'message': 'User location is not supported private-test-details'}}
        backend.generate.side_effect = urllib.error.HTTPError('test', 400, 'error', {}, reply(body))
        result = probe_model(backend, 'test')
        self.assertEqual(result['status'], 'region')
        self.assertNotIn('private-test-details', json.dumps(result))

    def test_unknown_model_is_never_silently_replaced(self):
        self.assertEqual(probe_name('unknown-high', {'other': {}}), 'unknown-high')
