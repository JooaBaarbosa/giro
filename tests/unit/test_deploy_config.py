"""Checagens estáticas dos workflows do n8n e do compose (sem subir containers)."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _dry_run_conditions() -> list[tuple[str, dict]]:
    found = []
    for path in sorted((ROOT / "n8n" / "workflows").glob("*.json")):
        for node in json.loads(path.read_text(encoding="utf-8"))["nodes"]:
            conditions = node.get("parameters", {}).get("conditions", {})
            for cond in conditions.get("conditions", []):
                if "WHATSAPP_DRY_RUN" in str(cond.get("leftValue", "")):
                    found.append((f"{path.name}:{node['name']}", conditions["options"]))
    return found


def test_dry_run_flag_read_case_insensitively_everywhere():
    conditions = _dry_run_conditions()
    assert len(conditions) == 2
    for where, options in conditions:
        assert options["caseSensitive"] is False, where


def test_postgres_and_n8n_bound_to_localhost_only():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    ports = re.findall(r'-\s*"([^"]+:\d+)"', compose)
    assert ports, "nenhuma porta publicada encontrada"
    for mapping in ports:
        if mapping.endswith((":5432", ":5678")):
            assert mapping.startswith("127.0.0.1:"), mapping
