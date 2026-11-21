#!/usr/bin/env bash
# LAN probe of a Vicohome camera on stock firmware. Run as root on a Linux box on the camera's LAN.
#
#   sudo ./cam_probe.sh <camera-ip> guided            # interactive walk-through (needs the phone app)
#   sudo ./cam_probe.sh <camera-ip> asleep            # ARP/ping + full TCP scan, ~5 min, no app needed
#   sudo ./cam_probe.sh <camera-ip> awake             # TCP + UDP scan now; run while live view or an SD fetch is on
#   sudo ./cam_probe.sh <camera-ip> listen <minutes>  # passive: capture + ARP timeline, no packets to the camera
#                                                     #   except one ARP request every 10 s
#   sudo ./cam_probe.sh <camera-ip> summary <dir>     # re-print the summary of an earlier run
#
# Needs: nmap, tcpdump, arping, iproute2. Results go to ./cam_probe_<time>_<mode>/ and stay on this machine.
# The printed summary replaces the camera's IP and MAC with CAM_IP / CAM_MAC and never prints payloads,
# so it is safe to paste. Set IFACE=... to pick the interface, FAST=1 for a quick awake scan.
set -u
CAM=${1:?usage: sudo $0 <camera-ip> guided|asleep|awake|listen <min>|summary <dir>}
MODE=${2:-guided}
IF=${IFACE:-$(ip route get "$CAM" | sed -n 's/.* dev \([^ ]*\).*/\1/p')}
say() { printf '\n=== %s (%s)\n' "$1" "$(date +%H:%M:%S)"; }
pause() { printf '\n>>> %s\n    Press Enter when done. ' "$1"; read -r _; }

if [ "$MODE" = summary ]; then cd "${3:?dir}"; else
  OUT=cam_probe_$(date +%Y%m%d_%H%M%S)_$MODE; mkdir -p "$OUT"; cd "$OUT"
fi
ping -c 2 -W 2 "$CAM" >/dev/null 2>&1
MAC=$(ip neigh show "$CAM" | awk '/lladdr/{print $5}')
[ -n "$MAC" ] && echo "$MAC" > .mac || MAC=$(cat .mac 2>/dev/null)
FILTER="host $CAM"; [ -n "$MAC" ] && FILTER="host $CAM or ether host $MAC"

start_capture() {
  tcpdump -i "$IF" -nn -U -w cam.pcap "$FILTER" >/dev/null 2>&1 & TCPD=$!
  tcpdump -i "$IF" -nn -U -w lan_bcast.pcap "broadcast or multicast" >/dev/null 2>&1 & TCPB=$!
}
stop_capture() { kill "${TCPD:-}" "${TCPB:-}" 2>/dev/null; sleep 1; }

# One ARP request every 10 s for <seconds>; one line per probe: time + replied/silent.
arp_timeline() {
  local end=$(( $(date +%s) + $1 ))
  while [ "$(date +%s)" -lt "$end" ]; do
    if arping -I "$IF" -c 1 -w 2 "$CAM" >/dev/null 2>&1; then echo "$(date +%H:%M:%S) reply"; else echo "$(date +%H:%M:%S) silent"; fi
    sleep 8
  done >> arp_timeline.txt
}

# FAST=1: top 2000 TCP / top 50 UDP at -T5, done in well under a minute (for a short wake such as an SD fetch).
if [ "${FAST:-0}" = 1 ]; then TCPP="--top-ports 2000 -T5"; UDPN=50; else TCPP="-p- -T4"; UDPN=300; fi
scan_tcp() { nmap -Pn -sS $TCPP --max-retries 1 "$CAM" -oN "tcp_$1.txt" >/dev/null 2>&1; }
scan_udp() { nmap -Pn -sU --top-ports $UDPN -T4 "$CAM" -oN "udp_$1.txt" >/dev/null 2>&1; }
scan_svc() {
  local ports; ports=$(grep -o '^[0-9]*/tcp *open' "tcp_$1.txt" 2>/dev/null | cut -d/ -f1 | paste -sd, -)
  [ -n "$ports" ] && nmap -Pn -sV -p "$ports" "$CAM" -oN "svc_$1.txt" >/dev/null 2>&1
}

case "$MODE" in
  asleep)
    start_capture; say "ARP timeline 3 min"; arp_timeline 180
    ping -c 20 -i 1 "$CAM" > ping_asleep.txt 2>&1
    say "TCP scan"; scan_tcp asleep; stop_capture ;;
  awake)
    start_capture; say "TCP scan (camera should be awake)"; scan_tcp awake; scan_svc awake
    say "UDP scan"; scan_udp awake; stop_capture ;;
  listen)
    MIN=${3:?minutes}; start_capture; say "Listening $MIN min"; arp_timeline $(( MIN * 60 )); stop_capture ;;
  guided)
    start_capture
    say "Step 1: asleep. ARP timeline 3 min, then a TCP scan"; arp_timeline 180
    ping -c 20 -i 1 "$CAM" > ping_asleep.txt 2>&1; scan_tcp asleep
    pause "Step 2: open LIVE VIEW of the camera in the Vicohome app now and keep it open (re-open if it drops)."
    scan_tcp awake; scan_svc awake; scan_udp awake
    pause "You can close live view."
    pause "Step 3: block the internet for the camera AND the phone at the router (or unplug the WAN). Try live view and SD playback in the app for 2 minutes. Note what works."
    pause "Step 4: restore the internet. Wait for one bird visit (or wave at the camera), then come back."
    stop_capture ;;
  summary) ;;
  *) echo "unknown mode $MODE"; exit 2 ;;
esac

red() { sed -e "s/$CAM/CAM_IP/g" -e "s/${MAC:-NO_MAC}/CAM_MAC/Ig"; }
{
  for f in tcp_asleep tcp_awake; do [ -f $f.txt ] && { say "$f: open ports"; grep -E '^[0-9]+/tcp' $f.txt || echo none; }; done
  [ -f udp_awake.txt ] && { say "UDP open while awake"; grep -E '^[0-9]+/udp +open ' udp_awake.txt || echo none; }
  [ -f svc_awake.txt ] && { say "Services"; grep -E '^[0-9]+/' svc_awake.txt || echo none; }
  [ -f ping_asleep.txt ] && { say "Ping"; tail -2 ping_asleep.txt; }
  if [ -f arp_timeline.txt ]; then
    say "ARP timeline: replies vs silent, and every change"
    awk '{n[$2]++} END {for (k in n) print k, n[k]}' arp_timeline.txt
    awk 'prev != $2 {print $1, $2; prev = $2}' arp_timeline.txt | head -60
  fi
  if [ -f cam.pcap ]; then
    say "Camera packets per minute (wake bursts show here)"
    tcpdump -nn -tttt -r cam.pcap 2>/dev/null | awk '{print substr($2,1,5)}' | uniq -c | head -120
    say "Camera conversations: protocol, local/remote, remote port, packets (remote IPs shown only if private)"
    tcpdump -nn -r cam.pcap "ip" 2>/dev/null | awk -v cam="$CAM" '
      { src=$3; dst=$5; sub(/:$/,"",dst); proto=($0 ~ / UDP| udp/)?"udp":(($0 ~ /Flags/)?"tcp":"other")
        split(src,s,"."); split(dst,d,".")
        sip=s[1]"."s[2]"."s[3]"."s[4]; dip=d[1]"."d[2]"."d[3]"."d[4]
        if (sip==cam) { peer=dip; port=d[5]; dir="out" } else { peer=sip; port=s[5]; dir="in" }
        if (peer ~ /^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.|224\.|239\.|255\.)/) scope=peer; else scope="internet"
        key=proto" "dir" "scope" port "port; c[key]++ }
      END { for (k in c) print c[k], k }' | sort -rn | head -40
    say "ARP/other link-layer from the camera"
    tcpdump -nn -e -r cam.pcap "not ip" 2>/dev/null | awk '{print $NF=="" ? "" : $0}' | sed -E 's/^[0-9:.]+ //' | cut -c1-90 | sort | uniq -c | sort -rn | head -15
  fi
  if [ -f lan_bcast.pcap ]; then
    say "Camera's own broadcast/multicast (dst, port: count)"
    tcpdump -nn -r lan_bcast.pcap "src host $CAM" 2>/dev/null | awk '{print $5}' | sort | uniq -c | sort -rn | head -20
    say "Broadcast/multicast packets naming the vendor (count only)"
    tcpdump -nn -A -r lan_bcast.pcap 2>/dev/null | grep -ciE 'vico|addx|a4x|smartaddx|safemo|cg6'
  fi
} 2>&1 | red | tee summary.txt
echo; echo "Saved to $(pwd)/summary.txt (IP and MAC redacted)."
