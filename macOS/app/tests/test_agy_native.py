import contextlib
import io
import json
import threading
import unittest
import urllib.error
import urllib.request

from agy_native import Handler, Server, PROD, DAILY, resolve_entitlement, canonical_model, native_command


class LogicTests(unittest.TestCase):
    def test_settings_preserved_but_provider_is_local_bridge(self):
        cmd = native_command('/agy', ['--settings', '{"modelProvider":"account","verbosity":"low"}',
                                      '--model', 'chosen', '--print', 'hello'])
        settings = json.loads(cmd[cmd.index('--settings') + 1])
        self.assertEqual(settings, {'modelProvider': 'gemini', 'verbosity': 'low'})
        self.assertEqual(cmd.count('--model'), 1)
        self.assertIn('chosen', cmd)

    def test_active_tier_is_distinct_from_free_tier(self):
        data = {'currentTier': {'id': 'standard-tier'}, 'cloudaicompanionProject': 'project',
                'ineligibleTiers': [{'tierId': 'free-tier', 'reasonCode': 'UNSUPPORTED_LOCATION'}]}
        self.assertEqual(resolve_entitlement(data), ('project', DAILY))
        data['paidTier'] = {'usesGcpTos': True}
        self.assertEqual(resolve_entitlement(data), ('project', PROD))

    def test_no_fabricated_entitlement(self):
        for data in ({}, {'currentTier': {}}, {'cloudaicompanionProject': 'project'}):
            with self.assertRaises(RuntimeError):
                resolve_entitlement(data)

    def test_aliases_only_point_to_available_model(self):
        models = {'gemini-3.8-flash-tiered': {}, 'gemini-3.1-flash-lite': {}}
        self.assertEqual(canonical_model('gemini-3.8-flash', models), 'gemini-3.8-flash-tiered')
        self.assertEqual(canonical_model('gemini-3.1-flash-lite-preview', models), 'gemini-3.1-flash-lite')
        self.assertEqual(canonical_model('other', models), 'other')
        self.assertEqual(canonical_model('gemini-3.8-flash', {}), 'gemini-3.8-flash')


class FakeResponse(io.BytesIO):
    status = 200


class FakeBackend:
    def __init__(self):
        self.received = []

    def generate(self, model, body, streaming):
        self.received.append((model, body, streaming))
        if model == 'denied':
            raise urllib.error.HTTPError('http://example.invalid', 403, 'Forbidden', {},
                                         io.BytesIO(b'{"error":{"code":403,"message":"Denied"}}'))
        response = {'candidates': [{'content': {'parts': [{'text': 'works'}]}}]}
        if streaming:
            return FakeResponse(b'data: ' + json.dumps({'response': response}).encode() + b'\r\n\r\n')
        return FakeResponse(json.dumps({'response': response}).encode())


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = Server(('127.0.0.1', 0), Handler)
        self.server.local_key = 'test-local-secret'
        self.server.backend = FakeBackend()
        self.worker = threading.Thread(target=self.server.serve_forever)
        self.worker.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()

    def request(self, path, key='test-local-secret', body=None):
        data = json.dumps(body or {'contents': []}).encode()
        request = urllib.request.Request(self.base + path, data=data,
                    headers={'x-goog-api-key': key, 'Content-Type': 'application/json'})
        return self.opener.open(request, timeout=3)

    def test_unary_preserves_tool_calls(self):
        body = {'contents': [], 'tools': [{'functionDeclarations': [{'name': 'tool'}]}]}
        with self.request('/v1beta/models/test:generateContent', body=body) as response:
            self.assertIn('candidates', json.load(response))
        self.assertEqual(self.server.backend.received, [('test', body, False)])

    def test_sse_unwraps_response(self):
        with self.request('/v1beta/models/test:streamGenerateContent?alt=sse') as response:
            raw = response.read()
        self.assertTrue(raw.startswith(b'data: {"candidates"'))
        self.assertTrue(raw.endswith(b'\n\n'))

    def test_unauthorized_cannot_call_backend(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/v1beta/models/test:generateContent', key='wrong')
        self.assertEqual(error.exception.code, 401)
        error.exception.close()
        self.assertEqual(self.server.backend.received, [])

    def test_google_rejection_is_preserved(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/v1beta/models/denied:generateContent')
        self.assertEqual(error.exception.code, 403)
        self.assertEqual(json.load(error.exception)['error']['message'], 'Denied')
        error.exception.close()


if __name__ == '__main__':
    unittest.main()
