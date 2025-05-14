import os
import asyncio
from abc import ABCMeta, abstractmethod
import logging

from bleak import BleakClient, BleakScanner, BleakGATTCharacteristic

from util import *

logger = logging.getLogger(__name__)

class NrfBleDfuController(object, metaclass=ABCMeta):
    ctrlpt_handle = None
    ctrlpt_cccd_handle = None
    data_handle = None

    pkt_receipt_interval = 10
    pkt_payload_size = 20

    def __init__(self, target_mac, firmware_path, datfile_path):
        self.target_mac = target_mac
        self.firmware_path = firmware_path
        self.datfile_path = datfile_path
        self.client = None
        self.notification_event = asyncio.Event()
        self.notification_data = None

    @abstractmethod
    def start(self):
        pass

    # --------------------------------------------------------------------------
    #  Check if the peripheral is running in bootloader (DFU) or application mode
    #  Returns True if the peripheral is in DFU mode
    # --------------------------------------------------------------------------
    @abstractmethod
    def check_DFU_mode(self):
        pass

    @abstractmethod
    # --------------------------------------------------------------------------
    #  Switch from application to bootloader (DFU)
    # --------------------------------------------------------------------------
    def switch_to_dfu_mode(self):
        pass

    # --------------------------------------------------------------------------
    #  Parse notification status results
    # --------------------------------------------------------------------------
    @abstractmethod
    def _dfu_parse_notify(self, notify):
        pass

    # --------------------------------------------------------------------------
    #  Wait for a notification and parse the response
    # --------------------------------------------------------------------------
    @abstractmethod
    async def _wait_and_parse_notify(self):
        pass

    @abstractmethod
    async def _on_connected(self):
        pass

    # --------------------------------------------------------------------------
    # Initialize:
    #    Hex: read and convert hexfile into bin_array
    #    Bin: read binfile into bin_array
    # --------------------------------------------------------------------------
    def input_setup(self):
        logger.info('Sending file %s to %s', os.path.split(self.firmware_path)[1], self.target_mac)

        if self.firmware_path is None:
            raise Exception("Input invalid")

        name, extent = os.path.splitext(self.firmware_path)

        if extent == ".bin":
            with open(self.firmware_path, 'rb') as f:
                self.bin_array = f.read()
            self.image_size = len(self.bin_array)
            logger.info('Binary image size: %d', self.image_size)
            logger.info('Binary CRC32: %08X', crc32_unsigned(self.bin_array))
            return

        if extent == ".hex":
            from intelhex import IntelHex
            intelhex = IntelHex(self.firmware_path)
            self.bin_array = intelhex.tobinarray()
            self.image_size = len(self.bin_array)
            logger.info('Bin array size: %d', self.image_size)
            return

        raise Exception("Input invalid")

    # --------------------------------------------------------------------------
    # Perform a scan and connect via bleak.
    # Will return True if a connection was established, False otherwise
    # --------------------------------------------------------------------------
    async def scan_and_connect(self, timeout=10):
        logger.info('Connecting to %s', self.target_mac)

        devices = await BleakScanner.discover(timeout=timeout)
        dfu_addr = uint_to_mac_string(mac_string_to_uint(self.target_mac) + 1)
        for device in devices:
            if device.address == self.target_mac:
                self.client = BleakClient(self.target_mac)
                await self.client.connect()
                await self._on_connected()
                logger.debug('Connected to %s', self.target_mac)
                return True
            if device.address == dfu_addr:
                self.client = BleakClient(dfu_addr)
                await self.client.connect()
                await self._on_connected()
                logger.debug('Connected to %s', dfu_addr)
                return True


        logger.warning(f"Device {self.target_mac} not found")
        return False

    # --------------------------------------------------------------------------
    #  Disconnect from the peripheral
    # --------------------------------------------------------------------------
    async def disconnect(self):
        if self.client and self.client.is_connected:
            await self.client.disconnect()
            logger.debug('Disconnected from %s', self.target_mac)

    async def target_mac_increase(self, inc):
        await self.disconnect()
        self.target_mac = uint_to_mac_string(mac_string_to_uint(self.target_mac) + inc)

        # Re-start gatttool with the new address
        await self.scan_and_connect()


    # --------------------------------------------------------------------------
    #  Wait for notification to arrive.
    # --------------------------------------------------------------------------
    async def _dfu_wait_for_notify(self):
        logger.debug("dfu_wait_for_notify")

        # bleak handles notifications asynchronously, so this method would
        # need to wait for the notification handler to process the data.
        await self.notification_event.wait()
        self.notification_event.clear()
        return self.notification_data

    # --------------------------------------------------------------------------
    #  Send a procedure + any parameters required
    # --------------------------------------------------------------------------
    async def _dfu_send_command(self, procedure, params=[]):

        command = bytes([procedure] + params)
        logger.debug('_dfu_send_command %s', command)

        await self.client.write_gatt_char(self.ctrlpt_handle, command)

        # Verify that command was successfully written
        # ???

    # --------------------------------------------------------------------------
    #  Send an array of bytes
    # --------------------------------------------------------------------------
    async def _dfu_send_data(self, data):
        await self.client.write_gatt_char(self.data_handle, bytearray(data))

    # --------------------------------------------------------------------------
    #  Enable notifications from Characteristic
    # --------------------------------------------------------------------------
    async def _enable_notifications(self, char: BleakGATTCharacteristic):
        logger.debug('_enable_notifications')

        await self.client.start_notify(char, self._notification_handler)

    def _notification_handler(self, sender, data):
        logger.debug('Notification received from %s: %s', sender, data)
        # store data in the class and use asyncio Event to notify
        self.notification_data = data
        self.notification_event.set()
