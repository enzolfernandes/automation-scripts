#!/usr/bin/env python3
"""Active IEEE 1588 Pdelay probe for a GE Reason RT430.

The tool sends VLAN-tagged Pdelay_Req frames and records any Pdelay_Resp /
Pdelay_Resp_Follow_Up frames.  It is intended to prove responder behaviour,
not to measure path-delay accuracy (which requires hardware timestamps).
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import struct
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from scapy.all import AsyncSniffer, Dot1Q, Ether, Raw, conf, get_if_hwaddr, sendp, wrpcap


__version__ = "0.1.0"

PTP_ETHERTYPE = 0x88F7
VLAN_ETHERTYPE = 0x8100
PDELAY_REQ = 0x2
PDELAY_RESP = 0x3
PDELAY_RESP_FOLLOW_UP = 0xA

DESTINATIONS = {
    "peer": "01:80:c2:00:00:0e",
    "general": "01:1b:19:00:00:00",
}

MESSAGE_NAMES = {
    0x0: "Sync",
    0x1: "Delay_Req",
    0x2: "Pdelay_Req",
    0x3: "Pdelay_Resp",
    0x8: "Follow_Up",
    0x9: "Delay_Resp",
    0xA: "Pdelay_Resp_Follow_Up",
    0xB: "Announce",
}


def normalize_mac(value: str) -> str:
    cleaned = value.replace("-", ":").lower()
    parts = cleaned.split(":")
    if len(parts) != 6 or any(len(part) != 2 for part in parts):
        raise argparse.ArgumentTypeError(f"MAC inválido: {value}")
    try:
        bytes(int(part, 16) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"MAC inválido: {value}") from exc
    return cleaned


def mac_to_clock_identity(mac: str) -> bytes:
    octets = bytes(int(part, 16) for part in normalize_mac(mac).split(":"))
    return octets[:3] + b"\xff\xfe" + octets[3:]


def u48(value: int) -> bytes:
    if not 0 <= value < (1 << 48):
        raise ValueError("seconds value does not fit in 48 bits")
    return value.to_bytes(6, "big")


def timestamp_fields(mode: str, utc_offset: int) -> tuple[int, int]:
    if mode == "zero":
        return 0, 0
    now_ns = time.time_ns()
    seconds, nanoseconds = divmod(now_ns, 1_000_000_000)
    if mode == "tai":
        seconds += utc_offset
    return seconds, nanoseconds


def build_pdelay_payload(
    *,
    source_mac: str,
    domain: int,
    sequence_id: int,
    source_port: int = 1,
    timestamp_mode: str = "tai",
    utc_offset: int = 37,
    two_step_flag: bool = False,
    unicast_flag: bool = False,
    log_interval: int = 127,
) -> bytes:
    seconds, nanoseconds = timestamp_fields(timestamp_mode, utc_offset)
    flags = (0x0200 if two_step_flag else 0) | (0x0400 if unicast_flag else 0)
    header = struct.pack(
        "!BBHBBHqI8sHHBb",
        PDELAY_REQ,
        0x02,
        54,
        domain,
        0,
        flags,
        0,
        0,
        mac_to_clock_identity(source_mac),
        source_port,
        sequence_id,
        5,
        log_interval,
    )
    body = u48(seconds) + struct.pack("!I", nanoseconds) + (b"\x00" * 10)
    payload = header + body
    if len(payload) != 54:
        raise AssertionError(f"PTP payload length is {len(payload)}, expected 54")
    return payload


def build_pdelay_frame(
    *,
    source_mac: str,
    destination_mac: str,
    vlan: int,
    pcp: int,
    domain: int,
    sequence_id: int,
    timestamp_mode: str,
    utc_offset: int,
    two_step_flag: bool = False,
    unicast_flag: bool = False,
    log_interval: int = 127,
) -> Ether:
    payload = build_pdelay_payload(
        source_mac=source_mac,
        domain=domain,
        sequence_id=sequence_id,
        timestamp_mode=timestamp_mode,
        utc_offset=utc_offset,
        two_step_flag=two_step_flag,
        unicast_flag=unicast_flag,
        log_interval=log_interval,
    )
    return (
        Ether(src=source_mac, dst=destination_mac, type=VLAN_ETHERTYPE)
        / Dot1Q(prio=pcp, dei=0, vlan=vlan, type=PTP_ETHERTYPE)
        / Raw(payload)
    )


@dataclass
class ParsedPtp:
    captured_at: str
    source_mac: str
    destination_mac: str
    vlan: int | None
    pcp: int | None
    message_type: int
    message_name: str
    domain: int
    flags: int
    clock_identity: str
    source_port: int
    sequence_id: int
    requesting_clock_identity: str | None = None
    requesting_port: int | None = None


def parse_ptp_frame(packet) -> ParsedPtp | None:
    raw = bytes(packet)
    if len(raw) < 14:
        return None
    ether_type = int.from_bytes(raw[12:14], "big")
    offset = 14
    vlan = None
    pcp = None
    if ether_type == VLAN_ETHERTYPE:
        if len(raw) < 18:
            return None
        tci = int.from_bytes(raw[14:16], "big")
        pcp = (tci >> 13) & 0x7
        vlan = tci & 0x0FFF
        ether_type = int.from_bytes(raw[16:18], "big")
        offset = 18
    if ether_type != PTP_ETHERTYPE or len(raw) < offset + 34:
        return None

    ptp = raw[offset:]
    message_type = ptp[0] & 0x0F
    domain = ptp[4]
    flags = int.from_bytes(ptp[6:8], "big")
    clock_identity = ptp[20:28].hex(":")
    source_port = int.from_bytes(ptp[28:30], "big")
    sequence_id = int.from_bytes(ptp[30:32], "big")
    requesting_clock_identity = None
    requesting_port = None
    if message_type in (PDELAY_RESP, PDELAY_RESP_FOLLOW_UP) and len(ptp) >= 54:
        requesting_clock_identity = ptp[44:52].hex(":")
        requesting_port = int.from_bytes(ptp[52:54], "big")

    captured_time = getattr(packet, "time", time.time())
    captured_at = datetime.fromtimestamp(float(captured_time), tz=timezone.utc).isoformat()
    return ParsedPtp(
        captured_at=captured_at,
        source_mac=raw[6:12].hex(":"),
        destination_mac=raw[0:6].hex(":"),
        vlan=vlan,
        pcp=pcp,
        message_type=message_type,
        message_name=MESSAGE_NAMES.get(message_type, f"0x{message_type:x}"),
        domain=domain,
        flags=flags,
        clock_identity=clock_identity,
        source_port=source_port,
        sequence_id=sequence_id,
        requesting_clock_identity=requesting_clock_identity,
        requesting_port=requesting_port,
    )


def list_interfaces() -> None:
    print("Interfaces visíveis pelo Npcap/Scapy:\n")
    conf.ifaces.show()


def is_admin() -> bool:
    if os.name != "nt":
        return os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def resolve_destinations(mode: str, rt_mac: str) -> list[tuple[str, str]]:
    if mode == "all":
        return [
            ("peer", DESTINATIONS["peer"]),
            ("general", DESTINATIONS["general"]),
            ("unicast", rt_mac),
        ]
    if mode == "unicast":
        return [(mode, rt_mac)]
    return [(mode, DESTINATIONS[mode])]


def output_base(output_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"rt430_probe_{stamp}"


def summarize(
    *,
    args: argparse.Namespace,
    source_mac: str,
    sent_records: list[dict],
    received_packets: Iterable,
) -> dict:
    source_clock = mac_to_clock_identity(source_mac).hex(":")
    parsed = [item for packet in received_packets if (item := parse_ptp_frame(packet))]
    responses = [item for item in parsed if item.message_type in (PDELAY_RESP, PDELAY_RESP_FOLLOW_UP)]
    matching = [
        item
        for item in responses
        if item.domain == args.domain
        and item.requesting_clock_identity == source_clock
        and item.requesting_port == args.source_port
    ]
    response_sequences = sorted({item.sequence_id for item in matching if item.message_type == PDELAY_RESP})
    follow_up_sequences = sorted(
        {item.sequence_id for item in matching if item.message_type == PDELAY_RESP_FOLLOW_UP}
    )
    requested_sequences = sorted({record["sequence_id"] for record in sent_records})
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": args.dry_run,
        "interface": args.iface,
        "source_mac": source_mac,
        "source_clock_identity": source_clock,
        "rt_mac": args.rt_mac,
        "domain": args.domain,
        "vlan": args.vlan,
        "pcp": args.pcp,
        "sent": sent_records,
        "received_ptp": [asdict(item) for item in parsed],
        "matching_pdelay_resp_sequences": response_sequences,
        "matching_pdelay_resp_follow_up_sequences": follow_up_sequences,
        "missing_pdelay_resp_sequences": sorted(set(requested_sequences) - set(response_sequences)),
        "result": (
            "DRY_RUN"
            if args.dry_run
            else "RESPONSE_RECEIVED" if response_sequences else "NO_RESPONSE"
        ),
        "accuracy_note": "This probe tests protocol response only; it does not measure path-delay accuracy.",
    }


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Envia Pdelay_Req IEEE 1588/C37.238 e procura respostas do RT430."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--list-interfaces", action="store_true", help="lista interfaces e encerra")
    parser.add_argument("--iface", help="nome ou GUID Npcap da interface Ethernet")
    parser.add_argument("--source-mac", type=normalize_mac, help="MAC de origem; padrão: MAC da interface")
    parser.add_argument("--rt-mac", type=normalize_mac, default="f8:02:78:10:b7:ae")
    parser.add_argument("--dest", choices=("peer", "general", "unicast", "all"), default="all")
    parser.add_argument("--domain", type=int, default=10)
    parser.add_argument("--vlan", type=int, default=400)
    parser.add_argument("--pcp", type=int, default=6)
    parser.add_argument("--source-port", type=int, default=1)
    parser.add_argument("--count", type=int, default=5, help="pedidos por modo de destino")
    parser.add_argument("--interval", type=float, default=1.0, help="intervalo entre pedidos, em segundos")
    parser.add_argument("--timeout", type=float, default=2.0, help="espera adicional após o último pedido")
    parser.add_argument("--start-sequence", type=int, default=1)
    parser.add_argument("--timestamp-mode", choices=("tai", "utc", "zero"), default="tai")
    parser.add_argument("--utc-offset", type=int, default=37, help="TAI-UTC atual, em segundos")
    parser.add_argument(
        "--legacy-two-step-flag",
        action="store_true",
        help="reproduz o twoStepFlag observado na mala 11:99",
    )
    parser.add_argument(
        "--legacy-log-interval",
        action="store_true",
        help="usa logMessageInterval=0, observado na mala 11:99",
    )
    parser.add_argument("--dry-run", action="store_true", help="gera PCAP/relatório sem transmitir")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "results",
    )
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.list_interfaces:
        return
    if not args.iface:
        parser.error("--iface é obrigatório (use --list-interfaces para descobrir o nome)")
    if not 0 <= args.domain <= 255:
        parser.error("--domain deve estar entre 0 e 255")
    if not 0 <= args.vlan <= 4094:
        parser.error("--vlan deve estar entre 0 e 4094")
    if not 0 <= args.pcp <= 7:
        parser.error("--pcp deve estar entre 0 e 7")
    if not 1 <= args.source_port <= 65535:
        parser.error("--source-port deve estar entre 1 e 65535")
    if not 1 <= args.count <= 1000:
        parser.error("--count deve estar entre 1 e 1000")
    if args.interval < 0 or args.timeout < 0:
        parser.error("--interval e --timeout não podem ser negativos")


def main() -> int:
    parser = create_parser()
    args = parser.parse_args()
    validate_args(parser, args)
    if args.list_interfaces:
        list_interfaces()
        return 0

    try:
        source_mac = args.source_mac or normalize_mac(get_if_hwaddr(args.iface))
    except Exception as exc:
        print(f"ERRO: não foi possível obter o MAC da interface: {exc}", file=sys.stderr)
        return 2
    if source_mac == "00:00:00:00:00:00":
        print("ERRO: interface sem MAC válido; selecione a interface Ethernet física.", file=sys.stderr)
        return 2
    if not args.dry_run and not is_admin():
        print("ERRO: execute o terminal como Administrador para transmitir quadros Ethernet.", file=sys.stderr)
        return 2

    destinations = resolve_destinations(args.dest, args.rt_mac)
    frame_records: list[dict] = []
    frames = []
    sequence = args.start_sequence
    log_interval = 0 if args.legacy_log_interval else 127
    for mode, destination in destinations:
        for _ in range(args.count):
            frame = build_pdelay_frame(
                source_mac=source_mac,
                destination_mac=destination,
                vlan=args.vlan,
                pcp=args.pcp,
                domain=args.domain,
                sequence_id=sequence,
                timestamp_mode=args.timestamp_mode,
                utc_offset=args.utc_offset,
                two_step_flag=args.legacy_two_step_flag,
                unicast_flag=mode == "unicast",
                log_interval=log_interval,
            )
            frames.append(frame)
            frame_records.append(
                {
                    "mode": mode,
                    "destination_mac": destination,
                    "sequence_id": sequence,
                    "flags": f"0x{((0x0200 if args.legacy_two_step_flag else 0) | (0x0400 if mode == 'unicast' else 0)):04x}",
                    "log_message_interval": log_interval,
                }
            )
            sequence = (sequence + 1) & 0xFFFF

    base = output_base(args.output_dir)
    sent_pcap = base.with_name(base.name + "_sent.pcap")
    capture_pcap = base.with_name(base.name + "_capture.pcap")
    report_file = base.with_suffix(".json")

    print("RT430 PTP active probe")
    print(f"Interface : {args.iface}")
    print(f"Source MAC: {source_mac}")
    print(f"RT430 MAC : {args.rt_mac}")
    print(f"PTP       : domain={args.domain}, VLAN={args.vlan}, PCP={args.pcp}")
    print(f"Modes     : {', '.join(mode for mode, _ in destinations)}")
    print(f"Frames    : {len(frames)}")

    captured = []
    if args.dry_run:
        print("DRY-RUN: nenhum quadro será transmitido.")
    else:
        sniffer = AsyncSniffer(iface=args.iface, store=True)
        sniffer.start()
        time.sleep(0.25)
        try:
            for record, frame in zip(frame_records, frames):
                print(
                    f"TX seq={record['sequence_id']:5d} "
                    f"mode={record['mode']:<7} dst={record['destination_mac']}"
                )
                sendp(frame, iface=args.iface, verbose=False)
                time.sleep(args.interval)
            time.sleep(args.timeout)
        finally:
            captured = sniffer.stop()

    wrpcap(str(sent_pcap), frames)
    if captured:
        wrpcap(str(capture_pcap), captured)
    report = summarize(
        args=args,
        source_mac=source_mac,
        sent_records=frame_records,
        received_packets=captured,
    )
    report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nResultado: {report['result']}")
    print(f"Pdelay_Resp correspondentes: {len(report['matching_pdelay_resp_sequences'])}")
    print(f"Relatório: {report_file}")
    print(f"PCAP TX  : {sent_pcap}")
    if captured:
        print(f"PCAP RX  : {capture_pcap}")
    return 0 if args.dry_run or report["result"] == "RESPONSE_RECEIVED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
