# Lila Hub (`lila-hub`) — Plug-and-Play Connector Board for TicketLabs Standalones

`lila-hub` is the open-source connector board that lets anyone snap together any combination of TicketLabs standalone packages (`lila-asi`, `lila-capability-engine`, `lila-vtalks`, `lila-oia`, `lila-sensa`, or their own `llama.cpp` / OpenAI backend) plus drop-in `plugins/` and entry-point extensions into one runtime.

## How It Works

Each TicketLabs package works 100% standalone. When you place them together or run `LilaHub.connect()` + `hub.attach_capability_providers(...)`, `lila-hub` automatically discovers which modules and plugins exist and wires their bridges together:

1. **`lila-capability-engine` + `lila-asi` (or `custom_llm_fn`):**
   - Automatically matches user queries to active `CapabilityBundle`s and injects the compact capability index + active tool schemas into the system prompt.
   - Feeds active tools directly into `AsiRuntime(..., active_tools=...)` so **E05 v2 Grammar-Aware Sparse `lm_head`** hardware-locks valid `<tool_call>` JSON.
2. **`lila-vtalks` & `lila-oia` & `lila-sensa`:**
   - Exposes `ParakeetTranscriber`, `OwnerAuthenticationService`/`OwnerProfileStore`, and `Sensa` media workflows automatically when present.
3. **Zero-`pip` Drop-In `plugins/` Scanning:**
   - Drop any plugin folder under `plugins/<name>/` with a `lila_plugin.json` manifest (`{"provider_id": "...", "factory": "module:callable"}`) and `LilaHub` loads it automatically with no `pip install` needed.
4. **Python Entry Points (`lila.capability_providers`):**
   - Pip-installed packages advertising `lila.capability_providers` entry points are also auto-attached cleanly with per-plugin error isolation.

## Quickstart (3 Lines to Connect Everything Installed)

```python
from lila_hub import LilaHub

hub = LilaHub(asi_model_path="model.asi", asi_mode="fast")
hub.connect()

print("Connected modules:", hub.status())
print(hub.chat("Hello! What can you help me with?"))
hub.close()
```

## Using With `llama.cpp` or OpenAI Instead of `lila-asi`

```python
from lila_hub import LilaHub

hub = LilaHub(custom_llm_fn=lambda messages: my_llama_cpp_chat(messages))
hub.connect()  # Still wires lila-capability-engine, lila-vtalks, and lila-oia!
print(hub.chat("Check the system health diagnostics."))
```

## License

Developed and maintained by TicketLabs, part of the Harboria Labs technology portfolio.
Licensed under the GNU Affero General Public License v3.0 (`AGPL-3.0-only`). See [LICENSE](LICENSE) and [NOTICE](NOTICE).
