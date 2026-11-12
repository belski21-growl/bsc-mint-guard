"""Shared BSC JSON-RPC helper: curl only, rotation + backoff, explicit blocks.

Rules (from field experience):
- NEVER urllib for BSC RPC (dies IncompleteRead); curl completes the same calls.
- eth_getLogs: explicit block numbers only, ~1000-block chunks (48.club
  rejects toBlock "latest" even for small ranges; 4000-block chunks time out).
- NEVER hardcode block numbers: callers read the live tip first.
"""
import json
import subprocess
import time

_DEAD = {}  # url -> dead_since timestamp; revived after 120s

RPCS = [
    # 48.club first: historically the most reliable for getLogs here;
    # blastapi/publicnode/dataseed rotate in when it throttles.
    "https://rpc-bsc.48.club",
    "https://bsc-mainnet.public.blastapi.io",
    "https://bsc.publicnode.com",
    "https://bsc-dataseed.binance.org",
]
ZERO_TOPIC = "0x" + "0" * 64


def rpc(method, params, rounds=2):
    """One JSON-RPC call with rotation. Returns result or raises RuntimeError.
    Rate-limit (429/-32005) responses are retried, never treated as a revert.
    Backoff is bounded: one attempt per URL per round with a short sleep
    between 429s (a 2s sleep per attempt made log scans take 40+ minutes
    when all endpoints were rate-limited — proven 2 Nov 2026)."""
    errors = []
    for rnd in range(rounds):
        attempted = False
        for url in RPCS:
            if url in _DEAD and time.time() - _DEAD[url] < 120:
                continue
            _DEAD.pop(url, None)  # revive after 120s
            attempted = True
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            p = subprocess.run(["curl", "-sS", "--max-time", "12",
                                "-H", "Content-Type: application/json",
                                "-d", body, url],
                               capture_output=True, text=True)
            try:
                out = json.loads(p.stdout)
            except Exception:
                errors.append(f"{url}: unparsable out={p.stdout[:60]!r} err={p.stderr[:80]!r}")
                _note_failure(url)
                continue
            if "result" in out and out["result"] is not None:
                _note_success(url)
                return out["result"]
            err = out.get("error", {})
            msg = json.dumps(err)
            if "429" in msg or "-32005" in msg or "Rate limit" in msg or "-32000" in msg:
                errors.append(f"{url}: rate-limited")
                _note_failure(url)
                time.sleep(0.5)
                continue
            errors.append(f"{url}: {msg[:160]}")
            _note_failure(url)
        if not attempted:
            # Every endpoint is in its 120s dead window: wait for the
            # earliest revival instead of burning chunks on empty tries.
            wait = min(120, max(1, min(int(_DEAD[u] + 120 - time.time())
                                       for u in _DEAD if u in _DEAD) or 30))
            time.sleep(wait)
        elif rnd < rounds - 1:
            time.sleep(5)
    raise RuntimeError("all RPCs failed: " + " | ".join(errors))


_FAIL_STREAK = {}


def _note_success(url):
    _FAIL_STREAK[url] = 0


def _note_failure(url):
    _FAIL_STREAK[url] = _FAIL_STREAK.get(url, 0) + 1
    if _FAIL_STREAK[url] >= 3:
        _DEAD[url] = time.time()


def tip():
    return int(rpc("eth_blockNumber", []), 16)


def eth_call(to, data, block="latest", from_addr=None):
    tx = {"to": to, "data": data}
    if from_addr:
        tx["from"] = from_addr
    blk = hex(block) if isinstance(block, int) else block
    return rpc("eth_call", [tx, blk])


def get_code(address):
    return rpc("eth_getCode", [address, "latest"])


def get_block_timestamp(block_number):
    blk = rpc("eth_getBlockByNumber", [hex(block_number), False])
    return int(blk["timestamp"], 16)


def get_logs(address, topics, from_block, to_block, chunk=1000):
    """eth_getLogs in explicit ~1000-block chunks. Returns flat log list."""
    out = []
    start = from_block
    while start <= to_block:
        end = min(start + chunk - 1, to_block)
        out.extend(rpc("eth_getLogs", [{
            "address": address,
            "topics": topics,
            "fromBlock": hex(start),
            "toBlock": hex(end),
        }]))
        start = end + 1
    return out


def checksum(addr):
    """EIP-55 checksum via Keccak-256 (NOT hashlib.sha3_256: NIST SHA3
    differs from Keccak and would produce wrong checksums)."""
    from Crypto.Hash import keccak
    a = addr.lower().replace("0x", "")
    k = keccak.new(digest_bits=256)
    k.update(a.encode())
    h = k.hexdigest()
    return "0x" + "".join(c.upper() if int(h[i], 16) >= 8 else c
                           for i, c in enumerate(a))


def sel(sig):
    """Function selector via keccak (never from memory)."""
    from Crypto.Hash import keccak
    k = keccak.new(digest_bits=256)
    k.update(sig.encode())
    return "0x" + k.hexdigest()[:8]


def padded_addr(addr):
    return "0" * 24 + addr.lower().replace("0x", "")
