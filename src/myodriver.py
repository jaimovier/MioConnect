import re
import time
from serial.tools.list_ports import comports
from src.public.bglib import BGLib
import serial
from src.public.myohw import *
from src.myo import Myo
from src.config import Config
from pythonosc import udp_client


class MyoDriver:

    def __init__(self):
        self.serial = serial.Serial(port=self._detect_port(), baudrate=9600, dsrdtr=1)
        self.osc = udp_client.SimpleUDPClient('localhost', 3000)
        self.lib = BGLib()

        self.myos = []

        self.myo_to_connect = None
        self.scanning = False
        self.connected = False

    @staticmethod
    def _detect_port():
        """
        Detect COM port.
        :return: COM port with the expected ID
        """
        print("Detecting available ports")
        for p in comports():
            if re.search(r'PID=2458:0*1', p[2]):
                print('Port detected: ', p[0])
                print()
                return p[0]
        return None

    def receive(self):
        """
        Check for received evens and handle them.
        """
        self.lib.check_activity(self.serial)

    def send(self, msg):
        """
        Send given message through serial.
        :param msg: packed message to send
        """
        # A small delay is required for the Myo to process them correctly
        time.sleep(Config.MESSAGE_DELAY)
        self.lib.send_command(self.serial, msg)

    def handle_discover(self, e, payload):
        """
        Handler for ble_evt_gap_scan_response event.
        """
        if self.scanning and not self.myo_to_connect:
            print("Device found", payload['sender'])
            if payload['data'].endswith(bytes(Final.myo_id)):
                if not self._has_paired_with(payload['sender']):
                    self.myo_to_connect = Myo(payload['sender'])
                    print("Myo found", self.myo_to_connect.address)
                    print()
                    self.scanning = False

    def _has_paired_with(self, address):
        for m in self.myos:
            if m.address == address:
                return True
        return False

    def handle_connect(self, e, payload):
        """
        Handler for ble_rsp_gap_connect_direct event.
        """
        if not payload['result'] == 0:
            raise RuntimeError

    def handle_disconnect(self, e, payload):
        """
        Handle for ble_evt_connection_disconnected event.
        """
        print("Disconnected:", payload)

    def handle_connection_status(self, e, payload):
        """
        Handler for ble_evt_connection_status event.
        """
        if payload['address'] == self.myo_to_connect.address and payload['flags'] == 5:
            # print("Connection status: ", payload)
            self.connected = True
            self.myo_to_connect.set_id(payload['connection'])
            print("Connected with id", self.myo_to_connect.connectionId)

    def handle_attribute_value(self, e, payload):
        """
        Handler for EMG events, expected as a ble_evt_attclient_attribute_value event with handle 43, 46, 49 or 52.
        """
        # TODO: Send data via OSC.
        emg_handles = [
            ServiceHandles.EmgData0Characteristic,
            ServiceHandles.EmgData1Characteristic,
            ServiceHandles.EmgData2Characteristic,
            ServiceHandles.EmgData3Characteristic
        ]
        imu_handles = [
            ServiceHandles.IMUDataCharacteristic
        ]
        if payload['atthandle'] in emg_handles:
            self.handle_emg(payload)
        elif payload['atthandle'] in imu_handles:
            self.handle_imu(payload)
        else:
            for myo in self.myos:
                myo.handle_attribute_value(payload)
            if not payload['atthandle'] == ServiceHandles.DeviceName and \
                    not payload['atthandle'] == ServiceHandles.FirmwareVersionCharacteristic:
                print(e, payload)

    def handle_emg(self, payload):
        if Config.PRINT_EMG:
            print("EMG", payload['connection'], payload['atthandle'], payload['value'])

        # Send first sample
        data = payload['value'][0:8]
        builder = udp_client.OscMessageBuilder("/myo/emg")
        builder.add_arg(str(payload['connection']), 's')
        for i in data:
            builder.add_arg(i, 'i')
        self.osc.send(builder.build())

        # Send second message
        data = payload['value'][8:16]
        builder = udp_client.OscMessageBuilder("/myo/emg")
        builder.add_arg(str(payload['connection']), 's')
        for i in data:
            builder.add_arg(i, 'i')
        self.osc.send(builder.build())

    def handle_imu(self, payload):
        if Config.PRINT_IMU:
            print("IMU", payload['connection'], payload['atthandle'], payload['value'])

        # Send orientation
        data = payload['value'][0:8]
        builder = udp_client.OscMessageBuilder("/myo/orientation")
        builder.add_arg(str(payload['connection']), 's')
        for i in data:
            builder.add_arg(i, 'f')
        self.osc.send(builder.build())

        # Send accelerometer
        data = payload['value'][8:14]
        builder = udp_client.OscMessageBuilder("/myo/accel")
        builder.add_arg(str(payload['connection']), 's')
        for i in data:
            builder.add_arg(i, 'f')
        self.osc.send(builder.build())

        # Send gyroscope
        data = payload['value'][14:20]
        builder = udp_client.OscMessageBuilder("/myo/gyro")
        builder.add_arg(str(payload['connection']), 's')
        for i in data:
            builder.add_arg(i, 'f')
        self.osc.send(builder.build())

    def set_handlers(self):
        """
        Set handlers for relevant events.
        """
        self.lib.ble_evt_gap_scan_response.add(self.handle_discover)
        self.lib.ble_rsp_gap_connect_direct.add(self.handle_connect)
        self.lib.ble_evt_attclient_attribute_value.add(self.handle_attribute_value)
        self.lib.ble_evt_connection_disconnected.add(self.handle_disconnect)
        self.lib.ble_evt_connection_status.add(self.handle_connection_status)
        # self.lib.ble_rsp_attclient_read_by_handle.add(print)
        # self.bglib.ble_rsp_attclient_attribute_write.add(print)

    def disconnect_all(self):
        """
        Stop possible scanning and close all connections.
        """
        self.send(self.lib.ble_cmd_gap_end_procedure())
        self.send(self.lib.ble_cmd_connection_disconnect(0))
        self.send(self.lib.ble_cmd_connection_disconnect(1))
        self.send(self.lib.ble_cmd_connection_disconnect(2))

    def add_myo_connection(self):
        """
        Procedure for connection with the Myo Armband. Scans, connects, disables sleep and starts EMG stream.
        """
        # Add handlers for expected events
        self.set_handlers()

        # Discover
        print("Scanning")
        self.send(self.lib.ble_cmd_gap_discover(1))

        # Await response
        self.scanning = True
        while self.myo_to_connect is None:
            self.lib.check_activity(self.serial)

        # End gap
        self.send(self.lib.ble_cmd_gap_end_procedure())

        # Direct connection
        print("Connecting to", self.myo_to_connect.address)
        self.send(self.lib.ble_cmd_gap_connect_direct(self.myo_to_connect.address, *Final.direct_connection_tail))

        # Await response
        while self.myo_to_connect.connectionId is None or not self.connected:
            self.lib.check_activity(self.serial)

        # Notify successful connection with print and vibration
        print("Connection successful. Setting up...")
        print()
        self.send(self.lib.ble_cmd_attclient_attribute_write(self.myo_to_connect.connectionId,
                                                             ServiceHandles.CommandCharacteristic,
                                                             [MyoCommand.myohw_command_vibrate,
                                                              0x01,
                                                              VibrationType.myohw_vibration_short]))

        # Disable sleep
        self.send(self.lib.ble_cmd_attclient_attribute_write(self.myo_to_connect.connectionId,
                                                             ServiceHandles.CommandCharacteristic,
                                                             [MyoCommand.myohw_command_set_sleep_mode,
                                                              0x01,
                                                              SleepMode.myohw_sleep_mode_never_sleep]))

        # Start EMG
        self.send(self.lib.ble_cmd_attclient_attribute_write(self.myo_to_connect.connectionId,
                                                             ServiceHandles.CommandCharacteristic,
                                                             [MyoCommand.myohw_command_set_mode,
                                                              0x03,
                                                              Config.EMG_MODE,
                                                              Config.IMU_MODE,
                                                              Config.CLASSIFIER_MODE]))

        # Subscribe for IMU
        self.send(self.lib.ble_cmd_attclient_attribute_write(self.myo_to_connect.connectionId,
                                                             ServiceHandles.IMUDataDescriptor,
                                                             Final.subscribe_payload))

        # Subscribe for EMG
        self.send(self.lib.ble_cmd_attclient_attribute_write(self.myo_to_connect.connectionId,
                                                             ServiceHandles.EmgData0Descriptor,
                                                             Final.subscribe_payload))
        self.send(self.lib.ble_cmd_attclient_attribute_write(self.myo_to_connect.connectionId,
                                                             ServiceHandles.EmgData1Descriptor,
                                                             Final.subscribe_payload))
        self.send(self.lib.ble_cmd_attclient_attribute_write(self.myo_to_connect.connectionId,
                                                             ServiceHandles.EmgData2Descriptor,
                                                             Final.subscribe_payload))
        self.send(self.lib.ble_cmd_attclient_attribute_write(self.myo_to_connect.connectionId,
                                                             ServiceHandles.EmgData3Descriptor,
                                                             Final.subscribe_payload))

        self.myos.append(self.myo_to_connect)
        print("Myo ready", self.myo_to_connect.connectionId, self.myo_to_connect.address)
        self.myo_to_connect = None
        self.scanning = False
        self.connected = False
        print()

    def run(self, myo_amount):
        self.disconnect_all()
        while len(self.myos) < myo_amount:
            print("CONNECTING MYO " + str(len(self.myos) + 1) + " OUT OF " + str(myo_amount))
            print()
            self.add_myo_connection()

    def get_info(self):
        print("Getting myo info")
        print()
        for myo in self.myos:
            self.send(self.lib.ble_cmd_attclient_read_by_handle(myo.connectionId,
                                                                ServiceHandles.DeviceName))
            self.send(self.lib.ble_cmd_attclient_read_by_handle(myo.connectionId,
                                                                ServiceHandles.FirmwareVersionCharacteristic))
        while not self._myos_ready():
            self.receive()
        print("Myo list:")
        for myo in self.myos:
            print(" - " + str(myo))
        print()

    def _myos_ready(self):
        for m in self.myos:
            if not m.ready():
                return False
        return True
