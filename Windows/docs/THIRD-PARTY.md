# Original third-party components

Bundled Google Antigravity CLI 1.4.12 and ACP Server 1.4.5 are unmodified original Google binaries. They remain subject to their original terms, not a new license granted by this wrapper package.

- Official installation/docs: https://antigravity.google/docs/getting-started
- Terms: https://antigravity.google/terms
- ACP release archive: https://dl.google.com/agy-extensions/releases/
- CLI update manifest service linked by Google's official installer: https://antigravity-cli-auto-updater-974169037036.us-central1.run.app
- ACP protocol: https://github.com/agentclientprotocol/agent-client-protocol
- Python (not bundled): https://www.python.org/downloads/
- Happ (not bundled; own subscription/config needed): https://happ.info/

No vendor installer is executed by the package and no model weights are included. Version/source details and SHA-256 hashes are recorded in app/BUILD-MANIFEST.json and app/SHA256SUMS.txt. The Python wrappers are a separate local integration and are not an official Google feature or entitlement grant.

All Python modules live under app/. ROOT in those modules is the app directory; manifest distribution paths are relative to its parent package directory. Main launchers are at package root; setup holds only Login and Check.
