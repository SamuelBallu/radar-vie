"""Choose the sign-in password and store its hash in Vercel.

Run from the project folder, in a terminal:  uv run python scripts/set_password.py
The password is typed without being shown and never leaves this computer: only its
Argon2 hash is sent to Vercel (APP_PASSWORD_HASH, production). Every signed-in device
is signed out, then the app is redeployed so the new hash takes effect.
"""

import getpass
import subprocess
import sys

from argon2 import PasswordHasher

MIN_LENGTH = 10


def main() -> None:
    password = getpass.getpass("New password: ")
    if len(password) < MIN_LENGTH:
        sys.exit(f"Use at least {MIN_LENGTH} characters.")
    if getpass.getpass("Same password again: ") != password:
        sys.exit("The two entries differ. Nothing was changed.")
    hashed = PasswordHasher().hash(password)

    subprocess.run(["vercel", "env", "rm", "APP_PASSWORD_HASH", "production", "--yes"], capture_output=True)
    added = subprocess.run(["vercel", "env", "add", "APP_PASSWORD_HASH", "production"], input=hashed, text=True)
    if added.returncode != 0:
        sys.exit("Vercel refused the new value. Is this folder linked (vercel link)?")
    print("Password saved. Redeploying so it takes effect...")
    subprocess.run(["vercel", "deploy", "--prod", "--yes"], check=True)
    print("Done. Sign in on your phone with the new password.")


if __name__ == "__main__":
    main()
