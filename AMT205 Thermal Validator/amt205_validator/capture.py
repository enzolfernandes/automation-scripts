from __future__ import annotations

import csv
import os
import queue
import re
import shutil
import socket
import subprocess
import threading
from pathlib import Path
from typing import Callable, Iterable

from .protocol import DEVICE_IP, DEVICE_PORT, PacketRecord


TSHARK_CANDIDATES = (
    Path(r"C:\Program Files\Wireshark\tshark.exe"),
    Path(r"C:\Program Files (x86)\Wireshark\tshark.exe"),
)
DUMPCAP_CANDIDATES = (
    Path(r"C:\Program Files\Wireshark\dumpcap.exe"),
    Path(r"C:\Program Files (x86)\Wireshark\dumpcap.exe"),
)
AMT_CAPTURE_FILTER = "tcp port 1001 and host 192.168.1.41"


class LiveStatusWatchdog:
    """Detect a silent AMT205 stream without confusing repeated polls with new events."""

    def __init__(self, timeout_s: float = 5.0) -> None:
        self.timeout_s = timeout_s
        self.active = False
        self.started_at: float | None = None
        self.last_seen_at: float | None = None
        self.disconnected = False

    def start(self, now: float) -> None:
        self.active = True
        self.started_at = now
        self.last_seen_at = None
        self.disconnected = False

    def stop(self) -> None:
        self.active = False
        self.started_at = None
        self.last_seen_at = None
        self.disconnected = False

    def observe(self, now: float) -> bool:
        restored = self.active and self.disconnected
        self.last_seen_at = now
        self.disconnected = False
        return restored

    def poll(self, now: float) -> bool:
        reference = self.last_seen_at if self.last_seen_at is not None else self.started_at
        if not self.active or self.disconnected or reference is None:
            return False
        if now - reference < self.timeout_s:
            return False
        self.disconnected = True
        return True


def find_tshark() -> Path:
    found = shutil.which("tshark")
    if found:
        return Path(found)
    for candidate in TSHARK_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("TShark was not found. Install Wireshark with the TShark component.")


def find_dumpcap() -> Path:
    found = shutil.which("dumpcap")
    if found:
        return Path(found)
    for candidate in DUMPCAP_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Dumpcap was not found. Repair the Wireshark installation.")


def list_interfaces() -> list[tuple[str, str]]:
    result = subprocess.run(
        [str(find_tshark()), "-D"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True
    )
    interfaces: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        number, separator, description = line.partition(". ")
        if separator and number.strip().isdigit():
            interfaces.append((number.strip(), description.strip()))
    return interfaces


def interface_alias(description: str) -> str:
    """Return the readable adapter name from a TShark interface description."""
    match = re.search(r"\(([^()]*)\)\s*$", description)
    return match.group(1).strip() if match else description.strip()


def routed_interface_alias(target_ip: str = DEVICE_IP) -> str | None:
    """Resolve the Windows adapter used to reach the test set without sending data."""
    if os.name != "nt":
        return None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((target_ip, DEVICE_PORT))
            local_ip = probe.getsockname()[0]
        flags = subprocess.CREATE_NO_WINDOW
        command = (
            f"Get-NetIPAddress -AddressFamily IPv4 -IPAddress '{local_ip}' -ErrorAction Stop | "
            "Select-Object -First 1 -ExpandProperty InterfaceAlias"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=flags,
        )
        alias = result.stdout.strip()
        return alias or None
    except (OSError, subprocess.SubprocessError):
        return None


def preferred_interface_index(interfaces: list[tuple[str, str]], route_alias: str | None) -> int | None:
    if route_alias:
        wanted = route_alias.casefold()
        for index, (_, description) in enumerate(interfaces):
            if interface_alias(description).casefold() == wanted:
                return index
    for marker in ("ethernet", "wi-fi", "wifi"):
        for index, (_, description) in enumerate(interfaces):
            if marker in interface_alias(description).casefold():
                return index
    return None


FIELDS = (
    "frame.number",
    "frame.time_epoch",
    "frame.time_relative",
    "ip.src",
    "tcp.srcport",
    "ip.dst",
    "tcp.dstport",
    "tcp.payload",
)


def _field_arguments() -> list[str]:
    arguments = ["-T", "fields", "-E", "separator=/t", "-E", "occurrence=f"]
    for field in FIELDS:
        arguments.extend(("-e", field))
    return arguments


def parse_field_line(line: str) -> PacketRecord | None:
    try:
        columns = next(csv.reader([line.rstrip("\r\n")], delimiter="\t"))
        if len(columns) != len(FIELDS) or not columns[7]:
            return None
        return PacketRecord(
            frame=int(columns[0]),
            epoch=float(columns[1]),
            relative=float(columns[2]),
            src=columns[3],
            src_port=int(columns[4] or 0),
            dst=columns[5],
            dst_port=int(columns[6] or 0),
            payload=bytes.fromhex(columns[7].replace(":", "")),
        )
    except (ValueError, IndexError, csv.Error):
        return None


def iter_pcap(path: Path) -> Iterable[PacketRecord]:
    command = [
        str(find_tshark()), "-n", "-r", str(path), "-Y", "tcp.payload && !tcp.analysis.retransmission",
        *_field_arguments(),
    ]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace"
    )
    assert process.stdout is not None
    for line in process.stdout:
        record = parse_field_line(line)
        if record is not None:
            yield record
    stderr = process.stderr.read() if process.stderr else ""
    code = process.wait()
    if code:
        raise RuntimeError(stderr.strip() or f"TShark exited with code {code}.")


class LiveCapture:
    def __init__(
        self,
        interface: str,
        on_packet: Callable[[PacketRecord], None],
        on_error: Callable[[str], None],
    ) -> None:
        self.interface = interface
        self.on_packet = on_packet
        self.on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tshark: subprocess.Popen[str] | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="amt205-live-capture", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self._tshark = subprocess.Popen(
                [str(find_tshark()), "-n", "-l", "-i", self.interface, "-f", AMT_CAPTURE_FILTER,
                 "-Y", "tcp.payload && !tcp.analysis.retransmission", *_field_arguments()],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                creationflags=flags,
            )
            assert self._tshark.stdout is not None
            while not self._stop.is_set():
                line = self._tshark.stdout.readline()
                if not line:
                    if self._tshark.poll() is not None:
                        stderr = self._tshark.stderr.read() if self._tshark.stderr else ""
                        if not self._stop.is_set():
                            raise RuntimeError(stderr.strip() or "Live monitoring stopped unexpectedly.")
                    continue
                packet = parse_field_line(line)
                if packet is not None:
                    self.on_packet(packet)
        except Exception as exc:  # surfaced to the UI thread
            self.on_error(str(exc))
        finally:
            self._terminate_processes()

    def _terminate_processes(self) -> None:
        for process in (self._tshark,):
            if process and process.poll() is None:
                process.terminate()
        for process in (self._tshark,):
            if process:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()

    def stop(self) -> None:
        self._stop.set()
        self._terminate_processes()
        if self._thread:
            self._thread.join(timeout=5)


class RawRecorder:
    """Record raw AMT205 traffic while LiveCapture continues monitoring independently."""

    def __init__(self, interface: str, raw_pcap: Path) -> None:
        self.interface = interface
        self.raw_pcap = raw_pcap
        self._process: subprocess.Popen[str] | None = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        if self.running:
            return
        self.raw_pcap.parent.mkdir(parents=True, exist_ok=True)
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._process = subprocess.Popen(
            [str(find_dumpcap()), "-q", "-i", self.interface, "-f", AMT_CAPTURE_FILTER, "-w", str(self.raw_pcap)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=flags,
        )

    def stop(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        self._process = None
