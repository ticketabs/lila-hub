"""
lila_hub — The Open-Source Connector Board & Universal Device Mesh for TicketLabs Standalones

Supports 3 Operating Modes out of the box:
  1. Mode 1 (On-Device Local): Snaps together installed TicketLabs packages (`lila-asi`,
     `lila-capability-engine`, `lila-vtalks`, `lila-oia`, `lila-sensa`), zero-pip drop-in
     folders under `plugins/` with a `lila_plugin.json` manifest, and Python entry points
     under `lila.capability_providers`.
  2. Mode 2 (Remote Island / Universal Client): Connects any phone, laptop, or script
     remotely to a running LilaDaemon or LilaHub server anywhere (LAN, Wi-Fi, Tailscale/VPN)
     with automatic failover to Mode 1 if offline.
  3. Mode 3 (Microcontroller & Edge Node Bridge): Registers ESP32 / Arduino / Raspberry Pi
     edge devices (over HTTP or Serial) as live callable tools on the hub so Lila can read
     sensors and control pins on any connected device from anywhere.
"""

from .connector import (
    ConnectedComponents,
    EdgeDeviceNode,
    LilaHub,
    LilaRemoteClient,
)

__all__ = [
    "ConnectedComponents",
    "EdgeDeviceNode",
    "LilaHub",
    "LilaRemoteClient",
]
