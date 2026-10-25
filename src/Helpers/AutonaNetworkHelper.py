import socket


def GetLocalIP():
    """Best-effort LAN IP of this machine.

    Opens a UDP socket toward a non-routable address purely to learn which
    local interface the OS would use; nothing is actually sent. Falls back to
    loopback when no network is available. Extracted from
    AutonaScoreboardWidget so the API info window can reuse the same address
    the web scoreboard URLs are built from.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip


def GetLocalIPs():
    """Every IPv4 address of this machine that is not the loopback.

    GetLocalIP() (the outgoing interface) first, then the rest the host name
    resolves to (a second network card, a VPN / virtual adapter). Used by the
    web server to recognise a request from this PC that arrives on one of
    them (ADR-005 S3a). Best effort: whatever cannot be resolved is left out.
    """
    found = [GetLocalIP()]
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        infos = []
    for info in infos:
        found.append(info[4][0])
    return tuple(dict.fromkeys(
        ip for ip in found if ip and not ip.startswith("127.")))


def GetWebServerHost():
    """Host to advertise in user-facing URLs. Matches the server bind:
    loopback unless LAN access is enabled (general.webserver_listen_lan).
    """
    from ..core.SettingsManager import SettingsManager  # avoid import cycle at module load
    if SettingsManager.Get("general.webserver_listen_lan", False):
        return GetLocalIP()
    return "127.0.0.1"
