"""
LilaHub Connector Board — auto-discovers and wires TicketLabs standalone modules together.
Works with 1 module, 2 modules, or all public modules installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import ssl
import sys
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence
import urllib.error
import urllib.request


@dataclass
class EdgeDeviceNode:
    """
    Represents a Microcontroller (ESP32 / Pico W / Arduino) or Edge SBC (Raspberry Pi)
    connected to LilaHub over HTTP/JSON (`http://192.168.x.x:port` or Tailscale IP).
    """

    node_id: str
    endpoint_url: str
    auth_token: Optional[str] = None
    capabilities: Sequence[str] = ("gpio_write", "gpio_read", "sensor_read", "command")
    timeout_seconds: float = 5.0

    def send_command(self, action: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = json.dumps({
            "node_id": self.node_id,
            "action": action,
            "params": dict(params or {}),
        }).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(payload)),
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        url = self.endpoint_url.rstrip("/") + "/cmd"
        req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))


class LilaRemoteClient:
    """
    Mode 2 (Remote Island Client): Connects any device (mobile, laptop, RPi, script)
    to a running LilaDaemon or LilaHub brain over HTTPS/mTLS or Tailscale/LAN.
    """

    def __init__(
        self,
        base_url: str = "https://127.0.0.1:8765",
        *,
        api_token: Optional[str] = None,
        ca_cert: Optional[str] = None,
        client_cert: Optional[str] = None,
        client_key: Optional[str] = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token or os.environ.get("LILA_API_TOKEN", "").strip()
        self.timeout_seconds = timeout_seconds
        self._ssl_ctx: Optional[ssl.SSLContext] = None
        if self.base_url.startswith("https://"):
            ca = ca_cert or os.environ.get("LILA_TLS_CA_CERT", "").strip()
            cc = client_cert or os.environ.get("LILA_TLS_CLIENT_CERT", "").strip()
            ck = client_key or os.environ.get("LILA_TLS_CLIENT_KEY", "").strip()
            if ca and os.path.isfile(ca):
                ctx = ssl.create_default_context(cafile=ca)
                if cc and ck and os.path.isfile(cc) and os.path.isfile(ck):
                    ctx.load_cert_chain(cc, ck)
                self._ssl_ctx = ctx

    def _headers(self, body_len: Optional[int] = None) -> Dict[str, str]:
        h: Dict[str, str] = {}
        if self.api_token:
            h["Authorization"] = f"Bearer {self.api_token}"
        if body_len is not None:
            h["Content-Type"] = "application/json; charset=utf-8"
            h["Content-Length"] = str(body_len)
        return h

    def is_reachable(self) -> bool:
        try:
            self.status()
            return True
        except Exception:
            return False

    def status(self) -> Dict[str, Any]:
        req = urllib.request.Request(f"{self.base_url}/status", headers=self._headers())
        with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=5.0) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    def chat(self, text: str, image_paths: Optional[List[str]] = None) -> str:
        body = json.dumps({"text": text, "image_paths": image_paths or []}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat",
            data=body,
            method="POST",
            headers=self._headers(len(body)),
        )
        with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=self.timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            return str(data.get("text") or data.get("reply") or data)


def _auto_extend_sys_path(extra_search_dirs: Optional[Sequence[str | Path]] = None) -> List[str]:
    """
    Search common sibling, dist/, home, and LILA_PACKAGES_DIR locations on the device
    so TicketLabs standalone folders are found even if the user only downloaded/cloned
    them without running `pip install`.
    """
    candidates: List[Path] = []
    if extra_search_dirs:
        for d in extra_search_dirs:
            if d:
                candidates.append(Path(d).expanduser())

    env_dirs = os.environ.get("LILA_PACKAGES_DIR", "").strip()
    if env_dirs:
        for part in env_dirs.split(os.pathsep):
            if part.strip():
                candidates.append(Path(part.strip()).expanduser())

    here = Path(__file__).resolve().parent
    # Relative to lila_hub/connector.py (e.g. inside dist/lila-hub/src/lila_hub)
    for root in [*list(here.parents)[2:4],
        Path.cwd(),
        Path.cwd() / "dist",
        Path.home() / "ticketlabs",
        Path.home() / ".local" / "share" / "lila" / "packages",
    ]:
        if not root.is_dir():
            continue
        candidates.extend([
            root,
            root / "lila-asi-linux-x86_64" / "python",
            root / "lila-asi" / "python",
            root / "lila-capability-engine" / "src",
            root / "lila-vtalks" / "src",
            root / "lila-oia" / "src",
        ])

    added: List[str] = []
    for c in candidates:
        try:
            resolved = str(c.resolve())
        except OSError:
            continue
        if os.path.isdir(resolved) and resolved not in sys.path:
            sys.path.insert(0, resolved)
            added.append(resolved)
    return added


def _auto_find_asi_model(explicit_path: Optional[str] = None) -> Optional[str]:
    """Locate an .asi cartridge from an explicit path, LILA_ASI_PATH, or standard device folders."""
    search_files: List[Path] = []
    if explicit_path:
        p = Path(explicit_path).expanduser()
        if p.is_file():
            return str(p.resolve())
        search_files.append(p)

    env_model = os.environ.get("LILA_ASI_PATH", "").strip()
    if env_model:
        p = Path(env_model).expanduser()
        if p.is_file():
            return str(p.resolve())

    search_dirs = [
        Path.cwd(),
        Path.cwd() / "models",
        Path.home() / ".lila" / "models",
        Path.home() / ".lila",
        Path.home() / "models",
        Path("/tmp"),
    ]
    if explicit_path:
        fname = Path(explicit_path).name
        for d in search_dirs:
            cand = d / fname
            if cand.is_file():
                return str(cand.resolve())

    for d in search_dirs:
        if not d.is_dir():
            continue
        for cand in sorted(d.glob("*.asi")):
            if cand.is_file():
                return str(cand.resolve())
    return explicit_path


@dataclass
class ConnectedComponents:
    asi_runtime: Optional[Any] = None
    capability_engine: Optional[Any] = None
    core_capability_adapter: Optional[Any] = None
    harness_capability_adapter: Optional[Any] = None
    voice_transcriber: Optional[Any] = None
    owner_auth_service: Optional[Any] = None
    discovered_modules: List[str] = field(default_factory=list)


class LilaHub:
    """
    Plug-and-Play Connector Board for TicketLabs Standalone Repositories.

    Example:
        from lila_hub import LilaHub

        hub = LilaHub(asi_model_path="model.asi")
        hub.connect()
        print(hub.status())
        print(hub.chat("Hello!"))
    """

    def __init__(
        self,
        *,
        asi_model_path: Optional[str] = None,
        n_ctx: int = 8192,
        n_threads: int = 2,
        asi_mode: str = "fast",
        custom_llm_fn: Optional[Callable[[List[Dict[str, str]]], str]] = None,
        remote_url: Optional[str] = None,
        remote_api_token: Optional[str] = None,
        prefer_remote: bool = False,
        session_id: str = "hub-default",
        search_dirs: Optional[Sequence[str | Path]] = None,
    ) -> None:
        self.asi_model_path = asi_model_path
        self.n_ctx = n_ctx
        self.n_threads = n_threads
        self.asi_mode = asi_mode
        self.custom_llm_fn = custom_llm_fn
        self.remote_url = remote_url
        self.remote_api_token = remote_api_token
        self.prefer_remote = prefer_remote
        self.session_id = session_id
        self.search_dirs = list(search_dirs) if search_dirs else []
        self.components = ConnectedComponents()
        self.remote_client: Optional[LilaRemoteClient] = (
            LilaRemoteClient(remote_url, api_token=remote_api_token)
            if remote_url
            else None
        )
        self.edge_nodes: Dict[str, EdgeDeviceNode] = {}
        self._tools: Dict[str, Any] = {}
        self._history: List[Dict[str, str]] = []

    def register_edge_node(self, node: EdgeDeviceNode) -> None:
        """
        Mode 3: Register a Microcontroller (ESP32 / Pico W / Arduino) or Edge SBC
        so Lila can control its pins/sensors from anywhere via E05 grammar tool calls.
        """
        self.edge_nodes[node.node_id] = node

        @dataclass
        class _EdgeArg:
            name: str
            type: str
            description: str
            required: bool = True
            choices: Optional[List[str]] = None

        @dataclass
        class _EdgeTool:
            name: str
            description: str
            args: List[_EdgeArg]
            execute: Callable[..., Any]

        safe_id = "".join(ch if ch.isalnum() else "_" for ch in node.node_id)
        tool = _EdgeTool(
            name=f"edge_{safe_id}_cmd",
            description=f"Control or read sensors/pins on remote microcontroller/edge device '{node.node_id}' ({node.endpoint_url})",
            args=[
                _EdgeArg("action", "string", "Action to perform on device", required=True, choices=list(node.capabilities)),
                _EdgeArg("pin", "int", "Optional GPIO pin number", required=False),
                _EdgeArg("value", "int", "Optional output value (0 or 1)", required=False),
                _EdgeArg("payload", "string", "Optional JSON/string payload", required=False),
            ],
            execute=lambda action, pin=0, value=0, payload="": node.send_command(
                action, {"pin": pin, "value": value, "payload": payload}
            ),
        )
        self.register_tool(tool)

    def register_tool(self, tool_obj: Any) -> None:
        """Register a tool object (with .name, .args, .execute) on the connector board."""
        self._tools[tool_obj.name] = tool_obj
        if self.components.capability_engine is not None:
            try:
                self.components.capability_engine.catalog.register_callable_tool(tool_obj)
            except Exception:
                pass

    def connect(self) -> ConnectedComponents:
        """Auto-discover and wire all installed or downloaded TicketLabs standalone packages."""
        self.close()
        _auto_extend_sys_path(self.search_dirs)
        resolved_model = _auto_find_asi_model(self.asi_model_path)
        if resolved_model:
            self.asi_model_path = resolved_model
        c = ConnectedComponents()

        # 1. Discover & wire lila-capability-engine
        try:
            try:
                from capability_engine import (
                    CapabilityEngine,
                    CoreCapabilityAdapter,
                    HarnessCapabilityAdapter,
                    register_builtin_capabilities,
                )
            except ImportError:
                from src.capability_engine import (
                    CapabilityEngine,
                    CoreCapabilityAdapter,
                    HarnessCapabilityAdapter,
                    register_builtin_capabilities,
                )
            eng = CapabilityEngine()
            register_builtin_capabilities(eng.skills, eng.catalog)
            c.capability_engine = eng
            c.core_capability_adapter = CoreCapabilityAdapter(eng)
            c.harness_capability_adapter = HarnessCapabilityAdapter(eng, strict_enforcement=True)
            c.discovered_modules.append("lila-capability-engine")
        except ImportError:
            pass

        # 2. Discover & wire lila-asi
        try:
            try:
                from lila_asi import AsiRuntime, apply_asi_mode_preset
            except ImportError:
                from src.core.asi_backend import AsiRuntime, apply_asi_mode_preset
            apply_asi_mode_preset(self.asi_mode)
            c.discovered_modules.append("lila-asi")
            if self.asi_model_path and Path(self.asi_model_path).is_file():
                c.asi_runtime = AsiRuntime(
                    self.asi_model_path,
                    n_ctx=self.n_ctx,
                    n_threads=self.n_threads,
                )
        except ImportError:
            pass

        # 3. Discover & wire lila-vtalks
        try:
            try:
                from vtalks.asr import ParakeetTranscriber
            except ImportError:
                from src.vtalks.asr import ParakeetTranscriber
            c.voice_transcriber = ParakeetTranscriber
            c.discovered_modules.append("lila-vtalks")
        except ImportError:
            pass

        # 4. Discover & wire lila-oia
        try:
            try:
                from oia import OwnerAuthenticationService, OwnerProfileStore
            except ImportError:
                from src.oia import OwnerAuthenticationService, OwnerProfileStore
            c.owner_auth_service = (OwnerAuthenticationService, OwnerProfileStore)
            c.discovered_modules.append("lila-oia")
        except ImportError:
            pass

        self.components = c
        return c

    def status(self) -> Dict[str, Any]:
        """Return a summary of all plugged-in TicketLabs modules, remote links, and microcontroller nodes."""
        c = self.components
        remote_ok = self.remote_client.is_reachable() if self.remote_client is not None else False
        return {
            "discovered_modules": list(c.discovered_modules),
            "asi_loaded": c.asi_runtime is not None,
            "asi_label": getattr(c.asi_runtime, "runtime_label", None),
            "remote_url": self.remote_url,
            "remote_reachable": remote_ok,
            "prefer_remote": self.prefer_remote,
            "capability_engine_active": c.capability_engine is not None,
            "capability_bundles_count": (
                len(c.capability_engine.catalog.all_bundles())
                if c.capability_engine is not None
                else 0
            ),
            "vtalks_available": c.voice_transcriber is not None,
            "oia_available": c.owner_auth_service is not None,
            "registered_tools_count": len(self._tools),
            "edge_nodes": list(self.edge_nodes.keys()),
        }

    def build_system_prompt(self, base_system: str, user_message: str = "") -> str:
        """If lila-capability-engine is plugged in, automatically assist-match and append compact capability index."""
        c = self.components
        if c.core_capability_adapter is None:
            return base_system
        block = c.core_capability_adapter.build_turn_capability_context(
            session_id=self.session_id,
            user_query=user_message,
        )
        if not block:
            return base_system
        return f"{base_system.rstrip()}\n\n{block}"

    def chat(
        self,
        user_message: str,
        *,
        system_prompt: str = "You are a helpful assistant.",
        max_tokens: int = 512,
        temperature: float = 0.2,
    ) -> str:
        """
        Run a full turn with automatic Mode 2 (Remote Hub) <-> Mode 1 (On-Device Local ASI) failover.
        """
        # Mode 2: Try Remote Hub first if prefer_remote=True (or if no local model is loaded)
        if self.remote_client is not None and (self.prefer_remote or self.components.asi_runtime is None):
            try:
                reply = self.remote_client.chat(user_message)
                self._history.append({"role": "user", "content": user_message})
                self._history.append({"role": "assistant", "content": reply})
                return reply
            except Exception:
                if self.components.asi_runtime is None and self.custom_llm_fn is None:
                    raise

        full_system = self.build_system_prompt(system_prompt, user_message)
        messages = [{"role": "system", "content": full_system}, *self._history, {"role": "user", "content": user_message}]

        if self.components.asi_runtime is not None:
            active_tools = list(self._tools.values()) if self._tools else None
            resp = self.components.asi_runtime.create_chat_completion(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=False,
                active_tools=active_tools,
            )
            reply = resp["choices"][0]["message"]["content"]
        elif self.custom_llm_fn is not None:
            reply = self.custom_llm_fn(messages)
        elif self.remote_client is not None:
            reply = self.remote_client.chat(user_message)
        else:
            raise RuntimeError("No inference backend connected (provide asi_model_path, remote_url, or custom_llm_fn).")

        self._history.append({"role": "user", "content": user_message})
        self._history.append({"role": "assistant", "content": reply})
        return reply

    def close(self) -> None:
        if self.components.asi_runtime is not None:
            self.components.asi_runtime.close()
            self.components.asi_runtime = None
