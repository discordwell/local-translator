"""
Bonjour/mDNS service advertisement for the Local Translator server.

Advertises the service as _jptranslate._tcp.local. so that the iPhone app
can automatically discover the server on the local network. The service type
is part of the wire contract with the iOS client and must not change.
"""

import socket
import threading
from zeroconf import ServiceInfo, Zeroconf


class BonjourService:
    """Manages Bonjour service advertisement."""

    SERVICE_TYPE = "_jptranslate._tcp.local."
    SERVICE_NAME = "Local Translator._jptranslate._tcp.local."

    def __init__(self, port: int):
        self.port = port
        self.zeroconf = None
        self.service_info = None
        self._thread = None

    def _get_local_ip(self) -> str:
        """Get the local IP address of this machine."""
        try:
            # Create a socket and connect to an external address
            # This doesn't actually send any data, just determines the local IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            # Fallback to localhost if we can't determine IP
            return "127.0.0.1"

    def _register_service(self):
        """Register the service in a separate thread."""
        try:
            self.zeroconf = Zeroconf()
            self.zeroconf.register_service(self.service_info)
            print(f"Bonjour service registered: {self.SERVICE_TYPE}")
        except Exception as e:
            print(f"Warning: Could not register Bonjour service: {e}")

    def start(self):
        """Start advertising the service via Bonjour."""
        if self.zeroconf is not None:
            return

        local_ip = self._get_local_ip()
        print(f"Advertising Bonjour service on {local_ip}:{self.port}")

        # Create service info
        self.service_info = ServiceInfo(
            type_=self.SERVICE_TYPE,
            name=self.SERVICE_NAME,
            addresses=[socket.inet_aton(local_ip)],
            port=self.port,
            properties={
                "version": "1.0",
                "name": "Local Translator Server",
            },
            server="jptranslate.local.",
        )

        # Register the service in a separate thread to avoid blocking asyncio
        self._thread = threading.Thread(target=self._register_service, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop advertising the service."""
        if self.zeroconf is not None:
            print("Unregistering Bonjour service...")
            try:
                self.zeroconf.unregister_service(self.service_info)
                self.zeroconf.close()
            except Exception:
                pass
            self.zeroconf = None
            self.service_info = None
            print("Bonjour service stopped")


# Global service instance
_service = None


def get_bonjour_service(port: int) -> BonjourService:
    """Get the global Bonjour service instance."""
    global _service
    if _service is None:
        _service = BonjourService(port)
    return _service
