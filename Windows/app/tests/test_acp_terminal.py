import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from acp_terminal import Client, RPCError, child_environment, display


class EnvironmentTests(unittest.TestCase):
    def test_private_profile_and_no_inherited_credentials(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake", "GOOGLE_API_KEY": "fake",
                                    "CLOUD_CODE_URL": "https://invalid.example",
                                    "AGY_ACP_FORCE_FILE_STORAGE": "0"}):
            before = dict(os.environ)
            env = child_environment(Path("/private/client"))
            self.assertEqual(dict(os.environ), before)
        self.assertEqual(env["AGY_ACP_FORCE_FILE_STORAGE"], "1")
        self.assertEqual(env["GEMINI_HOME"], str(Path('/private/client') / 'profile' / 'gemini'))
        self.assertNotIn("GEMINI_API_KEY", env)
        self.assertNotIn("GOOGLE_API_KEY", env)
        self.assertNotIn("CLOUD_CODE_URL", env)
        self.assertEqual(env["NO_PROXY"], "localhost,127.0.0.1,::1")

    def test_output_controls_are_removed(self):
        self.assertEqual(display("\x1b[31mhello\x1b[0m\x00\x07"), "hello")
        self.assertEqual(display("\x1b]52;c;clipboard\x07text"), "text")


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        script = self.root / "fake.py"
        script.write_text('''import json,sys
for line in sys.stdin:
 m=json.loads(line)
 method=m.get('method')
 if method=='permission':
  print(json.dumps({'jsonrpc':'2.0','id':'permission-id','method':'session/request_permission','params':{'toolCall':{'title':'Write file'},'options':[{'optionId':'yes','name':'Allow once','kind':'allow_once'}]}}),flush=True)
  reply=json.loads(sys.stdin.readline())
  print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':reply['result']}),flush=True)
 elif method=='error':
  print(json.dumps({'jsonrpc':'2.0','id':m['id'],'error':{'code':-32000,'message':'auth required'}}),flush=True)
 elif method=='stop': sys.exit(0)
 else:
  print(json.dumps({'jsonrpc':'2.0','method':'session/update','params':{'update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'hello'}}}}),flush=True)
  print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{'ok':True}}),flush=True)
''')
        self.client = Client([sys.executable, str(script)], os.environ.copy(), self.root / "log")

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_streaming_and_response(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(self.client.request("initialize", {}, 3), {"ok": True})
        self.assertIn("hello", output.getvalue())

    def test_permission_defaults_to_denial(self):
        with patch("builtins.input", return_value=""), contextlib.redirect_stdout(io.StringIO()):
            result = self.client.request("permission", {}, 3)
        self.assertEqual(result, {"outcome": {"outcome": "cancelled"}})

    def test_permission_selection(self):
        with patch("builtins.input", return_value="1"), contextlib.redirect_stdout(io.StringIO()):
            result = self.client.request("permission", {}, 3)
        self.assertEqual(result, {"outcome": {"outcome": "selected", "optionId": "yes"}})

    def test_auth_error_keeps_code(self):
        with self.assertRaises(RPCError) as error:
            self.client.request("error", {}, 3)
        self.assertEqual(error.exception.code, -32000)

    def test_server_exit_does_not_hang(self):
        with self.assertRaises(RuntimeError):
            self.client.request("stop", {}, 3)


if __name__ == "__main__":
    unittest.main()
