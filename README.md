# Lila Hub (`lila-hub`) — Plug-and-Play Connector Board for TicketLabs Standalones

`lila-hub` is the open-source connector board that lets anyone snap together any combination of TicketLabs standalone packages (`lila-asi`, `lila-capability-engine`, `lila-vtalks`, `lila-oia`, or their own `llama.cpp` / OpenAI backend) into one runtime.

## How It Works

Each TicketLabs package works 100% standalone. When you install two or more of them in the same Python environment and run `LilaHub.connect()`, `lila-hub` automatically discovers which modules are installed and wires their bridges together:

1. **`lila-capability-engine` + `lila-asi` (or `custom_llm_fn`):**
   - Automatically matches user queries to active `CapabilityBundle`s and injects the compact capability index + active tool schemas into the system prompt.
   - Feeds active tools directly into `AsiRuntime(..., active_tools=...)` so **E05 v2 Grammar-Aware Sparse `lm_head`** hardware-locks valid `<tool_call>` JSON.
2. **`lila-vtalks`:**
   - Exposes `ParakeetTranscriber` on `hub.components.voice_transcriber` when installed.
3. **`lila-oia`:**
   - Exposes `OwnerAuthenticationService` and `OwnerProfileStore` on `hub.components.owner_auth_service` when installed.

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
