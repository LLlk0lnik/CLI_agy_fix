# agyCLI — operational handoff for an AI assistant

Read this file, README.md, app/config.json and app/BUILD-MANIFEST.json first. The user wants this portable package running with their own Google account and local Happ proxy. Explain actions in Russian unless asked otherwise. No prior conversation is required.

## Scope and non-negotiable boundaries

- Work only inside this extracted package and an explicitly selected project. Do not modify Zed, its processes, profiles or credentials. Never read/export the OS keychain or Windows Credential Manager.
- Never print token files, OAuth secrets, Authorization headers, private prompts or the full app/profile/server.log. Do not upload profile or logs to a model/helper. Inspect only redacted status/field names when necessary.
- Do not change shell profiles, PATH, system proxy, VPN/TUN, payment-country settings or global model servers. Installation of Python/Happ or optional system agy is an explicit setup step, not an action performed by these scripts.
- The four launchers directly invoke Python entry points, never each other. Preserve this property. Login is a separate entry point; do not silently reuse a Zed token.
- Profiles are not a filesystem/network sandbox. Do not claim leak prevention, universal geographic access or automatic entitlement bypass.

## Mandatory preflight: reuse installed components; never install duplicates

Before suggesting or running ANY installer, inventory the existing components with read-only checks. Treat every installation step as conditional: if a suitable component is already present and works, record "already installed — skipped" and continue to the next step. Do not repeat an installer just because a README lists that step.

- **agy / ACP:** first check the package's three runtime binaries and app/BUILD-MANIFEST.json. The bundled agy and ACP already satisfy this package's dependency; do NOT install another system agy or another ACP package. You may also inspect command lookup and standard installation locations to report an existing system agy, but do not replace it, add another PATH entry, or silently switch the package away from its tested bundled binaries. A separate system agy installation is only for an explicit user request, never routine setup.
- **Happ:** check the installed applications before downloading anything. On macOS inspect /Applications and the user's Applications folder for an existing Happ app, including App Store/Desktop variants. On Windows use existing application/Start-menu entries and, if necessary, read-only uninstall-registration or standard installation-path checks. An already installed, suitable Happ means SKIP installation; use that instance and inspect its local proxy ports. Do not install a second Desktop/App Store variant alongside it. An inactive app or closed proxy port does not mean Happ is missing: open/configure the existing app instead.
- **Python:** check command lookup, interpreter versions and the Python Launcher on Windows (`py -0p`, `py -3 --version`, `python --version` as applicable; `command -v python3` and `python3 --version` on macOS). Reuse a compatible installed interpreter; no second Python installation or pip environment is needed for this stdlib-only package. Missing PATH lookup alone is not proof that Python is absent; check the launcher/normal installation locations first.
- **Proxy/config:** check app/config.json and the existing Happ instance. Reuse a working local proxy; do not reset profiles, import somebody else's subscription or start a global VPN merely to repeat setup instructions.

Report a short status for each dependency: available/compatible, present but needs configuration, incompatible, or missing. Install only genuinely missing required components. If an existing component is incompatible, explain the specific mismatch; do not silently reinstall, upgrade, replace or uninstall a working installation. Preserve user settings and seek the user's decision before a disruptive replacement. Do not install package managers, alternate VPN clients or extra copies as a workaround without an explicit request.

## Start from zero

1. Verify platform/CPU from app/BUILD-MANIFEST.json against this computer. This package targets macOS Apple Silicon ARM64, not Intel.
2. agy 1.4.12 and ACP 1.4.5 are already bundled under runtime. For an optional normal agy installation, consult https://antigravity.google/docs/getting-started; do not assume its PATH binary is used here.
3. Require Python 3.11+ from python.org (stdlib only), and Happ from happ.info plus the user's own subscription/config. No pip packages are needed.
4. Require a working loopback HTTP proxy. app/config.json defaults to HTTP 10809 and SOCKS5 10808, but actual Happ ports vary, particularly on Windows. Change only package app/config.json when needed. Global VPN/TUN remains the user's independent choice.
5. Fully extract the package into a user-owned writable, non-shared folder. Run setup/Check.command. It checks files/platform, HTTPS through the HTTP proxy, ACP initialize, and optionally account/model-catalog state. A clean package has no profile; "run Login" is expected.
6. Have the user run setup/Login.command and complete the browser OAuth flow themselves. Do not request or copy another person's credentials. Browser networking is independent of the agent's proxy environment.
7. Run Run-AGY.command from the intended project working directory. Check with a short --print request, then optionally read an explicitly created fixture. Do not touch unrelated project files.
8. Run-ACP.command is the separate fallback text client. It has /models, /model ID, /new and /quit.

## Files and existing contracts

- launcher_common.py: configuration(root=ROOT), runtime_path(name, root=ROOT, windows=None), clean_environment(base=None), proxy_environment(env, config), private_home_environment(env, home, windows=None), acp_environment(root=ROOT), process_group_options(), terminate_owned_process(proc), client_user_agent(). Config has exactly http_proxy, socks_proxy, default_model. Proxy URLs must be loopback, without credentials. No process writes global environment variables.
- acp_terminal.py: Client(command, env, log_path) manages JSON-RPC 2.0 over NDJSON stdio using ACP v1. initialize → authenticate(methodId=oauth-personal) when needed → session/new(cwd, mcpServers=[]) → session/prompt. Streaming uses session/update. session/request_permission requires explicit user selection; empty input denies. No client FS/terminal capabilities are advertised; the official harness owns its tools. GEMINI_HOME=app/profile/gemini and AGY_ACP_FORCE_FILE_STORAGE=1. Runtime path is platform-specific. Windows uses a new child process group; taskkill targets only that owned PID/tree on forced cleanup.
- agy_native.py: native_command(binary, arguments, default_model=...) preserves CLI flags and merges --settings while forcing modelProvider=gemini. Backend(token_path, config=None) refreshes the separate file token in memory, checks loadCodeAssist, requires currentTier plus cloudaicompanionProject, chooses daily/prod using paidTier.usesGcpTos, and forwards Gemini generation with tools/results through CCPA. It does not fabricate account or model responses. OAuth credentials go only to the fixed Google OAuth URL. Any ineligible free tier is distinct from an existing valid paid/standard tier, following observed ACP behavior.
- The bridge binds 127.0.0.1 on an ephemeral port, authenticates local requests with a random per-launch key, and unwraps CCPA JSON/SSE into Gemini responses. SSE blank lines normalize CRLF to LF. Model aliases are used only if a corresponding -tiered or non-preview ID exists in the fetched catalog. countTokens returns 501.
- Native child: bundled app/runtime/agy; HOME/GEMINI_HOME private. On Windows USERPROFILE/APPDATA/LOCALAPPDATA/HOMEDRIVE/HOMEPATH are also private. Google/Gemini/Antigravity credential/gateway variables are removed before setting the local provider key. AGY_CLI_DISABLE_AUTO_UPDATE=true. Do not broaden filesystem isolation claims: child tools can access normal files and may miss the real user's Git/SSH configuration.
- diagnostics.py: --offline checks local files/architecture without login/network. The normal check can refresh the separate profile token in memory, but never initiates browser login.
- app/verify_package.py checks app/SHA256SUMS.txt; modified app/config.json is an expected mismatch after local customization. app/pack_clean.py uses only distribution_files in app/BUILD-MANIFEST.json; never includes profile and resets app/config.json to the example. app/pack_clean.py regenerates checksums for the actual archive contents; refresh app/BUILD-MANIFEST.json version/provenance for a new release.

## State and privacy

app/profile/gemini/antigravity-acp/acp_token.json contains sensitive OAuth data. app/profile/native-home contains native CLI history/settings. app/profile/server.log may contain account metadata and temporary login URLs. On macOS creation uses restrictive UNIX modes. Windows file permissions inherit user-folder ACLs; POSIX chmod is not an ACL guarantee. Original shared archives contain none of this state. No real account/email/machine paths belong in distribution source, docs or manifests.

## Verification and honest limits

Run `python3 app/run_tests.py`. All 33 common tests were run on macOS; Windows path/env branches were tested logically, not with actual Windows executables. macOS real native text output and reading a test fixture were verified. Windows PE binaries were downloaded from official Google URLs and checked against upstream SHA-512/SHA-256, but actual Windows startup/login/dialog is unverified. Preserve this limitation in reports.

No arbitrary fallback model, endpoint, credential source or entitlement should be substituted silently. If Google returns 403/404, surface the failure and inspect model IDs/config without exposing credentials. Prefer bounded corrections in the affected file; preserve the working ACP fallback and the user's local profile. After code changes run relevant tests, then one necessary smoke check rather than repeated inference.

## Releasing a clean package

Use the manifest's explicit allowlist; no recursive ZIP of the working folder. Include only source, launchers, tests, example/default config, manifests/checksums, docs and the three original runtime binaries. Exclude profile, __pycache__, logs, .DS_Store, credentials and user paths. Preserve executable bits for macOS. Windows .cmd files use CRLF and directly invoke Python without shell-global changes. When sharing after customization use app/pack_clean.py; for a versioned release regenerate hashes and scan text for emails, user paths and literal tokens. Never call an archive "clean" based only on its name.

All Python modules live under app/. ROOT in those modules is the app directory; manifest distribution paths are relative to its parent package directory. Main launchers are at package root; setup holds only Login and Check.

## OAuth recovery contract

Backend.credentials(rejected_token=None) caches credentials with both wall-clock and monotonic deadlines, including a 60-second safety margin measured before the refresh HTTP request. Backend.authenticated_post retries Google HTTP 401 exactly once, before any response body is returned. The lock refreshes only the rejected token; reuse a newer token if another request refreshed it already. Generation must acquire credentials after model-catalog loading to avoid a stale token snapshot. Do not retry 403, transport failures or an interrupted stream: replay may duplicate model work. OAuth invalid_grant raises ReauthenticationRequired and asks for setup/Login; never loop or log token material. The 12 token-refresh tests use only fake credentials, including concurrent 401s, model-list refresh and paused monotonic time.
