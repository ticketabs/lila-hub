"""
LilaHub Connector Board — auto-discovers and wires TicketLabs standalone modules,
zero-pip drop-in `plugins/` manifests, and optional CapabilityProviders together.
Works with 1 module, 2 modules, or all public modules installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import ssl
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence
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
    roots = [
        *list(here.parents)[1:5],
        Path.cwd(),
        Path.cwd() / "dist",
        Path.home() / "ticketlabs",
        Path.home() / ".local" / "share" / "lila" / "packages",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        candidates.extend([
            root,
            root / "lila-asi-linux-x86_64" / "python",
            root / "lila-asi" / "python",
            root / "lila-capability-engine" / "src",
            root / "lila-vtalks" / "src",
            root / "lila-oia" / "src",
            root / "lila-sensa" / "src",
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


def _discover_plugin_dirs(extra_plugin_dirs: Optional[Sequence[str | Path]] = None) -> List[Path]:
    """Locate existing drop-in `plugins/` directories for zero-pip manifest scanning."""
    raw_candidates: List[Path] = []
    if extra_plugin_dirs:
        for d in extra_plugin_dirs:
            if d:
                raw_candidates.append(Path(d).expanduser())

    env_plugins = os.environ.get("LILA_PLUGINS_DIR", "").strip()
    if env_plugins:
        for part in env_plugins.split(os.pathsep):
            if part.strip():
                raw_candidates.append(Path(part.strip()).expanduser())

    here = Path(__file__).resolve().parent
    for parent in list(here.parents)[1:6]:
        raw_candidates.append(parent / "plugins")

    raw_candidates.extend([
        Path.cwd() / "plugins",
        Path.cwd().parent / "plugins",
        Path.home() / ".local" / "share" / "lila" / "plugins",
    ])

    unique: List[Path] = []
    seen: set[str] = set()
    for cand in raw_candidates:
        try:
            resolved = cand.resolve()
        except OSError:
            continue
        key = str(resolved)
        if resolved.is_dir() and key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def _load_dotted_symbol(spec: str) -> Any:
    """Import and return attribute denoted by `'module.path:attr'`."""
    clean = str(spec or "").strip()
    if ":" not in clean:
        raise ValueError(f"Expected 'module:attribute' spec, got {spec!r}")
    mod_name, attr_path = clean.split(":", 1)
    obj: Any = importlib.import_module(mod_name.strip())
    for part in attr_path.strip().split("."):
        obj = getattr(obj, part)
    return obj


def _auto_find_asi_model(explicit_path: Optional[str] = None) -> Optional[str]:
    """Locate an .asi cartridge from an explicit path, LILA_ASI_PATH, or standard device folders."""
    if explicit_path:
        p = Path(explicit_path).expanduser()
        if p.is_file():
            return str(p.resolve())

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


def _is_env_disabled(name: str, default_enabled: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return not default_enabled
    return raw.strip().lower() in {"0", "false", "off", "no"}


def _invoke_provider_factory(
    factory: Any,
    *,
    capability_engine: Any,
    tool_registry: Optional[Any],
    state_root: Optional[Path],
) -> Any:
    if not callable(factory):
        return factory
    try:
        return factory(
            capability_engine=capability_engine,
            tool_registry=tool_registry,
            state_root=state_root,
        )
    except TypeError:
        return factory()


@dataclass
class ConnectedComponents:
    asi_runtime: Optional[Any] = None
    capability_engine: Optional[Any] = None
    core_capability_adapter: Optional[Any] = None
    harness_capability_adapter: Optional[Any] = None
    voice_transcriber: Optional[Any] = None
    owner_auth_service: Optional[Any] = None
    sensa_cls: Optional[Any] = None
    discovered_modules: List[str] = field(default_factory=list)
    attached_providers: Dict[str, Any] = field(default_factory=dict)
    provider_registrations: Dict[str, Any] = field(default_factory=dict)
    provider_errors: Dict[str, str] = field(default_factory=dict)


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
        load_asi: bool = True,
        custom_llm_fn: Optional[Callable[[List[Dict[str, str]]], str]] = None,
        remote_url: Optional[str] = None,
        remote_api_token: Optional[str] = None,
        prefer_remote: bool = False,
        session_id: str = "hub-default",
        search_dirs: Optional[Sequence[str | Path]] = None,
        plugin_dirs: Optional[Sequence[str | Path]] = None,
    ) -> None:
        self.asi_model_path = asi_model_path
        self.n_ctx = n_ctx
        self.n_threads = n_threads
        self.asi_mode = asi_mode
        self.load_asi = bool(load_asi)
        self.custom_llm_fn = custom_llm_fn
        self.remote_url = remote_url
        self.remote_api_token = remote_api_token
        self.prefer_remote = prefer_remote
        self.session_id = session_id
        self.search_dirs = list(search_dirs) if search_dirs else []
        self.plugin_dirs = list(plugin_dirs) if plugin_dirs else []
        self.components = ConnectedComponents()
        self.remote_client: Optional[LilaRemoteClient] = (
            LilaRemoteClient(remote_url, api_token=remote_api_token)
            if remote_url
            else None
        )
        self.edge_nodes: Dict[str, EdgeDeviceNode] = {}
        self._tools: Dict[str, Any] = {}
        self._history: List[Dict[str, str]] = []
        self._provider_factories: Dict[str, Callable[..., Any]] = {}
        self._provider_detach_hooks: Dict[str, Callable[..., Any]] = {}

    def register_provider_factory(self, name: str, factory: Callable[..., Any]) -> None:
        """Register an additional custom CapabilityProvider factory for auto-attachment."""
        clean = str(name or "").strip()
        if not clean:
            raise ValueError("Provider factory name must not be empty.")
        self._provider_factories[clean] = factory

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
        eng = self.components.capability_engine
        if eng is not None and hasattr(eng, "catalog"):
            try:
                try:
                    from capability_engine.models import ToolDescriptor
                except ImportError:
                    from src.capability_engine.models import ToolDescriptor
                schema_fn = getattr(tool_obj, "as_prompt_description", None)
                schema_text = (
                    schema_fn()
                    if callable(schema_fn)
                    else f"{tool_obj.name}(): {getattr(tool_obj, 'description', '')}"
                )
                desc = ToolDescriptor(
                    name=str(tool_obj.name),
                    capability_id=f"tool.{tool_obj.name}",
                    category=str(getattr(tool_obj, "category", "hub") or "hub"),
                    summary=str(getattr(tool_obj, "description", "")),
                    risk=str(getattr(tool_obj, "risk", "moderate") or "moderate"),
                    permission_hint=str(getattr(tool_obj, "permission", "auto") or "auto"),
                    estimated_schema_tokens=0,
                    schema_text=schema_text,
                )
                eng.catalog.register_tool_descriptor(desc, actor="lila_hub")
            except Exception:
                pass

    def connect(self, *, capability_engine: Optional[Any] = None) -> ConnectedComponents:
        """Auto-discover and wire all installed or downloaded TicketLabs standalone packages."""
        self.close()
        _auto_extend_sys_path(self.search_dirs)
        resolved_model = _auto_find_asi_model(self.asi_model_path) if self.load_asi else self.asi_model_path
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
            if capability_engine is not None:
                eng = capability_engine
            else:
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
            if self.load_asi:
                apply_asi_mode_preset(self.asi_mode)
            c.discovered_modules.append("lila-asi")
            if self.load_asi and self.asi_model_path and Path(self.asi_model_path).is_file():
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
                from oia.service import OwnerAuthenticationService, OwnerProfileStore
            except ImportError:
                from src.oia.service import OwnerAuthenticationService, OwnerProfileStore
            c.owner_auth_service = (OwnerAuthenticationService, OwnerProfileStore)
            c.discovered_modules.append("lila-oia")
        except ImportError:
            pass

        # 5. Discover lila-sensa (Media Intelligence)
        try:
            try:
                from sensa import Sensa
            except ImportError:
                from src.sensa import Sensa
            c.sensa_cls = Sensa
            c.discovered_modules.append("lila-sensa")
        except ImportError:
            pass

        self.components = c
        return c

    def _register_if_provider(
        self,
        eng: Any,
        name: str,
        candidate: Any,
        *,
        tool_registry: Optional[Any],
        allow_auto_activate: bool,
        actor: str,
    ) -> None:
        if candidate is None:
            return
        if not (hasattr(candidate, "describe_skills") and hasattr(candidate, "describe_bundles")):
            return
        pid = str(getattr(candidate, "provider_id", "") or name).strip() or name
        existing = {p.provider_id for p in eng.providers.list_providers()}
        if pid not in existing:
            reg = eng.providers.register_provider(
                candidate,
                tool_registry=tool_registry,
                allow_auto_activate=allow_auto_activate,
                actor=actor,
            )
            self.components.provider_registrations[pid] = reg
        self.components.attached_providers[pid] = candidate

    def _attach_drop_in_plugins(
        self,
        eng: Any,
        *,
        tool_registry: Optional[Any],
        base_state: Path,
        allow_auto_activate: bool,
        actor: str,
    ) -> None:
        """Scan drop-in `plugins/<name>/lila_plugin.json` folders without requiring pip install."""
        c = self.components
        for pdir in _discover_plugin_dirs(self.plugin_dirs):
            try:
                children = sorted(pdir.iterdir(), key=lambda p: p.name)
            except OSError:
                continue
            for folder in children:
                if not folder.is_dir():
                    continue
                manifest_path = folder / "lila_plugin.json"
                if not manifest_path.is_file():
                    continue
                pid = folder.name
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    pid = str(manifest.get("provider_id") or folder.name).strip() or folder.name
                    if pid in c.attached_providers:
                        continue
                    enabled_env = str(manifest.get("enabled_env") or "").strip()
                    if enabled_env and _is_env_disabled(enabled_env, default_enabled=True):
                        continue
                    if manifest.get("requires_tool_registry") and tool_registry is None:
                        continue

                    py_paths = manifest.get("python_paths") or [".", "src"]
                    for rel in reversed(list(py_paths)):
                        cand = (folder / str(rel)).resolve()
                        if cand.is_dir():
                            s = str(cand)
                            if s not in sys.path:
                                sys.path.insert(0, s)

                    factory = _load_dotted_symbol(str(manifest["factory"]))
                    ws_subpath = str(manifest.get("workspace_subpath") or "").strip()
                    if ws_subpath:
                        inst = factory(eng, tool_registry, workspace_path=base_state / ws_subpath)
                    else:
                        inst = _invoke_provider_factory(
                            factory,
                            capability_engine=eng,
                            tool_registry=tool_registry,
                            state_root=base_state,
                        )
                    if inst is None:
                        continue

                    self._register_if_provider(
                        eng,
                        pid,
                        inst,
                        tool_registry=tool_registry,
                        allow_auto_activate=allow_auto_activate,
                        actor=actor,
                    )
                    c.attached_providers[pid] = inst
                    detach_spec = str(manifest.get("detach") or "").strip()
                    if detach_spec:
                        self._provider_detach_hooks[pid] = _load_dotted_symbol(detach_spec)
                    mod_label = f"plugin:{pid}"
                    if mod_label not in c.discovered_modules:
                        c.discovered_modules.append(mod_label)
                except Exception as exc:
                    c.provider_errors[pid] = f"{type(exc).__name__}: {exc}"

    def attach_capability_providers(
        self,
        capability_engine: Optional[Any] = None,
        *,
        tool_registry: Optional[Any] = None,
        state_root: Optional[Path] = None,
        transcriber: Optional[Any] = None,
        allow_auto_activate: bool = False,
        actor: str = "owner",
    ) -> Dict[str, Any]:
        """
        Auto-discover and attach standalone CapabilityProviders (`sensa`, zero-pip drop-in
        `plugins/*/lila_plugin.json` packages, `lila.capability_providers` entry points, and
        registered custom factories) with per-provider error isolation.
        """
        eng = capability_engine or self.components.capability_engine
        if eng is None:
            return {}
        if self.components.capability_engine is None:
            self.components.capability_engine = eng

        c = self.components
        base_state = Path(state_root) if state_root is not None else (Path.home() / ".local" / "share" / "lila")

        # 1. Optional Sensa Media Intelligence provider
        if "sensa" not in c.attached_providers:
            if not _is_env_disabled("LILA_SENSA_ENABLED", default_enabled=True):
                try:
                    from src.harness.media_adapter import attach_sensa_provider
                    sensa_provider, reg = attach_sensa_provider(
                        eng.providers,
                        tool_registry=tool_registry,
                        transcriber=transcriber,
                        allow_auto_activate=allow_auto_activate,
                        actor=actor,
                    )
                    c.attached_providers["sensa"] = sensa_provider
                    c.provider_registrations["sensa"] = reg
                    if "lila-sensa" not in c.discovered_modules:
                        c.discovered_modules.append("lila-sensa")
                except Exception as exc:
                    c.provider_errors["sensa"] = f"{type(exc).__name__}: {exc}"

        # 2. Zero-pip drop-in `plugins/<name>/lila_plugin.json` folders
        self._attach_drop_in_plugins(
            eng,
            tool_registry=tool_registry,
            base_state=base_state,
            allow_auto_activate=allow_auto_activate,
            actor=actor,
        )

        # 3. Installed Python entry points (`lila.capability_providers`)
        try:
            eps = importlib.metadata.entry_points(group="lila.capability_providers")
        except Exception:
            eps = ()
        for ep in eps:
            ep_name = str(getattr(ep, "name", "") or "entry_point").strip()
            if ep_name in c.attached_providers:
                continue
            try:
                loaded = ep.load()
                inst = _invoke_provider_factory(
                    loaded,
                    capability_engine=eng,
                    tool_registry=tool_registry,
                    state_root=base_state,
                )
                self._register_if_provider(
                    eng,
                    ep_name,
                    inst,
                    tool_registry=tool_registry,
                    allow_auto_activate=allow_auto_activate,
                    actor=actor,
                )
            except Exception as exc:
                c.provider_errors[ep_name] = f"{type(exc).__name__}: {exc}"

        # 4. Caller-registered custom provider factories
        for name, factory in self._provider_factories.items():
            if name in c.attached_providers:
                continue
            try:
                inst = _invoke_provider_factory(
                    factory,
                    capability_engine=eng,
                    tool_registry=tool_registry,
                    state_root=base_state,
                )
                self._register_if_provider(
                    eng,
                    name,
                    inst,
                    tool_registry=tool_registry,
                    allow_auto_activate=allow_auto_activate,
                    actor=actor,
                )
            except Exception as exc:
                c.provider_errors[name] = f"{type(exc).__name__}: {exc}"

        return dict(c.attached_providers)

    def get_provider(self, provider_id: str) -> Optional[Any]:
        """Return an attached CapabilityProvider by ID, if present."""
        return self.components.attached_providers.get(provider_id)

    def detach_all_providers(
        self,
        capability_engine: Optional[Any] = None,
        *,
        session_ids: Sequence[str] = ("default",),
        actor: str = "owner",
    ) -> List[str]:
        """Detach and close every attached CapabilityProvider cleanly."""
        eng = capability_engine or self.components.capability_engine
        c = self.components
        detached: List[str] = []

        for pid, provider in list(c.attached_providers.items()):
            try:
                hook = self._provider_detach_hooks.pop(pid, None)
                if eng is not None:
                    if callable(hook):
                        hook(eng)
                    else:
                        eng.providers.unregister_provider(pid, session_ids=session_ids, actor=actor)
            except Exception:
                pass
            finally:
                close_fn = getattr(provider, "close", None)
                if callable(close_fn):
                    try:
                        close_fn()
                    except Exception:
                        pass
            detached.append(pid)

        c.attached_providers.clear()
        c.provider_registrations.clear()
        self._provider_detach_hooks.clear()
        return detached

    def status(self) -> Dict[str, Any]:
        """Return a summary of all plugged-in TicketLabs modules, providers, remote links, and edge nodes."""
        c = self.components
        remote_ok = self.remote_client.is_reachable() if self.remote_client is not None else False
        return {
            "discovered_modules": list(c.discovered_modules),
            "attached_providers": sorted(c.attached_providers.keys()),
            "provider_errors": dict(c.provider_errors),
            "plugin_dirs": [str(d) for d in _discover_plugin_dirs(self.plugin_dirs)],
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
            "sensa_available": c.sensa_cls is not None,
            "registered_tools_count": len(self._tools),
            "edge_nodes": list(self.edge_nodes.keys()),
        }

    def build_system_prompt(self, base_system: str, user_message: str = "") -> str:
        """If lila-capability-engine is plugged in, automatically assist-match and append capability context."""
        c = self.components
        if c.core_capability_adapter is None:
            return base_system
        messages, _ = c.core_capability_adapter.build_turn_messages(
            session_id=self.session_id,
            static_prefix=base_system,
            user_text=user_message,
            canonical_history=(),
        )
        return str(messages[0].get("content", base_system))

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
        if self.components.attached_providers:
            self.detach_all_providers(self.components.capability_engine)
        if self.components.asi_runtime is not None:
            self.components.asi_runtime.close()
            self.components.asi_runtime = None
