"""Inside the add-on image under its AppArmor profile: the WebRTC pieces the SD card link uses.

Run by the AppArmor job in CI. Lists the host's interfaces (netlink, ctypes/ldconfig), gathers
ICE host candidates (UDP sockets) and checks the process really is confined.
"""

import asyncio
import ctypes.util
import sys

import aioice.ice
from aiortc import RTCPeerConnection


async def main() -> int:
    with open("/proc/self/attr/current", encoding="utf-8") as fh:
        label = fh.read().strip()
    print("confined as:", label)
    if not label.startswith("vicohome_mqtt"):
        print("FAIL not running under the vicohome_mqtt profile")
        return 1
    print("libc:", ctypes.util.find_library("c"))
    addrs = aioice.ice.get_host_addresses(use_ipv4=True, use_ipv6=False)
    print("host addresses:", len(addrs))
    pc = RTCPeerConnection()
    pc.createDataChannel("probe")
    await pc.setLocalDescription(await pc.createOffer())
    candidates = pc.localDescription.sdp.count("a=candidate:")
    await pc.close()
    print("ICE candidates:", candidates)
    if not addrs or not candidates:
        print("FAIL no interfaces or ICE candidates")
        return 1
    print("RESULT OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
