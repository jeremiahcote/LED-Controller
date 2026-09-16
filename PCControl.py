import socket

# Jerry's PC: MSI B550M PRO-VDH WIFI, onboard Realtek Ethernet.
# Waking from a full shutdown needs, on the PC: Windows Fast Startup off, and in
# the BIOS "ErP Ready" disabled and "Resume By PCI-E Device" enabled.
PC_MAC = "04:7C:16:17:C9:A3"

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
