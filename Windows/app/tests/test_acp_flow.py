import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import acp_terminal as terminal
from launcher_common import configuration, runtime_path, require_proxy, verify_acp_runtime

FAKE = r"""import json,sys,pathlib
root=pathlib.Path(__file__).parent.parent
scenario=json.loads((root/'scenario.json').read_text())
authenticated=False
options=[{'id':'model-choice','category':'model','type':'select','currentValue':'google-default',
          'options':[{'value':'google-default','name':'Default'},{'value':'offered-model','name':'Offered'}]}]
for line in sys.stdin:
 m=json.loads(line)
 with (root/'trace.jsonl').open('a') as f:f.write(json.dumps(m)+'\n')
 method=m.get('method');params=m.get('params',{});response={}
 if method=='initialize':
  response={'protocolVersion':1,'agentInfo':{'name':'antigravity-acp','version':'test'},
            'authMethods':[{'id':'oauth-personal'}], 'agentCapabilities':{'loadSession':True}}
 elif method=='authenticate': authenticated=True
 elif method in ('session/new','session/load'):
  if scenario=='blocked':
   print(json.dumps({'jsonrpc':'2.0','id':m['id'],'error':{'code':-32603,'message':'HTTP 403 PERMISSION_DENIED'}}),flush=True);continue
  if scenario=='auth' and not authenticated:
   print(json.dumps({'jsonrpc':'2.0','id':m['id'],'error':{'code':-32000,'message':'Authentication required'}}),flush=True);continue
  response={'sessionId':'official-session','configOptions':options}
 elif method=='session/set_config_option':response={'configOptions':options}
 elif method=='session/prompt':
  if scenario=='permission':
   print(json.dumps({'jsonrpc':'2.0','id':'permission','method':'session/request_permission',
     'params':{'toolCall':{'title':'Test action'},'options':[{'optionId':'allow','kind':'allow_once','name':'Allow once'}]}}),flush=True)
   reply=json.loads(sys.stdin.readline())
   with (root/'trace.jsonl').open('a') as f:f.write(json.dumps(reply)+'\n')
  if scenario!='empty':
   print(json.dumps({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'official-session',
    'update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'OK'}}}}),flush=True)
  response={'stopReason':'end_turn'}
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':response}),flush=True)
"""


class ACPFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        (self.root / 'runtime').mkdir()
        self.agent = runtime_path('acp', self.root)
        self.agent.write_text(FAKE)
        harness = runtime_path('harness', self.root)
        harness.write_bytes(b'unmodified-test-harness')
        metadata = {p.name: {'size': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                    for p in (self.agent, harness)}
        (self.root / 'BUILD-MANIFEST.json').write_text(json.dumps({'binaries': metadata}))
        self.scenario('normal')
        self.workspace = self.root / 'workspace with spaces'
        self.workspace.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def scenario(self, name):
        (self.root / 'scenario.json').write_text(json.dumps(name))

    def trace(self):
        path = self.root / 'trace.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def run_client(self, args):
        original_client = terminal.Client
        def spawn(command, env, log_path, **kwargs):
            self.assertEqual(command, [str(self.agent)])
            self.assertNotIn('GEMINI_API_KEY', env)
            self.assertNotIn('GOOGLE_GEMINI_BASE_URL', env)
            self.assertEqual(env['HTTPS_PROXY'], 'http://127.0.0.1:10809')
            return original_client([sys.executable, str(self.agent)], env, log_path, **kwargs)
        # A bait credential file must never be opened by our client.
        token = self.root / 'profile/gemini/antigravity-acp/acp_token.json'
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text('do-not-read-test-token')
        original_read = Path.open
        def safe_open(path, *a, **kw):
            if path == token:
                raise AssertionError('Client opened agent OAuth credentials')
            return original_read(path, *a, **kw)
        with patch.object(terminal, 'Client', side_effect=spawn), \
             patch.object(terminal, 'require_proxy'), patch.object(Path, 'open', safe_open), \
             patch.dict(os.environ, {'GEMINI_API_KEY': 'inherited-test-secret',
                                     'GOOGLE_GEMINI_BASE_URL': 'https://do-not-use.invalid'}), \
             contextlib.redirect_stdout(io.StringIO()):
            return terminal.main([*args, '--cwd', str(self.workspace)], root=self.root)

    def test_check_only_initializes_without_authentication(self):
        self.assertEqual(self.run_client(['--check']), 0)
        self.assertEqual([r['method'] for r in self.trace()], ['initialize'])
        params = self.trace()[0]['params']
        self.assertEqual(params['clientInfo']['name'], 'agycli-terminal')
        self.assertFalse(params['clientCapabilities']['terminal'])

    def test_prompt_uses_stdio_agent_and_server_model_option(self):
        self.assertEqual(self.run_client(['-p', 'yolo', '--model', 'offered-model']), 0)
        trace = self.trace()
        self.assertEqual([r['method'] for r in trace],
                         ['initialize', 'session/new', 'session/set_config_option', 'session/prompt'])
        self.assertEqual(trace[1]['params'], {'cwd': str(self.workspace), 'mcpServers': []})
        self.assertEqual(trace[2]['params']['configId'], 'model-choice')
        self.assertEqual(trace[3]['params']['prompt'][0]['text'], 'yolo')
        self.assertNotIn('do-not-read-test-token', json.dumps(trace))

    def test_login_is_only_an_advertised_acp_authenticate(self):
        self.assertEqual(self.run_client(['--login']), 0)
        self.assertEqual([r['method'] for r in self.trace()], ['initialize', 'authenticate'])

    def test_missing_login_retries_session_once_via_agent(self):
        self.scenario('auth')
        self.assertEqual(self.run_client(['-p', 'hello']), 0)
        self.assertEqual([r['method'] for r in self.trace()],
                         ['initialize', 'session/new', 'authenticate', 'session/new', 'session/prompt'])

    def test_403_is_propagated_without_login_or_network_fallback(self):
        self.scenario('blocked')
        with self.assertRaisesRegex(terminal.RPCError, '403 PERMISSION_DENIED'):
            self.run_client(['-p', 'hello'])
        self.assertEqual([r['method'] for r in self.trace()], ['initialize', 'session/new'])

    def test_unknown_model_is_never_sent_to_agent(self):
        with self.assertRaises(ValueError):
            self.run_client(['--model', 'invented-model', '-p', 'hello'])
        self.assertEqual([r['method'] for r in self.trace()], ['initialize', 'session/new'])

    def test_continue_loads_official_session_without_replaying_prompt(self):
        self.run_client(['-p', 'first'])
        (self.root / 'trace.jsonl').unlink()
        self.assertEqual(self.run_client(['-c', '-p', 'second']), 0)
        trace = self.trace()
        self.assertEqual([r['method'] for r in trace], ['initialize', 'session/load', 'session/prompt'])
        self.assertEqual(trace[1]['params']['sessionId'], 'official-session')
        self.assertEqual(trace[2]['params']['prompt'][0]['text'], 'second')

    def test_continue_different_workspace_fails_before_agent_start(self):
        self.run_client(['-p', 'first'])
        (self.root / 'trace.jsonl').unlink()
        self.workspace = self.root / 'another-workspace'
        self.workspace.mkdir()
        with self.assertRaisesRegex(RuntimeError, 'другой папке'):
            self.run_client(['-c', '-p', 'second'])
        self.assertEqual(self.trace(), [])

    def test_yolo_approves_only_advertised_permission(self):
        self.scenario('permission')
        self.assertEqual(self.run_client(['yolo', '-p', 'hello']), 0)
        reply = next(r for r in self.trace() if r.get('id') == 'permission')
        self.assertEqual(reply['result'], {'outcome': {'outcome': 'selected', 'optionId': 'allow'}})

    def test_empty_prompt_response_does_not_claim_model_success(self):
        self.scenario('empty')
        with self.assertRaisesRegex(RuntimeError, 'не вернул текстовый ответ'):
            self.run_client(['-p', 'hello'])

    def test_diagnostic_prompt_does_not_overwrite_resume_pointer(self):
        self.run_client(['-p', 'first'])
        pointer = self.root / 'profile/last-session.json'
        before = pointer.read_bytes()
        self.assertEqual(self.run_client(['--no-save-session', '-p', 'probe']), 0)
        self.assertEqual(pointer.read_bytes(), before)

    def test_modified_agent_is_rejected_before_spawn(self):
        self.agent.write_text(FAKE.replace('authenticated=False', 'authenticated=True '))
        with self.assertRaises(RuntimeError):
            self.run_client(['--check'])
        self.assertEqual(self.trace(), [])


class BoundaryTests(unittest.TestCase):
    def test_smart_dns_cannot_be_enabled_and_auto_migrates_to_proxy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'config.json'
            path.write_text(json.dumps({'routing': 'auto'}))
            self.assertEqual(configuration(root)['routing'], 'proxy')
            path.write_text(json.dumps({'routing': 'smart_dns'}))
            with self.assertRaisesRegex(ValueError, 'SmartDNS removed'):
                configuration(root)
            path.write_text(json.dumps({'fallback_http_proxies': ['http://127.0.0.1:20809']}))
            with self.assertRaises(ValueError):
                configuration(root)

    def test_missing_proxy_fails_without_starting_agent(self):
        with patch('socket.create_connection', side_effect=ConnectionRefusedError):
            with self.assertRaisesRegex(RuntimeError, 'Прокси Happ недоступен'):
                require_proxy(configuration(Path('/no-local-config')))

    def test_model_selection_does_not_invent_or_rename_legacy_models(self):
        client = unittest.mock.Mock()
        client.config_options = []
        client.session_id = 'test'
        session = {'models': {'availableModels': [{'modelId': 'official-model'}]}}
        terminal.select_model(client, session, 'official-model')
        client.request.assert_called_once_with('session/set_model',
                                             {'sessionId': 'test', 'modelId': 'official-model'})
        with self.assertRaises(ValueError):
            terminal.select_model(client, session, 'official-model-high')

    def test_unadvertised_auth_method_is_never_called(self):
        client = unittest.mock.Mock()
        with self.assertRaises(RuntimeError):
            terminal.authenticate(client, {'authMethods': []})
        client.request.assert_not_called()
