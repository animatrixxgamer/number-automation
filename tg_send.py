#!/usr/bin/env python3
"""Send one message from an already signed-in Telegram session.

    python3 tg_send.py --session work @friend "hello there"
    python3 tg_send.py --session work +15551234567 "hello there"
    python3 tg_send.py +15551234567 "hello there"        # the only saved session

Reuses sessions/<label>.session from tg_login.py and reads TG_API_ID /
TG_API_HASH the same way it does. One recipient per run: run it again for the
next person. There is no number-list mode on purpose.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys

from telethon import TelegramClient, errors

from tg_login import (
    SESSION_DIR,
    get_credentials,
    normalize_phone,
    retry_flood,
    session_label,
    session_state,
)

USAGE_HINT = "Log in once with: python3 tg_login.py --session <label>"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send one Telegram message from one of your saved sessions."
    )
    parser.add_argument(
        "--session",
        metavar="LABEL",
        type=session_label,
        help="which saved session to send from (default: the only one saved)",
    )
    parser.add_argument(
        "recipient",
        help="who to message: @username, or a phone number with country code (+...)",
    )
    parser.add_argument("message", nargs="+", help="text to send")
    return parser.parse_args(argv)


def resolve_session(label: str | None) -> str:
    """Pick the session to use, refusing to guess between several."""
    saved = (
        sorted(path.stem for path in SESSION_DIR.glob("*.session"))
        if SESSION_DIR.is_dir()
        else []
    )
    if label:
        if label not in saved:
            sys.exit(
                f"No saved Telegram session named '{label}' in {SESSION_DIR}/.\n"
                + USAGE_HINT
            )
        return label
    if not saved:
        sys.exit(f"No saved Telegram sessions in {SESSION_DIR}/.\n" + USAGE_HINT)
    if len(saved) > 1:
        sys.exit(
            "More than one session is saved ("
            + ", ".join(saved)
            + "). Choose one with --session <label>."
        )
    return saved[0]


def target(raw: str) -> str:
    """Accept @username or a phone number; nothing else is guessed."""
    value = raw.strip()
    if value.startswith("@"):
        if not re.fullmatch(r"[A-Za-z0-9_]{4,32}", value[1:]):
            sys.exit(f"'{value}' does not look like a Telegram username.")
        return value
    if value and (value.startswith("+") or re.fullmatch(r"[\d\s-]+", value)):
        return normalize_phone(value)
    sys.exit("Give a recipient: @username, or a phone number with country code.")


async def send_once(recipient: str, text: str, label: str) -> None:
    api_id, api_hash = get_credentials()
    path = SESSION_DIR / f"{label}.session"
    if session_state(path) != "signed in":
        sys.exit(
            f"Session '{label}' holds no login ({session_state(path)}).\n"
            + USAGE_HINT
        )

    client = TelegramClient(str(path), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            sys.exit(
                f"Session '{label}' is no longer signed in (revoked from another "
                "device?).\n" + USAGE_HINT
            )
        try:
            sent = await retry_flood(
                lambda: client.send_message(recipient, text), "sending"
            )
        except (
            errors.UsernameInvalidError,
            errors.UsernameNotOccupiedError,
        ):
            sys.exit(f"Telegram has no user named {recipient}.")
        except errors.PhoneNumberInvalidError:
            sys.exit("Telegram rejected that phone number (include the country code).")
        except errors.PeerFloodError:
            sys.exit(
                "Telegram blocked this send as spam. Stop here: sending to numbers "
                "you do not talk to gets this account limited or banned."
            )
        except errors.UserIsBlockedError:
            sys.exit(f"{recipient} has blocked this account.")
        except errors.ChatWriteForbiddenError:
            sys.exit(f"This account is not allowed to write to {recipient}.")
        except errors.RPCError as exc:
            sys.exit(f"Telegram error: {type(exc).__name__}: {exc}")
    finally:
        await client.disconnect()

    print(f"Sent to {recipient} (message id {sent.id}).")


def main() -> None:
    args = parse_args()
    recipient = target(args.recipient)  # cheap check first, before touching disk
    label = resolve_session(args.session)
    asyncio.run(send_once(recipient, " ".join(args.message), label))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
    except errors.RPCError as exc:
        print(f"Telegram error: {type(exc).__name__}: {exc}")
