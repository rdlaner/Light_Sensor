"""Light Sensor Config File"""
# pyright: reportGeneralTypeIssues=false
# Standard imports
from micropython import const

# Third party imports
from mp_libs import logging

config = {
    # Platform Configuration

    # App Configuration
    "device_name": "light_sensor",
    "light_sleep_sec": const(10),
    "deep_sleep_sec": const(120),
    "fake_sleep": False,
    "logging_level": logging.INFO,
    "log_to_fs": False,
    "log_to_buffer": False,
    "buffer_logging_level": logging.CRITICAL,
    "debug": False,
    "lux_cycles": 10,
    "batt_cap": 1050,

    # Transport Configuration
    # supported transports are: "espnow", "miniot", or "mqtt"
    "network_transport": "miniot",
    "network_prefix_id": "Light",

    # Wifi configuration parameters

    # Mqtt configuration parameters
    "keep_alive_sec": const(60),
    "connect_retries": const(5),
    "recv_timeout_sec": const(10),
    "socket_timeout_sec": const(1),

    # Espnow configuration parameters
    "epn_peer_mac": b'p\x04\x1d\xad|\xc0',
    "epn_channel": const(1),
    "epn_timeout_ms": 0
}
