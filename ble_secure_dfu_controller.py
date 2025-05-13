import asyncio
import math
import struct
import os

from array import array
import time
from util import *

import logging

from bleak.uuids import normalize_uuid_str

from nrf_ble_dfu_controller import NrfBleDfuController

logger = logging.getLogger(__file__)
verbose = True

class Procedures:
    CREATE          = 0x01
    SET_PRN         = 0x02
    CALC_CHECKSUM   = 0x03
    EXECUTE         = 0x04
    SELECT          = 0x06
    RESPONSE        = 0x60

    PARAM_COMMAND   = 0x01
    PARAM_DATA      = 0x02

    string_map = {
        CREATE          : "CREATE",
        SET_PRN         : "SET_PRN",
        CALC_CHECKSUM   : "CALC_CHECKSUM",
        EXECUTE         : "EXECUTE",
        SELECT          : "SELECT",
        RESPONSE        : "RESPONSE",
    }

    @staticmethod
    def to_string(proc):
        return Procedures.string_map[proc]

    @staticmethod
    def from_string(proc_str):
        return int(proc_str, 16)

class Results:
    INVALID_CODE                = 0x00
    SUCCESS                     = 0x01
    OPCODE_NOT_SUPPORTED        = 0x02
    INVALID_PARAMETER           = 0x03
    INSUFF_RESOURCES            = 0x04
    INVALID_OBJECT              = 0x05
    UNSUPPORTED_TYPE            = 0x07
    OPERATION_NOT_PERMITTED     = 0x08
    OPERATION_FAILED            = 0x0A

    string_map = {
        INVALID_CODE            : "INVALID_CODE",
        SUCCESS                 : "SUCCESS",
        OPCODE_NOT_SUPPORTED    : "OPCODE_NOT_SUPPORTED",
        INVALID_PARAMETER       : "INVALID_PARAMETER",
        INSUFF_RESOURCES        : "INSUFFICIENT_RESOURCES",
        INVALID_OBJECT          : "INVALID_OBJECT",
        UNSUPPORTED_TYPE        : "UNSUPPORTED_TYPE",
        OPERATION_NOT_PERMITTED : "OPERATION_NOT_PERMITTED",
        OPERATION_FAILED        : "OPERATION_FAILED",
    }

    @staticmethod
    def to_string(res):
        return Results.string_map[res]

    @staticmethod
    def from_string(res_str):
        return int(res_str, 16)


class BleDfuControllerSecure(NrfBleDfuController):
    UUID_DFU = normalize_uuid_str("FE59")
    UUID_BUTTONLESS = "8e400001-f315-4f60-9fb8-838830daea50"
    UUID_CONTROL_POINT = "8ec90001-f315-4f60-9fb8-838830daea50"
    UUID_PACKET = "8ec90002-f315-4f60-9fb8-838830daea50"

    def __init__(self, target_mac, firmware_path, datfile_path):
        super().__init__(target_mac, firmware_path, datfile_path)
        self.ctrlpt_uuid = self.UUID_CONTROL_POINT
        self.packet_uuid = self.UUID_PACKET

    async def _on_connected(self):
        pass


    # --------------------------------------------------------------------------
    #  Start the firmware update process
    # --------------------------------------------------------------------------
    async def start(self):
        dfus = [s for s in self.client.services if s.uuid.upper() == self.UUID_DFU.upper() ]
        if not dfus:
            raise Exception('No DFU service')
        self.service = dfus[0]

        chars = { c.uuid.upper(): c for c in self.service.characteristics}

        self.ctrlpt_handle = chars[self.UUID_CONTROL_POINT.upper()]
        self.data_handle = chars[self.UUID_PACKET.upper()]

        if verbose:
            print(f"Control Point Handle: {self.ctrlpt_handle}")
            print(f"Packet Handle: {self.data_handle}")

        # Enable notifications from the Control Point characteristic
        await self._enable_notifications(self.ctrlpt_handle)

        # Set the Packet Receipt Notification interval
        prn = uint16_to_bytes_le(self.pkt_receipt_interval)
        await self._dfu_send_command(Procedures.SET_PRN, prn)

        await self._wait_and_parse_notify()

        await self._dfu_send_init()
        await self._dfu_send_image()


    # --------------------------------------------------------------------------
    #  Check if the peripheral is running in bootloader (DFU) or application mode
    #  Returns True if the peripheral is in DFU mode
    # --------------------------------------------------------------------------
    async def check_DFU_mode(self):
        """Returns True if already in DFU mode, False otherwise"""
        print('services = ', [s.uuid for s in self.client.services])
        for s in self.client.services:
            if s.uuid.upper() == self.UUID_DFU.upper():
                return True
        return False


    async def switch_to_dfu_mode(self):
        """Send buttonless DFU mode entry command"""

        # await self._enable_notifications(self.ctrlpt_handle)
        await self.client.write_gatt_char(self.UUID_BUTTONLESS, b'\x01', response=True)

        # Wait some time for board to reboot
        await asyncio.sleep(0.5)

        # Increase the mac address by one and reconnect
        await self.target_mac_increase(1)
        return await self.scan_and_connect()

    # --------------------------------------------------------------------------
    #  Parse notification status results
    # --------------------------------------------------------------------------

    def _dfu_parse_notify(self, notify: bytes):
        if len(notify) < 3:
            print("notify data length error")
            return None

        if verbose: print('RX:', notify)

        dfu_notify_opcode = notify[0]
        if dfu_notify_opcode == Procedures.RESPONSE:

            dfu_procedure = notify[1]
            dfu_result  = notify[2]

            # if verbose: print "opcode: {0}, proc: {1}, res: {2}".format(dfu_notify_opcode, procedure_str, result_str)
            logger.info(
                "0x%02x, proc: %s, res: %s",
                dfu_notify_opcode, Procedures.to_string(dfu_procedure), Results.to_string(dfu_result))

            # Packet Receipt notifications are sent in the exact same format
            # as responses to the CALC_CHECKSUM procedure.
            if(dfu_procedure == Procedures.CALC_CHECKSUM and dfu_result == Results.SUCCESS):
                offset = bytes_to_uint32_le(notify[3:7])
                crc32 = bytes_to_uint32_le(notify[7:11])

                logger.info('CALC_CHECKSUM, res:%s, offset:%X, crc:%X', Results.to_string(dfu_result), offset, crc32)

                return (dfu_procedure, dfu_result, offset, crc32)

            elif(dfu_procedure == Procedures.SELECT and dfu_result == Results.SUCCESS):
                max_size = bytes_to_uint32_le(notify[3:7])
                offset = bytes_to_uint32_le(notify[7:11])
                crc32 = bytes_to_uint32_le(notify[11:15])

                logger.info('SELECT, res:%s, max_size:%s, offset:%X, crc:%X', Results.to_string(dfu_result), max_size, offset, crc32)

                return (dfu_procedure, dfu_result, max_size, offset, crc32)

            else:
                logger.info('%s, res:%s', Procedures.to_string(dfu_procedure), Results.to_string(dfu_result))
                return (dfu_procedure, dfu_result)
        # op, result = notify[0], notify[1]
        # if result != Results.SUCCESS:
        #     raise Exception(f"DFU failed with result code {result}")
        # if op == Procedures.RESPONSE:
        #     if notify[2] == Procedures.SELECT:
        #         max_size = int.from_bytes(notify[4:8], 'little')
        #         offset = int.from_bytes(notify[8:12], 'little')
        #         crc = int.from_bytes(notify[12:16], 'little')
        #         return (Procedures.SELECT, Results.SUCCESS, max_size, offset, crc)
        #     elif notify[2] == Procedures.CALC_CHECKSUM:
        #         offset = int.from_bytes(notify[4:8], 'little')
        #         crc = int.from_bytes(notify[8:12], 'little')
        #         return (Procedures.CALC_CHECKSUM, Results.SUCCESS, offset, crc)
        #     return (Procedures.RESPONSE, Results.SUCCESS)
        # return None

    # --------------------------------------------------------------------------
    #  Wait for a notification and parse the response
    # --------------------------------------------------------------------------
    async def _wait_and_parse_notify(self):
        if verbose: print("Waiting for notification")
        notify = await self._dfu_wait_for_notify()

        if notify is None:
            raise Exception("No notification received")

        result = self._dfu_parse_notify(notify)
        if result[1] != Results.SUCCESS:
            raise Exception("Error in {} procedure, reason: {}".format(
                Procedures.to_string(result[0]),
                Results.to_string(result[1])))

        return result

    # --------------------------------------------------------------------------
    #  Send the Init info (*.dat file contents) to peripheral device.
    # --------------------------------------------------------------------------
    async def _dfu_send_init(self):
        with open(self.datfile_path, 'rb') as df:
            init_bin_array = df.read()
        init_size = len(init_bin_array)
        init_crc = 0

        # Select command
        await self._dfu_send_command(Procedures.SELECT, [Procedures.PARAM_COMMAND])
        _, _, max_size, offset, crc32 = await self._wait_and_parse_notify()

        if offset != init_size or crc32 != init_crc:
            if offset == 0 or offset > init_size:
                # Create command
                await self._dfu_send_command(Procedures.CREATE, [Procedures.PARAM_COMMAND] + uint32_to_bytes_le(init_size))
                res = await self._wait_and_parse_notify()
            segment_count = 0
            for i in range(0, init_size, self.pkt_payload_size):
                segment = init_bin_array[i:i + self.pkt_payload_size]
                await self._dfu_send_data(segment)
                segment_count += 1
                if (segment_count % self.pkt_receipt_interval) == 0:
                    (proc, res, offset, crc32) = await self._wait_and_parse_notify()
                    if res != Results.SUCCESS:
                        raise Exception("bad notification status: {}".format(Results.to_string(res)))

            # Calculate CRC
            await self._dfu_send_command(Procedures.CALC_CHECKSUM)
            await self._wait_and_parse_notify()

        # Execute command
        await self._dfu_send_command(Procedures.EXECUTE)
        await self._wait_and_parse_notify()

        print("Init packet successfully transferred")

    # --------------------------------------------------------------------------
    #  Send the Firmware image to peripheral device.
    # --------------------------------------------------------------------------
    async def _dfu_send_image(self):
        if verbose: print("dfu_send_image")

        # Select Data Object
        await self._dfu_send_command(Procedures.SELECT, [Procedures.PARAM_DATA])
        _, _, max_size, offset, crc32 = await self._wait_and_parse_notify()

        # Split the firmware into multiple objects
        num_objects = int(math.ceil(self.image_size / float(max_size)))
        print("Max object size: %d, num objects: %d, offset: %d, total size: %d" % (max_size, num_objects, offset, self.image_size))

        time_start = time.time()
        obj_offset = (offset // max_size) * max_size
        while obj_offset < self.image_size:
            obj_offset += await self._dfu_send_object(obj_offset, max_size)
        # Image uploaded successfully, update the progress bar
        print_progress(self.image_size, self.image_size)

        duration = time.time() - time_start
        print("\nUpload complete in {} minutes and {} seconds".format(int(duration / 60), int(duration % 60)))

    # --------------------------------------------------------------------------
    #  Send a single data object of given size and offset.
    # --------------------------------------------------------------------------
    async def _dfu_send_object(self, offset, obj_max_size):
        if offset != self.image_size:
            if offset == 0 or offset >= obj_max_size or crc32 != crc32_unsigned(self.bin_array[0:offset]):
                # Create Data Object
                size = min(obj_max_size, self.image_size - offset)
                await self._dfu_send_command(Procedures.CREATE, [Procedures.PARAM_DATA] + uint32_to_bytes_le(size))
                await self._wait_and_parse_notify()

            segment_count = 0
            segment_total = int(math.ceil(min(obj_max_size, self.image_size-offset)/float(self.pkt_payload_size)))

            segment_begin = offset
            segment_end = min(offset+obj_max_size, self.image_size)

            for i in range(segment_begin, segment_end, self.pkt_payload_size):
                num_bytes = min(self.pkt_payload_size, segment_end - i)
                segment = self.bin_array[i:i + num_bytes]
                await self._dfu_send_data(segment)
                segment_count += 1

                # print "j: {} i: {}, end: {}, bytes: {}, size: {} segment #{} of {}".format(
                #     offset, i, segment_end, num_bytes, self.image_size, segment_count, segment_total)

                if (segment_count % self.pkt_receipt_interval) == 0:
                    try:
                        (proc, res, offset, crc32) = await self._wait_and_parse_notify()
                    except Exception as e:
                        logger.warning('err, need to re-transmit object, %s', e)
                        return 0

                    if res != Results.SUCCESS:
                        raise Exception("bad notification status: {}".format(Results.to_string(res)))

                    local_crc = crc32_unsigned(self.bin_array[0:offset])
                    if crc32 != local_crc:
                        logger.warning('crc mismatch: %X != %X', crc32, local_crc)
                        # Something went wrong, need to re-transmit this object
                        return 0

                    print_progress(offset, self.image_size, barLength = 50)

            # Calculate CRC
            await self._dfu_send_command(Procedures.CALC_CHECKSUM)
            _, _, offset, crc32 = await self._wait_and_parse_notify()
            local_crc = crc32_unsigned(self.bin_array[0:offset])
            if(crc32 != local_crc):
                logger.warning('crc mismatch: %X != %X', crc32, local_crc)
                return 0
        # Execute command
        await self._dfu_send_command(Procedures.EXECUTE)
        await self._wait_and_parse_notify()

        # If everything executed correctly, return amount of bytes transferred
        return obj_max_size
