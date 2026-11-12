#!/usr/bin/env python3
"""Fingerprint a BSC contract against the template library.

Reads runtime bytecode via eth_getCode (stdlib only, no web3 dependency),
extracts PUSH4 dispatcher candidates, and matches templates.json.

Usage:
    python3 fingerprint.py <contract-address> [--json-only]
    python3 fingerprint.py --list

Exit codes: 0 = FULL or FAMILY match, 1 = PARTIAL or NO_MATCH,
            2 = error (bad address, no code, all RPCs failed).

Rules baked in (see README.md):
  - Transport failure is NEVER a match result: all-RPC-failed => error, not NO_MATCH.
  - A shared selector (e.g. mint(uint256)) yields FAMILY, never a single
    guessed winner: distinguishing_checks must be run before grinding.
  - Proxy (EIP-1167) bytecode is followed to its implementation when possible.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
      "Content-Type": "application/json"}
RPCS = [
    "https://bsc-dataseed1.binance.org",
    "https://bsc-dataseed2.binance.org",
    "https://bsc-dataseed.binance.org",
    "https://rpc.48.club",
    "https://bsc.publicnode.com",
]
ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
# EIP-1167 minimal proxy: 363d3d373d3d3d363d73 <20-byte impl> 5af43d82803e903d91602b57fd5bf3
PROXY_RE = re.compile(r"^363d3d373d3d3d363d73([0-9a-f]{40})5af43d82803e903d91602b57fd5bf3$")


def rpc_call(url, method, params):
    """JSON-RPC via curl, not urllib: BSC endpoints routinely kill urllib
    connections mid-body (IncompleteRead), while curl completes the same
    calls."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    cmd = ["curl", "-sS", "--max-time", "25", "--retry", "2",
           "-A", UA["User-Agent"], "-H", "Content-Type: application/json",
           "--data", body, url]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if proc.returncode != 0:
        raise RuntimeError(f"curl failed: {proc.stderr.strip()[:200]}")
    out = json.loads(proc.stdout)
    if "error" in out:
        raise RuntimeError(f"rpc error: {out['error']}")
    return out["result"]


def get_code(address):
    """eth_getCode across RPCs. Returns (code_hex, rpc_used). Raises on total failure."""
    errors = []
    for url in RPCS:
        try:
            code = rpc_call(url, "eth_getCode", [address, "latest"])
            if isinstance(code, str) and code.startswith("0x"):
                return code, url
            errors.append(f"{url}: bad result {code!r}")
        except Exception as e:  # noqa: BLE001 - transport failure is retried, not fatal
            errors.append(f"{url}: {e}")
    raise RuntimeError("all RPCs failed for eth_getCode: " + " | ".join(errors))


def extract_push4(code_hex):
    """Collect 4-byte values following a PUSH4 (0x63) opcode in runtime bytecode."""
    raw = bytes.fromhex(code_hex[2:])
    found = set()
    i = 0
    while i < len(raw) - 4:
        if raw[i] == 0x63:
            found.add("0x" + raw[i + 1:i + 5].hex())
            i += 5
        else:
            i += 1
    return found


def resolve_proxy(code_hex):
    m = PROXY_RE.match(code_hex[2:].lower())
    return ("0x" + m.group(1)) if m else None


def main(argv):
    if "--list" in argv:
        data = json.loads(TEMPLATES.read_text())
        for t in data["templates"]:
            print(f"{t.get('id', '?'):22s} {t.get('family', '?'):22s} "
                  f"{t.get('status', 'unknown'):8s} {t.get('confidence', '?')}")
        return 0
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1 or not ADDR_RE.match(args[0]):
        print("usage: fingerprint.py <0x-contract-address> [--json-only]", file=sys.stderr)
        return 2
    address = args[0]
    json_only = "--json-only" in argv
    try:
        code, rpc_used = get_code(address)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if code in ("0x", "0x0"):
        print(f"NO_CODE: {address} has no runtime bytecode (EOA or undeployed).")
        return 2
    selectors = extract_push4(code)
    impl = resolve_proxy(code)
    impl_note = None
    if impl:
        try:
            icode, _ = get_code(impl)
            if icode not in ("0x", "0x0"):
                selectors |= extract_push4(icode)
                impl_note = f"proxy -> implementation {impl} (selectors merged)"
        except Exception:  # noqa: BLE001 - proxy follow is best-effort; base set still stands
            impl_note = f"proxy -> implementation {impl} (unreadable, base selectors only)"
    data = json.loads(TEMPLATES.read_text())
    results = []
    for t in data["templates"]:
        req = t.get("required_selectors") or []
        if not req:
            continue  # pattern-only templates are checklists, not auto-matchable
        present = [s for s in req if s in selectors]
        if len(present) == len(req):
            results.append((t, "FULL", present, []))
        # PARTIAL needs >= 2 selectors: a single shared generic selector
        # (e.g. freeMinted) is noise, not evidence (lesson: onimaru entry
        # matched HarvestLand on freeMinted alone, 1/9).
        elif len(present) >= 2:
            results.append((t, "PARTIAL", present, [s for s in req if s not in selectors]))
    # Verdict follows the BEST match: a PARTIAL alongside a FULL must not
    # drag the verdict down (lesson: onimaru PARTIAL next to harvestland FULL
    # wrongly produced PARTIAL). FAMILY = several FULL matches sharing a
    # selector set — never a guessed single winner.
    full_ids = [t["id"] for t, v, _, _ in results if v == "FULL"]
    verdict = "NO_MATCH"
    if len(full_ids) > 1:
        verdict = "FAMILY"
    elif len(full_ids) == 1:
        verdict = "FULL"
    elif results:
        verdict = "PARTIAL"
    out = {
        "address": address,
        "rpc": rpc_used,
        "code_len_bytes": (len(code) - 2) // 2,
        "selector_candidates": len(selectors),
        "proxy": impl_note,
        "verdict": verdict,
        "matches": [
            {"id": t.get("id", "?"), "family": t.get("family", "?"),
             "status": t.get("status", "unknown"),
             "verdict": v, "present": p, "missing": m,
             "mint_route": t.get("mint_route"),
             "pow": t.get("pow"),
             "quota_checks": t.get("quota_checks"),
             "distinguishing_checks": t.get("distinguishing_checks"),
             "pitfalls": t.get("pitfalls")}
            for t, v, p, m in results
        ],
    }
    if json_only:
        print(json.dumps(out, indent=2))
    else:
        print(f"contract: {address}  (via {rpc_used}, {out['code_len_bytes']} bytes code, "
              f"{out['selector_candidates']} PUSH4 candidates)")
        if impl_note:
            print(f"proxy: {impl_note}")
        print(f"verdict: {verdict}")
        if not results:
            print("No template matched. Dissect it manually, then add a template.")
            print("Pattern checklists still available: agentid_share, erc721_standard_free.")
        for t, v, p, m in results:
            print(f"\n[{v}] {t['id']} ({t['family']}, status={t['status']})")
            print(f"  present: {', '.join(p) if p else '-'}")
            if m:
                print(f"  missing: {', '.join(m)}")
            if t.get("mint_route"):
                mr = t["mint_route"]
                print(f"  route: {mr.get('function')} value={mr.get('value')}")
            if t.get("pow"):
                print(f"  pow: {t['pow'].get('hash')} preimage={t['pow'].get('preimage')}")
            for c in (t.get("distinguishing_checks") or []):
                print(f"  distinguish: {c}")
            for c in (t.get("pitfalls") or [])[:2]:
                print(f"  pitfall: {c}")
        if verdict == "FAMILY":
            print("\nFAMILY verdict: do NOT pick one winner by selector alone.")
            print("Run the distinguishing_checks above (read verify() first) before any grind.")
    return 0 if verdict in ("FULL", "FAMILY") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
