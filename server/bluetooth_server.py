"""
Bluetooth LE server for Japan Translator.

The Mac acts as a BLE peripheral, advertising a translation service.
The iPhone connects as a BLE central and sends/receives audio data.

BLE has limited MTU (~512 bytes), so we chunk audio data for transfer.
"""

import struct
from typing import Optional, Callable

import objc
from Foundation import NSObject, NSData
from CoreBluetooth import (
    CBUUID,
    CBPeripheralManager,
    CBMutableService,
    CBMutableCharacteristic,
    CBAdvertisementDataServiceUUIDsKey,
    CBAdvertisementDataLocalNameKey,
    CBATTErrorSuccess,
    CBCharacteristicPropertyWrite,
    CBCharacteristicPropertyWriteWithoutResponse,
    CBCharacteristicPropertyRead,
    CBCharacteristicPropertyNotify,
    CBAttributePermissionsReadable,
    CBAttributePermissionsWriteable,
    CBManagerStatePoweredOn,
)

# Service and Characteristic UUIDs
SERVICE_UUID = "12345678-1234-1234-1234-123456789ABC"
# Characteristic for sending audio TO the Mac (Japanese audio for JA->EN)
AUDIO_INPUT_UUID = "12345678-1234-1234-1234-123456789001"
# Characteristic for receiving translation results FROM the Mac
TRANSLATION_OUTPUT_UUID = "12345678-1234-1234-1234-123456789002"
# Characteristic for receiving audio FROM the Mac (Japanese audio for EN->JA)
AUDIO_OUTPUT_UUID = "12345678-1234-1234-1234-123456789003"
# Characteristic for sending commands (translation mode, etc.)
COMMAND_UUID = "12345678-1234-1234-1234-123456789004"

# Commands
CMD_JA_TO_EN = 0x01  # Japanese speech -> English text
CMD_EN_TO_JA = 0x02  # English speech -> Japanese speech
CMD_AUDIO_START = 0x10  # Start of audio data
CMD_AUDIO_CHUNK = 0x11  # Audio data chunk
CMD_AUDIO_END = 0x12    # End of audio data


class BluetoothServer(NSObject):
    """BLE Peripheral server for translation service."""

    def init(self):
        self = objc.super(BluetoothServer, self).init()
        if self is None:
            return None

        self._on_audio_received = None
        self._peripheral_manager = None
        self._service = None

        # Characteristics
        self._audio_input_char = None
        self._translation_output_char = None
        self._audio_output_char = None
        self._command_char = None

        # State
        self._is_advertising = False
        self._current_command = 0
        self._audio_buffer = bytearray()
        self._subscribed_centrals = []

        return self

    def setCallback_(self, callback):
        """Set the callback for when audio is received."""
        self._on_audio_received = callback

    def start(self):
        """Start the Bluetooth peripheral."""
        self._peripheral_manager = CBPeripheralManager.alloc().initWithDelegate_queue_(
            self, None
        )

    def stop(self):
        """Stop advertising and clean up."""
        if self._peripheral_manager and self._is_advertising:
            self._peripheral_manager.stopAdvertising()
            self._is_advertising = False

    def sendTextResponse_(self, text):
        """Send translated text back to the iPhone."""
        if self._translation_output_char and self._peripheral_manager:
            data = text.encode('utf-8')
            ns_data = NSData.dataWithBytes_length_(data, len(data))
            self._peripheral_manager.updateValue_forCharacteristic_onSubscribedCentrals_(
                ns_data, self._translation_output_char, None
            )

    def sendAudioResponse_(self, audio_data):
        """Send translated audio back to the iPhone in chunks."""
        import time
        from Foundation import NSRunLoop, NSDate

        if not self._audio_output_char or not self._peripheral_manager:
            print("Cannot send audio - no characteristic or peripheral manager")
            return

        chunk_size = 500  # BLE MTU limit (leaving room for overhead)
        total_chunks = (len(audio_data) + chunk_size - 1) // chunk_size
        print(f"Sending {len(audio_data)} bytes in {total_chunks} chunks...")

        # Send start marker with total size
        start_marker = struct.pack('>BI', CMD_AUDIO_START, len(audio_data))
        ns_data = NSData.dataWithBytes_length_(start_marker, len(start_marker))
        self._peripheral_manager.updateValue_forCharacteristic_onSubscribedCentrals_(
            ns_data, self._audio_output_char, None
        )

        # Small delay to let start marker through
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.01))

        # Send chunks with pacing
        sent_chunks = 0
        for i in range(0, len(audio_data), chunk_size):
            chunk = bytes([CMD_AUDIO_CHUNK]) + audio_data[i:i + chunk_size]
            ns_data = NSData.dataWithBytes_length_(chunk, len(chunk))

            # Try to send, retry if queue is full
            success = self._peripheral_manager.updateValue_forCharacteristic_onSubscribedCentrals_(
                ns_data, self._audio_output_char, None
            )

            sent_chunks += 1

            # Pace the sends - run loop briefly every few chunks
            if sent_chunks % 10 == 0:
                NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.005))

        print(f"Sent {sent_chunks} chunks")

        # Small delay before end marker
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.02))

        # Send end marker
        end_marker = bytes([CMD_AUDIO_END])
        ns_data = NSData.dataWithBytes_length_(end_marker, len(end_marker))
        self._peripheral_manager.updateValue_forCharacteristic_onSubscribedCentrals_(
            ns_data, self._audio_output_char, None
        )
        print("Audio send complete")

    # MARK: - CBPeripheralManagerDelegate methods

    def peripheralManagerDidUpdateState_(self, peripheral):
        """Called when Bluetooth state changes."""
        state = peripheral.state()

        if state == CBManagerStatePoweredOn:
            print("Bluetooth is powered on, setting up service...")
            self._setup_service()
        elif state == 4:  # CBManagerStatePoweredOff
            print("Bluetooth is powered off")
        else:
            print(f"Bluetooth state: {state}")

    def _setup_service(self):
        """Set up the GATT service and characteristics."""
        # Create characteristics
        self._audio_input_char = CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
            CBUUID.UUIDWithString_(AUDIO_INPUT_UUID),
            CBCharacteristicPropertyWrite | CBCharacteristicPropertyWriteWithoutResponse,
            None,
            CBAttributePermissionsWriteable
        )

        self._translation_output_char = CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
            CBUUID.UUIDWithString_(TRANSLATION_OUTPUT_UUID),
            CBCharacteristicPropertyNotify | CBCharacteristicPropertyRead,
            None,
            CBAttributePermissionsReadable
        )

        self._audio_output_char = CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
            CBUUID.UUIDWithString_(AUDIO_OUTPUT_UUID),
            CBCharacteristicPropertyNotify,
            None,
            CBAttributePermissionsReadable
        )

        self._command_char = CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
            CBUUID.UUIDWithString_(COMMAND_UUID),
            CBCharacteristicPropertyWrite | CBCharacteristicPropertyWriteWithoutResponse,
            None,
            CBAttributePermissionsWriteable
        )

        # Create service
        self._service = CBMutableService.alloc().initWithType_primary_(
            CBUUID.UUIDWithString_(SERVICE_UUID),
            True
        )
        self._service.setCharacteristics_([
            self._audio_input_char,
            self._translation_output_char,
            self._audio_output_char,
            self._command_char,
        ])

        # Add service
        self._peripheral_manager.addService_(self._service)

    def peripheralManager_didAddService_error_(self, peripheral, service, error):
        """Called when service is added."""
        if error:
            print(f"Error adding service: {error}")
            return

        print("Service added, starting advertising...")

        # Start advertising
        self._peripheral_manager.startAdvertising_({
            CBAdvertisementDataServiceUUIDsKey: [CBUUID.UUIDWithString_(SERVICE_UUID)],
            CBAdvertisementDataLocalNameKey: "LocalTranslator",
        })

    def peripheralManagerDidStartAdvertising_error_(self, peripheral, error):
        """Called when advertising starts."""
        if error:
            print(f"Error starting advertising: {error}")
            return

        self._is_advertising = True
        print("Bluetooth advertising started - waiting for iPhone connection...")

    def peripheralManager_didReceiveWriteRequests_(self, peripheral, requests):
        """Called when iPhone writes to a characteristic."""
        for request in requests:
            char_uuid = request.characteristic().UUID().UUIDString().upper()
            data = request.value()

            if data:
                # Convert NSData to bytes
                length = data.length()
                byte_data = data.bytes()
                if byte_data:
                    byte_data = bytes(byte_data[:length])

                    if char_uuid == COMMAND_UUID.upper():
                        self._handle_command(byte_data)
                    elif char_uuid == AUDIO_INPUT_UUID.upper():
                        self._handle_audio_data(byte_data)

            # Respond to the request
            peripheral.respondToRequest_withResult_(request, CBATTErrorSuccess)

    def peripheralManager_central_didSubscribeToCharacteristic_(self, peripheral, central, characteristic):
        """Called when a central subscribes to notifications."""
        print(f"Central subscribed to {characteristic.UUID().UUIDString()}")
        if central not in self._subscribed_centrals:
            self._subscribed_centrals.append(central)

    def peripheralManager_central_didUnsubscribeFromCharacteristic_(self, peripheral, central, characteristic):
        """Called when a central unsubscribes from notifications."""
        print(f"Central unsubscribed from {characteristic.UUID().UUIDString()}")
        if central in self._subscribed_centrals:
            self._subscribed_centrals.remove(central)

    def _handle_command(self, data):
        """Handle command from iPhone."""
        if len(data) < 1:
            return

        cmd = data[0]

        if cmd == CMD_JA_TO_EN:
            self._current_command = CMD_JA_TO_EN
            self._audio_buffer = bytearray()
            print("Mode: Japanese -> English")
        elif cmd == CMD_EN_TO_JA:
            self._current_command = CMD_EN_TO_JA
            self._audio_buffer = bytearray()
            print("Mode: English -> Japanese")
        elif cmd == CMD_AUDIO_START:
            self._audio_buffer = bytearray()
            print("Audio transfer started")
        elif cmd == CMD_AUDIO_END:
            print(f"Audio transfer complete: {len(self._audio_buffer)} bytes")
            # Trigger translation
            if self._on_audio_received:
                self._on_audio_received(bytes(self._audio_buffer), self._current_command)

    def _handle_audio_data(self, data):
        """Handle audio chunk from iPhone."""
        self._audio_buffer.extend(data)


# Global instance
_bluetooth_server = None


def get_bluetooth_server(on_audio_received):
    """Get or create the global Bluetooth server instance."""
    global _bluetooth_server
    if _bluetooth_server is None:
        _bluetooth_server = BluetoothServer.alloc().init()
        _bluetooth_server.setCallback_(on_audio_received)
    return _bluetooth_server
