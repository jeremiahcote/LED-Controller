import os
import socket
import subprocess

# Jerry's PC: MSI B550M PRO-VDH WIFI, onboard Realtek Ethernet.
# Waking from a full shutdown needs, on the PC: Windows Fast Startup off, and in
# the BIOS "ErP Ready" disabled and "Resume By PCI-E Device" enabled.
PC_MAC = "04:7C:16:17:C9:A3"

# Shutdown and cancel go over SSH, each with its own key that the PC only
# accepts for one forced command ("shutdown /s /t 30 ..." and "shutdown /a"),
# so neither key can do anything else on the PC.
PC_USER = "jerem"
PC_LAST_KNOWN_IP = "192.168.50.181"
SHUTDOWN_KEY = os.path.expanduser("~/.ssh/pc_shutdown")
CANCEL_KEY = os.path.expanduser("~/.ssh/pc_cancel")
SHUTDOWN_DELAY = 30
# The PC's host key is pinned under this name rather than its IP, so a new
# DHCP address doesn't trip host key checking.
PC_HOST_ALIAS = "jerrys-pc"

# Both the limited and the subnet broadcast, on the two usual WoL ports. Which
# one a given router passes varies, and a duplicate packet is harmless.
BROADCASTS = [("255.255.255.255", 9), ("192.168.50.255", 9), ("192.168.50.255", 7)]


def wake_pc(mac: str = PC_MAC) -> None:
    """Send a Wake-on-LAN magic packet."""
    packet = b"\xff" * 6 + bytes.fromhex(mac.replace(":", "").replace("-", "")) * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # UDP can drop, and sending is instant, so repeat a few times.
        for _ in range(3):
            for target in BROADCASTS:
                s.sendto(packet, target)


def find_pc_ip() -> str:
    """The PC's current IP from the neighbour table, else its last known IP."""
    try:
        out = subprocess.run(["ip", "neigh"], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return PC_LAST_KNOWN_IP
    mac = PC_MAC.lower()
    for line in out.splitlines():
        if mac in line.lower():
            return line.split()[0]
    return PC_LAST_KNOWN_IP


def _run_forced_command(key: str) -> tuple[subprocess.CompletedProcess | None, str]:
    """SSH to the PC with a restricted key; the PC decides what actually runs."""
    host = find_pc_ip()
    try:
        result = subprocess.run(
            [
                "ssh", "-T", "-i", key,
                "-o", "BatchMode=yes",
                "-o", "IdentitiesOnly=yes",
                "-o", "ConnectTimeout=5",
                "-o", f"HostKeyAlias={PC_HOST_ALIAS}",
                f"{PC_USER}@{host}",
            ],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"ssh to {host} failed: {e}"
    return result, host


def shutdown_pc() -> tuple[bool, str]:
    """Start the PC's 30-second shutdown. Returns (ok, message). Blocks briefly."""
    result, host = _run_forced_command(SHUTDOWN_KEY)
    if result is None:
        return False, host
    if result.returncode != 0:
        return False, f"ssh to {host} exited {result.returncode}: {(result.stderr or result.stdout).strip()}"
    return True, f"shutdown requested on {host}"


def cancel_pc_shutdown() -> tuple[bool, str]:
    """Cancel a pending shutdown on the PC. Returns (ok, message)."""
    result, host = _run_forced_command(CANCEL_KEY)
    if result is None:
        return False, host
    output = (result.stdout + result.stderr).strip()
    if result.returncode == 0:
        return True, f"shutdown cancelled on {host}"
    # Windows error 1116: there was no shutdown to abort.
    if "(1116)" in output:
        return True, "no shutdown was pending"
    return False, f"ssh to {host} exited {result.returncode}: {output}"
