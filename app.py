"""Ambient Light Sensor Application

# TODO: Add ping operation to validate channel instead of scanning on every boot
"""
# pylint: disable=import-outside-toplevel

# Standard imports
import io
import machine
import sys
from machine import I2C, Pin
from micropython import const

# Third party imports
from homeassistant.device import HomeAssistantDevice
from homeassistant.device_class import DeviceClass
from homeassistant.sensor import HomeAssistantSensor
from mp_libs import event_sm
from mp_libs import logging
from mp_libs.enum import Enum
from mp_libs.memory import BackupDict, BackupList
from mp_libs.network import Network
from mp_libs.protocols.espnow_protocol import ScanError
from mp_libs.power import powerfeather
from mp_libs.power import lc709204f as fg
from mp_libs.time import ptp
from mp_libs.sensors import veml7700
from mp_libs.sleep import light_sleep

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
    buffer_handler.setLevel(config["buffer_logging_level"])
    buffer_handler.setFormatter(logging.Formatter("%(mono)d %(levelname)s-%(name)s:%(message)s"))
    logging.getLogger().addHandler(buffer_handler)


class DeviceState(Enum):
    LUX_SAMPLING = const(0)
    ALL_SAMPLING = const(1)


class StateAll(event_sm.InterfaceState):
    """All sensor sampling state"""
    def __init__(
        self,
        ha_device: HomeAssistantDevice
    ):
        super().__init__("All")
        self.ha_device = ha_device

    def entry(self):
        logger.info("Reading all sensors...")
        self.ha_device.read_sensors()

        logger.info("Publishing sensor data...")
        self.ha_device.publish_sensors()

    def exit(self):
        # Transition to next state on next iteration
        backup_ram[BACKUP_NAME_STATE] = DeviceState.LUX_SAMPLING


class StateLux(event_sm.InterfaceState):
    """Lux sampling state"""
    def __init__(
        self,
        ha_sensor: HomeAssistantSensor,
        ha_device: HomeAssistantDevice,
        total_iterations: int
    ):
        super().__init__("Lux")
        self.ha_sensor = ha_sensor
        self.ha_device = ha_device
        self.total_iterations = total_iterations
        self.iteration = total_iterations

    def entry(self):
        logger.info("Reading lux sensor...")
        self.ha_device.read(self.ha_sensor)

        logger.info("Publishing sensor data...")
        self.ha_device.publish_sensors()

        if self.iteration > 0:
            self.iteration -= 1

    def exit(self):
        if self.iteration == 0:
            # Transition to next state on next iteration
            self.iteration = self.total_iterations
            backup_ram[BACKUP_NAME_STATE] = DeviceState.ALL_SAMPLING


def backup_ram_init():
    """Initialize persistent data"""
    logger.info("Initializing backup RAM...")
    backup_ram.reset()
    backup_ram[BACKUP_NAME_STATE] = DeviceState.LUX_SAMPLING


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


def main():  # pylint: disable=too-many-locals,too-many-statements
    """Main loop"""
    # Wake reasoning
    first_boot = machine.reset_cause() in [machine.PWRON_RESET, machine.HARD_RESET]
    logger.info(f"Reset cause: {machine.reset_cause()}")
    logger.info(f"Wake reason: {machine.wake_reason()}")
    logger.info(f"First boot: {first_boot}")

    # Board init
    logger.debug("Board init...")
    pf = powerfeather.PowerFeather(batt_type=powerfeather.BatteryType.GENERIC_3V7, batt_cap=config["batt_cap"])
    pf.batt_charging_enable(True)

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
        backup_ram_init()
    elif not backup_ram_is_valid():
        backup_ram_init()

    # Sensors init
    logger.debug("Sensors init...")
    lux_i2c = I2C(1, scl=Pin.board.I2C_SCL1, sda=Pin.board.I2C_SDA1, freq=400000)
    lux_sensor = veml7700.VEML7700(lux_i2c)
    lux_sensor.gain(veml7700.ALS_GAIN_1_8)
    lux_sensor.integration_time(veml7700.ALS_50MS)

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

    # Setup state table - order must match DeviceState enum vals
    state_table = [
        StateLux(ha_sensor_lux, ha_device, config["lux_cycles"]),
        StateAll(ha_device)
    ]

    logger.info("Starting light reading...")
    while True:
        pf.led_on()
        net.connect()

        state_id = backup_ram[BACKUP_NAME_STATE]
        curr_state = state_table[state_id]

        curr_state.entry()
        curr_state.exit()

        if config["log_to_buffer"]:
            # Copy out and clear logs first in case any additional logs are generated during publish
            logger.info("Sending logs...")
            logs = backup_logs.copy()
            backup_logs.clear()
            ha_device.publish_logs(logs, recover=True)

        net.disconnect()
        pf.led_off()
        light_sleep(config["light_sleep_sec"], lambda: config["fake_sleep"])
