import Foundation
import Security

/// Keychain-backed storage for the server bearer token.
///
/// The token used to live in UserDefaults, i.e. an unencrypted plist that is
/// included in iTunes/iCloud backups — leaking it grants full read/write access
/// to the health backend. `kSecAttrAccessibleAfterFirstUnlock` keeps it readable
/// by background HealthKit wakes while excluding it from backups.
private enum TokenKeychain {
    private static let service = "com.hime.serverAuth"
    private static let account = "serverAuthToken"

    private static var baseQuery: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }

    static func read() -> String? {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func write(_ value: String) {
        let query = baseQuery
        guard !value.isEmpty else {
            SecItemDelete(query as CFDictionary)
            return
        }
        let attributes: [String: Any] = [
            kSecValueData as String: Data(value.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
        ]
        if SecItemUpdate(query as CFDictionary, attributes as CFDictionary) == errSecSuccess { return }
        var insert = query
        insert.merge(attributes) { _, new in new }
        SecItemAdd(insert as CFDictionary, nil)
    }
}

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

    /// UserDefaults key the token used to live under, kept only for migration.
    private static let legacyTokenKey = "serverAuthToken"

    /// Bearer token for server authentication. Empty means no auth
    /// (fine for localhost). Set via the Settings screen. Stored in the
    /// Keychain; transparently migrated out of UserDefaults on first read.
    static var authToken: String {
        get {
            if let stored = TokenKeychain.read() { return stored }
            if let legacy = UserDefaults.standard.string(forKey: legacyTokenKey), !legacy.isEmpty {
                TokenKeychain.write(legacy)
                UserDefaults.standard.removeObject(forKey: legacyTokenKey)
                return legacy
            }
            return ""
        }
        set {
            TokenKeychain.write(newValue)
            UserDefaults.standard.removeObject(forKey: legacyTokenKey)
        }
    }

    // MARK: - Deferred onboarding survey
    //
    // The goal survey is captured during onboarding, BEFORE the user enters an
    // auth token (that happens later, in Settings). Posting it at capture time
    // would be unauthenticated → 401 → silently lost. So we stash the payload
    // locally and flush it the moment an auth token exists (SettingsView calls
    // flushPendingSurvey after the token is saved; ContentView flushes on launch).
    private static let pendingSurveyKey = "hime.pendingGoalSurvey"

    // Single-flight guard. flushPendingSurvey() is called from four places
    // (onboarding completion, ContentView launch, two SettingsView token-save
    // paths); without this, two calls can read the stash before either clears
    // it and each POSTs the same survey → duplicate onboarding rows.
    //
    // Isolated to the main actor: as a plain static Bool the check-then-set was
    // itself racy, so two concurrent callers could both pass the guard and
    // reproduce the very double-submit it exists to prevent.
    @MainActor private static var isFlushingSurvey = false

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
    /// pending, there is still no token, or a flush is already in progress.
    ///
    /// Idempotency: the stash is claimed (removed) BEFORE the POST so a
    /// concurrent flush sees nothing and can't double-submit; on any failure
    /// the payload is restored so the next launch/Settings flush retries it.
    @MainActor
    static func flushPendingSurvey() async {
        guard !isFlushingSurvey else { return }
        guard !authToken.isEmpty,
              let data = UserDefaults.standard.data(forKey: pendingSurveyKey) else { return }
        isFlushingSurvey = true
        defer { isFlushingSurvey = false }

        // Claim the stash up-front; restore it if we don't reach a 2xx.
        UserDefaults.standard.removeObject(forKey: pendingSurveyKey)

        let cfg = load()
        guard let url = URL(string: "\(cfg.apiBaseURL)/api/agent/onboarding-survey") else {
            UserDefaults.standard.set(data, forKey: pendingSurveyKey)
            return
        }
        var req = APIClient.request(url, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = data
        guard let (_, resp) = try? await URLSession.shared.data(for: req),
              let http = resp as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            UserDefaults.standard.set(data, forKey: pendingSurveyKey)
            return
        }
        // Success: stash already cleared above.
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
        // Strip the port. IPv6 literals are full of colons, so `lastIndex(of:)`
        // alone mangles them ("fd00::1" → "fd00:"); handle the bracketed and
        // bare forms explicitly.
        if s.hasPrefix("[") {
            // "[fd00::1]:8765" → "fd00::1"
            if let close = s.firstIndex(of: "]") {
                s = String(s[s.index(after: s.startIndex)..<close])
            }
        } else if s.filter({ $0 == ":" }).count <= 1, let idx = s.lastIndex(of: ":") {
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
