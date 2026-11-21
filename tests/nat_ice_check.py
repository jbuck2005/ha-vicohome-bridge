"""The SD card link without host networking: ICE from a bridged container to a camera on the LAN.

Run by the AppArmor job in CI. ``camera`` plays the camera in a network namespace that has no route
to the container network, as a real camera has none to Home Assistant's add-on network. ``bridge``
runs inside the add-on image on a bridged Docker network, so its packets leave through the host's
NAT, and sets up ICE the way sdfetch does (LAN interface filter, no STUN). The only working path is
the camera answering the NAT'd address it sees (a peer-reflexive candidate).

    python3 tests/nat_ice_check.py camera <dir>   # in the camera's namespace
    python3 tests/nat_ice_check.py bridge <dir>   # in the add-on image

After the hello, the camera streams BULK_BYTES in 1200-byte messages, like an SD clip coming off the
card, so a link that sets up through NAT but stalls during a transfer fails too.
"""

import asyncio
import os
import sys
import time

BULK_BYTES = 8 * 1024 * 1024
CHUNK = 1200

from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription


def _write(path: str, text: str) -> None:
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(path + ".tmp", path)


async def _wait_read(path: str, timeout: float = 60) -> str:
    for _ in range(int(timeout * 5)):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        await asyncio.sleep(0.2)
    raise TimeoutError(path)


def _candidates(pc) -> list[str]:
    return [line for line in pc.localDescription.sdp.splitlines() if line.startswith("a=candidate:")]


async def camera(d: str) -> int:
    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))

    @pc.on("datachannel")
    def _dc(ch):
        @ch.on("message")
        def _echo(msg):
            ch.send(b"camera:" + (msg if isinstance(msg, bytes) else msg.encode()))
            block = os.urandom(CHUNK)
            for _ in range(BULK_BYTES // CHUNK):
                ch.send(block)

    offer = await _wait_read(f"{d}/offer.sdp")
    await pc.setRemoteDescription(RTCSessionDescription(offer, "offer"))
    await pc.setLocalDescription(await pc.createAnswer())
    print("camera candidates:", _candidates(pc), flush=True)
    _write(f"{d}/answer.sdp", pc.localDescription.sdp)
    await _wait_read(f"{d}/done", 150)
    await pc.close()
    return 0


async def bridge(d: str) -> int:
    sys.path.insert(0, os.environ.get("BRIDGE_APP", "/app"))
    import sdfetch  # applies the add-on's ICE patches and link statistics
    from bridge import Bridge

    ip = Bridge._lan_ip()
    print("bridge address:", ip, flush=True)
    sdfetch._ICE_IFACE[0] = ip
    sdfetch.net_reset()
    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    ch = pc.createDataChannel("probe")
    loop = asyncio.get_running_loop()
    got = loop.create_future()
    bulk = loop.create_future()
    seen = [0]

    @ch.on("open")
    def _open():
        ch.send(b"hello")

    @ch.on("message")
    def _msg(msg):
        if not got.done():
            got.set_result(msg)
            return
        seen[0] += len(msg)
        if seen[0] >= BULK_BYTES // CHUNK * CHUNK and not bulk.done():
            bulk.set_result(True)

    await pc.setLocalDescription(await pc.createOffer())
    print("bridge candidates:", _candidates(pc), flush=True)
    _write(f"{d}/offer.sdp", pc.localDescription.sdp)
    await pc.setRemoteDescription(RTCSessionDescription(await _wait_read(f"{d}/answer.sdp"), "answer"))
    try:
        reply = await asyncio.wait_for(got, 30)
        if reply != b"camera:hello":
            print("FAIL unexpected reply", reply)
            return 1
        t0 = time.time()
        try:
            await asyncio.wait_for(bulk, 60)
        except asyncio.TimeoutError:
            print("FAIL bulk transfer stalled at %d of %d kB: %s" % (seen[0] // 1024, BULK_BYTES // 1024,
                                                                   sdfetch.net_stats(pc)), flush=True)
            return 1
        dt = time.time() - t0
        print("bulk: %d kB in %.1f s (%.0f kB/s): %s" % (seen[0] // 1024, dt, seen[0] / 1024 / max(dt, 0.001),
                                                        sdfetch.net_stats(pc)), flush=True)
    except asyncio.TimeoutError:
        print("FAIL no data-channel reply through NAT (ICE state %s)" % pc.iceConnectionState)
        return 1
    finally:
        _write(f"{d}/done", "")
        await pc.close()
    print("RESULT OK", flush=True)
    return 0


if __name__ == "__main__":
    role, folder = sys.argv[1], sys.argv[2]
    sys.exit(asyncio.run(camera(folder) if role == "camera" else bridge(folder)))
