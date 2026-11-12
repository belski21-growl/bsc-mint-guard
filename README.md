# bsc-mint-guard

Field-tested screening tools for BSC NFT mints. Before you spend gas on a
new mint, run the checks that catch template farms, copy-paste traps, and
wrong assumptions — with one cheap read-only call each.

BSC is flooded with template farms: one deployer spinning dozens of
near-identical collections, each with a fresh name and a fresh site. The
collections look different. The bytecode doesn't lie. This repo is the
toolkit that grew out of months of screening those mints on-chain, one
contract at a time.

## What it does

| Tool | What it answers |
|---|---|
| `guard/check_owner.py` | Is this contract's owner a known template-farm deployer, or a factory already seen with another contract? |
| `guard/fingerprint.py` | Which known template family does this bytecode belong to — if any? |
| `guard/fetch_image.py` | What does the actual NFT art look like? (fetches and renders the tokenURI image, including on-chain SVG) |
| `data/deployer-blacklist.json` | Curated list of proven template-farm deployers (12 entries, 400+ collections) |
| `data/factory-registry.json` | Factory owners seen before — a second collection from the same factory is the same template |
| `LESSONS.md` | Twenty field notes: the mistakes that cost real gas, and the rules that prevent them |

No wallet, no signing, no transactions. Everything here is read-only until
you decide to mint — and the minting itself is deliberately not in this repo.

## Quickstart

```bash
git clone https://github.com/belski21-growl/bsc-mint-guard.git
cd bsc-mint-guard
pip install -r requirements.txt

# 1. Screen the deployer (one owner() call + bytecode length/hash)
python3 guard/check_owner.py 0xYourContractAddress
# SKIP 0x... owner=0x11038f4a... code_len=7803 keccak=...

# 2. Fingerprint the bytecode against known templates
python3 guard/fingerprint.py 0xYourContractAddress
# verdict: FAMILY

# 3. Look at the art before you decide anything
python3 guard/fetch_image.py 0xYourContractAddress 1 sample.png
```

A `SKIP` from `check_owner.py` means: same owner as a proven template
farm — do not mint. A `FAMILY` verdict from `fingerprint.py` means: several
known templates share this selector set — run the distinguishing checks in
`guard/templates.json` (read `verify()` first) before doing anything
expensive like grinding a proof-of-work nonce.

## The deployer check, in one paragraph

Most template-farm collections are `Ownable`, so `owner()` returns the
deployer. One `eth_call` for `owner()` (selector `0x8da5cb5b`), one
`eth_getCode` for bytecode length and hash, and you compare against
`data/deployer-blacklist.json`. If the owner is itself a contract (a
factory), the first appearance is recorded in `data/factory-registry.json`;
a second collection from the same factory is the same template, so it is
`SKIP-FACTORY-REPEAT`. `owner()` is a proxy for a fast check, not proof of
the creation transaction — but for `Ownable` templates, owner == deployer,
and it has matched explorer-verified creators every time we checked.

## The wave rule

Several NEW contracts in one wave sharing the SAME owner + the SAME
`code_len` + the SAME metadata host = one template farm. Skip all of them
and blacklist the owner at once. The exception: collections that are still
one project (same brand, site, and socials) — evaluate those normally.

## Requirements

- Python 3.9+
- `curl` (all RPC calls go through curl — BSC endpoints routinely kill
  urllib connections mid-body with `IncompleteRead`)
- `pycryptodome` — exact Keccak-256 (needed for correct EIP-55 checksums
  and selector computation; NIST SHA3 differs from Keccak)
- `pillow`, `cairosvg` — only for `fetch_image.py`

Public BSC RPC endpoints are used by default
(`bsc-dataseed1/2`, `rpc.48.club`, `bsc.publicnode.com`). Set
`MINT_GUARD_FROM` to use your own address as the `from` for `eth_call`
(the default is the zero address; any address works for a read-only call).

## Contributing

Found another template farm? Open a PR adding it to
`data/deployer-blacklist.json` with on-chain evidence: the deployer
address, `code_len`, and at least two collection contracts sharing it.
Found a new template? Add it to `guard/templates.json` following the
existing shape — selectors must be `proven` (seen in a dispatcher, a
simulation, or a successful mint) or `computed` (keccak of a function
name proven to exist), never guessed.

## Disclaimer

This is a curated heuristic, not financial or legal advice. A blacklist
hit means "same owner as a proven template farm" — strong evidence, not a
verdict. A clean screen does not mean a project is safe: always read the
verified source when it exists, check the official site and socials, and
never mint more than you can afford to lose. All deployer and contract
addresses in this repo are public on-chain data.

## License

MIT — see [LICENSE](LICENSE).
