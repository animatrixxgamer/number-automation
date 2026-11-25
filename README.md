# number-automation

Two small local CLIs for signing in to **your own** messaging accounts on a PC.
One account at a time, numbers typed by hand, credentials stored locally.

| Tool | Runtime | Login model |
| --- | --- | --- |
| [`tg_login.py`](tg_login.py) | Python 3.10+ | Telegram code (or QR), 2FA supported |
| [`wa_login.js`](wa_login.js) | Node 20+ | WhatsApp **linked device** (QR or 8-char pairing code) |

---

# Telegram — `tg_login.py`

Telethon / MTProto. Prompts for your number, sends a login code, keeps the
session on disk so the next run is already signed in.

- Code login, with `r` at the prompt to request a fresh code.
- QR fallback (`q` at the prompt, or at the start) for when the code never
  arrives; auto-refreshes every ~30s.
- Two-step verification (2FA) via a hidden password prompt.
- Wrong/expired codes, flood waits, banned/invalid numbers all handled by name.
- Session saved as `sessions/<label>.session` (dir `700`, file `600`); the
  label is the phone digits unless you pass `--session <label>`. Phone, code,
  password and session string are never printed.

## About SMS codes

Telegram stopped sending SMS login codes to third-party clients on 2023-02-18.
Per [core.telegram.org/api/auth](https://core.telegram.org/api/auth), third-party
apps sign in using "Telegram codes, Fragment codes, email codes, future auth
tokens, QR codes, and passkeys". Telethon's `force_sms` flag is deprecated and
silently ignored, so this tool never relies on SMS.

## Setup

1. Create an app at <https://my.telegram.org/apps> and copy its `api_id` and
   `api_hash`.
2. `python3 -m pip install -r requirements.txt`
3. `cp .env.example .env` and fill in `TG_API_ID` / `TG_API_HASH`
   (or export them in your shell).

## Usage

```bash
python3 tg_login.py                  # session named after the number
python3 tg_login.py --session work   # sessions/work.session
python3 tg_login.py --list           # saved labels and whether each is signed in
python3 tg_login.py --remove work    # delete sessions/work.session (asks first)
```

`--remove` names the session, shows its file and status, and deletes only after
you type `yes`; no credentials are printed. It forgets the login on this PC only
— press `l` in the menu (or remove the PC under Telegram → Settings → Devices)
to end the session for real.

With `--session`, a session that already exists skips the phone prompt; the
number is only asked for when a fresh code login is needed.

```
Your phone number with country code (+...): +15551234567
Log in with [c]ode or [q]R (default c): c
Login code ('r' = new code, 'q' = use QR): 12345
Two-step verification is enabled for this account.   # only with 2FA
Password (hidden):
Signed in as Your Name @username | id 123456789
```

| input | meaning |
| --- | --- |
| the code | submit it |
| `r` | request a fresh login code |
| `q` | switch to QR login |

Already signed in? The menu offers `s` (send a test message to yourself),
`l` (log out this PC) or Enter to quit. Delete the session file — or press `l` —
to forget the login here.

---

# WhatsApp — `wa_login.js`

Baileys / WhatsApp Web multi-device. **WhatsApp has no OTP login for third-party
clients**: the only way in is to link this PC as a device from a phone that is
already signed in.

## Setup

```bash
npm install
```

## Usage

```bash
node wa_login.js                 # or: npm run wa:login
node wa_login.js --session work  # pair a second account, kept apart
node wa_login.js --list          # saved labels and whether each is still paired
node wa_login.js --remove work   # delete wa-sessions/work (asks first)
```

`--remove` shows the label, path and pairing status, then deletes the directory
only after you type `yes`; no credentials are printed. It forgets the link on
this PC only — press `l` in the menu (or remove the device on the phone under
Linked devices) to unlink the device itself.

```
Pair with [q]R code or [p]airing code (default q): p
Your WhatsApp number with country code: 15551234567
```

The number is only asked for when you choose the pairing code; QR mode needs
none.

- **QR** — scan the terminal code: WhatsApp → **Linked devices** → **Link a
  device**. The QR rotates about every 20 seconds; the newest one is printed.
- **Pairing code** — WhatsApp shows an 8-character code; on the phone go to
  **Linked devices** → **Link with phone number** and type it. The code expires,
  so it is re-requested roughly once a minute.

Either way you must approve the link on the phone. After it opens:

| input | meaning |
| --- | --- |
| `s` | send a test message to yourself |
| `n` | send one message to a number you type |
| `l` | log out this PC (removes the linked device) |
| Enter | quit, leaving the PC linked |

Send a single message later without the menu:

```bash
node wa_send.js 15551234567 "hello"
node wa_send.js --session work 15551234567 "hello"
npm run wa:send -- 15551234567 "hello"
```

`--list` shows only labels and status — never keys or numbers. `--remove <label>`
deletes one saved session after a typed `yes` confirmation.

Credentials live in `wa-sessions/<label>/` (default `wa-sessions/main/`,
gitignored) and `--session <label>` keeps several of your own accounts apart.
Delete that directory, or remove the device on the phone, to unlink. The connection reconnects with
backoff; if the device was removed remotely the session is deleted and you
re-pair.

## Limits worth knowing

- Unofficial protocol use is against WhatsApp's Terms; keep it to your own
  number, don't mass-message, and don't run it from a fresh datacenter IP.
- `wa_send.js` sends to one number per invocation — no lists, no rate-limit
  dodging. Bulk messaging from a personal number is what gets numbers banned.
- If you need programmatic business messaging, use Meta's official Cloud API
  instead; it is the only supported path for that.

---

# Scope

Both tools handle one account per run and stay interactive: no number lists, no
loops over numbers, no account creation, no server-side component. `--session
<label>` only names the directory for an account you signed in yourself, so a
few of your own accounts can sit side by side. Credentials stay on the machine
that runs them.

Files that must never be committed: `.env`, `sessions/`, `wa-sessions/` — all
already in `.gitignore`.
