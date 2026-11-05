# ACP integration contract

The default launch path is agym / Run-AGY / Run-ACP -> acp_terminal.py -> the pinned Google ACP executable and its original localharness_external companion. agy_native.py is a compatibility shim only. Do not reintroduce a Google HTTP bridge, credential file reads, custom OAuth clients, token refresh, synthetic User-Agent identity, model aliasing, DNS/SNI targets or binary patches.

Authentication: initialize ACP v1 with honest clientInfo agycli-terminal; select the advertised oauth-personal method and call authenticate. session/new or session/load may retry once after ACP AuthenticationRequired (-32000). Other errors, including account restrictions, propagate without route fallback. Never impersonate Zed to obtain editor-only model entitlements.

Capabilities: fs.readTextFile=false, fs.writeTextFile=false, terminal=false. The official harness owns its tools. Unknown client requests return -32601. Permission requests require a user selection by default. Explicit yolo selects an advertised allow_once only, never invents a permission. Model selection uses session/set_config_option for an advertised category=model, otherwise the legacy advertised availableModels and session/set_model. Session resume uses ACP session/load, never copies conversation history into prompts.

Configuration: loopback HTTP/SOCKS proxies, default_model (empty means the official agent default), routing=proxy, fallback_http_proxies=[]. Legacy auto maps to proxy without a fallback. SmartDNS and nonempty fallbacks are rejected. The local HTTP proxy is checked before login/session creation. Environment changes are child-local. Proxy use by the upstream agent and its route through Happ are not an OS firewall guarantee.

Credentials and logs: GEMINI_HOME points to app/profile/gemini; AGY_ACP_FORCE_FILE_STORAGE=1 preserves profile isolation. HOME is not changed, allowing the real browser launcher. Only the official agent opens credentials. The client writes server.log and last-session.json (ACP session ID and canonical cwd only), both within the ignored profile. No existing profile is copied, deleted or migrated. Never include profiles in artifacts.

Integrity: verify_acp_runtime hashes the ACP executable and companion harness against BUILD-MANIFEST before every spawn. Upstream archive hashes were checked against the current ACP Registry and extracted files compared with the bundled bytes on 2026-11-05. Both platforms share identical Python code. Keep manifest allowlists and checksums current using tools/release.py; do not publish LFS pointer stubs.

Validation: run app/run_tests.py on each package; tools/test_release.py covers packaging and cross-platform source parity. test_acp_flow.py uses a real fake-agent subprocess over stdio to verify auth, prompts, models, permission requests, error propagation, resume and no credential reads. --check only initializes the agent; --model-test is opt-in and uses quota. No successful generation or account reinstatement should be claimed from a handshake.

See ZED-ACP-AUDIT.md for fixed source revisions, provenance and differences from Zed. The official agent/harness are proprietary binaries; their internals were not source-audited. This client is not officially certified by Google.
