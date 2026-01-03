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
    "light_sleep_sec": const(5),
    "deep_sleep_sec": const(120),
    "upload_rate_sec": const(120),
    "receive_rate_sec": const(120),
    "receive_window_sec": const(0.5),
    "time_sync_rate_sec": const(600),
    "send_discovery_rate_sec": const(600),
    "display_refresh_rate_sec": const(120),
    "ambient_pressure": const(1000),
    # "temp_offset_c": const(4.4),
    "temp_offset_c": const(0.5),
    "force_deep_sleep": True,
    "fake_sleep": False,
    "logging_level": logging.INFO,
    "log_to_fs": False,
    "log_to_buffer": False,
    "buffer_logging_level": logging.INFO,
    "debug": True,

    # Display Configuration
    "display_enable": False,

    # Transport Configuration
    # supported transports are: "espnow", "miniot", or "mqtt"
    "enable_network": True,
    "network_transport": "miniot",
    "network_prefix_id": "Light",

    # Wifi configuration parameters
    "wifi_channel": const(8),

    # Mqtt configuration parameters
    "topics": {
        "pressure_topic": "homeassistant/aranet/pressure",
        "cmd_topic": "homeassistant/number/generic-device/cmd",
    },
    "keep_alive_sec": const(60),
    "keep_alive_margin_sec": const(20),
    "connect_retries": const(5),
    "recv_timeout_sec": const(10),
    "socket_timeout_sec": const(1),

    # Espnow configuration parameters
    "epn_peer_mac": b'p\x04\x1d\xad|\xc0',
    "epn_channel": const(1),
    "epn_timeout_ms": 0
}
