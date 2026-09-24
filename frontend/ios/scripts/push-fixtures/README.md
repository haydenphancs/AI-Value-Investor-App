# Push fixtures

`xcrun simctl push` delivers a payload through the **full** `UNUserNotificationCenter`
pipeline — categories and action buttons, `interruption-level`, `thread-id`, badge,
`willPresent`, `didReceive`, and the routing chain. Only the APNs leg needs a real
device, so this covers the entire client half with no backend and no phone.

```bash
DEV=$(xcrun simctl list devices booted -j | python3 -c "import json,sys;print(next(d['udid'] for v in json.load(sys.stdin)['devices'].values() for d in v if d['state']=='Booted'))")
xcrun simctl push "$DEV" com.phan.caydex price_alert_crypto.json
```

## What every tap should do

**Every tap opens the notification's DETAIL screen first** — the same screen Tracking → Alerts
opens for a row: icon, title, the full body, "Received …", then the destination rows
("Open NVDA", "Read the news", …). Nothing opens the ticker, report or investor screen until
the user picks one of those rows. That applies to the lock screen, Notification Center, a
banner (backgrounded, killed or foreground) and the "View" action alike.

Every fixture carries a `dedup_key`, as every live sender does. The detail screen shows the
payload at once and then fetches the full row by that key
(`GET /users/me/notifications/lookup`). A fixture key matches no row in your database, so the
pushed copy simply stays — which is also exactly what a backend without the lookup route does.
To see the swap, set `dedup_key` to a real key for the signed-in simulator account.

## What each fixture proves

| Fixture | Checks |
|---|---|
| `ticker_move` | the baseline path — banner, tap, DETAIL, then "Open NVDA" → ticker screen |
| `ticker_move_long_body` | the body arrives at the 180-char banner cut ("…"), as APNs gets it; the full text only comes from the lookup |
| `research_complete` | `route: "report"` — the detail's first row is "Read the full report", which opens the REPORT (with its persona), not the ticker |
| `earnings_upcoming` / `earnings_result` | earnings copy + the `earnings` thread groups them; detail offers "See the numbers" |
| `insider_trade` / `congress_trade` | `interruption-level: passive` (iOS may batch); detail offers the Holders sub-tab |
| `price_alert_crypto` | **the routing regression**: "Open BTC" must open `CryptoDetailView`. The old handler hardcoded `.stock` and showed stock fundamentals for a coin. |
| `unroutable` | no ticker and no report id → a detail with NO destination rows. This used to be a silent no-op: tappable banner, nothing happened, nothing logged. |
| `ticker_move_markread` | `badge: 3`. Long-press → **Mark as Read** must decrement the app-icon badge WITHOUT opening the app; without the key in the payload that button is a no-op, which is what shipped. |

## Cold launch

Warm-foreground taps work even when the handler is broken — that is why the
cold-launch bug survived manual testing. Test it properly:

```bash
xcrun simctl terminate "$DEV" com.phan.caydex
xcrun simctl push "$DEV" com.phan.caydex ticker_move.json
# tap the banner; the app launches and MUST land on the notification's detail
```

`ContentView`'s `.onChange(of: appState.pendingPushNotification, initial: true)` exists for
exactly this path. A plain `.onChange` fires only on a change *after* first render, and the
notification is parked before any view exists.
