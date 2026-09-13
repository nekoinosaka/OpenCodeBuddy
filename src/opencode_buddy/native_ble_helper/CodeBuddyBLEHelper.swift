import AppKit
import CoreBluetooth
import Foundation

private let targetService = CBUUID(string: "6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
private let rxUUID = CBUUID(string: "6E400002-B5A3-F393-E0A9-E50E24DCCA9E")
private let txUUID = CBUUID(string: "6E400003-B5A3-F393-E0A9-E50E24DCCA9E")

private struct Config {
    let sessionDir: URL
    let commandsDir: URL
    let eventsURL: URL
    let deviceID: String?
    let deviceName: String?

    static func parse() throws -> Config {
        var args = Array(CommandLine.arguments.dropFirst())
        func take(_ flag: String) -> String? {
            guard let idx = args.firstIndex(of: flag), idx + 1 < args.count else { return nil }
            let value = args[idx + 1]
            args.removeSubrange(idx...(idx + 1))
            return value
        }

        guard let session = take("--session-dir") else {
            throw NSError(domain: "CodeBuddyBLEHelper", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "Missing --session-dir",
            ])
        }
        let deviceID = take("--device-id").flatMap { $0.isEmpty ? nil : $0 }
        let deviceName = take("--device-name").flatMap { $0.isEmpty ? nil : $0 }
        let sessionDir = URL(fileURLWithPath: session, isDirectory: true)
        return Config(
            sessionDir: sessionDir,
            commandsDir: sessionDir.appendingPathComponent("commands", isDirectory: true),
            eventsURL: sessionDir.appendingPathComponent("events.jsonl", isDirectory: false),
            deviceID: deviceID,
            deviceName: deviceName
        )
    }
}

private struct CommandEnvelope: Decodable {
    let seq: Int
    let op: String
    let line: String?
}

final class AppDelegate: NSObject, NSApplicationDelegate, CBCentralManagerDelegate, CBPeripheralDelegate {
    private var window: NSWindow?
    private var textView: NSTextView?
    private var config: Config!
    private var central: CBCentralManager!
    private var peripheral: CBPeripheral?
    private var rxChar: CBCharacteristic?
    private var txChar: CBCharacteristic?
    private var commandTimer: Timer?
    private var scanStartedAt = Date()
    private var rxBuffer = Data()
    private var pendingChunks: [Data] = []
    private var activeSeq: Int?
    private var ready = false
    private var stopping = false
    private var showsDebugWindow: Bool {
        ProcessInfo.processInfo.environment["CODE_BUDDY_BLE_HELPER_DEBUG_WINDOW"] == "1"
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        do {
            config = try Config.parse()
            try FileManager.default.createDirectory(at: config.commandsDir, withIntermediateDirectories: true)
            try Data().write(to: config.eventsURL, options: .atomic)
        } catch {
            fputs("CodeBuddyBLEHelper launch failed: \(error)\n", stderr)
            NSApp.terminate(nil)
            return
        }

        if showsDebugWindow {
            buildWindow()
        }
        emit(["event": "launch"])

        emit(["event": "central_create_started"])
        central = CBCentralManager(delegate: self, queue: nil)
        emit(["event": "central_created"])
        if showsDebugWindow {
            NSApp.activate(ignoringOtherApps: true)
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        commandTimer?.invalidate()
        commandTimer = nil
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        showsDebugWindow
    }

    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        emit(["event": "central_state", "value": central.state.rawValue])
        guard central.state == .poweredOn else {
            if central.state == .unauthorized || central.state == .unsupported || central.state == .poweredOff {
                emitError("Bluetooth unavailable: state=\(central.state.rawValue)")
            }
            return
        }

        scanStartedAt = Date()
        central.scanForPeripherals(withServices: nil, options: [
            CBCentralManagerScanOptionAllowDuplicatesKey: false,
        ])
        emit(["event": "scan_started"])
    }

    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral, advertisementData: [String : Any], rssi RSSI: NSNumber) {
        let name = peripheral.name ?? ""
        let identifier = peripheral.identifier.uuidString.uppercased()
        let serviceUUIDs = (advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID] ?? [])
            .map(\.uuidString)
        emit([
            "event": "discovered",
            "name": name,
            "identifier": identifier,
            "rssi": RSSI.intValue,
            "service_uuids": serviceUUIDs,
        ])

        if matches(peripheral: peripheral, advertisementData: advertisementData) {
            self.peripheral = peripheral
            peripheral.delegate = self
            central.stopScan()
            emit(["event": "connect_started", "identifier": identifier, "name": name])
            central.connect(peripheral)
        }
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        emit([
            "event": "connected_transport",
            "identifier": peripheral.identifier.uuidString.uppercased(),
            "name": peripheral.name ?? "",
        ])
        peripheral.discoverServices([targetService])
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        emitError("connect failed: \(error?.localizedDescription ?? "unknown")")
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        ready = false
        rxChar = nil
        txChar = nil
        emit([
            "event": "disconnected",
            "identifier": peripheral.identifier.uuidString.uppercased(),
            "name": peripheral.name ?? "",
            "error": error?.localizedDescription ?? "",
        ])
        if stopping {
            NSApp.terminate(nil)
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        if let error {
            emitError("discover services failed: \(error.localizedDescription)")
            return
        }
        peripheral.services?.forEach { service in
            peripheral.discoverCharacteristics([txUUID, rxUUID], for: service)
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        if let error {
            emitError("discover characteristics failed: \(error.localizedDescription)")
            return
        }
        service.characteristics?.forEach { characteristic in
            if characteristic.uuid == rxUUID {
                rxChar = characteristic
            }
            if characteristic.uuid == txUUID {
                txChar = characteristic
            }
        }

        if let txChar {
            peripheral.setNotifyValue(true, for: txChar)
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        if let error {
            emitError("start notify failed: \(error.localizedDescription)")
            return
        }
        guard characteristic.uuid == txUUID, characteristic.isNotifying else { return }
        ready = true
        if commandTimer == nil {
            commandTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
                self?.tick()
            }
        }
        emit([
            "event": "connected",
            "identifier": peripheral.identifier.uuidString.uppercased(),
            "name": peripheral.name ?? "",
        ])
    }

    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        if let error {
            let seq = activeSeq ?? -1
            activeSeq = nil
            pendingChunks.removeAll()
            emit([
                "event": "command_error",
                "seq": seq,
                "message": error.localizedDescription,
            ])
            return
        }
        sendNextChunk()
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard error == nil, let data = characteristic.value else { return }
        rxBuffer.append(data)
        while let newline = rxBuffer.firstIndex(of: 0x0A) {
            let lineData = rxBuffer[..<newline]
            rxBuffer.removeSubrange(...newline)
            guard !lineData.isEmpty else { continue }
            handleNotification(lineData)
        }
    }

    private func tick() {
        if central.state == .poweredOn && peripheral == nil && Date().timeIntervalSince(scanStartedAt) > 12 {
            emitError("Timed out scanning for BLE buddy")
            stopping = true
            NSApp.terminate(nil)
            return
        }

        guard ready else { return }
        guard activeSeq == nil else { return }

        let files: [URL]
        do {
            files = try FileManager.default.contentsOfDirectory(
                at: config.commandsDir,
                includingPropertiesForKeys: nil
            )
            .filter { $0.pathExtension == "json" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
        } catch {
            emitError("Failed to read command directory: \(error.localizedDescription)")
            return
        }

        guard let next = files.first else { return }
        do {
            let data = try Data(contentsOf: next)
            try FileManager.default.removeItem(at: next)
            let envelope = try JSONDecoder().decode(CommandEnvelope.self, from: data)
            handleCommand(envelope)
        } catch {
            emitError("Failed to process command file \(next.lastPathComponent): \(error.localizedDescription)")
        }
    }

    private func handleCommand(_ envelope: CommandEnvelope) {
        switch envelope.op {
        case "write_json":
            guard let line = envelope.line, let rxChar, let peripheral else {
                emit([
                    "event": "command_error",
                    "seq": envelope.seq,
                    "message": "Missing line or RX characteristic",
                ])
                return
            }
            let rawLine = line.hasSuffix("\n") ? line : line + "\n"
            let raw = Data(rawLine.utf8)
            let mtu = 180
            pendingChunks = stride(from: 0, to: raw.count, by: mtu).map { idx in
                raw.subdata(in: idx..<min(raw.count, idx + mtu))
            }
            activeSeq = envelope.seq
            peripheral.writeValue(pendingChunks.removeFirst(), for: rxChar, type: .withResponse)
        case "shutdown":
            emit(["event": "ack", "seq": envelope.seq])
            stopping = true
            if let peripheral {
                central.cancelPeripheralConnection(peripheral)
            } else {
                NSApp.terminate(nil)
            }
        default:
            emit([
                "event": "command_error",
                "seq": envelope.seq,
                "message": "Unsupported op: \(envelope.op)",
            ])
        }
    }

    private func sendNextChunk() {
        guard let seq = activeSeq else { return }
        if pendingChunks.isEmpty {
            activeSeq = nil
            emit(["event": "ack", "seq": seq])
            return
        }
        guard let peripheral, let rxChar else {
            activeSeq = nil
            pendingChunks.removeAll()
            emit([
                "event": "command_error",
                "seq": seq,
                "message": "Missing peripheral or RX characteristic",
            ])
            return
        }
        peripheral.writeValue(pendingChunks.removeFirst(), for: rxChar, type: .withResponse)
    }

    private func handleNotification(_ lineData: Data) {
        guard
            let object = try? JSONSerialization.jsonObject(with: lineData) as? [String: Any],
            let cmd = object["cmd"] as? String
        else {
            emit(["event": "notification", "line": String(decoding: lineData, as: UTF8.self)])
            return
        }

        if cmd == "permission" {
            emit([
                "event": "permission",
                "id": object["id"] as? String ?? "",
                "decision": object["decision"] as? String ?? "",
            ])
            return
        }

        if cmd == "question" {
            emit([
                "event": "question",
                "id": object["id"] as? String ?? "",
                "answers": object["answers"] as? [[String]] ?? [],
                "reject": object["reject"] as? Bool ?? false,
            ])
            return
        }

        emit([
            "event": "notification",
            "line": String(decoding: lineData, as: UTF8.self),
        ])
    }

    private func matches(peripheral: CBPeripheral, advertisementData: [String: Any]) -> Bool {
        if config.deviceID?.uppercased() == "__SCAN_ONLY__" {
            return false
        }
        let identifier = peripheral.identifier.uuidString.uppercased()
        if let expected = config.deviceID?.uppercased(), expected == identifier {
            return true
        }

        let name = (peripheral.name ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if let expectedName = config.deviceName?.trimmingCharacters(in: .whitespacesAndNewlines), !expectedName.isEmpty {
            return name == expectedName
        }

        let localName = (advertisementData[CBAdvertisementDataLocalNameKey] as? String ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let serviceUUIDs = (advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID] ?? [])
            .map { $0.uuidString.uppercased() }
        return name.hasPrefix("OpenCode-")
            || localName.hasPrefix("OpenCode-")
            || serviceUUIDs.contains(targetService.uuidString.uppercased())
    }

    private func buildWindow() {
        let frame = NSRect(x: 0, y: 0, width: 640, height: 360)
        let debugWindow = NSWindow(
            contentRect: frame,
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        debugWindow.title = "CodeBuddy BLE Helper"
        let scroll = NSScrollView(frame: frame)
        scroll.hasVerticalScroller = true
        scroll.autoresizingMask = [.width, .height]
        let logView = NSTextView(frame: frame)
        logView.isEditable = false
        logView.isRichText = false
        logView.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        logView.autoresizingMask = [.width, .height]
        scroll.documentView = logView
        debugWindow.contentView = scroll
        debugWindow.center()
        debugWindow.makeKeyAndOrderFront(nil)
        textView = logView
        window = debugWindow
    }

    private func emit(_ payload: [String: Any]) {
        guard JSONSerialization.isValidJSONObject(payload) else { return }
        guard let data = try? JSONSerialization.data(withJSONObject: payload), var line = String(data: data, encoding: .utf8) else { return }
        line.append("\n")
        if let storage = textView?.textStorage {
            storage.append(NSAttributedString(string: line))
            textView?.scrollToEndOfDocument(nil)
        }
        if let handle = try? FileHandle(forWritingTo: config.eventsURL) {
            try? handle.seekToEnd()
            try? handle.write(contentsOf: Data(line.utf8))
            try? handle.close()
        }
    }

    private func emitError(_ message: String) {
        emit([
            "event": "error",
            "message": message,
        ])
    }
}

@main
struct CodeBuddyBLEHelperMain {
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        let showsDebugWindow = ProcessInfo.processInfo.environment["CODE_BUDDY_BLE_HELPER_DEBUG_WINDOW"] == "1"
        app.setActivationPolicy(showsDebugWindow ? .regular : .accessory)
        app.delegate = delegate
        app.run()
    }
}
