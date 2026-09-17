# Notes for Claude

## Where you're running

Sessions started from the Claude app via Remote Control ("Jerry's PC") run on
the Windows PC itself, not in a cloud sandbox. From the PC you can reach the
Raspberry Pi ("Navi") directly:

```bash
ssh navi            # host navi.local, user admin, key ~/.ssh/ledpi_ed25519
```

So deploy changes yourself instead of asking the user to.

## Deploying to Navi

Commit and push from the PC, then:

```bash
ssh navi 'cd ~/LED-Controller && git pull -q && systemctl --user restart navi-voice'
ssh navi 'journalctl _SYSTEMD_USER_UNIT=navi-voice.service -o cat -n 20'   # check it says "Listening."
```

The repo is at `~/LED-Controller` on the Pi, with its virtualenv in `.venv_pi`.
On the PC the virtualenv is `.venv_win`. Don't use `.venv`, which is an old macOS
environment.

## Secrets: never commit these

This repo is public. These live only on the Pi in `~/.config/navi/`, as
`chmod 600` files:

- `shutdown_passphrase`, `unlock_passphrase`: spoken passphrases for voice PC
  shutdown and wake. Never write their contents into code, comments, docs,
  commit messages, or logs, not even as examples.
- `web_token`: the web interface token.

Before committing, check that no passphrase word appears in tracked files. Ask
the user for the words if you need them; don't read the files into the
conversation just to check.

## Other gotchas

- When killing processes on the Pi, don't `pkill -f` a pattern that also
  appears in your own ssh command line, because it kills your session. Find the
  PID first (e.g. `ss -ltnp "sport = :8765"`).
- New voice words must be in the Vosk small model's vocabulary, or recognition
  breaks. Check them on the Pi by creating a `KaldiRecognizer` with the word
  list after `vosk.SetLogLevel(0)` and looking for "missing in vocabulary"
  warnings.
- Only one device can control the LED strips at a time. Don't run voice control
  on the PC while Navi is running.
