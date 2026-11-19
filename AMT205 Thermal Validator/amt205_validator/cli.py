from __future__ import annotations

import argparse
from pathlib import Path

from .capture import iter_pcap
from .configuration import load_config
from .report import create_excel
from .session import TestSession, default_session_name


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze an AMT205 capture and generate a consolidated thermal report.")
    parser.add_argument("pcap", type=Path, help=".pcap or .pcapng file")
    parser.add_argument("--output", type=Path, default=Path("sessions"), help="Output folder")
    parser.add_argument("--profile", choices=("current_25", "current_50", "current_75", "current_100", "voltage_300", "custom"), default="current_50")
    parser.add_argument("--fan", choices=("MAX", "NORMAL"), default="MAX", help="Fan condition: Max Power or comparative Normal mode.")
    parser.add_argument("--serial", default="")
    parser.add_argument("--ambient", type=float, default=23.0)
    parser.add_argument("--repetition", type=int, default=1)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.pcap.exists():
        raise SystemExit(f"File not found: {args.pcap}")
    config = load_config()
    metadata = {
        "profile": args.profile,
        "fan_mode": "MAX",
        "fan_mode_required": args.fan,
        "injection_frequency_hz": float(config.get("test_scope", {}).get("injection_frequency_hz", 50.0)),
        "serial_number": args.serial,
        "ambient_c": args.ambient,
        "repetition": args.repetition,
        **config["equipment"],
    }
    session = TestSession(config, metadata)
    for packet in iter_pcap(args.pcap):
        session.process(packet)
    destination = args.output / default_session_name()
    evidence = destination / "evidence"
    bundle = session.save(evidence, args.pcap)
    report = create_excel(bundle, destination / "AMT205_Thermal_Test_Report.xlsx")
    print(report.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
