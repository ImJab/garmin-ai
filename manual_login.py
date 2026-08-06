"""
Run this once, yourself, whenever the saved Garmin session expires.

Everything you type here goes straight from your keyboard to Garmin's
servers - it is never sent to Claude and is never written to disk. Only the
resulting session token gets saved, to tokens/garmin_tokens.json.
"""
import getpass

import garminconnect

TOKENS_DIR = "tokens"


def prompt_mfa():
    return input("Enter the 2FA code Garmin just sent you: ").strip()


def main():
    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password (hidden as you type): ")

    api = garminconnect.Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
    api.login(TOKENS_DIR)
    print("\nLogin successful - session token saved to tokens/garmin_tokens.json")
    print("Next: tell Claude you're done, and it'll push the refreshed token to GitHub.")


if __name__ == "__main__":
    main()
