# Supabase Auth email templates

Supabase, not the backend, sends Caydex's sign-up, password and email-change emails, from templates that live
only in the Supabase dashboard. The dashboard has no history and nothing checks it, which is how
the reset email's wording ended up in the **Confirm signup** template (found 2026-11-26: every new
email/password account was stuck at "confirm your email").

These files are the source of truth. Paste them into the dashboard; never edit the dashboard copy
on its own. `backend/tests/test_auth_email_template_doc_parity.py` pins these FILES (and the docs
that point at them). It cannot read the live dashboard, so a green test says nothing about what
Supabase is sending today. Only the manual check at the bottom does.

## What to paste

| Supabase template | Subject | Body file |
|---|---|---|
| Confirm signup | `Confirm your Caydex email` | `confirm-signup.html` |
| Reset Password | `Reset your Caydex password` | `reset-password.html` |
| Change Email Address | `Confirm your Caydex email change` | `change-email.html` |

Each file's `<title>` repeats its subject, so you can tell them apart at a glance.

## Where to paste it

1. Supabase dashboard → the Caydex project → **Authentication → Emails → Templates**.
2. Open **Confirm signup**:
   - **Subject heading**: paste `Confirm your Caydex email`, exactly.
   - **Message body**: switch to the source (HTML) view, select everything, delete it, and paste
     the whole of `confirm-signup.html`.
   - Save.
3. Open **Reset Password** and do the same with `Reset your Caydex password` and
   `reset-password.html`.
4. Open **Change Email Address** and do the same with `Confirm your Caydex email change` and
   `change-email.html`.
5. Leave **Magic Link**, **Invite user** and **Reauthentication** at their defaults. Nothing in
   the app sends them.
6. Authentication → **Providers → Email**: keep **Secure email change** ON. With it, a change
   needs a code from the current address AND one from the new address, and the app asks for
   both. Turned off, a code would go to the new address only, the app would wait for a second
   code that never comes, and one stolen password would be enough to move the account.
7. Do the manual check below.

The template list opens on Confirm signup, the template the 2026-11-26 incident was pasted into
by mistake. Read the template's name above the editor before you paste.

## What each placeholder does

- `{{ .Token }}` is the one-time code. Its length is the project's **Email OTP Length** setting,
  8 today. The backend accepts 6 to 12 digits, and the app's copy never states a length, so
  changing the setting needs no code change. **Email OTP Expiration** sets how long the code works,
  and the emails say only that it "expires shortly".
- `{{ .NewEmail }}` is used only in Change Email Address: the address the account is moving to.
  Supabase sends that template twice for one change, to the current address and to the new one,
  each with its own `{{ .Token }}`, so its wording fits both readers. It never shows the CURRENT
  address (`{{ .Email }}`): a mistyped new address would otherwise tell a stranger whose account
  it is.
- `{{ .TokenHash }}` is used only in Confirm signup. It goes into the link
  `https://caydexinvest.com/auth/confirm?token_hash={{ .TokenHash }}&type=signup` (written
  `&amp;type=signup` in the HTML source, which is correct). That page is for app versions
  without a code screen (1.0 to 1.03). Opening the link shows a **Confirm my email** button and
  uses nothing up, so a mail scanner that opens links cannot spend the token. Only pressing the
  button confirms the address, on the server. The page then says to go back to the app and sign in,
  and to use Forgot password if the password doesn't work. The link confirms the address with the
  password it was FIRST registered with. Someone who registered again with a new password must
  use the first one or Forgot password. The code screen in the app sets the typed password
  instead. Each new email replaces the earlier ones, so only the newest email's link works; the
  page's "expired" message says so.
  The backend masks the token in its own logs. Railway's edge may still log the full link, which is
  one more reason the token works once and expires.

The link is written out to `caydexinvest.com` instead of `{{ .SiteURL }}` because the page is
served by our backend on that domain. The Site URL is a separate setting, and it should also be
`https://caydexinvest.com`.

## Never use `{{ .ConfirmationURL }}`

Supabase's default templates use `{{ .ConfirmationURL }}`. For sign-up that link confirms through
Supabase's implicit flow, which ends by putting a live Supabase session (an access token and a
refresh token) in the browser's address bar and history on caydexinvest.com.
`/api/v1/auth/session-exchange` turns any such token into a full Caydex session, so anyone who
could read that browser history could take the account. That is why neither template uses it.
In the Reset Password and Change Email Address templates it would also be a dead end: the app
asks for a code, not a link.

Keep the two templates' wording separate too. The sign-up email must never say "reset" or
"your password won't change". The reset email must never ask the reader to confirm anything.

## Manual check: after ANY change on the Emails screen

This includes a template, the SMTP settings, the OTP length or the sender. Do both steps, every
time:

1. **Register a fresh address in the app** (a plus-address of your own works). The email's subject
   must be `Confirm your Caydex email`, and it must show a code AND a "Confirm my email" link.
   Enter the code in the app, or tap the link on an older app version. Either way you end up
   signed in, or you are told to sign in.
2. **Forgot password? with that same address.** The subject must be `Reset your Caydex password`,
   and the email must show a code and no link. The code must work in the app.
3. **Change the email** (Settings → Change Email) to a second plus-address of your own. Two emails
   with the subject `Confirm your Caydex email change` must arrive, one at each address, each with
   its own code and no link. Enter both; Profile must then show the new address.

Then delete the test account (Settings → Delete Account). These steps create real production
accounts.

## Local Supabase stack (Supabase CLI)

`supabase/config.toml` can load the same files. Copy them first and point the link at your local
backend (for example `http://127.0.0.1:8000/auth/confirm?...`). Never edit the copies here for that.

```toml
[auth.email]
enable_confirmations = true
otp_length = 8

[auth.email.template.confirmation]
subject = "Confirm your Caydex email"
content_path = "./templates/confirm-signup.html"

[auth.email.template.recovery]
subject = "Reset your Caydex password"
content_path = "./templates/reset-password.html"

[auth.email.template.email_change]
subject = "Confirm your Caydex email change"
content_path = "./templates/change-email.html"
```

`double_confirm_changes = true` under `[auth.email]` is the local name of Secure email change. It
is the CLI's default; keep it.

## Which code depends on these emails

- `POST /api/v1/auth/register` and `POST /api/v1/auth/resend-confirmation` make Supabase send
  **Confirm signup**.
- `POST /api/v1/auth/confirm-email` (the app's code screen) verifies its code as type `signup`.
- `GET /auth/confirm` shows the button page for its link, and `POST /auth/confirm` verifies it.
- `POST /api/v1/auth/forgot-password` makes Supabase send **Reset Password**.
  `POST /api/v1/auth/reset-password`, `POST /api/v1/auth/set-password` and (for an account with no
  password) `POST /api/v1/auth/change-email` verify its code as type `recovery`.
- `POST /api/v1/auth/change-email` makes Supabase send **Change Email Address**, once to each
  address. `POST /api/v1/auth/change-email/confirm` verifies both codes as type `email_change`.

When something fails after a code was verified, the code is already used up. The backend then
answers `AUTH_CONFIRMED_SIGN_IN_FAILED` on confirm-email (the address is confirmed: sign in, or
use Forgot password), and `AUTH_CODE_USED` or `AUTH_PASSWORD_REJECTED` on reset- and
set-password (send a new code). Every new password must pass Supabase's own rule: 8 or more
characters, at most 72 bytes, and an ASCII lowercase letter, an ASCII uppercase letter, a digit
and a symbol from Supabase's list. Reset- and set-password check it before the code is used.
SYSTEM_DESIGN_GUIDELINES §9.1 has the details.

The full table of Supabase Auth settings these flows need (Confirm email, OTP length and expiry,
Site URL, Redirect URLs, rate limits, IP Address Forwarding, the Hide My Email relay) is in the
launch checklist, §5b(a). That file is kept out of git.
