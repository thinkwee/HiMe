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
