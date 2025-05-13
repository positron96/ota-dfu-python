#!/usr/bin/env python3
"""
------------------------------------------------------------------------------
 DFU Server for Nordic nRF51/nRF52 based systems.
 Conforms to nRF51_SDK 11.0 BLE_DFU requirements.
------------------------------------------------------------------------------
"""
import os
import argparse
import time
import math
import traceback

from unpacker import Unpacker

from ble_secure_dfu_controller import BleDfuControllerSecure
from ble_legacy_dfu_controller import BleDfuControllerLegacy

async def main():

    try:
        parser = argparse.ArgumentParser(
            description="DFU Server for Nordic nRF51/nRF52 based systems.",
            usage='%(prog)s -f <hex_file> -a <dfu_target_address>\n\nExample:\n\tdfu.py -f application.hex -d application.dat -a cd:e3:4a:47:1c:e4'
        )

        parser.add_argument('-a', '--address',
                            type=str,
                            required=True,
                            help='DFU target address.')

        parser.add_argument('-f', '--file',
                            type=str,
                            dest='hexfile'
                            help='Hex file to be uploaded.')

        parser.add_argument('-d', '--dat',
                            type=str,
                            dest='datfile',
                            help='DAT file to be uploaded.')

        parser.add_argument('-z', '--zip',
                            type=str,
                            dest=zipfile
                            help='Zip file to be used.')

        parser.add_argument('--secure',
                            action='store_true',
                            default=True,
                            help='Use secure bootloader (Nordic SDK > 12).')

        parser.add_argument('--legacy',
                            action='store_false',
                            dest='secure_dfu',
                            help='Use legacy bootloader (Nordic SDK < 12).')

        parser.add_argument('-v', '--verbose',
                            action='store_true',
                            help='Increase verbosity.')

        options = parser.parse_args()

    except Exception as e:
        print(e)
        parser.print_help()
        exit(2)

    try:
        ''' Validate input parameters '''

        if not options.address:
            parser.print_help()
            exit(2)

        unpacker = None
        hexfile = None
        datfile = None

        if options.zip:
            if options.hexfile or options.datfile:
                print("Conflicting input directives")
                exit(2)

            unpacker = Unpacker()
            try:
                hexfile, datfile = unpacker.unpack_zipfile(options.zipfile)
            except Exception as e:
                print("ERR")
                print(e)
                pass

        else:
            if not options.hexfile or not options.datfile:
                parser.print_help()
                exit(2)

            if not os.path.isfile(options.hexfile):
                print("Error: Hex file doesn't exist")
                exit(2)

            if not os.path.isfile(options.datfile):
                print("Error: DAT file doesn't exist")
                exit(2)

            hexfile = options.hexfile
            datfile = options.datfile

        ''' Start of Device Firmware Update processing '''
        if options.verbose:
            init_msg = \
"""
    ================================
    ==                            ==
    ==         DFU Server         ==
    ==                            ==
    ================================
"""
            print(init_msg)

        if options.secure_dfu:
            ble_dfu = BleDfuControllerSecure(options.address.upper(), hexfile, datfile)
        else:
            ble_dfu = BleDfuControllerLegacy(options.address.upper(), hexfile, datfile)

        # Initialize inputs
        await ble_dfu.input_setup()

        # Connect to peer device. Assume application mode.
        if await ble_dfu.scan_and_connect():
            if not await ble_dfu.check_DFU_mode():
                print("Need to switch to DFU mode")
                success = await ble_dfu.switch_to_dfu_mode()
                if not success:
                    print("Couldn't reconnect")
        else:
            # The device might already be in DFU mode (MAC + 1)
            ble_dfu.target_mac_increase(1)

            # Try connection with new address
            print("Couldn't connect, will try DFU MAC")
            if not await ble_dfu.scan_and_connect():
                raise Exception("Can't connect to device")

        await ble_dfu.start()

        # Disconnect from peer device if not done already and clean up.
        await ble_dfu.disconnect()

        # If Unpacker for zipfile used then delete Unpacker
        if unpacker is not None:
            unpacker.delete()

    except Exception as e:
        print(f"Exception at line {traceback.format_exc()}: {e}")
        pass

    if options.verbose:
        print("DFU Server done")

"""
------------------------------------------------------------------------------

------------------------------------------------------------------------------
"""
if __name__ == '__main__':
    asyncio.run(main())
