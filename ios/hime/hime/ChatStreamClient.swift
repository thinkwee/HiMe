//
//  ChatStreamClient.swift
//  hime
//
//  Live downlink for in-app chat. Connects to the backend's existing
//  per-user agent event stream (`/api/stream/agent`) — a *separate* socket
//  from `WebSocketClient` (which is the HealthKit uplink on port 8765). The
//  `?client=ios` marker lets the backend treat this connection as iOS
//  presence so it knows whether to deliver replies live (online) or via APNs
//  (offline). The app closes this socket when it backgrounds, so presence is
//  an accurate online signal.
//

import Foundation

@MainActor
final class ChatStreamClient: NSObject {
    private var task: URLSessionWebSocketTask?
    private var session: URLSession?
    private var pingTimer: Timer?
    private var isConnected = false

    /// Called on the main actor for every decoded agent event.
    var onEvent: (([String: Any]) -> Void)?

    /// Map the API base URL (http→ws, https→wss) and append the stream path.
    private func streamURL() -> URL? {
        let base = ServerConfig.load().apiBaseURL
        var wsBase = base
        if base.hasPrefix("https://") {
            wsBase = "wss://" + base.dropFirst("https://".count)
        } else if base.hasPrefix("http://") {
            wsBase = "ws://" + base.dropFirst("http://".count)
        }
        // Single-user backend: the agent monitor stream is keyed by user id
        // in the path (always "LiveUser" here). ?client=ios marks this socket
        // as the app's presence connection (drives the WS-vs-APNs decision).
        var comps = URLComponents(string: "\(wsBase)/api/stream/agent/LiveUser")
        comps?.queryItems = [URLQueryItem(name: "client", value: "ios")]
        return comps?.url
    }

    func connect() {
        guard !isConnected, let url = streamURL() else { return }
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 60
        let session = URLSession(configuration: cfg)
        // Bearer header rather than `?token=`: query strings are recorded verbatim
        // in reverse-proxy / tunnel access logs. `webSocketTask(with: URLRequest)`
        // carries custom headers through the HTTP upgrade, and the backend's
        // `_ws_token_ok` (backend/api/stream_routes.py) reads either form.
        var req = URLRequest(url: url)
        let token = ServerConfig.authToken
        if !token.isEmpty {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        let task = session.webSocketTask(with: req)
        self.session = session
        self.task = task
        isConnected = true
        task.resume()
        receiveLoop()
        startPing()
    }

    func disconnect() {
        isConnected = false
        pingTimer?.invalidate()
        pingTimer = nil
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        session?.invalidateAndCancel()
        session = nil
    }

    private func startPing() {
        pingTimer?.invalidate()
        pingTimer = Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in self.task?.sendPing { _ in } }
        }
    }

    private func receiveLoop() {
        task?.receive { [weak self] result in
            guard let self else { return }
            Task { @MainActor in
                guard self.isConnected else { return }
                switch result {
                case .failure:
                    // Socket dropped — let the view model reconnect on next appear/foreground.
                    self.isConnected = false
                case .success(let message):
                    switch message {
                    case .string(let text):
                        self.handle(text)
                    case .data(let data):
                        if let s = String(data: data, encoding: .utf8) { self.handle(s) }
                    @unknown default:
                        break
                    }
                    self.receiveLoop()
                }
            }
        }
    }

    private func handle(_ text: String) {
        guard let data = text.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return }
        onEvent?(obj)
    }
}
