"""Vendor-only command for creating signed activation codes; never ship its private key."""

from __future__ import annotations

import argparse
import base64
import json
from datetime import date
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def init(private_key: Path, public_key: Path) -> None:
    key = Ed25519PrivateKey.generate()
    private_key.parent.mkdir(parents=True, exist_ok=True)
    private_key.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public_key.write_text(_b64(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)), encoding="utf-8")
    print(f"Created vendor key at {private_key} and public key at {public_key}.")


def issue(private_key: Path, expires_on: str, plan: str) -> None:
    payload = _b64(json.dumps({"v": 1, "expires_on": date.fromisoformat(expires_on).isoformat(), "plan": plan}, separators=(",", ":")).encode())
    key = serialization.load_pem_private_key(private_key.read_bytes(), password=None)
    print(f"{payload}.{_b64(key.sign(payload.encode()))}")


parser = argparse.ArgumentParser()
sub = parser.add_subparsers(dest="command", required=True)
setup = sub.add_parser("init")
setup.add_argument("--private-key", type=Path, required=True)
setup.add_argument("--public-key", type=Path, default=Path("license_public_key.txt"))
create = sub.add_parser("issue")
create.add_argument("--private-key", type=Path, required=True)
create.add_argument("--expires-on", required=True)
create.add_argument("--plan", default="授权")
args = parser.parse_args()
if args.command == "init":
    init(args.private_key, args.public_key)
else:
    issue(args.private_key, args.expires_on, args.plan)
