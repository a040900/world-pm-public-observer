# World MCP read-only verification R0

Verdict: WORLD_CONNECTOR_READONLY_MARKET_AND_INDICATIVE_ORDERBOOK_CONFIRMED_AT_RECEIPT_TIME.

This is a bounded diagnostic, not an authority adjudication, an edge sample, or execution authorization. No wallet credential was listed or used. No order, signature, simulation, submission, or broadcast was requested.

## Evidence and exact calls

Raw MCP envelopes and inputs are preserved in `evidence/world-mcp-readonly-verification-20261001-r0/mcp-receipts.json`. The callable tools in this Codex conversation were `mcp__codex_apps__world_xyz_discover_plugins`, `mcp__codex_apps__world_xyz_get_contract`, and `mcp__codex_apps__world_xyz_use_plugin`. This was not a direct unauthenticated HTTP call to the user's MCP URL. Connector credential transport and PayBox internal routing were not observed.

1. `discover_plugins({query:"World prediction markets",limit:2})`: official World plugin, enabled=true, contract_uri=paybox://plugins/world.
2. `get_contract({contract_uri:"paybox://plugins/world"})`: deployed contract annotates get_events/orderbook readOnlyHint=true and destructiveHint=false. Buy/change/redeem are state-changing; none were invoked.
3. `use_plugin({plugin_id:"world",tool_id:"world_get_events",input:{series_ticker:"WXBTC5M",limit:2,with_nested_markets:true}})` at 2026-09-30 19:37:00 UTC.
4. `use_plugin({plugin_id:"world",tool_id:"world_orderbook",input:{id:"WXBTC5M-26SEP301935-5"}})` at 19:37:18 UTC.
5. Same orderbook tool with `{id:"2CYxQFmsPfq6vJY9Q2PMrQ2X74VjDjAbEmkBZbU54eAr",by_mint:true}`. Response timestamp 19:38:51 UTC, same mints, YES=null, NO bid=null and ask=0.01085305. Prices and availability changed; no persistence or fill guarantee is established.

## Observed market and first orderbook

Ticker WXBTC5M-26SEP301935-5; active window 19:35-19:40 UTC, October 1 03:35-03:40 Asia/Taipei. Receipt 19:37:18 UTC was inside the window.

| Field | YES | NO |
| --- | --- | --- |
| Bid | 0.66335431 | 0.27664564 |
| Ask | 0.72335438 | 0.33664566 |
| bidQty and askQty | 13.8273 | 29.710878 |
| Mint | 2CYxQFmsPfq6vJY9Q2PMrQ2X74VjDjAbEmkBZbU54eAr | GFtvPpAm6e8XtJ2885ANqaWQrRQhstS4CpinKFhmsKJF |

Both sides carry slotId=452060642 and ts=1790797038 (19:37:18 UTC). Quantities are reported fields, not independently verified firm liquidity. Event-list prices and later orderbook prices differ and are not assumed to be synchronized.

## Independent identity check

`onchain-crosscheck.json` contains the public Solana getProgramAccounts request/response, using the existing repository parser, prediCt owner, 320-byte account size, start-time memcmp offset 258. No recent-signature discovery was used. The query ran at approximately 19:40:34 UTC against the explicitly pinned earlier window, not a newly discovered current window.

Decoded ledger `2rZkD42LonjCzwS3ENBz66y7ofmcg73CpruQ56HrZfqv`, CASH mint, YES mint, NO mint, startTs=1790796900, endTs=1790797200 all match the MCP market. This confirms identity and window independently; it does not independently verify off-chain prices or sizes. RPC only: getProgramAccounts.

Luna independent review of the first four MCP receipts agreed with the narrow verdict. It explicitly rejected stronger claims of public keyless HTTP access, fillability, DFlow independence, OAuth scope, or trading capability.

## Limits and handoff

- Read-only data access succeeded through this conversation's installed connector without manually supplying a DFlow key or website JWT. This does not prove unauthenticated public MCP access or that PayBox internally needs no key/token.
- No outAmount, minOutAmount, routePlan, orderToken, venue, or serialized transaction was obtained. Fillable quote/build remains unconfirmed. No new alpha conclusion is drawn.
- No direct observation proves MCP avoids DFlow or sponsorship_unavailable.
- Another chat's tool availability/session may differ. Its conclusion cannot be compared without its exact claim and evidence; model identity alone does not resolve the discrepancy.
- Authority decision and research protocol are unchanged. Stop after this verification; a reviewer may use the receipts to challenge either answer.
