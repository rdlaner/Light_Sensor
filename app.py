"""Ambient Light Sensor Application"""
# pylint: disable=import-outside-toplevel

# Standard imports
import io
import machine
import sys
import time
from machine import I2C, Pin, RTC
from micropython import const

# Third party imports
from homeassistant.device import HomeAssistantDevice
from homeassistant.device_class import DeviceClass
from homeassistant.sensor import HomeAssistantSensor
from mp_libs import logging
from mp_libs.enum import Enum
from mp_libs.memory import BackupDict, BackupList
from mp_libs.network import Network
from mp_libs.protocols.espnow_protocol import ScanError
from mp_libs.power import powerfeather
from mp_libs.power import lc709204f as fg
from mp_libs.time import ptp
from mp_libs.sensors import veml7700
from mp_libs.sleep import deep_sleep, light_sleep

# Local imports
from config import config

# Constants
BACKUP_RAM_SIZE_BYTES = const(250)
BACKUP_NAME_STATE = "state"

# Globals
backup_ram = BackupDict(offset=0, size=BACKUP_RAM_SIZE_BYTES)
logger = logging.getLogger("app")
logger.setLevel(config["logging_level"])
logging.getLogger().setLevel(config["logging_level"])
for handler in logging.getLogger().handlers:
    handler.setLevel(config["logging_level"])
if config["log_to_fs"]:
    file_handler = logging.FileHandler("root_log.txt", "a")
    file_handler.setLevel(config["logging_level"])
    file_handler.setFormatter(logging.Formatter("%(mono)d %(levelname)s-%(name)s:%(message)s"))
    logging.getLogger().addHandler(file_handler)
if config["log_to_buffer"]:
    backup_logs = BackupList(offset=BACKUP_RAM_SIZE_BYTES)
    backup_logs.clear()
    buffer_handler = logging.BufferHandler(backup_logs)
    buffer_handler.setLevel(logging.CRITICAL)
    buffer_handler.setFormatter(logging.Formatter("%(mono)d %(levelname)s-%(name)s:%(message)s"))
    logging.getLogger().addHandler(buffer_handler)


class DeviceState(Enum):
    STATE_LIGHT_SLEEP = const(0)
    STATE_DEEP_SLEEP_SAMPLING = const(1)
    STATE_DEEP_SLEEP = const(2)


def backup_ram_init(is_usb_connected: bool):
    """Initialize persistent data"""
    logger.info("Initializing backup RAM...")
    backup_ram.reset()

    if is_usb_connected:
        backup_ram[BACKUP_NAME_STATE] = DeviceState.STATE_LIGHT_SLEEP
    else:
        backup_ram[BACKUP_NAME_STATE] = DeviceState.STATE_DEEP_SLEEP_SAMPLING


def backup_ram_is_valid() -> bool:
    """Check if backup ram has been properly initialized"""
    logger.debug("Checking backup ram...")

    try:
        _ = backup_ram[BACKUP_NAME_STATE]
    except KeyError:
        return False

    return True


def network_init() -> "Network":
    """Creates and returns a Network instance specified in config file

    Raises:
        RuntimeError: Unsupported network transport from config

    Returns:
        Network: New Network instance.
    """
    logger.info("Initializing network ")

    if config["network_transport"] == "espnow":
        network = Network.create_espnow(config["network_prefix_id"])
    elif config["network_transport"] == "miniot":
        network = Network.create_min_iot(config["network_prefix_id"])
    else:
        raise RuntimeError(f"Unsupported network transport: {config['network_transport']}")

    return network


def reset(msg: str = "", exc_info=None) -> None:
    """Reset device.

    Should be used for attempting to recover a device due to unrecoverable failures.

    Instead of resetting directly here, we will throw an exception that will get caught by main.py.
    main.py will write the optional message to the file system and then clean up before rebooting.

    Args:
        msg (str, optional): Optional reboot message.
        exc_info (Exception, optional): Optional exception instance.
    """
    logger.warning("Rebooting...")

    if exc_info:
        buf = io.StringIO()
        sys.print_exception(exc_info, buf)
        msg = f"{msg}\n{buf.getvalue()}"

    raise RuntimeError(msg)


def main():
    # Wake reasoning
    first_boot = machine.reset_cause() in [machine.PWRON_RESET, machine.HARD_RESET]
    logger.info(f"Reset cause: {machine.reset_cause()}")
    logger.info(f"Wake reason: {machine.wake_reason()}")
    logger.info(f"First boot: {first_boot}")

    # Board init
    logger.debug("Board init...")
    pf = powerfeather.PowerFeather(batt_type=powerfeather.BatteryType.GENERIC_3V7, batt_cap=1050)

    def cb_button(pin: Pin) -> None:
        print("Button Pressed! Toggling charging.")
        try:
            if pf._charger.charging_enable:
                pf.batt_charging_enable(False)
            else:
                pf.batt_charging_enable(True)
        except RuntimeError:
            logger.warning("Can't toggle charging. Either no batt connected or it hasn't been configured")
    pf.register_button_irq(cb_button)

    # Backup RAM
    logger.debug("Backup RAM...")
    if first_boot:
        backup_ram_init(pf.is_usb_connected())
    elif not backup_ram_is_valid():
        backup_ram_init(pf.is_usb_connected())

    # Sensors init
    logger.debug("Sensors init...")
    lux_i2c = I2C(1, scl=Pin.board.I2C_SCL1, sda=Pin.board.I2C_SDA1, freq=400000)
    lux_sensor = veml7700.VEML7700(lux_i2c)
    logger.debug(f"Lux gain:             {lux_sensor.gain(veml7700.ALS_GAIN_1_8)}")
    logger.debug(f"Lux integration time: {lux_sensor.integration_time(veml7700.ALS_50MS)}")
    logger.debug(f"Lux resolution:       {lux_sensor.resolution()}")

    # Network init
    net = network_init()
    net.connect()

    # Scan for EspNow master
    try:
        peer_channel = net.scan()[0]
    except ScanError as exc:
        logger.exception("Network scan failed", exc_info=exc)
    else:
        backup_ram["pc"] = peer_channel

    if config["log_to_buffer"]:
        # Adjust log level now that scan is done (it's noisy)
        buffer_handler.setLevel(config["buffer_logging_level"])

    # Sync with master
    # TODO: Only sync periodically, no need on every boot cycle (if we are using deep sleep)
    ptp.time_sync(is_async=False, rx_fxn=net.receive)

    # Homeassistant init
    def batt_volt_read():
        val = None
        try:
            val = pf.batt_voltage()
        except (powerfeather.BatteryError, fg.FuelGaugeError):
            pass
        return val
    def batt_charge_read():
        val = None
        try:
            val = pf.batt_charge()
        except (powerfeather.BatteryError, fg.FuelGaugeError):
            pass
        return val
    def batt_time_left_read():
        val = None
        try:
            val = pf.batt_time_left()
        except (powerfeather.BatteryError, fg.FuelGaugeError):
            pass
        return val

    ha_sensor_lux = HomeAssistantSensor("lux", lux_sensor.lux, 2, DeviceClass.ILLUMINANCE, "lx")
    ha_sensor_supply_v = HomeAssistantSensor("supply_v", pf.supply_voltage, 2, DeviceClass.VOLTAGE, "mV")
    ha_sensor_supply_c = HomeAssistantSensor("supply_c", pf.supply_current, 2, DeviceClass.CURRENT, "mA")
    ha_sensor_batt_v = HomeAssistantSensor("batt_v", batt_volt_read, 2, DeviceClass.VOLTAGE, "mV")
    ha_sensor_batt_c = HomeAssistantSensor("batt_c", pf.batt_current, 2, DeviceClass.CURRENT, "mA")
    ha_sensor_batt_charge = HomeAssistantSensor("batt_chrg", batt_charge_read, 2, DeviceClass.BATTERY, "%")
    ha_sensor_batt_status = HomeAssistantSensor("batt_stat", pf.batt_charging_status)
    ha_sensor_batt_time = HomeAssistantSensor("batt_dur", batt_time_left_read, 0, DeviceClass.DURATION, "min")
    ha_device = HomeAssistantDevice("lux_device", "PF", net.send)

    ha_device.add_sensor(ha_sensor_lux)
    ha_device.add_sensor(ha_sensor_supply_v)
    ha_device.add_sensor(ha_sensor_supply_c)
    ha_device.add_sensor(ha_sensor_batt_v)
    ha_device.add_sensor(ha_sensor_batt_c)
    ha_device.add_sensor(ha_sensor_batt_charge)
    ha_device.add_sensor(ha_sensor_batt_status)
    ha_device.add_sensor(ha_sensor_batt_time)
    ha_device.send_discovery()

    term_current = pf._charger.term_current
    batt_voltage = batt_charge = batt_cycles = batt_health = batt_time_left = 0
    logger.info("Starting light reading...")
    while True:
        pf.led_on()
        net.disconnect()
        net.connect()

        try:
            # Get data
            logger.info("Reading sensors...")
            ha_device.read_sensors()

            # Send data
            ha_device.publish_sensors()
            if config["log_to_buffer"]:
                logger.info("Sending logs...")

                # Copy out and clear logs first in case any additional logs are generated during publish
                logs = backup_logs.copy()
                backup_logs.clear()
                ha_device.publish_logs(logs, recover=True)
        except Exception as exc:
            reset("Caught unexpected exception. Rebooting.", exc_info=exc)

        pf.led_off()
        light_sleep(10, lambda: config["fake_sleep"])
