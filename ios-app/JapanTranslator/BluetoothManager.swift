import Foundation
import CoreBluetooth
import Combine

/// Manages Bluetooth LE communication with the Mac translation server.
/// The iPhone acts as a BLE Central, connecting to the Mac's peripheral.
@MainActor
class BluetoothManager: NSObject, ObservableObject {
    // Connection state
    @Published var isConnected: Bool = false
    @Published var isScanning: Bool = false
    @Published var statusMessage: String = "Searching for Mac..."

    // Translation results
    @Published var translatedText: String = ""
    @Published var receivedAudio: Data?

    // UUIDs (must match server)
    private let serviceUUID = CBUUID(string: "12345678-1234-1234-1234-123456789ABC")
    private let audioInputUUID = CBUUID(string: "12345678-1234-1234-1234-123456789001")
    private let translationOutputUUID = CBUUID(string: "12345678-1234-1234-1234-123456789002")
    private let audioOutputUUID = CBUUID(string: "12345678-1234-1234-1234-123456789003")
    private let commandUUID = CBUUID(string: "12345678-1234-1234-1234-123456789004")

    // Commands
    private let CMD_JA_TO_EN: UInt8 = 0x01
    private let CMD_EN_TO_JA: UInt8 = 0x02
    private let CMD_AUDIO_START: UInt8 = 0x10
    private let CMD_AUDIO_CHUNK: UInt8 = 0x11
    private let CMD_AUDIO_END: UInt8 = 0x12

    // CoreBluetooth
    private var centralManager: CBCentralManager!
    private var peripheral: CBPeripheral?
    private var audioInputCharacteristic: CBCharacteristic?
    private var translationOutputCharacteristic: CBCharacteristic?
    private var audioOutputCharacteristic: CBCharacteristic?
    private var commandCharacteristic: CBCharacteristic?

    // Audio buffer for receiving chunked audio
    private var audioReceiveBuffer = Data()
    private var expectedAudioSize: Int = 0

    override init() {
        super.init()
        centralManager = CBCentralManager(delegate: self, queue: nil)
    }

    /// Start scanning for the Mac translation server.
    func startScanning() {
        guard centralManager.state == .poweredOn else {
            statusMessage = "Bluetooth is off"
            return
        }

        isScanning = true
        statusMessage = "Searching for Mac..."
        centralManager.scanForPeripherals(withServices: [serviceUUID], options: nil)
    }

    /// Stop scanning.
    func stopScanning() {
        centralManager.stopScan()
        isScanning = false
    }

    /// Disconnect from the Mac.
    func disconnect() {
        if let peripheral = peripheral {
            centralManager.cancelPeripheralConnection(peripheral)
        }
        peripheral = nil
        isConnected = false
    }

    /// Send Japanese audio for translation to English text.
    func translateJapaneseToEnglish(audioData: Data) {
        guard isConnected else { return }

        // Send command
        sendCommand(CMD_JA_TO_EN)

        // Send audio
        sendAudio(audioData)
    }

    /// Send English audio for translation to Japanese audio.
    func translateEnglishToJapanese(audioData: Data) {
        guard isConnected else { return }

        // Send command
        sendCommand(CMD_EN_TO_JA)

        // Send audio
        sendAudio(audioData)
    }

    private func sendCommand(_ command: UInt8) {
        guard let char = commandCharacteristic, let peripheral = peripheral else { return }

        let data = Data([command])
        peripheral.writeValue(data, for: char, type: .withResponse)
    }

    private func sendAudio(_ audioData: Data) {
        guard let char = audioInputCharacteristic, let peripheral = peripheral else { return }

        let chunkSize = 512 // BLE MTU limit

        // Send start command
        sendCommand(CMD_AUDIO_START)

        // Send audio in chunks
        var offset = 0
        while offset < audioData.count {
            let end = min(offset + chunkSize, audioData.count)
            let chunk = audioData[offset..<end]
            peripheral.writeValue(chunk, for: char, type: .withResponse)
            offset = end
        }

        // Send end command
        sendCommand(CMD_AUDIO_END)
    }
}

// MARK: - CBCentralManagerDelegate

extension BluetoothManager: CBCentralManagerDelegate {
    nonisolated func centralManagerDidUpdateState(_ central: CBCentralManager) {
        Task { @MainActor in
            switch central.state {
            case .poweredOn:
                statusMessage = "Bluetooth ready"
                startScanning()
            case .poweredOff:
                statusMessage = "Bluetooth is off"
                isConnected = false
            case .unauthorized:
                statusMessage = "Bluetooth unauthorized"
            case .unsupported:
                statusMessage = "Bluetooth unsupported"
            default:
                statusMessage = "Bluetooth unavailable"
            }
        }
    }

    nonisolated func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                                    advertisementData: [String: Any], rssi RSSI: NSNumber) {
        Task { @MainActor in
            print("Found peripheral: \(peripheral.name ?? "Unknown")")

            self.peripheral = peripheral
            self.stopScanning()
            self.statusMessage = "Connecting to Mac..."

            central.connect(peripheral, options: nil)
        }
    }

    nonisolated func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        Task { @MainActor in
            print("Connected to peripheral")
            self.statusMessage = "Connected, discovering services..."

            peripheral.delegate = self
            peripheral.discoverServices([serviceUUID])
        }
    }

    nonisolated func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        Task { @MainActor in
            print("Disconnected from peripheral")
            self.isConnected = false
            self.statusMessage = "Disconnected"
            self.peripheral = nil

            // Try to reconnect
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                self.startScanning()
            }
        }
    }

    nonisolated func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        Task { @MainActor in
            print("Failed to connect: \(error?.localizedDescription ?? "Unknown error")")
            self.statusMessage = "Connection failed"

            // Retry
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                self.startScanning()
            }
        }
    }
}

// MARK: - CBPeripheralDelegate

extension BluetoothManager: CBPeripheralDelegate {
    nonisolated func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        Task { @MainActor in
            guard error == nil else {
                print("Service discovery error: \(error!)")
                return
            }

            guard let services = peripheral.services else { return }

            for service in services {
                if service.uuid == serviceUUID {
                    peripheral.discoverCharacteristics([
                        audioInputUUID,
                        translationOutputUUID,
                        audioOutputUUID,
                        commandUUID
                    ], for: service)
                }
            }
        }
    }

    nonisolated func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        Task { @MainActor in
            guard error == nil else {
                print("Characteristic discovery error: \(error!)")
                return
            }

            guard let characteristics = service.characteristics else { return }

            for char in characteristics {
                switch char.uuid {
                case audioInputUUID:
                    self.audioInputCharacteristic = char
                case translationOutputUUID:
                    self.translationOutputCharacteristic = char
                    peripheral.setNotifyValue(true, for: char)
                case audioOutputUUID:
                    self.audioOutputCharacteristic = char
                    peripheral.setNotifyValue(true, for: char)
                case commandUUID:
                    self.commandCharacteristic = char
                default:
                    break
                }
            }

            // Check if we have all characteristics
            if audioInputCharacteristic != nil &&
               translationOutputCharacteristic != nil &&
               audioOutputCharacteristic != nil &&
               commandCharacteristic != nil {
                self.isConnected = true
                self.statusMessage = "Connected to Mac"
                print("All characteristics discovered, ready for translation")
            }
        }
    }

    nonisolated func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        Task { @MainActor in
            guard error == nil, let data = characteristic.value else { return }

            if characteristic.uuid == translationOutputUUID {
                // Received translated text
                if let text = String(data: data, encoding: .utf8) {
                    self.translatedText = text
                    print("Received translation: \(text)")
                }
            } else if characteristic.uuid == audioOutputUUID {
                // Received audio data (chunked)
                handleAudioChunk(data)
            }
        }
    }

    private func handleAudioChunk(_ data: Data) {
        guard data.count >= 1 else { return }

        let command = data[0]

        switch command {
        case CMD_AUDIO_START:
            // Start of audio, read expected size
            if data.count >= 5 {
                expectedAudioSize = Int(data[1]) << 24 | Int(data[2]) << 16 | Int(data[3]) << 8 | Int(data[4])
                audioReceiveBuffer = Data()
                print("Audio transfer started, expecting \(expectedAudioSize) bytes")
            }
        case CMD_AUDIO_CHUNK:
            // Audio chunk
            if data.count > 1 {
                audioReceiveBuffer.append(data[1...])
            }
        case CMD_AUDIO_END:
            // End of audio
            print("Audio transfer complete: \(audioReceiveBuffer.count) bytes")
            receivedAudio = audioReceiveBuffer
            audioReceiveBuffer = Data()
        default:
            break
        }
    }
}
