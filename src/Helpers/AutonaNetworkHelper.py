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
