# number-automation

Two small local CLIs for signing in to **your own** messaging accounts on a PC.
One account at a time, numbers typed by hand, credentials stored locally.

| Tool | Runtime | Login model |
| --- | --- | --- |
| [`tg_login.py`](tg_login.py) | Python 3.10+ | Telegram code (or QR), 2FA supported |
| [`wa_login.js`](wa_login.js) | Node 20+ | WhatsApp **linked device** (QR or 8-char pairing code) |

## Where it runs

Both tools are plain command-line programs, so you can run them on any machine
you control — your laptop, a home server, a Raspberry Pi or a VPS. All they need
is the runtime, a terminal you can type into, and a directory to keep the saved
sessions in. There is no hosted backend, no database, no open port, no bot token
and no account on our side: the login code or QR still comes from
Telegram/WhatsApp, and the session stays in `sessions/` or `wa-sessions/` on the
machine that ran the tool.

A few things that matter when it is a server rather than your own desktop:

- Run it in an interactive shell (SSH, `tmux` or `screen`). Both tools ask
  questions and print a QR code; closing the window mid-login cancels it.
- Start them from the same directory every time. Session paths are relative to
  the current directory, so `cd` into the checkout first or the next run will
  not find the login.
- Keep the session directories private (they are already `700`/`600`) and out
  of any backup or sync you do not trust — these files *are* the login.
- Telegram needs outbound network access to Telegram; WhatsApp needs to reach
  WhatsApp Web. If the host blocks them, the tools cannot sign in.
- For WhatsApp, link from a familiar network first. A brand-new datacenter IP
  is exactly the kind of link the phone may refuse.

There are **no `/start` or other bot commands** in these tools — they are not
Telegram or WhatsApp bots, and none is needed. "Starting" one is just running
the command in the next section; everything else is the prompts you answer.

## Start commands

One-time setup (in the checkout):

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # Telegram
npm ci                                                               # WhatsApp
cp .env.example .env    # then fill in TG_API_ID / TG_API_HASH
```

Start a tool:

```bash
.venv/bin/python tg_login.py                  # Telegram, session named after the number
.venv/bin/python tg_login.py --session work   # Telegram, second account
node wa_login.js                              # WhatsApp, session 'main'
node wa_login.js --session work               # WhatsApp, second account
```

If you installed Python packages globally instead of in a venv, use
`python3 tg_login.py` in place of `.venv/bin/python tg_login.py`. From another
directory, either `cd` into the checkout or wrap the call:

```bash
(cd /opt/number-automation && .venv/bin/python tg_login.py --session work)
(cd /opt/number-automation && node wa_login.js --list)
```

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
python3 tg_login.py --logout work    # end it on Telegram, then delete it (asks first)
```

`--remove` names the session, shows its file and status, and deletes only after
you type `yes`; no credentials are printed. It forgets the login on this PC only
— press `l` in the menu (or remove the PC under Telegram → Settings → Devices)
to end the session for real.

`--logout <label>` does both in one step: it connects with that session, calls
Telegram's `log_out`, so the PC disappears from Settings → Devices, and only then
deletes `sessions/<label>.session`. It asks for a typed `yes` first, prints no
keys, and if Telegram refuses the logout the local file is kept so you can try
again. An already-dead session is simply removed.

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
node wa_login.js --logout work   # unlink the device, then delete it (asks first)
```

`--remove` shows the label, path and pairing status, then deletes the directory
only after you type `yes`; no credentials are printed. It forgets the link on
this PC only — press `l` in the menu (or remove the device on the phone under
Linked devices) to unlink the device itself.

`--logout <label>` unlinks for real: it connects with the saved session, calls
the WhatsApp logout that removes this PC from **Linked devices** on the phone,
and only then deletes the directory. It asks for a typed `yes` first, prints no
credentials, and keeps the files if the unlink fails (so you can retry or remove
the device on the phone) instead of leaving a linked device behind.

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
deletes one saved session after a typed `yes` confirmation; `--logout <label>`
unlinks the device on WhatsApp first, then deletes it.

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
loops over numbers, no account creation. `--session <label>` only names the
directory for an account you signed in yourself, so a few of your own accounts
can sit side by side. Credentials stay on the machine that runs them.

They are not bots or services. Nothing listens on a port, nothing answers
`/start`, and there is no webhook or scheduled job: each run is one manual
login, message or logout. You can keep the checkout on any host you like, but
it has to be driven from a terminal.

Files that must never be committed: `.env`, `sessions/`, `wa-sessions/` — all
already in `.gitignore`.
