import Foundation

// A flexible payload for HealthKit samples.
// "ts": Unix timestamp
// "v":  The numeric value
// "f":  The feature name (e.g., "steps", "heart_rate")
// Wire example: {"ts":1709500042.3, "v":72.0, "f":"heart_rate"}
struct HealthPayload: Codable {
    let ts: Double
    let v:  Double
    let f:  String

    init(ts: Double, value: Double, feature: String) {
        self.ts = ts
        self.v = value
        self.f = feature
    }

    private enum CodingKeys: String, CodingKey {
        case ts, v, f
    }
}
