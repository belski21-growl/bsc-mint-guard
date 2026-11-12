#!/usr/bin/env python3
"""Pre-mint deployer screen for BSC NFT contracts.

Before spending gas on a new mint, read the contract's owner() and compare
it against a curated list of known template-farm deployers, plus a registry
of factory owners seen before. One cheap read-only call can save you from
minting the 40th copy of the same template.

Usage:
    python3 check_owner.py <contract> [<contract> ...]

One line per contract:
    VERDICT <contract> owner=<address|-> code_len=<n> keccak=<hash>

Verdicts:
    SKIP                owner matches the deployer blacklist -> do not mint.
    SKIP-FACTORY-REPEAT owner is a factory contract already recorded with a
                        DIFFERENT contract -> same template, do not mint.
    WATCH               owner is on the watch list (not a skip) -> proceed,
                        but note it.
    OK                  owner is neither skip nor watch -> proceed.
    NOOWNER             owner() reverted or returned empty -> NOT a reason
                        to block; proceed to the other gates.
    OK-FACTORY-OWNER    owner is a contract, first time seen -> proceed, and
                        the helper records this factory owner + contract in
                        data/factory-registry.json so a second contract from
                        the same factory becomes SKIP-FACTORY-REPEAT.

Also writes a JSON summary to check-owner-last.json (overwritten each run).

Notes:
  - owner() is a PROXY for a fast check, not proof of the creation tx.
    A blacklist hit means "same owner as a proven template farm", which is
    strong evidence, not a legal verdict.
  - RPC via curl (never urllib: BSC endpoints routinely kill urllib
    connections mid-body with IncompleteRead, while curl completes them).
  - The `from` address for eth_call defaults to the zero address and can
    be overridden with the MINT_GUARD_FROM environment variable. Any
    address works for a read-only call; use your own wallet if you prefer.
  - Data files live next to this script under ../data/.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "..", "data")
SKIP_FILE = os.path.join(DATA_DIR, "deployer-blacklist.json")
REGISTRY_FILE = os.path.join(DATA_DIR, "factory-registry.json")
OUT_JSON = os.path.join(HERE, "check-owner-last.json")
FROM = os.environ.get(
    "MINT_GUARD_FROM", "0x0000000000000000000000000000000000000000"
)
RPCS = [
    "https://bsc-dataseed1.binance.org",
    "https://bsc-dataseed2.binance.org",
    "https://bsc-dataseed.binance.org",
    "https://rpc.48.club",
]


def rpc(method, params):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    for url in RPCS:
        try:
            r = subprocess.run(
                ["curl", "-sS", "--max-time", "25",
                 "-H", "Content-Type: application/json",
                 "-d", json.dumps(payload), url],
                capture_output=True, text=True,
            )
            j = json.loads(r.stdout)
            if "result" in j and j["result"] not in (None, "0x"):
                return j["result"]
        except Exception:
            pass
    return None


def load_registry():
    try:
        with open(REGISTRY_FILE) as f:
            return json.load(f)
    except Exception:
        return {"factories": {}}


def record_factory_owner(owner_lc, contract, code_len, keccak):
    """Record the FIRST appearance of a factory owner + contract (idempotent)."""
    try:
        reg = load_registry()
        facs = reg.setdefault("factories", {})
        entry = facs.setdefault(
            owner_lc,
            {"collections": [], "skip_listed": False,
             "note": "Recorded automatically by check_owner.py."},
        )
        if not any(c["contract"].lower() == contract.lower()
                   for c in entry["collections"]):
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            entry["collections"].append(
                {"name": "", "contract": contract,
                 "code_len": code_len, "first_seen": now}
            )
            reg["updated"] = now
            with open(REGISTRY_FILE, "w") as f:
                json.dump(reg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def code_keccak(code):
    """Keccak-256 of runtime bytecode, truncated for display. Falls back
    to hashlib.sha3_256 only if pycryptodome is missing (note: NIST SHA3
    differs from Keccak, so install pycryptodome for exact values)."""
    if not code:
        return None
    try:
        from Crypto.Hash import keccak
        k = keccak.new(digest_bits=256)
        k.update(bytes.fromhex(code[2:]))
        return k.hexdigest()[:18]
    except Exception:
        import hashlib
        return hashlib.sha3_256(bytes.fromhex(code[2:])).hexdigest()[:18]


def main():
    addrs = sys.argv[1:]
    if not addrs:
        print("usage: check_owner.py <contract> [<contract> ...]")
        return 2
    with open(SKIP_FILE) as f:
        lst = json.load(f)
    skip = {s["deployer"].lower(): s for s in lst.get("skip", [])}
    watch = {w["owner"].lower(): w for w in lst.get("watch", [])}
    results = []
    for addr in addrs:
        addr = addr.strip()
        owner_raw = rpc("eth_call", [
            {"from": FROM, "to": addr, "data": "0x8da5cb5b"}, "latest",
        ])
        code = rpc("eth_getCode", [addr, "latest"])
        code_len = (len(code) - 2) // 2 if code else None
        keccak = code_keccak(code)
        if not owner_raw:
            verdict, owner = "NOOWNER", None
        else:
            owner = "0x" + owner_raw[-40:]
            ol = owner.lower()
            if ol in skip:
                verdict = "SKIP"
            elif ol in watch:
                verdict = "WATCH"
            else:
                # Is the owner itself a contract (a factory)?
                ocode = rpc("eth_getCode", [owner, "latest"])
                if ocode and ocode != "0x" and len(ocode) > 4:
                    reg = load_registry()
                    entry = reg.get("factories", {}).get(ol)
                    others = [c for c in (entry or {}).get("collections", [])
                              if c["contract"].lower() != addr.lower()]
                    if others:
                        verdict = "SKIP-FACTORY-REPEAT"
                    else:
                        verdict = "OK-FACTORY-OWNER"
                        record_factory_owner(ol, addr, code_len, keccak)
                else:
                    verdict = "OK"
        print(f"{verdict} {addr} owner={owner} code_len={code_len} keccak={keccak}")
        results.append({"contract": addr, "verdict": verdict, "owner": owner,
                        "code_len": code_len, "code_keccak": keccak})
    with open(OUT_JSON, "w") as f:
        json.dump(results, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
