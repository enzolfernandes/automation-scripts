from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path


SESSION_PREFERENCE_DEFAULTS = {
    "source_mode": "PCAP File",
    "profile": "current_50",
    "serial_number": "",
    "ambient_c": "23",
    "repetition": "1",
    "output_location": "",
    "smart_delay_enabled": True,
    "smart_delay_min_c": "45.00",
    "smart_delay_max_c": "55.00",
    "maximum_temperature_setting_c": "60.00",
    "earth_enabled": True,
    "fan_mode_max_power": True,
    "compare_feedback": True,
}


def application_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_config(path: Path | None = None) -> dict:
    if path is not None:
        return json.loads(path.read_text(encoding="utf-8"))
    default_config = json.loads((application_root() / "config" / "default.json").read_text(encoding="utf-8"))
    user_config = application_root() / "config" / "user.json"
    if not user_config.exists():
        return default_config
    overrides = json.loads(user_config.read_text(encoding="utf-8"))
    merged = _deep_merge(default_config, overrides)
    # Application identity belongs to the installed release, not to remembered
    # operator settings. It must never be rolled back by an older user.json.
    merged["schema_version"] = default_config["schema_version"]
    merged["application"] = deepcopy(default_config["application"])
    # Schema 4 formalizes the internally approved injection-stability rule and
    # the Fan Max Power historical-time multiplier. Schema 5 fixes the new
    # route to Max Power and records the 50 Hz comparison condition. Schema 6
    # records that the historical reference runs already used Max Power, so
    # their times must not receive a second 3x multiplier. Schema 7 enables
    # comparative Normal-mode sessions and formalizes fan-specific initial
    # and recovery targets. Older remembered criteria must not silently
    # override those approvals.
    if int(overrides.get("schema_version", 0)) < 4:
        merged["stability"] = deepcopy(default_config["stability"])
        merged["fan_mode"] = deepcopy(default_config["fan_mode"])
    if int(overrides.get("schema_version", 0)) < 5:
        merged["test_scope"] = deepcopy(default_config["test_scope"])
        # Standard profile names are part of the approved route description;
        # refresh them while preserving any operator-defined Free Profile.
        for profile_key in ("current_25", "current_50", "current_75", "current_100", "voltage_300"):
            if profile_key in default_config.get("profiles", {}):
                merged.setdefault("profiles", {}).setdefault(profile_key, {})["label"] = default_config["profiles"][profile_key]["label"]
    if int(overrides.get("schema_version", 0)) < 6:
        merged["fan_mode"] = deepcopy(default_config["fan_mode"])
    if int(overrides.get("schema_version", 0)) < 7:
        merged["initial_temperature_c"] = deepcopy(default_config["initial_temperature_c"])
        merged["initial_baseline"] = deepcopy(default_config["initial_baseline"])
        merged["thermal_recovery"] = deepcopy(default_config["thermal_recovery"])
        merged["test_scope"] = deepcopy(default_config["test_scope"])
        for profile_key in ("current_25", "current_50", "current_75", "current_100", "voltage_300"):
            if profile_key in default_config.get("profiles", {}):
                merged.setdefault("profiles", {}).setdefault(profile_key, {})["label"] = default_config["profiles"][profile_key]["label"]
    stability = merged.setdefault("stability", {})
    # Versions through 0.5.3 stored an unsupported 300 s assumption. Unless an
    # operator explicitly approves a duration in the new UI, discard that value.
    if not stability.get("window_approved", False):
        stability["window_s"] = None
    return merged


def _deep_merge(base: dict, overrides: dict) -> dict:
    merged = deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def save_config(config: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def session_preferences(config: dict) -> dict:
    preferences = deepcopy(SESSION_PREFERENCE_DEFAULTS)
    stored = config.get("user_preferences", {}).get("last_session", {})
    if isinstance(stored, dict):
        preferences.update({key: deepcopy(value) for key, value in stored.items() if key in preferences})
    if preferences["source_mode"] not in ("PCAP File", "Live Monitor"):
        preferences["source_mode"] = SESSION_PREFERENCE_DEFAULTS["source_mode"]
    if preferences["profile"] not in config.get("profiles", {}):
        preferences["profile"] = SESSION_PREFERENCE_DEFAULTS["profile"]
    if str(preferences["repetition"]) not in ("1", "2", "3"):
        preferences["repetition"] = SESSION_PREFERENCE_DEFAULTS["repetition"]
    preferences["fan_mode_max_power"] = bool(preferences["fan_mode_max_power"])
    return preferences


def update_session_preferences(config: dict, preferences: dict) -> None:
    config.setdefault("user_preferences", {})["last_session"] = {
        key: deepcopy(preferences.get(key, default))
        for key, default in SESSION_PREFERENCE_DEFAULTS.items()
    }


def session_config(base: dict) -> dict:
    active = deepcopy(base)
    active.pop("user_preferences", None)
    return active
