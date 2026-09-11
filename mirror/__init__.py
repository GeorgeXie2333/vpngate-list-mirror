"""A read-only mirror of the public VPN Gate directory."""

SOURCE_URL = "https://www.vpngate.net/api/iphone/"
SCHEMA_VERSION = 1
DATA_PATHS = ("data/vpngate.csv", "data/servers.json", "data/countries.json")
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_CONFIG_BYTES = 128 * 1024
MAX_SERVERS = 5000
MAX_SAFE_INTEGER = 2**53 - 1


class MirrorError(ValueError):
    """An explicit validation or publication failure."""
