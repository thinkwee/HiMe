import Foundation

/// Derives all service URLs from a single user-provided server address.
///
/// Input examples:
///   - `192.168.1.100`    → local mode  (ws://ip:8765, http://ip:8000)
///   - `123.45.67.89`     → local mode  (same)
///   - `homelab.local`    → local mode  (ws://host:8765, http://host:8000) — mDNS
///   - `example.com`      → tunnel mode (wss://watch.example.com, https://api.example.com)
///
/// Legacy inputs are also accepted and migrated:
///   - `ws://192.168.1.100:8765` → extracted to `192.168.1.100`
///   - `wss://watch.example.com` → extracted to `example.com`
struct ServerConfig {

    static let defaultAddress = "192.168.1.100"

    /// The raw user-provided base address (just a host or domain, no scheme/port).
    let baseAddress: String

    /// Whether this looks like a plain IP (v4) rather than a domain name.
    var isLocal: Bool {
        let host = baseAddress
        if host == "localhost" { return true }
        // mDNS / Bonjour hostnames (e.g. "homelab.local") — use LAN port layout.
        if host.hasSuffix(".local") { return true }
        // Bare IPv4 dotted-decimal.
        let parts = host.split(separator: ".")
        return parts.count == 4 && parts.allSatisfy { $0.allSatisfy(\.isNumber) }
    }

    /// WebSocket URL for the Watch Exporter data sync.
    var watchURL: String {
        if isLocal {
            return "ws://\(baseAddress):8765"
        } else {
            return "wss://watch.\(baseAddress)"
        }
    }

    /// HTTP(S) base URL for the backend API (port 8000).
    var apiBaseURL: String {
        if isLocal {
            return "http://\(baseAddress):8000"
        } else {
            return "https://api.\(baseAddress)"
        }
    }

    /// HTTP(S) base URL for the Watch Exporter HTTP endpoints (port 8765).
    var watchHTTPBaseURL: String {
        if isLocal {
            return "http://\(baseAddress):8765"
        } else {
            return "https://watch.\(baseAddress)"
        }
    }

    /// Bearer token for server authentication. Empty means no auth
    /// (fine for localhost). Set via the Settings screen.
    static var authToken: String {
        get { UserDefaults.standard.string(forKey: "serverAuthToken") ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: "serverAuthToken") }
    }

    // MARK: - Deferred onboarding survey
    //
    // The goal survey is captured during onboarding, BEFORE the user enters an
    // auth token (that happens later, in Settings). Posting it at capture time
    // would be unauthenticated → 401 → silently lost. So we stash the payload
    // locally and flush it the moment an auth token exists (SettingsView calls
    // flushPendingSurvey after the token is saved; ContentView flushes on launch).
    private static let pendingSurveyKey = "hime.pendingGoalSurvey"

    static var hasPendingSurvey: Bool {
        UserDefaults.standard.data(forKey: pendingSurveyKey) != nil
    }

    /// Persist the survey payload (`["goals": …, "answers": …]`) locally.
    static func stashPendingSurvey(_ payload: [String: Any]) {
        if let data = try? JSONSerialization.data(withJSONObject: payload) {
            UserDefaults.standard.set(data, forKey: pendingSurveyKey)
        }
    }

    /// Re-POST a stashed survey once a token is available. No-op if nothing is
    /// pending or there is still no token. Clears the stash only on a 2xx.
    static func flushPendingSurvey() async {
        guard !authToken.isEmpty,
              let data = UserDefaults.standard.data(forKey: pendingSurveyKey) else { return }
        let cfg = load()
        guard let url = URL(string: "\(cfg.apiBaseURL)/api/agent/onboarding-survey") else { return }
        var req = APIClient.request(url, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = data
        guard let (_, resp) = try? await URLSession.shared.data(for: req),
              let http = resp as? HTTPURLResponse, (200...299).contains(http.statusCode) else { return }
        UserDefaults.standard.removeObject(forKey: pendingSurveyKey)
    }

    // MARK: - Persistence

    private static let key = "serverBaseAddress"

    /// Load from UserDefaults, migrating legacy formats if needed.
    static func load() -> ServerConfig {
        let raw = UserDefaults.standard.string(forKey: key)
            ?? migrateLegacy()
            ?? defaultAddress
        return ServerConfig(baseAddress: raw)
    }

    /// Save to UserDefaults.
    func save() {
        UserDefaults.standard.set(baseAddress, forKey: ServerConfig.key)
    }

    /// Migrate from old `serverURL` / `serverHost` keys.
    private static func migrateLegacy() -> String? {
        if let old = UserDefaults.standard.string(forKey: "serverURL"), !old.isEmpty {
            let extracted = extractBase(from: old)
            UserDefaults.standard.set(extracted, forKey: key)
            UserDefaults.standard.removeObject(forKey: "serverURL")
            UserDefaults.standard.removeObject(forKey: "serverHost")
            return extracted
        }
        if let old = UserDefaults.standard.string(forKey: "serverHost"), !old.isEmpty {
            UserDefaults.standard.set(old, forKey: key)
            UserDefaults.standard.removeObject(forKey: "serverHost")
            return old
        }
        return nil
    }

    /// Extract a base domain/IP from a full URL string.
    ///   "wss://watch.example.com" → "example.com"
    ///   "ws://192.168.1.100:8765" → "192.168.1.100"
    ///   "192.168.1.100"           → "192.168.1.100"
    ///   "example.com"             → "example.com"
    static func extractBase(from raw: String) -> String {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)

        for prefix in ["wss://", "ws://", "https://", "http://",
                        "wss:", "ws:", "https:", "http:"] {
            if s.hasPrefix(prefix) { s = String(s.dropFirst(prefix.count)); break }
        }
        if let idx = s.firstIndex(of: "/") { s = String(s[..<idx]) }
        if let idx = s.lastIndex(of: ":") {
            let after = String(s[s.index(after: idx)...])
            if after.allSatisfy(\.isNumber) { s = String(s[..<idx]) }
        }
        for sub in ["watch.", "api.", "dashboard."] {
            if s.hasPrefix(sub) {
                let stripped = String(s.dropFirst(sub.count))
                if stripped.contains(".") { s = stripped; break }
            }
        }
        return s
    }
}
