#!/usr/bin/env python3
"""
One-time QR login for the Smart Life cloud backend.

Run once (and again only if the session is revoked):

    ./venv/bin/python3 tuya_auth.py

Your User Code is in the Smart Life / Tuya Smart app:
Me -> gear icon -> Account and Security -> User Code.

The resulting session is written to `sharing_token.json`; the widget refreshes
its tokens automatically from then on.
"""
import sys

import qrcode

from tuya_sharing_api import (
    SESSION_FILE,
    await_login,
    get_api,
    load_session,
    qr_payload,
    query_devices,
    request_qr,
)


def print_qr(payload):
    qr = qrcode.QRCode(border=1)
    qr.add_data(payload)
    qr.make(fit=True)
    qr.print_ascii(out=sys.stdout, invert=True)


def verify(session):
    """Sanity-check the new session by listing the account's devices."""
    api = get_api()
    print(f"Logged in (uid {session['token_info']['uid']}, endpoint {session['endpoint']})")

    response = api.get("/v1.0/m/life/users/homes")
    homes = response.get("result", []) if response.get("success") else []
    if not homes:
        print("Warning: no homes returned for this account.")
        return

    for home in homes:
        devices = api.get(
            "/v1.0/m/life/ha/home/devices", {"homeId": str(home["ownerId"])}
        )
        print(f"\nHome: {home['name']}")
        for device in devices.get("result", []):
            codes = ", ".join(
                sorted(s["code"] for s in device.get("status", []))
            ) or "no status"
            print(f"  {device['id']}  {device.get('name', '')}")
            print(f"      {codes}")


def main():
    if load_session() and "--force" not in sys.argv:
        print(f"Already authenticated ({SESSION_FILE.name}). Re-run with --force to log in again.")
        return 0

    user_code = input("Smart Life User Code: ").strip()
    if not user_code:
        print("User Code is required.", file=sys.stderr)
        return 1

    control, qr_token = request_qr(user_code)

    print("\nOpen the Smart Life app -> '+' (top right) -> Scan, and scan this code:\n")
    print_qr(qr_payload(qr_token))
    print("Waiting for confirmation in the app...\n")

    session = await_login(control, qr_token, user_code)
    verify(session)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Login failed: {e}", file=sys.stderr)
        sys.exit(1)
