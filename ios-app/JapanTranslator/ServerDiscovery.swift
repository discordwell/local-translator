import Foundation
import Network
import Combine

/// Handles Bonjour service discovery to find the translation server on the local network.
@MainActor
class ServerDiscovery: ObservableObject {
    /// The discovered server URL (e.g., "http://192.168.1.100:8000")
    @Published var serverURL: URL?

    /// Whether currently searching for a server
    @Published var isSearching: Bool = false

    /// Connection status message for display
    @Published var statusMessage: String = "Searching for server..."

    private var browser: NWBrowser?
    private var connection: NWConnection?

    private let serviceType = "_jptranslate._tcp"

    init() {
        startBrowsing()
    }

    deinit {
        browser?.cancel()
        connection?.cancel()
    }

    /// Start browsing for the translation server via Bonjour.
    func startBrowsing() {
        guard browser == nil else { return }

        isSearching = true
        statusMessage = "Searching for server..."

        let parameters = NWParameters()
        parameters.includePeerToPeer = true

        let browser = NWBrowser(for: .bonjour(type: serviceType, domain: nil), using: parameters)

        browser.stateUpdateHandler = { [weak self] state in
            Task { @MainActor in
                self?.handleBrowserStateChange(state)
            }
        }

        browser.browseResultsChangedHandler = { [weak self] results, changes in
            Task { @MainActor in
                self?.handleBrowseResults(results)
            }
        }

        browser.start(queue: .main)
        self.browser = browser
    }

    /// Stop browsing for services.
    func stopBrowsing() {
        browser?.cancel()
        browser = nil
        connection?.cancel()
        connection = nil
    }

    private func handleBrowserStateChange(_ state: NWBrowser.State) {
        switch state {
        case .ready:
            print("Browser ready")
        case .failed(let error):
            print("Browser failed: \(error)")
            statusMessage = "Network error"
            isSearching = false
        case .cancelled:
            print("Browser cancelled")
        default:
            break
        }
    }

    private func handleBrowseResults(_ results: Set<NWBrowser.Result>) {
        // Look for the first available translation server
        if let result = results.first {
            resolveEndpoint(result)
        } else {
            // No services found
            serverURL = nil
            statusMessage = "Searching for server..."
        }
    }

    private func resolveEndpoint(_ result: NWBrowser.Result) {
        // Cancel any existing connection
        connection?.cancel()

        let connection = NWConnection(to: result.endpoint, using: .tcp)

        connection.stateUpdateHandler = { [weak self] state in
            Task { @MainActor in
                self?.handleConnectionStateChange(state, for: result)
            }
        }

        connection.start(queue: .main)
        self.connection = connection
    }

    private func handleConnectionStateChange(_ state: NWConnection.State, for result: NWBrowser.Result) {
        switch state {
        case .ready:
            // Connection established - extract the resolved IP and port
            if let endpoint = connection?.currentPath?.remoteEndpoint,
               case .hostPort(let host, let port) = endpoint {
                let hostString: String
                switch host {
                case .ipv4(let ipv4):
                    hostString = "\(ipv4)"
                case .ipv6(let ipv6):
                    hostString = "[\(ipv6)]"
                case .name(let name, _):
                    hostString = name
                @unknown default:
                    hostString = "localhost"
                }

                let urlString = "http://\(hostString):\(port)"
                if let url = URL(string: urlString) {
                    self.serverURL = url
                    self.statusMessage = "Connected to \(hostString)"
                    self.isSearching = false
                    print("Resolved server: \(urlString)")
                }
            }

        case .failed(let error):
            print("Connection failed: \(error)")
            serverURL = nil
            statusMessage = "Connection failed"
            isSearching = false

        case .cancelled:
            break

        default:
            break
        }
    }

    /// Manually set server URL for testing or manual configuration.
    func setManualServer(host: String, port: Int) {
        let urlString = "http://\(host):\(port)"
        if let url = URL(string: urlString) {
            serverURL = url
            statusMessage = "Connected to \(host)"
            isSearching = false
        }
    }

    /// Refresh the server discovery.
    func refresh() {
        stopBrowsing()
        serverURL = nil
        startBrowsing()
    }
}
