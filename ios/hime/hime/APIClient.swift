import Foundation

/// Builds a `URLRequest` with the configured auth token (if any).
///
/// When `ServerConfig.authToken` is non-empty, the request includes an
/// `Authorization: Bearer <token>` header so the backend's
/// `BearerAuthMiddleware` accepts it.
///
/// Usage:
///     let (data, _) = try await URLSession.shared.data(for: APIClient.request(url))
///     let (data, _) = try await URLSession.shared.data(for: APIClient.request(url, method: "POST"))
enum APIClient {
    static func request(_ url: URL, method: String = "GET") -> URLRequest {
        var req = URLRequest(url: url)
        req.httpMethod = method
        let token = ServerConfig.authToken
        if !token.isEmpty {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        return req
    }
}

/// Outcome of probing a server address with the configured credentials.
enum ServerProbeResult {
    case ok
    /// Reachable, but the bearer token was rejected (HTTP 401).
    case unauthorized
    case serverError
    case unreachable
    case invalidAddress
}

extension APIClient {
    /// "Test Connection": `/health` is public, so on its own it passes with a
    /// wrong token. Check reachability there, then hit an authenticated
    /// endpoint (`/api/agent/status`) with the current token so a bad token is
    /// reported distinctly instead of as a success.
    static func probe(_ cfg: ServerConfig) async -> ServerProbeResult {
        guard let healthURL = URL(string: "\(cfg.apiBaseURL)/health"),
              let authedURL = URL(string: "\(cfg.apiBaseURL)/api/agent/status") else {
            return .invalidAddress
        }
        var health = URLRequest(url: healthURL)
        health.timeoutInterval = 6
        do {
            let (_, response) = try await URLSession.shared.data(for: health)
            guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
                return .serverError
            }
        } catch {
            return .unreachable
        }

        var authed = request(authedURL)
        authed.timeoutInterval = 6
        do {
            let (_, response) = try await URLSession.shared.data(for: authed)
            guard let http = response as? HTTPURLResponse else { return .serverError }
            if http.statusCode == 401 { return .unauthorized }
            return (200...299).contains(http.statusCode) ? .ok : .serverError
        } catch {
            return .unreachable
        }
    }
}
