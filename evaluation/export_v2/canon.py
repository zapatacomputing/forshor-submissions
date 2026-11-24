"""Canonical JSON and the epoch hash. The encoding must agree byte for byte with the publisher's, because the epoch id is a
hash of it: strings, integers, booleans and simple floats such as 0.5; keys sorted; no whitespace. Any divergence shows up as
a changed epoch id, which `pytests/test_operational_pin.py` and AGENTS.md rule 1 both pin."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def epoch_of(model: dict) -> str:
    """The first 16 hex characters of sha256(canonical(model))."""
    return sha256_hex(model)[:16]


def policy_digest(policy_json: dict) -> str:
    """A policy's identity: its mode and bands only. The engine writes a display title into `policy_json.name`, which differs
    between a policy's PhaseProduct and full-run panels, so hashing the whole document is not a stable identity."""
    return sha256_hex({"mode": policy_json["mode"], "bands": policy_json["bands"]})
