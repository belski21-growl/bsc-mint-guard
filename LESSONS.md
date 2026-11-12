# Field notes

Twenty rules learned the expensive way, screening BSC NFT mints on-chain.
Each one cost real gas, real time, or a real missed mint before it became a
rule. The evidence is on-chain; the lesson is generic.

## 1. Never write a selector from memory

`mint(address,uint256)` is `0x40c10f19`. `mint(uint256)` is `0xa0712d68`.
We once shipped the first one where the second belonged: three previews
reverted, and the computed selector previewed clean and minted. Always
compute selectors via keccak at runtime — `guard/rpc.py: sel()` does exactly
that — and use the SAME selector in simulation and in the real call.

## 2. Never shift a C uint64 by 64 bits or more

It is undefined behaviour, and on x86 GCC masks the shift count to 6 bits —
so bytes silently repeat every 8 bytes. We once found a "valid" proof-of-work
nonce that passed its own solver's check but failed independent Python
verification, because the nonce bytes were repeating. Fill multi-byte
big-endian values from the least-significant end with shifts 0..56 only, and
always verify a found nonce with an independent implementation before
spending gas.

## 3. Never assume which hash a proof-of-work contract checks

Read `verify()` from the verified contract source BEFORE grinding. We once
ground keccak for 43 minutes and "found" a nonce — but the contract checked
`sha256`, and that nonce's sha256 digest had 0 leading zero bits against 34
required. It could never mint. The hash function is a fact about the
contract, not a guess.

## 4. A shared selector is a family, not a winner

`mint(uint256)` alone proves nothing about proof-of-work: four contracts
sharing that selector turned out to be plain phased free mints (first mint
free per wallet, then paid, no `verify()` at all). Always read the verified
source and look for `verify()` before calling something "PoW" or planning
solver work. `guard/fingerprint.py` returns `FAMILY` — never a single
guessed winner — for exactly this reason.

## 5. Fingerprint before you dissect

For any new contract, match its bytecode against known templates first. A
`FULL` or `FAMILY` match tells you the mint route, the quota checks, and
the pitfalls before you spend an hour reading code you have already read.
Template-first is the difference between ten minutes and an afternoon.

## 6. `owner()` is a proxy, not creation-tx proof

For `Ownable` templates, owner == deployer, and it has matched
explorer-verified creators every time we checked. But it is still a proxy:
use it as a fast pre-mint screen, not as a claim about who deployed the
contract. That is what `guard/check_owner.py` does — one cheap call before
any gas.

## 7. The wave rule

Several NEW contracts in one wave sharing the SAME owner + the SAME
`code_len` + the SAME metadata host = one template farm. Skip all of them
and blacklist the owner at once. The exception: collections that are still
one project (same brand, site, and socials) — evaluate those normally.
What distinguishes a farm from a project is shared identity, not shared
code alone.

## 8. A factory seen twice is one template

If a contract's owner is itself a contract (a factory) and that factory
already appeared with a DIFFERENT collection, the two collections are the
same template. Record the first appearance; skip the second.
`guard/check_owner.py` does this automatically via
`data/factory-registry.json`.

## 9. Use curl for BSC RPC, never urllib

BSC endpoints routinely kill urllib connections mid-body
(`IncompleteRead`); curl completes the same calls. Every RPC helper in
this repo shells out to curl for that reason.

## 10. A zero result on one endpoint proves nothing

One public endpoint once returned all zeroes for a valid `eth_call` while
two others returned the true state. Never conclude "nothing is due" from a
zero result on a single endpoint — cross-check a second endpoint before
acting on zeroes.

## 11. Rate limits are inconclusive, not failures

A `-32005` (limit exceeded) or a 429 is not a revert and not a "no". Rotate
endpoints, wait, and re-check. Treating a rate limit as an answer is how
you miss a mint — or declare a live pool dead.

## 12. `eth_getLogs` needs explicit blocks

`toBlock: "latest"` fails on some endpoints even for small ranges. Always
pass explicit block numbers, in ~1000-block chunks (4000-block chunks time
out). And free RPC state only goes back ~100–1000 blocks: verifying
historical state for a days-old contract via free RPC is impossible —
use an explorer or an archive node instead.

## 13. Build calldata in code, never hand-copy it

A six-character copy slip in hand-copied calldata failed a preview twice;
the programmatically rebuilt calldata previewed clean at once. ABI-encode
from values, assert the length, and use the same bytes in simulation and
in the real call. For dynamic-array arguments, the offset is the TOTAL head
size (e.g. 64 bytes for two arguments), not 32.

## 14. Checksums everywhere

Some signers reject lowercase addresses outright. Always use EIP-55
checksummed addresses for `from` and `to` — and note that Keccak-256 is
NOT NIST SHA3: `hashlib.sha3_256` produces wrong checksums. Use
`pycryptodome`'s Keccak (see `guard/rpc.py: checksum()`).

## 15. Decode data-URI metadata inline

`tokenURI` can return `data:application/json;base64` with the metadata
embedded — never fetch it as a URL. The `image` field inside may itself be
`ipfs://` (resolve via a gateway) or `data:image/svg+xml` (decode and
render, don't judge from the fetch failure). `guard/fetch_image.py`
handles all of these.

## 16. Wait for JS-rendered pages

A collection page first renders "0 items" as a skeleton and fills in the
real numbers after ~10 seconds. Reading too early once produced a floor of
0 for ~40 collections at once. Wait for real content before reading any
number off a marketplace page.

## 17. Free hosting proves nothing about a project

`pages.dev`, `vercel.app`, `netlify.app`, `github.io` and friends are free,
one-click, and identity-free — a template farm spins one live site per
project. A live site is not a quality signal. Verify the project behind
the domain: who runs it, where else it appears, what the contract says.

## 18. Unrevealed art is a design choice, not a red flag

A sealed/unrevealed placeholder passes the image gate when the web and
social gates pass — it reveals by design. What fails the gate is template
or duplicated art across collections, a failed web/social gate, or a
technically failed fetch.

## 19. Only mint from the official route

Never mint from a route found only in a social post. Follow the project's
own site or docs to the contract and the function, and verify the route
with a clean simulation first. A post can be stale, wrong, or malicious;
the official route is the only one you can check.

## 20. No `totalSupply()` doesn't mean no supply

Some contracts are not enumerable: `totalSupply()` reverts, and the supply
lives in `totalMinted()` instead. Others are the reverse. Probe both
before concluding a pool is empty — and remember that a reverted `eth_call`
probe is not proof the free route is dead; a clean simulation outranks it.

---

*All of these were learned on BSC (chain 56) between October and November
2026, screening real mints. The tools that encode them are in `guard/`; the
data is in `data/`.*
