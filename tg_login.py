#!/usr/bin/env python3
"""Log in to your own Telegram account from this PC (Telethon / MTProto).

Flow
----
1. Reads TG_API_ID / TG_API_HASH from the environment or .env
2. Asks for your phone number (typed by hand, one account per run)
3. Offers two ways in:
     [c]ode  - Telegram sends the login code to another Telegram app / email /
               Fragment, and you type it here
     [q]R    - you scan a QR code with an already logged-in Telegram app
4. Handles 2FA passwords, code resend, code expiry and flood waits
       5. Saves the session to sessions/<label>.session (default: your phone
          digits) so you stay logged in; --session <label> names it yourself


Only one account is handled per run: no number list, no loops over numbers,
no account creation.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import re
import sqlite3
import sys
from pathlib import Path

try:
    import qrcode
except ImportError:  # QR rendering is optional; the tg:// link still works
    qrcode = None

from telethon import TelegramClient, errors

API_PORTAL = "https://my.telegram.org/apps"
SESSION_DIR = Path("sessions")
QR_TIMEOUT = 30
PHONE_PROMPT = "Your phone number with country code (+...): "

# Telegram's own docs: third-party clients no longer receive SMS codes. They
# sign in with Telegram codes, Fragment codes, email codes, QR codes, future
# auth tokens or passkeys, so force_sms is deprecated and silently ignored.
SMS_NOTE = (
    "Note: third-party clients do not receive SMS codes any more. The code "
    "arrives in your Telegram app (or by email/Fragment), or use the QR login."
)


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env reader; real environment variables win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def get_credentials() -> tuple[int, str]:
    load_dotenv()
    api_id_raw = os.environ.get("TG_API_ID", "").strip()
    api_hash = os.environ.get("TG_API_HASH", "").strip()

    problems = []
    if not api_id_raw:
        problems.append("TG_API_ID is missing")
    elif not api_id_raw.isdigit():
        problems.append("TG_API_ID must be a number")
    if not api_hash:
        problems.append("TG_API_HASH is missing")
    elif not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
        problems.append("TG_API_HASH must be 32 hex characters")

    if problems:
        sys.exit(
            "Cannot start:\n  - "
            + "\n  - ".join(problems)
            + f"\n\nCreate your own app at {API_PORTAL} and put its api_id / "
            "api_hash in .env (see .env.example).\nNever commit .env or share "
            "these values."
        )
    return int(api_id_raw), api_hash


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"[^\d]", "", raw)
    if digits.startswith("00"):  # international prefix instead of '+'
        digits = digits[2:]
    if not 6 <= len(digits) <= 15:
        sys.exit("That does not look like a phone number with a country code.")
    return "+" + digits


def session_label(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", value):
        raise argparse.ArgumentTypeError("use 1-40 letters, digits, '-' or '_'")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Log in to one of your own Telegram accounts on this PC."
    )
    parser.add_argument(
        "--session",
        metavar="LABEL",
        type=session_label,
        help="name for this account's session file (default: the phone digits); "
        "use a different label per account, e.g. --session work",
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--list",
        action="store_true",
        help="show the saved session labels and whether each holds a login",
    )
    actions.add_argument(
        "--remove",
        metavar="LABEL",
        type=session_label,
        help="delete one saved session after you confirm; nothing secret is printed",
    )
    actions.add_argument(
        "--logout",
        metavar="LABEL",
        type=session_label,
        help="end one session on Telegram (removes this PC from Devices), then "
        "delete the saved file; asks first and prints nothing secret",
    )
    return parser.parse_args(argv)


def session_path_for(name: str) -> Path:
    SESSION_DIR.mkdir(mode=0o700, exist_ok=True)
    try:
        SESSION_DIR.chmod(0o700)  # tighten it even if it already existed
    except OSError:
        pass
    return SESSION_DIR / f"{name}.session"


def session_state(path: Path) -> str:
    """Read the stored authorisation key without touching or showing it."""
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "select count(*) from sessions where auth_key is not null"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return "unreadable file"
    return "signed in" if row and row[0] else "no login stored"


def list_sessions() -> None:
    files = sorted(SESSION_DIR.glob("*.session")) if SESSION_DIR.is_dir() else []
    if not files:
        print(f"No saved Telegram sessions in {SESSION_DIR}/.")
        print("Log in once with: python3 tg_login.py --session <label>")
        return

    width = max(len(path.stem) for path in files)
    print(f"Telegram sessions in {SESSION_DIR}/:")
    for path in files:
        print(f"  {path.stem:<{width}}  {session_state(path)}")
    print(
        "\nReuse one with --session <label>, drop one with --remove <label>, or "
        "end it on Telegram and drop it with --logout <label>. Only labels and "
        "status are shown; session keys are never printed. A session revoked "
        "from another device can still look signed in here until it is used."
    )


def remove_session(label: str) -> None:
    """Delete one saved session, but only after an explicit confirmation."""
    path = SESSION_DIR / f"{label}.session"
    journal = Path(f"{path}-journal")
    # Journal first, so the main file only goes after the sidecar succeeded.
    files = [item for item in (journal, path) if item.exists()]
    if not files:
        print(f"No saved Telegram session named '{label}' in {SESSION_DIR}/.")
        print("See what is saved with: python3 tg_login.py --list")
        return

    status = session_state(path) if path.exists() else "leftover journal only"
    print(f"Session '{label}' ({status}) will be deleted:")
    for item in files:
        print(f"  {item}")
    print(
        "This only forgets the login on this PC. Telegram keeps the device "
        "listed until you log it out there: press 'l' in the menu first, or "
        "remove this PC on the phone under Settings -> Devices."
    )
    if prompt(f"Delete '{label}'? type 'yes': ").lower() != "yes":
        print("Nothing deleted.")
        return

    for item in files:
        try:
            item.unlink()
        except OSError as exc:
            sys.exit(f"Could not delete {item}: {exc}")
    print(f"Deleted session '{label}'. No login data was printed.")


async def logout_session(label: str) -> None:
    """End one session on Telegram itself, then delete the local copy."""
    path = SESSION_DIR / f"{label}.session"
    journal = Path(f"{path}-journal")
    if not path.exists():
        print(f"No saved Telegram session named '{label}' in {SESSION_DIR}/.")
        print("See what is saved with: python3 tg_login.py --list")
        return

    api_id, api_hash = get_credentials()
    status = session_state(path)
    print(f"Session '{label}' ({status}) will be logged out and deleted:")
    print(f"  {path}")
    print(
        "This ends the session on Telegram itself: this PC disappears from "
        "Settings -> Devices, and this login stops working anywhere it is used."
    )
    if prompt(f"Log out and delete '{label}'? type 'yes': ").lower() != "yes":
        print("Nothing deleted.")
        return

    client = TelegramClient(str(path), api_id, api_hash)
    try:
        await client.connect()
    except (sqlite3.Error, OSError) as exc:
        sys.exit(f"Could not open the saved session: {exc}")

    try:
        if await client.is_user_authorized():
            await client.log_out()  # Telethon deletes its own session file here
            print("Logged out on Telegram; this PC is no longer a listed device.")
        else:
            print("This session was already not signed in; nothing to end on Telegram.")
    except errors.AuthKeyUnregisteredError:
        print("Telegram says this session was already gone; nothing to end there.")
    except errors.FloodWaitError as exc:
        await sleep_flood(exc, "logging out")
        print("Nothing deleted; run --logout again after the wait.")
        return
    except errors.RPCError as exc:
        print(f"Telegram error: {type(exc).__name__}: {exc}")
        print("Nothing deleted; the session on this PC is unchanged.")
        return
    finally:
        await client.disconnect()

    # log_out() usually removed the file already; drop anything left behind.
    for item in (journal, path):
        if item.exists():
            try:
                item.unlink()
            except OSError as exc:
                sys.exit(f"Could not delete {item}: {exc}")
    print(f"Deleted session '{label}'. No login data was printed.")


def secure_session_file(path: Path) -> None:
    """Keep session credentials private to this user."""
    for candidate in (path, Path(f"{path}-journal")):
        if candidate.exists():
            try:
                candidate.chmod(0o600)
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# ui helpers
# --------------------------------------------------------------------------- #
def prompt(text: str) -> str:
    try:
        return input(text).strip()
    except EOFError:
        sys.exit("\nAborted.")


def render_qr(url: str) -> None:
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="")
    print("Scan with Telegram -> Settings -> Devices -> Link Desktop Device\n")
    if qrcode is None:
        print("(install 'qrcode' to draw the code in the terminal)")
    else:
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    print(f"or open this link on the phone that is already signed in:\n  {url}\n")


async def sleep_flood(exc: errors.FloodWaitError, what: str) -> None:
    wait = int(getattr(exc, "seconds", 0) or 0)
    print(f"Telegram is rate limiting {what}. Waiting {wait}s (Ctrl-C to stop).")
    await asyncio.sleep(wait + 1)


async def retry_flood(call, what: str):
    """Call again after any flood wait Telegram asks for."""
    while True:
        try:
            return await call()
        except errors.FloodWaitError as exc:
            await sleep_flood(exc, what)


def describe_me(me) -> str:
    name = " ".join(part for part in (me.first_name, me.last_name) if part)
    handle = f"@{me.username}" if me.username else "(no username)"
    return f"{name or '(no name)'} {handle} | id {me.id}"


# --------------------------------------------------------------------------- #
# login flows
# --------------------------------------------------------------------------- #
async def ask_password(client: TelegramClient) -> None:
    """Two-step verification. Re-prompts on a wrong password."""
    print("Two-step verification is enabled for this account.")
    while True:
        password = getpass.getpass("Password (hidden): ")
        if not password:
            print("Empty password, try again.")
            continue
        try:
            await client.sign_in(password=password)
            return
        except errors.PasswordHashInvalidError:
            print("Wrong password.")
        except errors.FloodWaitError as exc:
            await sleep_flood(exc, "password attempts")


async def code_login(client: TelegramClient, phone: str) -> bool:
    try:
        sent = await retry_flood(
            lambda: client.send_code_request(phone), "login code requests"
        )
    except errors.PhoneNumberInvalidError:
        sys.exit("Telegram rejected that phone number (include the country code).")
    except errors.PhoneNumberBannedError:
        sys.exit("This number is banned from Telegram.")
    except errors.ApiIdInvalidError:
        sys.exit(f"api_id / api_hash rejected. Check them at {API_PORTAL}")

    print(SMS_NOTE)
    phone_code_hash = sent.phone_code_hash

    while True:
        code = prompt("Login code ('r' = new code, 'q' = use QR): ")
        if not code:
            continue
        if code.lower() == "q":
            return await qr_login(client)
        if code.lower() == "r":
            sent = await retry_flood(
                lambda: client.send_code_request(phone), "login code requests"
            )
            phone_code_hash = sent.phone_code_hash
            print("A new code was requested; nothing is printed here on purpose.")
            continue

        try:
            await client.sign_in(
                phone=phone, code=code, phone_code_hash=phone_code_hash
            )
            return True
        except errors.SessionPasswordNeededError:
            await ask_password(client)
            return True
        except errors.PhoneCodeInvalidError:
            print("That code is wrong. Try again.")
        except errors.PhoneCodeExpiredError:
            # Keep the hash: it is still what a resend needs.
            print("That code expired. Request a new one with 'r'.")
        except errors.PhoneCodeEmptyError:
            print("Enter the code you received.")
        except errors.FloodWaitError as exc:
            await sleep_flood(exc, "sign-in attempts")


async def qr_login(client: TelegramClient) -> bool:
    """QR login, refreshed every ~30s until scanned or cancelled."""
    print("QR login: you need a phone that is already signed in to Telegram.")
    qr = await retry_flood(client.qr_login, "QR login")
    while True:
        render_qr(qr.url)
        try:
            await qr.wait(timeout=QR_TIMEOUT)
            return True
        except errors.SessionPasswordNeededError:
            await ask_password(client)
            return True
        except (asyncio.TimeoutError, errors.TimeoutError):
            await retry_flood(qr.recreate, "QR refresh")


# --------------------------------------------------------------------------- #
# already signed in
# --------------------------------------------------------------------------- #
async def signed_in_menu(client: TelegramClient, path: Path) -> None:
    while True:
        me = await client.get_me()
        print(f"\nSigned in as {describe_me(me)}")
        print(f"Session {path} keeps you logged in; just run this again.\n")
        print("  [s] send a test message to yourself")
        print("  [l] log out of this PC (invalidates the session)")
        print("  [Enter] quit")
        choice = prompt("> ").lower()

        if choice in ("", "q", "quit"):
            print("Bye.")
            return
        if choice == "s":
            await client.send_message("me", "Test message from tg_login.py on my PC.")
            print("Sent to your Saved Messages.")
        elif choice == "l":
            if prompt("Log out this session? type 'yes': ").lower() == "yes":
                await client.log_out()
                print("Logged out; the stored session is no longer valid.")
                return
        else:
            print("Unknown choice.")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
async def main() -> None:
    args = parse_args()
    if args.list:
        list_sessions()
        return
    if args.remove:
        remove_session(args.remove)
        return
    if args.logout:
        await logout_session(args.logout)
        return

    api_id, api_hash = get_credentials()

    phone = None
    if args.session:
        path = session_path_for(args.session)
    else:
        phone = normalize_phone(prompt(PHONE_PROMPT))
        path = session_path_for(re.sub(r"[^\d]", "", phone))

    client = TelegramClient(
        str(path),
        api_id,
        api_hash,
        device_model="PC",
        system_version="Linux",
        app_version="tg-login 1.0",
    )

    await client.connect()
    try:
        if await client.is_user_authorized():
            await signed_in_menu(client, path)
            return

        # QR needs no number, so with --session it is only asked for a code login.
        if phone is None:
            phone = normalize_phone(prompt(PHONE_PROMPT))

        choice = prompt("Log in with [c]ode or [q]R (default c): ").lower()
        ok = (
            await qr_login(client)
            if choice.startswith("q")
            else await code_login(client, phone)
        )

        if ok and await client.is_user_authorized():
            secure_session_file(path)
            print(f"\nSigned in as {describe_me(await client.get_me())}")
            print(f"Session saved to {path} (chmod 600); the next run stays logged in.")
        else:
            print("Login did not complete.")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
    except errors.RPCError as exc:
        print(f"Telegram error: {type(exc).__name__}: {exc}")
