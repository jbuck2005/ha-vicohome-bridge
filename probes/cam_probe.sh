#!/usr/bin/env bash
# LAN probe of a Vicohome camera on stock firmware. Run on a Linux box on the camera's LAN:
#   sudo ./cam_probe.sh <camera-ip> [iface]
# Needs: nmap, tcpdump, arping (iputils or Thomas Habets'), timeout. Takes about 25 minutes.
# Writes ./cam_probe_<time>/ with raw files, and prints a summary with the camera's IP and MAC
# replaced by CAM_IP / CAM_MAC, so the summary is safe to paste. Raw files stay on this machine.
set -u
CAM=${1:?usage: sudo $0 <camera-ip> [iface]}
IF=${2:-$(ip route get "$CAM" | sed -n 's/.* dev \([^ ]*\).*/\1/p')}
OUT=cam_probe_$(date +%Y%m%d_%H%M%S); mkdir -p "$OUT"; cd "$OUT"
say() { printf '\n=== %s (%s)\n' "$1" "$(date +%H:%M:%S)"; }
pause() { printf '\n>>> %s\n    Press Enter when done. ' "$1"; read -r _; }

ping -c 2 -W 2 "$CAM" >/dev/null 2>&1; MAC=$(ip neigh show "$CAM" | awk '/lladdr/{print $5}')
FILTER="host $CAM"; [ -n "$MAC" ] && FILTER="host $CAM or ether src $MAC"
say "Step 1: background capture of everything the camera broadcasts or multicasts, and anything to/from this host"
tcpdump -i "$IF" -nn -U -w all.pcap "$FILTER" >/dev/null 2>&1 &
TCPD=$!
tcpdump -i "$IF" -nn -U -w lan_bcast.pcap "broadcast or multicast" >/dev/null 2>&1 &
TCPB=$!

say "Step 2: is it answering while asleep? 3 min of ARP and ping"
timeout 180 arping -I "$IF" -c 90 -i 2 "$CAM" > arp_asleep.txt 2>&1 || timeout 180 arping -I "$IF" -c 90 -w 2 "$CAM" > arp_asleep.txt 2>&1
ping -c 20 -i 1 "$CAM" > ping_asleep.txt 2>&1
nmap -Pn -sS -p- -T4 --max-retries 1 "$CAM" -oN tcp_asleep.txt >/dev/null 2>&1

pause "Step 3: open LIVE VIEW of the camera in the Vicohome app now and keep it open (re-open if it drops)."
nmap -Pn -sS -p- -T4 --max-retries 1 "$CAM" -oN tcp_awake.txt >/dev/null 2>&1
nmap -Pn -sU --top-ports 300 -T4 "$CAM" -oN udp_awake.txt >/dev/null 2>&1
nmap -Pn -sV -p "$(grep -o '^[0-9]*/tcp *open' tcp_awake.txt | cut -d/ -f1 | paste -sd, -)" "$CAM" -oN svc_awake.txt >/dev/null 2>&1 || true
pause "You can close live view."

pause "Step 4: block the internet for the camera AND your phone at the router (or unplug the WAN), then try live view and SD playback in the app for 2 minutes. Note what works."
pause "Step 5: restore the internet. Then wait for one bird visit (or wave at the camera), and press Enter after it."

kill "$TCPD" "$TCPB" 2>/dev/null; sleep 1

red() { sed -e "s/$CAM/CAM_IP/g" -e "s/${MAC:-NO_MAC}/CAM_MAC/Ig"; }
{
  say "ARP while asleep (replies / sent)"; grep -Ei 'received|reply|response' arp_asleep.txt | tail -3
  say "Ping while asleep"; tail -2 ping_asleep.txt
  say "TCP open while asleep"; grep -E '^[0-9]+/tcp' tcp_asleep.txt || echo none
  say "TCP open while awake"; grep -E '^[0-9]+/tcp' tcp_awake.txt || echo none
  say "UDP open|filtered while awake (open only)"; grep -E '^[0-9]+/udp +open ' udp_awake.txt || echo none
  say "Services"; grep -E '^[0-9]+/' svc_awake.txt 2>/dev/null || echo none
  say "Camera's broadcast/multicast (proto, dst, port: count)"
  tcpdump -nn -r lan_bcast.pcap "src host $CAM" 2>/dev/null | awk '{print $3, $5}' | sed 's/\.[0-9]*$//;' | sort | uniq -c | sort -rn | head -30
  say "Broadcast/multicast packets naming the vendor (count only; payload stays in lan_bcast.pcap)"
  tcpdump -nn -A -r lan_bcast.pcap 2>/dev/null | grep -ciE 'vico|addx|a4x|smartaddx|safemo|cg6' 
  say "Camera traffic seen by this host (conversations)"
  tcpdump -nn -r all.pcap 2>/dev/null | awk '{print $2, $3, $4, $5}' | cut -d' ' -f1- | sed -E 's/\.[0-9]+ / /g' | sort | uniq -c | sort -rn | head -30
} 2>&1 | red | tee summary.txt
echo; echo "Summary saved to $OUT/summary.txt (IP and MAC redacted). Paste it in the thread."
