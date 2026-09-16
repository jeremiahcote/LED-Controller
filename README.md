# LED-Controller

Controls the two Bluetooth LED strips in my room from macOS, Windows, and a
Raspberry Pi ("Navi") that listens for voice commands.

| Strip | Controller | Address | Voice name |
|---|---|---|---|
| LED 1 | QHM-S931 | `36:46:3F:08:93:13` | bed lights |
| LED 2 | MELK-OA10 (LotusLight X) | `BE:69:ED:24:E6:06` | wall lights |

The addresses are the Windows/Linux ones. macOS uses its own CoreBluetooth
identifiers instead (see `LEDControllerMacOS.py`).

## Files

| File | What it is |
|---|---|
| `LEDControllerMacOS.py`, `LEDControllerGUIMacOS.py` | Original macOS script and GUI |
| `LEDControllerWindows.py` | Bluetooth control used on Windows **and** the Pi |
| `LEDControllerGUIWindows.py` | Windows GUI |
| `LEDStartupWindows.py` | Turns the strips cyan; was run by a Windows logon task (now disabled) |
| `LEDVoiceControl.py` | Voice control ("Navi") |
| `PCControl.py` | Turns the PC on (Wake-on-LAN) and off (SSH) |
| `navi-voice.service` | systemd user service that runs voice control on the Pi |
| `testMAC.py` | Scans for Bluetooth devices and prints their addresses |

## Voice commands

Everything must start with **"Navi"**, either in the same breath or up to 6
seconds after saying "Navi" on its own.

- "Navi, wall lights red" / "Navi, bed lights light blue" / "Navi, purple"
- "Navi, lights off" / "Navi, wall lights off"
- "Navi, good morning" / "Navi, I'm home" → both cyan
- "Navi, good night" / "Navi, goodbye" → both off
- "Navi, turn on / boot up / wake up / start my computer" → Wake-on-LAN
- "Navi, shut down / turn off my computer" → PC shuts down after 30 seconds.
  The bed lights flash for 2 seconds at the start and again at 15 seconds (red,
  or off if they're already red), then return to what Navi last set them to.
  Needs "shut down" or "turn … off"; a lone "off" does nothing.

"Computer" and "PC" are interchangeable in all PC commands.
- "Navi, cancel" → cancels a pending PC shutdown and any remaining flash

Colours: red, green, blue, cyan, light blue, sky blue, purple, pink, yellow,
orange, white. No "bed"/"wall" means both strips. A new command interrupts one
that's still running.

## Raspberry Pi setup (Navi)

Pi 4 running Raspberry Pi OS (Debian 13 "Trixie", 64-bit), user `admin`, with a
PS5 DualSense controller plugged in **by USB** as the microphone. (Bluetooth is
kept free for the light strips.)

### 1. System settings (need sudo)

```bash
sudo apt-get update && sudo apt-get install -y libportaudio2
sudo rfkill unblock bluetooth
sudo usermod -aG bluetooth admin
sudo loginctl enable-linger admin

# Rename to "navi". Imager-flashed images use cloud-init, which resets the
# hostname on every boot unless told not to.
echo 'preserve_hostname: true' | sudo tee /etc/cloud/cloud.cfg.d/99-keep-hostname.cfg
sudo raspi-config nonint do_hostname navi

# BlueZ forgets unpaired devices (and their GATT cache) 30s after last seeing
# them, so every command redid service discovery -- exactly where LED 2 tends
# to drop. Keep them for a day instead. (0 would mean "never keep", not
# "forever".)
sudo sed -i 's/^#TemporaryTimeout = 30$/TemporaryTimeout = 86400/' /etc/bluetooth/main.conf
sudo systemctl restart bluetooth
sudo reboot
```

### 2. Code, Python environment, speech model (no sudo)

```bash
git clone https://github.com/jeremiahcote/LED-Controller.git ~/LED-Controller
cd ~/LED-Controller

# Not .venv -- that folder in the repo is an old macOS environment.
python3 -m venv .venv_pi
.venv_pi/bin/pip install bleak vosk sounddevice requests

mkdir -p models && cd models
curl -sSL -o m.zip https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
python3 -c "import zipfile; zipfile.ZipFile('m.zip').extractall('.')" && rm m.zip
cd ..
```

### 3. Run at boot

```bash
mkdir -p ~/.config/systemd/user
cp navi-voice.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now navi-voice
```

### Day to day

```bash
# Live log
journalctl _SYSTEMD_USER_UNIT=navi-voice.service -f -o cat

# Deploy new code
cd ~/LED-Controller && git pull && systemctl --user restart navi-voice
```

From Windows these work through `ssh navi` (see `~/.ssh/config`: host
`navi.local`, user `admin`, key `~/.ssh/ledpi_ed25519`).

The service restarts itself if it crashes, and also if the mic disappears
(e.g. the controller is unplugged), so plugging it back in is enough.

## Windows setup

```bash
py -3 -m venv .venv_win
.venv_win/Scripts/python -m pip install bleak vosk sounddevice requests customtkinter
```

Download the Vosk model into `models/` as above. Run the GUI with
`.venv_win/Scripts/pythonw LEDControllerGUIWindows.py`, or voice control with
`.venv_win/Scripts/python LEDVoiceControl.py`. Voice control prefers the
DualSense mic and falls back to the TONOR USB mic.

### Wake-on-LAN from a full shutdown

Tested working on the PC (MSI B550M PRO-VDH WIFI, onboard Realtek Ethernet)
with:

- Windows Fast Startup **off** (Power Options, or `powercfg /h off` as admin).
  With it on, "Shut down" is a partial hibernate and the wake packet is
  ignored.
- BIOS → Settings → Advanced → Power Management Setup → **ErP Ready: Disabled**
- BIOS → Settings → Advanced → Wake Up Event Setup → **Resume By PCI-E Device:
  Enabled**
- Network adapter properties: **Wake on Magic Packet** and **Shutdown
  Wake-On-Lan** enabled (the defaults).

### Shutdown over SSH

The Pi has two keys, each of which the PC accepts for a single forced command:
`~/.ssh/pc_shutdown` (a 30-second shutdown) and `~/.ssh/pc_cancel`
(`shutdown /a`). Neither can do anything else. The PC's host key is pinned on
the Pi as `jerrys-pc`, so the PC's IP can change.

On the Pi, create the keys:

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/pc_shutdown -C navi-pc-shutdown
ssh-keygen -t ed25519 -N "" -f ~/.ssh/pc_cancel -C navi-pc-cancel
```

On the PC, from an **administrator** PowerShell:

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd; Set-Service sshd -StartupType Automatic

# Keys only. KbdInteractiveAuthentication goes at the top because the end of
# the file is inside a "Match Group administrators" block.
$c = "$env:ProgramData\ssh\sshd_config"
(Get-Content $c) -replace '^#?\s*PasswordAuthentication\s.*$', 'PasswordAuthentication no' | Set-Content $c -Encoding ascii
Set-Content $c -Encoding ascii -Value ("KbdInteractiveAuthentication no`r`n" + (Get-Content $c -Raw))

# Admin accounts read this file, not ~/.ssh/authorized_keys, and it's ignored
# unless only Administrators and SYSTEM can access it.
$keys = "$env:ProgramData\ssh\administrators_authorized_keys"
Add-Content -Path $keys -Encoding ascii -Value 'command="shutdown /s /t 30 /c \"Navi is shutting down this PC in 30 seconds. Run shutdown /a to cancel.\"",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty <contents of the Pi''s ~/.ssh/pc_shutdown.pub>'
Add-Content -Path $keys -Encoding ascii -Value 'command="shutdown /a",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty <contents of the Pi''s ~/.ssh/pc_cancel.pub>'
icacls $keys /inheritance:r /grant "*S-1-5-32-544:F" /grant "*S-1-5-18:F"

Set-NetFirewallRule -Name OpenSSH-Server-In-TCP -Enabled True -Profile Any -RemoteAddress LocalSubnet
Restart-Service sshd
```

Then on the Pi, pin the PC's host key (compare it against
`ssh-keyscan -t ed25519 127.0.0.1` run on the PC itself):

```bash
echo "jerrys-pc $(ssh-keyscan -t ed25519 192.168.50.181 2>/dev/null | cut -d' ' -f2-)" >> ~/.ssh/known_hosts
```

## Troubleshooting

- **Run only one controller at a time.** Each strip accepts a single
  connection, so the Pi, the PC, the Mac, and the phone app all compete. Make
  sure voice control isn't running on the PC while Navi is running.
- **A strip stops showing up in scans:** unplug it for ~10 seconds.
- **Addresses:** run `testMAC.py` to list nearby devices.
- **Why some odd-looking code exists** (the handshake frames, write delays,
  scanning before connecting, importing `sounddevice` inside a thread): see the
  comments in `LEDControllerWindows.py` and `LEDVoiceControl.py`. Each one fixes
  a specific, tested failure.
