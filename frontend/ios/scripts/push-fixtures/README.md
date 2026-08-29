# Push fixtures

`xcrun simctl push` delivers a payload through the **full** `UNUserNotificationCenter`
pipeline — categories and action buttons, `interruption-level`, `thread-id`, badge,
`willPresent`, `didReceive`, and the routing chain. Only the APNs leg needs a real
device, so this covers the entire client half with no backend and no phone.

```bash
DEV=$(xcrun simctl list devices booted -j | python3 -c "import json,sys;print(next(d['udid'] for v in json.load(sys.stdin)['devices'].values() for d in v if d['state']=='Booted'))")
xcrun simctl push "$DEV" com.phan.caydex price_alert_crypto.json
```

## What each fixture proves

| Fixture | Checks |
|---|---|
| `ticker_move` | the baseline path — banner, tap, ticker detail |
| `research_complete` | `route: "report"` — reports were NOT routable at all before |
| `earnings_upcoming` / `earnings_result` | earnings copy + the `earnings` thread groups them |
| `insider_trade` / `congress_trade` | `interruption-level: passive` (iOS may batch) |
| `price_alert_crypto` | **the routing regression**: `asset_type: crypto` must open `CryptoDetailView`. The old handler hardcoded `.stock` and showed stock fundamentals for a coin. |
| `unroutable` | no ticker and no report id → lands in the INBOX. This used to be a silent no-op: tappable banner, nothing happened, nothing logged. |
| `ticker_move_markread` | carries `dedup_key` and `badge: 3`. Long-press → **Mark as Read** must decrement the app-icon badge; without the key in the payload that button is a no-op, which is what shipped. |

## Cold launch

Warm-foreground taps work even when the handler is broken — that is why the
cold-launch bug survived manual testing. Test it properly:

```bash
xcrun simctl terminate "$DEV" com.phan.caydex
xcrun simctl push "$DEV" com.phan.caydex ticker_move.json
# tap the banner; the app launches and MUST land on the ticker
```

Both `.onChange(..., initial: true)` flags (ContentView + HomeDashboardView) exist for
exactly this path. A plain `.onChange` fires only on a change *after* first render, and
the route is set before either view exists.
