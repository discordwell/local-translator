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
CMD_TEST_AUDIO = 0xFF   # Test: send sample audio without needing voice input


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
        self._ready_to_update = True  # Flow control flag

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
            # Debug logging (commented out):
            # print(f"[DEBUG] Text notification result: {result}")
            # audio_result = self._peripheral_manager.updateValue_forCharacteristic_onSubscribedCentrals_(
            #     ns_data, self._audio_output_char, None
            # )
            # print(f"[DEBUG] Audio notification (same pattern) result: {audio_result}")

    def sendAudioResponse_(self, audio_data):
        """Send translated audio back to the iPhone in chunks."""
        from Foundation import NSRunLoop, NSDate

        if not self._audio_output_char or not self._peripheral_manager:
            print("Cannot send audio - no characteristic or peripheral manager")
            return

        chunk_size = 182  # BLE notification chunk size
        total_chunks = (len(audio_data) + chunk_size - 1) // chunk_size
        print(f"Sending {len(audio_data)} bytes in {total_chunks} chunks...")

        sent_count = 0
        failed_count = 0
        first_failure_logged = False

        def pump(seconds):
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(seconds))

        def send_notification(data):
            """Send a single notification with debug logging."""
            nonlocal sent_count, failed_count, first_failure_logged

            result = self._peripheral_manager.updateValue_forCharacteristic_onSubscribedCentrals_(
                data, self._audio_output_char, None
            )

            # Debug: log first failure details (commented out)
            # if not result and not first_failure_logged:
            #     print(f"[DEBUG] First updateValue returned: {result} (type: {type(result)})")
            #     print(f"[DEBUG] Audio char UUID: {self._audio_output_char.UUID().UUIDString()}")
            #     print(f"[DEBUG] Subscribed centrals: {self._subscribed_centrals}")
            #     first_failure_logged = True

            if result:
                sent_count += 1
                return True
            else:
                # Wait for queue to drain and retry multiple times
                for retry in range(5):
                    self._ready_to_update = False
                    # Wait up to 500ms for callback, or use fixed delay
                    for _ in range(50):
                        pump(0.01)
                        if self._ready_to_update:
                            break
                    result = self._peripheral_manager.updateValue_forCharacteristic_onSubscribedCentrals_(
                        data, self._audio_output_char, None
                    )
                    if result:
                        sent_count += 1
                        return True
                failed_count += 1
                return False

        # Send start marker with total size
        # print(f"[AUDIO] Sending on characteristic UUID: {self._audio_output_char.UUID().UUIDString()}")
        start_marker = struct.pack('>BI', CMD_AUDIO_START, len(audio_data))
        # print(f"[AUDIO] START marker: {start_marker.hex()} (cmd={CMD_AUDIO_START}, size={len(audio_data)})")
        ns_data = NSData.dataWithBytes_length_(start_marker, len(start_marker))
        send_notification(ns_data)
        pump(0.01)

        # Send audio chunks with pacing
        for i in range(0, len(audio_data), chunk_size):
            chunk = bytes([CMD_AUDIO_CHUNK]) + audio_data[i:i + chunk_size]
            ns_data = NSData.dataWithBytes_length_(chunk, len(chunk))
            send_notification(ns_data)
            pump(0.01)  # 10ms between each chunk

        pump(0.02)

        # Send end marker
        end_marker = bytes([CMD_AUDIO_END])
        # print(f"[AUDIO] END marker: {end_marker.hex()} (cmd={CMD_AUDIO_END})")
        ns_data = NSData.dataWithBytes_length_(end_marker, len(end_marker))
        send_notification(ns_data)

        print(f"Audio send complete: {sent_count} sent, {failed_count} failed")

    def peripheralManagerIsReadyToUpdateSubscribers_(self, peripheral):
        """Called when the BLE queue has space again."""
        self._ready_to_update = True

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
        # Only set up once
        if self._service is not None:
            print("Service already set up, just re-advertising...")
            if not self._is_advertising:
                self._peripheral_manager.startAdvertising_({
                    CBAdvertisementDataServiceUUIDsKey: [CBUUID.UUIDWithString_(SERVICE_UUID)],
                    CBAdvertisementDataLocalNameKey: "LocalTranslator",
                })
            return

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
            CBCharacteristicPropertyNotify | CBCharacteristicPropertyRead,
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
        print(f"[DEBUG] Received {len(requests)} write request(s)")
        for request in requests:
            char_uuid = request.characteristic().UUID().UUIDString().upper()
            data = request.value()

            print(f"[DEBUG] Write to characteristic: {char_uuid}")
            if data:
                # Convert NSData to bytes
                length = data.length()
                byte_data = data.bytes()
                print(f"[DEBUG] Data length: {length}")
                if byte_data:
                    byte_data = bytes(byte_data[:length])
                    print(f"[DEBUG] First few bytes: {byte_data[:min(10, len(byte_data))]}")

                    if char_uuid == COMMAND_UUID.upper():
                        print("[DEBUG] -> Routing to command handler")
                        self._handle_command(byte_data)
                    elif char_uuid == AUDIO_INPUT_UUID.upper():
                        print(f"[DEBUG] -> Routing to audio handler ({len(byte_data)} bytes)")
                        self._handle_audio_data(byte_data)
                    else:
                        print(f"[DEBUG] -> Unknown characteristic: {char_uuid}")
            else:
                print("[DEBUG] No data in request")

            # Respond to the request
            peripheral.respondToRequest_withResult_(request, CBATTErrorSuccess)

    def peripheralManager_central_didSubscribeToCharacteristic_(self, peripheral, central, characteristic):
        """Called when a central subscribes to notifications."""
        print(f"Central subscribed to {characteristic.UUID().UUIDString()}")
        if central not in self._subscribed_centrals:
            self._subscribed_centrals.append(central)

        # Auto-test disabled - was interfering with real translations
        # text_subs = self._translation_output_char.subscribedCentrals() if self._translation_output_char else None
        # audio_subs = self._audio_output_char.subscribedCentrals() if self._audio_output_char else None
        # if text_subs and len(text_subs) > 0 and audio_subs and len(audio_subs) > 0:
        #     if not hasattr(self, '_test_sent') or not self._test_sent:
        #         self._test_sent = True
        #         print("\n*** AUTO-TEST: Both characteristics subscribed, sending test audio in 2 seconds... ***")
        #         from Foundation import NSTimer
        #         NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        #             2.0, self, '_runAutoTest', None, False
        #         )
        pass

    def _runAutoTest(self):
        """Timer callback to run auto test."""
        print("\n*** Running auto test... ***")
        self._send_test_audio()

    def scheduleAudioSend_(self, audio_data):
        """Schedule audio send via timer to let runloop process BLE events."""
        from Foundation import NSTimer
        self._pending_audio = audio_data
        print(f"Scheduling audio send ({len(audio_data)} bytes) in 1 second...")
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, self, '_runScheduledAudioSend', None, False
        )

    def _runScheduledAudioSend(self):
        """Timer callback to send scheduled audio."""
        if hasattr(self, '_pending_audio') and self._pending_audio:
            print("Running scheduled audio send...")
            self.sendAudioResponse_(self._pending_audio)
            self._pending_audio = None

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
        elif cmd == CMD_TEST_AUDIO:
            print("Test mode: sending sample audio...")
            self._send_test_audio()

    def _send_test_audio(self):
        """Send a small test audio sample to debug BLE notifications."""
        import numpy as np
        import io
        import soundfile as sf

        # Generate 0.5 second of 440Hz sine wave (A note)
        sample_rate = 16000
        duration = 0.5
        t = np.linspace(0, duration, int(sample_rate * duration), False)
        audio = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)

        # Convert to WAV bytes
        buffer = io.BytesIO()
        sf.write(buffer, audio, sample_rate, format='WAV')
        audio_data = buffer.getvalue()

        print(f"Generated {len(audio_data)} bytes of test audio")

        # Send text first to confirm that works
        self.sendTextResponse_("Test audio incoming...")

        # Now try to send audio
        self.sendAudioResponse_(audio_data)

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
