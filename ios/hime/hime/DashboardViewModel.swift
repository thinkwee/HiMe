import Foundation
import Combine

// MARK: - API Response Models

struct AgentStatusResponse: Decodable {
    let success: Bool
    let running: Bool?
    let status: AgentStatus?
    let config: AgentConfig?

    struct AgentStatus: Decodable {
        let state: String?
        let analysis_state: String?
        let cycle_count: Int?
        let last_analysis_time: String?
        let cumulative_tokens: CumulativeTokens?
        let data_store_stats: DataStoreStats?
    }

    struct AgentConfig: Decodable {
        let llm_provider: String?
        let model: String?
    }

    struct CumulativeTokens: Decodable {
        let prompt_tokens: Int?
        let completion_tokens: Int?
        let thoughts_tokens: Int?
    }

    struct DataStoreStats: Decodable {
        let total_records: Int?
    }
}

struct ReportsResponse: Decodable {
    let success: Bool
    let data: [AgentReport]?
}

struct AgentReport: Identifiable, Decodable {
    var id: Int { _id }
    let _id: Int
    let created_at: String?
    let title: String?
    let content: String
    let alert_level: String?

    enum CodingKeys: String, CodingKey {
        case _id = "id"
        case created_at
        case title
        case content
        case alert_level
    }
}

struct FeatureTypesResponse: Decodable {
    let success: Bool
    let feature_types: [String]?
}

// MARK: - Health Metric Models

struct DataPoint: Identifiable {
    let id = UUID()
    let date: Date
    let value: Double
}

/// A sleep stage block: start time, duration, and stage type
struct SleepBlock: Identifiable {
    let id = UUID()
    let start: Date
    let durationMinutes: Double
    let stage: SleepStage
}

enum SleepStage: String, CaseIterable {
    case deep = "Deep"
    case core = "Core"
    case rem = "REM"
    case awake = "Awake"
    case inBed = "In Bed"
}

/// How a metric should be rendered in the expanded chart card.
enum ChartStyle {
    case line       // Continuous line + area fill (heart_rate, blood_oxygen, etc.)
    case bar        // Vertical bars for daily totals (steps, energy, etc.)
    case point      // Scatter dots + optional trend line (mobility metrics)
    case sleepBar   // Horizontal stacked bar (sleep stages)
}

/// Y-axis domain configuration for a metric chart.
struct YAxisConfig {
    let min: Double?   // nil = auto from data
    let max: Double?   // nil = auto from data
    let floor: Double? // absolute minimum for auto-min (e.g. 30 bpm)
    let ceiling: Double? // absolute maximum for auto-max
    let padding: Double  // fraction added above/below data range (0.1 = 10%)

    static let auto = YAxisConfig(min: nil, max: nil, floor: nil, ceiling: nil, padding: 0.1)

    /// Compute concrete (lo, hi) from actual data values (already in chart scale).
    func resolve(dataMin: Double, dataMax: Double) -> (Double, Double) {
        let span = dataMax - dataMin
        let pad = Swift.max(span * padding, 0.5)

        var lo = min ?? (dataMin - pad)
        var hi = max ?? (dataMax + pad)

        if let f = floor { lo = Swift.max(lo, f) ; lo = Swift.min(lo, dataMin) }
        if let c = ceiling { hi = Swift.min(hi, c) ; hi = Swift.max(hi, dataMax) }

        if lo >= hi { lo = dataMin - 1; hi = dataMax + 1 }
        return (lo, hi)
    }
}

struct MetricSeries: Identifiable {
    var id: String { feature }
    let feature: String
    let displayName: String
    let unit: String
    let dataPoints: [DataPoint]
    let latestValue: Double?
    let trend: MetricTrend
    let chartStyle: ChartStyle
    let yAxisConfig: YAxisConfig
    /// Sleep stage blocks for the unified sleep card (only populated for feature == "sleep_unified")
    var sleepBlocks: [SleepBlock] = []
}

struct MetricCategoryData: Identifiable {
    var id: String { category.rawValue }
    let category: MetricCategory
    let series: [MetricSeries]
}

enum MetricTrend {
    case up, down, stable, unknown

    var icon: String {
        switch self {
        case .up:      return "arrow.up.right"
        case .down:    return "arrow.down.right"
        case .stable:  return "equal"
        case .unknown: return "minus"
        }
    }

    var color: String {
        switch self {
        case .up:      return "green"
        case .down:    return "red"
        case .stable:  return "blue"
        case .unknown: return "gray"
        }
    }
}

enum MetricCategory: String, CaseIterable {
    case heart       = "Heart & Vitals"
    case sleep       = "Sleep & Mindfulness"
    case activity    = "Activity & Fitness"
    case workouts    = "Workouts"
    case mobility    = "Mobility & Gait"
    case environment = "Environment & Nutrition"
    case body        = "Body & Wellness"

    var icon: String {
        switch self {
        case .workouts:    return "dumbbell.fill"
        case .activity:    return "figure.run"
        case .heart:       return "heart.fill"
        case .sleep:       return "moon.fill"
        case .mobility:    return "figure.walk"
        case .environment: return "bolt.fill"
        case .body:        return "figure.stand"
        }
    }

    var color: String {
        switch self {
        case .workouts:    return "amber"
        case .activity:    return "orange"
        case .heart:       return "red"
        case .sleep:       return "indigo"
        case .mobility:    return "green"
        case .environment: return "yellow"
        case .body:        return "cyan"
        }
    }

    /// Feature keys that belong to this category, matching the web frontend TAXONOMY.
    var featureKeys: [String] {
        switch self {
        case .workouts:
            return [
                "workout_running_duration", "workout_running_distance", "workout_running_energy",
                "workout_cycling_duration", "workout_cycling_distance", "workout_cycling_energy",
                "workout_swimming_duration", "workout_swimming_distance", "workout_swimming_energy",
                "workout_walking_duration", "workout_walking_distance", "workout_walking_energy",
                "workout_hiking_duration", "workout_hiking_distance", "workout_hiking_energy",
                "workout_yoga_duration", "workout_yoga_energy",
                "workout_strength_duration", "workout_strength_energy",
                "workout_hiit_duration", "workout_hiit_energy",
                "workout_elliptical_duration", "workout_elliptical_distance", "workout_elliptical_energy",
                "workout_rowing_duration", "workout_rowing_distance", "workout_rowing_energy",
                "workout_core_duration", "workout_core_energy",
                "workout_flexibility_duration", "workout_cooldown_duration"
            ]
        case .activity:
            return [
                "steps", "distance", "flights_climbed", "active_energy", "resting_energy",
                "stand_time", "exercise_time"
            ]
        case .heart:
            return [
                "heart_rate", "resting_heart_rate", "walking_heart_rate_avg",
                "heart_rate_variability", "heart_rate_recovery",
                "blood_oxygen", "respiratory_rate", "vo2max",
                "atrial_fibrillation_burden", "high_heart_rate_event",
                "low_heart_rate_event", "irregular_heart_rhythm_event"
            ]
        case .sleep:
            return [
                "sleep_unified", "mindful_session", "sleeping_wrist_temp"
            ]
        case .mobility:
            return [
                "walking_speed", "walking_steadiness", "walking_asymmetry",
                "walking_double_support", "walking_step_length",
                "stair_ascent_speed", "stair_descent_speed",
                "running_vertical_oscillation", "running_ground_contact",
                "running_stride_length", "running_power", "six_minute_walk"
            ]
        case .environment:
            return [
                "audio_exposure_event", "uv_index", "water"
            ]
        case .body:
            return [
                "body_mass", "body_mass_index", "time_in_daylight"
            ]
        }
    }

    /// Keyword fragments used for dynamic categorisation of unknown feature keys,
    /// mirroring the web frontend's `TAXONOMY[].matches` arrays.
    var matchKeywords: [String] {
        switch self {
        case .workouts:
            return [
                "workout_running", "workout_cycling", "workout_swimming", "workout_walking",
                "workout_hiking", "workout_yoga", "workout_strength", "workout_hiit",
                "workout_elliptical", "workout_rowing", "workout_core", "workout_flexibility",
                "workout_cooldown"
            ]
        case .activity:
            return [
                "step", "distance", "flight", "energy", "calorie", "stand", "exercise",
                "move", "push", "cycling", "swimming", "active", "downhill",
                "strokes", "cadence", "pace", "velocity", "acceleration", "power", "metabolic"
            ]
        case .heart:
            return [
                "heart", "pulse", "respiratory", "oxygen", "saturation", "bloodpressure",
                "glucose", "vitals", "spo2", "temperature", "beat", "atrial",
                "fibrillation", "ecg", "ekg", "cardio", "vo2"
            ]
        case .sleep:
            return ["sleep", "mindful", "rem", "arousal", "insomnia", "awake", "deepsleep"]
        case .mobility:
            return [
                "gait", "walking", "steplength", "asymmetry", "steadiness", "balance",
                "stair", "sixminute", "support", "swing", "groundcontact", "vertical"
            ]
        case .environment:
            return [
                "audio", "noise", "exposure", "dietary", "water", "nutrition", "uv",
                "vitamin", "sugar", "carb", "fat", "protein", "mineral", "micro",
                "milligram", "ounce", "fiber", "iron", "calcium", "potassium",
                "sodium", "caffeine"
            ]
        case .body:
            return [
                "body", "mass", "fat", "height", "waist", "bmi", "weight", "composition",
                "menstrual", "period", "cycle", "ovulation", "symptoms", "sexual",
                "headache", "mood", "fatigue", "sore", "pain", "health", "general"
            ]
        }
    }

    /// Return the category for an arbitrary feature key using keyword matching
    /// (same logic as the web frontend's `getCategory` function).
    static func categorise(_ featureKey: String) -> MetricCategory {
        let lower = featureKey.lowercased()
            .replacingOccurrences(of: "hkquantitytypeidentifier", with: "")
            .replacingOccurrences(of: "hkcategorytypeidentifier", with: "")

        for cat in MetricCategory.allCases {
            if cat.matchKeywords.contains(where: { lower.contains($0) }) {
                return cat
            }
        }
        // Default to Body & Wellness (last category), matching web behaviour
        return .body
    }

    func displayName(for key: String) -> String {
        switch key {
        // Workouts
        case "workout_running_duration":     return "Running Duration"
        case "workout_running_distance":     return "Running Distance"
        case "workout_running_energy":       return "Running Energy"
        case "workout_cycling_duration":     return "Cycling Duration"
        case "workout_cycling_distance":     return "Cycling Distance"
        case "workout_cycling_energy":       return "Cycling Energy"
        case "workout_swimming_duration":    return "Swimming Duration"
        case "workout_swimming_distance":    return "Swimming Distance"
        case "workout_swimming_energy":      return "Swimming Energy"
        case "workout_walking_duration":     return "Walking Duration"
        case "workout_walking_distance":     return "Walking Distance"
        case "workout_walking_energy":       return "Walking Energy"
        case "workout_hiking_duration":      return "Hiking Duration"
        case "workout_hiking_distance":      return "Hiking Distance"
        case "workout_hiking_energy":        return "Hiking Energy"
        case "workout_yoga_duration":        return "Yoga Duration"
        case "workout_yoga_energy":          return "Yoga Energy"
        case "workout_strength_duration":    return "Strength Duration"
        case "workout_strength_energy":      return "Strength Energy"
        case "workout_hiit_duration":        return "HIIT Duration"
        case "workout_hiit_energy":          return "HIIT Energy"
        case "workout_elliptical_duration":  return "Elliptical Duration"
        case "workout_elliptical_distance":  return "Elliptical Distance"
        case "workout_elliptical_energy":    return "Elliptical Energy"
        case "workout_rowing_duration":      return "Rowing Duration"
        case "workout_rowing_distance":      return "Rowing Distance"
        case "workout_rowing_energy":        return "Rowing Energy"
        case "workout_core_duration":        return "Core Training Duration"
        case "workout_core_energy":          return "Core Training Energy"
        case "workout_flexibility_duration": return "Flexibility Duration"
        case "workout_cooldown_duration":    return "Cooldown Duration"
        // Activity & Fitness
        case "steps":                         return "Steps"
        case "distance":                      return "Distance"
        case "flights_climbed":               return "Flights Climbed"
        case "active_energy":                 return "Active Energy"
        case "resting_energy":                return "Resting Energy"
        case "stand_time":                    return "Stand Time"
        case "exercise_time":                 return "Exercise Time"
        case "cycling_cadence":               return "Cycling Cadence"
        case "cycling_power":                 return "Cycling Power"
        case "cycling_speed":                 return "Cycling Speed"
        case "running_power":                 return "Running Power"
        case "walking_step_length":           return "Step Length"
        // Heart & Vitals
        case "heart_rate":                    return "Heart Rate"
        case "resting_heart_rate":            return "Resting Heart Rate"
        case "walking_heart_rate_avg":        return "Walking Heart Rate"
        case "heart_rate_variability":        return "HRV"
        case "heart_rate_recovery":           return "Heart Rate Recovery"
        case "blood_oxygen":                  return "Blood O\u{2082}"
        case "respiratory_rate":              return "Respiratory Rate"
        case "vo2max":                        return "VO2 Max"
        case "atrial_fibrillation_burden":    return "AFib Burden"
        case "high_heart_rate_event":         return "High HR Event"
        case "low_heart_rate_event":          return "Low HR Event"
        case "irregular_heart_rhythm_event":  return "Irregular Rhythm"
        // Sleep & Mindfulness
        case "sleep_unified":                 return "Sleep"
        case "sleep_in_bed":                  return "In Bed"
        case "sleep_asleep":                  return "Asleep"
        case "sleep_core":                    return "Core Sleep"
        case "sleep_deep":                    return "Deep Sleep"
        case "sleep_rem":                     return "REM"
        case "sleep_awake":                   return "Awake"
        case "mindful_session":               return "Mindful Session"
        case "sleeping_wrist_temp":           return "Wrist Temp"
        // Mobility & Gait
        case "walking_speed":                 return "Walking Speed"
        case "walking_steadiness":            return "Steadiness"
        case "walking_asymmetry":             return "Asymmetry"
        case "walking_double_support":        return "Double Support"
        case "stair_ascent_speed":            return "Stair Ascent Speed"
        case "stair_descent_speed":           return "Stair Descent Speed"
        case "running_vertical_oscillation":  return "Vertical Oscillation"
        case "running_ground_contact":        return "Ground Contact"
        case "running_stride_length":         return "Stride Length"
        case "six_minute_walk":               return "6 Min Walk"
        // Environment & Nutrition
        case "audio_exposure_event":          return "Audio Events"
        case "uv_index":                      return "UV Index"
        case "water":                         return "Water"
        // Body & Wellness
        case "body_mass":                     return "Weight"
        case "body_mass_index":               return "BMI"
        case "time_in_daylight":              return "Daylight"
        default:
            return key.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    func unit(for key: String) -> String {
        switch key {
        case _ where key.hasPrefix("workout_") && key.hasSuffix("_duration"):   return "min"
        case _ where key.hasPrefix("workout_") && key.hasSuffix("_distance"):   return "km"
        case _ where key.hasPrefix("workout_") && key.hasSuffix("_energy"):     return "kcal"
        case "steps", "flights_climbed":                                        return ""
        case "active_energy", "resting_energy":                                 return "kcal"
        case "exercise_time", "stand_time", "mindful_session", "time_in_daylight": return "min"
        case "distance":                                                        return "km"
        case "cycling_cadence":                                                 return "RPM"
        case "cycling_power", "running_power":                                  return "W"
        case "cycling_speed", "walking_speed", "stair_ascent_speed", "stair_descent_speed": return "m/s"
        case "walking_step_length", "running_stride_length", "running_vertical_oscillation": return "cm"
        case "heart_rate", "resting_heart_rate", "walking_heart_rate_avg",
             "heart_rate_recovery", "high_heart_rate_event", "low_heart_rate_event": return "bpm"
        case "heart_rate_variability", "running_ground_contact":                return "ms"
        case "blood_oxygen", "walking_steadiness", "walking_asymmetry",
             "walking_double_support", "atrial_fibrillation_burden":            return "%"
        case "respiratory_rate":                                                return "br/min"
        case "vo2max":                                                          return "mL/kg·min"
        case "irregular_heart_rhythm_event", "audio_exposure_event":            return ""
        case "sleep_unified":                                                     return "h"
        case _ where key.hasPrefix("sleep_"):                                   return "min"
        case "sleeping_wrist_temp":                                             return "°C"
        case "uv_index":                                                        return ""
        case "water":                                                           return "ml"
        case "body_mass":                                                       return "kg"
        case "body_mass_index":                                                 return ""
        case "six_minute_walk":                                                 return "m"
        default:                                                                return ""
        }
    }

    func formatValue(_ raw: Double, key: String) -> String {
        switch key {
        case _ where key.hasPrefix("workout_") && key.hasSuffix("_duration"):
            return String(format: "%.1f", raw / 60.0)  // seconds → minutes
        case _ where key.hasPrefix("workout_") && key.hasSuffix("_distance"):
            return String(format: "%.2f", raw / 1000.0) // meters → km
        case _ where key.hasPrefix("workout_") && key.hasSuffix("_energy"):
            return "\(Int(raw))"                         // kcal
        case "steps", "flights_climbed":
            return "\(Int(raw))"
        case "active_energy", "resting_energy":
            return "\(Int(raw))"
        case "exercise_time", "stand_time", "mindful_session", "time_in_daylight":
            return "\(Int(raw / 60))"
        case "heart_rate", "resting_heart_rate", "walking_heart_rate_avg",
             "heart_rate_recovery", "high_heart_rate_event", "low_heart_rate_event":
            return "\(Int(raw))"
        case "heart_rate_variability", "running_ground_contact":
            return String(format: "%.0f", raw)
        case "blood_oxygen", "walking_steadiness", "walking_asymmetry",
             "walking_double_support", "atrial_fibrillation_burden":
            return String(format: "%.1f", raw * 100)
        case "sleep_unified":
            let hours = raw / 3600
            return String(format: "%.1f", hours)
        case _ where key.hasPrefix("sleep_"):
            return "\(Int(raw / 60))"
        case "walking_speed", "cycling_speed", "stair_ascent_speed", "stair_descent_speed":
            return String(format: "%.2f", raw)
        case "distance":
            return String(format: "%.2f", raw / 1000.0)
        case "body_mass", "body_mass_index", "uv_index", "sleeping_wrist_temp":
            return String(format: "%.1f", raw)
        case "vo2max":
            return String(format: "%.1f", raw)
        case "walking_step_length", "running_stride_length":
            return String(format: "%.1f", raw * 100)  // meters → cm
        case "six_minute_walk":
            return String(format: "%.0f", raw)
        case "water":
            return String(format: "%.0f", raw)
        default:
            return String(format: "%.1f", raw)
        }
    }

    /// Value used for chart Y-axis (human-readable scale)
    func chartValue(_ raw: Double, key: String) -> Double {
        switch key {
        case _ where key.hasPrefix("workout_") && key.hasSuffix("_duration"):
            return raw / 60.0      // seconds → minutes
        case _ where key.hasPrefix("workout_") && key.hasSuffix("_distance"):
            return raw / 1000.0    // meters → km
        case "exercise_time", "stand_time", "mindful_session", "time_in_daylight":
            return raw / 60.0
        case "blood_oxygen", "walking_steadiness", "walking_asymmetry",
             "walking_double_support", "atrial_fibrillation_burden":
            return raw * 100.0
        case "sleep_unified":
            return raw / 3600.0  // seconds → hours
        case _ where key.hasPrefix("sleep_"):
            return raw / 60.0
        case "distance":
            return raw / 1000.0
        case "walking_step_length", "running_stride_length":
            return raw * 100.0   // meters → cm
        default:
            return raw
        }
    }

    // MARK: - Per-Metric Chart Configuration

    /// Cumulative features that should be aggregated into daily bars.
    static let dailyBarFeatures: Set<String> = [
        "steps", "distance", "flights_climbed", "active_energy", "resting_energy",
        "stand_time", "exercise_time", "water", "mindful_session", "time_in_daylight"
    ]

    /// How many minutes of data to request for a given feature.
    func timeWindowMinutes(for key: String) -> Int {
        switch key {
        // 24h: high-frequency continuous vitals
        case "heart_rate", "blood_oxygen", "respiratory_rate", "heart_rate_variability":
            return 1440
        // 7d: daily-aggregated activity + environment
        case "steps", "distance", "flights_climbed", "active_energy", "resting_energy",
             "stand_time", "exercise_time", "water", "time_in_daylight", "mindful_session":
            return 10080
        // 14d: once-daily vitals & mobility
        case "resting_heart_rate", "walking_heart_rate_avg",
             "heart_rate_recovery", "atrial_fibrillation_burden",
             "sleeping_wrist_temp",
             "walking_speed", "walking_steadiness", "walking_asymmetry",
             "walking_double_support", "walking_step_length",
             "stair_ascent_speed", "stair_descent_speed",
             "running_vertical_oscillation", "running_ground_contact", "running_stride_length":
            return 20160
        // 30d: slow-changing body & fitness
        case "vo2max", "body_mass", "body_mass_index", "six_minute_walk":
            return 43200
        // 30d: workouts (sparse events)
        case _ where key.hasPrefix("workout_"):
            return 43200
        // Sleep: 7d (handled specially)
        case "sleep_unified":
            return 10080
        default:
            return 10080
        }
    }

    func chartStyle(for key: String) -> ChartStyle {
        if key == "sleep_unified" { return .sleepBar }
        if Self.dailyBarFeatures.contains(key) { return .bar }
        if key.hasPrefix("workout_") { return .bar }
        // Mobility: sparse samples → point
        switch key {
        case "walking_speed", "walking_steadiness", "walking_asymmetry",
             "walking_double_support", "walking_step_length",
             "stair_ascent_speed", "stair_descent_speed",
             "running_vertical_oscillation", "running_ground_contact",
             "running_stride_length", "six_minute_walk":
            return .point
        // Events: point markers
        case "high_heart_rate_event", "low_heart_rate_event",
             "irregular_heart_rhythm_event", "audio_exposure_event":
            return .point
        default:
            return .line
        }
    }

    func yAxisConfig(for key: String) -> YAxisConfig {
        switch key {
        // Heart rate: floor at 30, pad ±10
        case "heart_rate":
            return YAxisConfig(min: nil, max: nil, floor: 30, ceiling: nil, padding: 0.15)
        case "resting_heart_rate", "walking_heart_rate_avg", "heart_rate_recovery":
            return YAxisConfig(min: nil, max: nil, floor: 30, ceiling: nil, padding: 0.15)
        // Blood oxygen: fixed 85-100%
        case "blood_oxygen":
            return YAxisConfig(min: 85, max: 100, floor: nil, ceiling: nil, padding: 0)
        // Respiratory rate: floor at 8
        case "respiratory_rate":
            return YAxisConfig(min: nil, max: nil, floor: 8, ceiling: nil, padding: 0.15)
        // HRV: starts at 0
        case "heart_rate_variability":
            return YAxisConfig(min: 0, max: nil, floor: nil, ceiling: nil, padding: 0.2)
        // AFib: 0-based, ceiling at 5% if data is small
        case "atrial_fibrillation_burden":
            return YAxisConfig(min: 0, max: nil, floor: nil, ceiling: nil, padding: 0.2)
        // Cumulative daily bars: start at 0
        case "steps", "distance", "flights_climbed", "active_energy", "resting_energy",
             "stand_time", "exercise_time", "water", "mindful_session", "time_in_daylight":
            return YAxisConfig(min: 0, max: nil, floor: nil, ceiling: nil, padding: 0.1)
        // Workouts: start at 0
        case _ where key.hasPrefix("workout_"):
            return YAxisConfig(min: 0, max: nil, floor: nil, ceiling: nil, padding: 0.1)
        // Percentages: 0-based
        case "walking_steadiness", "walking_asymmetry", "walking_double_support":
            return YAxisConfig(min: 0, max: nil, floor: nil, ceiling: nil, padding: 0.15)
        // Body: tight padding
        case "body_mass":
            return YAxisConfig(min: nil, max: nil, floor: nil, ceiling: nil, padding: 0.05)
        case "body_mass_index":
            return YAxisConfig(min: nil, max: nil, floor: nil, ceiling: nil, padding: 0.05)
        // VO2max: tight
        case "vo2max":
            return YAxisConfig(min: nil, max: nil, floor: nil, ceiling: nil, padding: 0.1)
        default:
            return .auto
        }
    }
}

// MARK: - Dashboard ViewModel

@MainActor
class DashboardViewModel: ObservableObject {

    @Published var isAgentRunning: Bool = false
    @Published var lastAnalysisTime: String? = nil
    @Published var agentModel: String? = nil
    @Published var analysisCycles: Int = 0
    @Published var totalRecords: Int = 0
    @Published var agentState: String = "idle"
    @Published var analysisState: String = "idle"
    @Published var promptTokens: Int = 0
    @Published var completionTokens: Int = 0
    @Published var thoughtsTokens: Int = 0

    @Published var metricCategories: [MetricCategoryData] = []
    @Published var reports: [AgentReport] = []

    @Published var isLoadingStatus: Bool = false
    @Published var isLoadingReports: Bool = false
    @Published var isLoadingMetrics: Bool = false
    @Published var errorMessage: String? = nil

    private var refreshTimer: Timer? = nil
    private var isRefreshing = false
    private let userId = "LiveUser"

    // MARK: - Lifecycle

    func startRefreshing() {
        refreshTimer?.invalidate()
        fetchAll()
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.fetchAll()
            }
        }
    }

    func stopRefreshing() {
        refreshTimer?.invalidate()
        refreshTimer = nil
    }

    // MARK: - Fetch All

    func fetchAll() {
        guard !isRefreshing else { return }
        isRefreshing = true
        Task {
            await withTaskGroup(of: Void.self) { group in
                group.addTask { await self.fetchAgentStatus() }
                group.addTask { await self.fetchReports() }
                group.addTask { await self.fetchHealthMetrics() }
            }
            isRefreshing = false
        }
    }

    // MARK: - Agent Status

    private func fetchAgentStatus() async {
        let base = ServerConfig.load().apiBaseURL
        guard let url = URL(string: "\(base)/api/agent/status?user_id=\(userId)") else { return }
        isLoadingStatus = true
        defer { isLoadingStatus = false }

        do {
            let (data, _) = try await URLSession.shared.data(for: APIClient.request(url))
            let response = try JSONDecoder().decode(AgentStatusResponse.self, from: data)
            isAgentRunning = response.running ?? false
            lastAnalysisTime = response.status?.last_analysis_time
            agentModel = response.config?.model
            analysisCycles = response.status?.cycle_count ?? 0
            totalRecords = response.status?.data_store_stats?.total_records ?? 0
            agentState = response.status?.state ?? "idle"
            analysisState = response.status?.analysis_state ?? "idle"
            promptTokens = response.status?.cumulative_tokens?.prompt_tokens ?? 0
            completionTokens = response.status?.cumulative_tokens?.completion_tokens ?? 0
            thoughtsTokens = response.status?.cumulative_tokens?.thoughts_tokens ?? 0
            errorMessage = nil
        } catch {
            isAgentRunning = false
        }
    }

    // MARK: - Reports

    private func fetchReports() async {
        let base = ServerConfig.load().apiBaseURL
        guard let url = URL(string: "\(base)/api/agent/memory/\(userId)?query_type=reports") else { return }
        isLoadingReports = true
        defer { isLoadingReports = false }

        do {
            let (data, _) = try await URLSession.shared.data(for: APIClient.request(url))
            let response = try JSONDecoder().decode(ReportsResponse.self, from: data)
            reports = response.data ?? []
            publishLatestReportToWidget()
        } catch {
            // Silently ignore — server may not be running
        }
    }

    // MARK: - Health Metrics from Server API (same data as web dashboard)

    private struct DashboardResponse: Decodable {
        let success: Bool
        let features: [String: [ServerDataPoint]]?
    }

    private struct ServerDataPoint: Decodable {
        let ts: Double
        let v: Double
    }

    func fetchHealthMetrics() async {
        isLoadingMetrics = true
        defer { isLoadingMetrics = false }

        let base = ServerConfig.load().apiBaseURL
        // Request 30 days (max window); client-side filtering trims per metric.
        guard let url = URL(string: "\(base)/api/data/dashboard?minutes=43200") else { return }

        do {
            var request = APIClient.request(url)
            request.cachePolicy = .reloadIgnoringLocalCacheData
            request.timeoutInterval = 30
            let (data, _) = try await URLSession.shared.data(for: request)
            let response = try JSONDecoder().decode(DashboardResponse.self, from: data)
            guard let features = response.features else { return }

            let sleepStageKeys = Set(["sleep_in_bed", "sleep_asleep", "sleep_core", "sleep_deep", "sleep_rem", "sleep_awake"])
            // Pre-register ALL hardcoded keys so dynamic matching never duplicates them.
            var processedKeys = Set<String>()
            processedKeys.formUnion(sleepStageKeys)
            for cat in MetricCategory.allCases {
                processedKeys.formUnion(cat.featureKeys)
            }

            var categories: [MetricCategoryData] = []

            for category in MetricCategory.allCases {
                var seriesList: [MetricSeries] = []

                for key in category.featureKeys {
                    if key == "sleep_unified" {
                        if let sleepSeries = self.buildUnifiedSleepSeries(from: features, stageKeys: sleepStageKeys) {
                            seriesList.append(sleepSeries)
                        }
                        continue
                    }

                    if let s = buildSeries(key: key, points: features[key], category: category) {
                        seriesList.append(s)
                    }
                }

                // Dynamic: server features not in any hardcoded list
                for (key, points) in features where !processedKeys.contains(key) && !points.isEmpty {
                    guard MetricCategory.categorise(key) == category else { continue }
                    processedKeys.insert(key)
                    if let s = buildSeries(key: key, points: points, category: category) {
                        seriesList.append(s)
                    }
                }

                categories.append(MetricCategoryData(category: category, series: seriesList))
            }
            metricCategories = categories
            publishMetricsToWidget()
        } catch {
            // Server may not be running — silently ignore
        }
    }

    /// Build a MetricSeries for a single feature key, applying time-window filtering,
    /// daily aggregation (for bar charts), and chart/Y-axis configuration.
    private func buildSeries(key: String, points: [ServerDataPoint]?, category: MetricCategory) -> MetricSeries? {
        guard let points = points, !points.isEmpty else { return nil }

        let windowMinutes = category.timeWindowMinutes(for: key)
        let cutoff = Date().addingTimeInterval(-Double(windowMinutes) * 60).timeIntervalSince1970
        let filtered = points.filter { $0.ts >= cutoff }
        guard !filtered.isEmpty else { return nil }

        let style = category.chartStyle(for: key)
        let yConfig = category.yAxisConfig(for: key)

        let dataPoints: [DataPoint]
        let latestRaw: Double?
        let trend: MetricTrend

        if style == .bar && MetricCategory.dailyBarFeatures.contains(key) {
            // Aggregate into daily totals
            let (dailyPoints, dailyTotalsRaw) = aggregateDaily(filtered, key: key, category: category)
            dataPoints = dailyPoints
            latestRaw = dailyTotalsRaw.last  // last day's raw total
            trend = computeTrendFromDailyTotals(dailyTotalsRaw)
        } else {
            dataPoints = filtered.map { p in
                DataPoint(date: Date(timeIntervalSince1970: p.ts), value: category.chartValue(p.v, key: key))
            }
            latestRaw = filtered.last?.v
            trend = computeTrendFromPoints(filtered)
        }

        return MetricSeries(
            feature: key,
            displayName: category.displayName(for: key),
            unit: category.unit(for: key),
            dataPoints: dataPoints,
            latestValue: latestRaw,
            trend: trend,
            chartStyle: style,
            yAxisConfig: yConfig
        )
    }

    /// Aggregate hourly server data into daily totals for bar charts.
    /// Returns (chart-scale DataPoints, raw daily totals for trend).
    private func aggregateDaily(_ points: [ServerDataPoint], key: String, category: MetricCategory) -> ([DataPoint], [Double]) {
        let calendar = Calendar.current
        var dayBuckets: [Date: Double] = [:]
        for p in points {
            let date = Date(timeIntervalSince1970: p.ts)
            let dayStart = calendar.startOfDay(for: date)
            dayBuckets[dayStart, default: 0] += p.v
        }
        let sorted = dayBuckets.sorted { $0.key < $1.key }
        let dataPoints = sorted.map { DataPoint(date: $0.key, value: category.chartValue($0.value, key: key)) }
        let rawTotals = sorted.map { $0.value }
        return (dataPoints, rawTotals)
    }

    /// Trend for daily totals: compare last day vs previous day.
    private func computeTrendFromDailyTotals(_ totals: [Double]) -> MetricTrend {
        guard totals.count >= 2 else { return .unknown }
        let last = totals[totals.count - 1]
        let prev = totals[totals.count - 2]
        let diff = last - prev
        let threshold = abs(prev) * 0.05
        if diff > threshold { return .up }
        if diff < -threshold { return .down }
        return .stable
    }

    // MARK: - Unified Sleep Builder

    private func buildUnifiedSleepSeries(from features: [String: [ServerDataPoint]], stageKeys: Set<String>) -> MetricSeries? {
        let stageMap: [String: SleepStage] = [
            "sleep_core": .core, "sleep_deep": .deep,
            "sleep_rem": .rem, "sleep_awake": .awake,
            "sleep_in_bed": .inBed, "sleep_asleep": .core  // unspecified asleep → core
        ]

        var blocks: [SleepBlock] = []
        for (key, stage) in stageMap {
            guard let points = features[key] else { continue }
            for p in points {
                // ts is endDate (post-fix), v is duration in seconds.
                // Derive actual start = endDate - duration.
                let startTs = p.ts - p.v
                blocks.append(SleepBlock(
                    start: Date(timeIntervalSince1970: startTs),
                    durationMinutes: p.v / 60.0,
                    stage: stage
                ))
            }
        }

        guard !blocks.isEmpty else { return nil }
        let sortedBlocks = blocks.sorted { $0.start < $1.start }

        // Group by the calendar day the block ends on (matches DashboardView logic).
        let calendar = Calendar.current
        var nightGroups: [Date: [SleepBlock]] = [:]
        for block in sortedBlocks {
            let endDate = block.start.addingTimeInterval(block.durationMinutes * 60)
            let day = calendar.startOfDay(for: endDate)
            nightGroups[day, default: []].append(block)
        }

        // Find last night: the most recent night key that is before now
        let sortedNights = nightGroups.keys.sorted()
        guard let lastNightKey = sortedNights.last else { return nil }
        let lastNightBlocks = nightGroups[lastNightKey] ?? []

        // Compute last night's actual sleep (exclude awake & inBed)
        let lastNightSleepSeconds = lastNightBlocks
            .filter { $0.stage != .awake && $0.stage != .inBed }
            .reduce(0.0) { $0 + $1.durationMinutes * 60 }

        // Build daily totals for trend
        var dailyTotals: [(Date, Double)] = []
        for nightDate in sortedNights {
            let sleepSec = (nightGroups[nightDate] ?? [])
                .filter { $0.stage != .awake && $0.stage != .inBed }
                .reduce(0.0) { $0 + $1.durationMinutes * 60 }
            dailyTotals.append((nightDate, sleepSec))
        }
        let dataPoints = dailyTotals.map { DataPoint(date: $0.0, value: $0.1 / 3600.0) }

        return MetricSeries(
            feature: "sleep_unified",
            displayName: "Sleep",
            unit: "h",
            dataPoints: dataPoints,
            latestValue: lastNightSleepSeconds,  // last night's total in seconds
            trend: dataPoints.count >= 2
                ? ((dataPoints.last?.value ?? 0) > dataPoints[dataPoints.count - 2].value ? .up : .down)
                : .unknown,
            chartStyle: .sleepBar,
            yAxisConfig: .auto,
            sleepBlocks: sortedBlocks
        )
    }

    // MARK: - Trend Computation

    private func computeTrendFromPoints(_ points: [ServerDataPoint]) -> MetricTrend {
        guard points.count >= 2 else { return .unknown }
        let last = points[points.count - 1].v
        let prev = points[points.count - 2].v
        let diff = last - prev
        let threshold = abs(prev) * 0.05
        if diff > threshold { return .up }
        if diff < -threshold { return .down }
        return .stable
    }

    // MARK: - Helpers

    func formattedAnalysisTime() -> String {
        guard let timeStr = lastAnalysisTime else { return "Never" }
        // Backend sends UTC timestamps without a timezone suffix — append "Z"
        let hasTZ = timeStr.hasSuffix("Z") || timeStr.contains("+") || (timeStr.count > 19 && timeStr.dropFirst(19).contains("-"))
        let utcStr = hasTZ ? timeStr : timeStr + "Z"
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: utcStr) {
            let relative = RelativeDateTimeFormatter()
            relative.unitsStyle = .abbreviated
            return relative.localizedString(for: date, relativeTo: Date())
        }
        formatter.formatOptions = [.withInternetDateTime]
        if let date = formatter.date(from: utcStr) {
            let relative = RelativeDateTimeFormatter()
            relative.unitsStyle = .abbreviated
            return relative.localizedString(for: date, relativeTo: Date())
        }
        return String(timeStr.prefix(19))
    }

    func alertColor(for level: String?) -> String {
        switch level?.lowercased() {
        case "critical", "high": return "red"
        case "warning", "medium": return "orange"
        case "info", "low":       return "blue"
        default:                  return "green"
        }
    }

    // MARK: - Widget snapshot publishing

    /// Pick the freshest value of each interesting metric from
    /// metricCategories and write them into the App Group snapshot.
    /// The widget reads them in this order, so the first N (depending
    /// on widget size) are what the user sees.
    func publishMetricsToWidget() {
        // (key, label, unit, transform). transform converts the raw
        // server value into the display value (e.g. seconds → hours).
        let priority: [(key: String, name: String, unit: String, xform: (Double) -> Double)] = [
            ("heart_rate",                "Heart",  "bpm",  { $0 }),
            ("sleep_unified",             "Sleep",  "h",    { $0 / 3600 }),     // seconds → hours
            ("steps",                     "Steps",  "",     { $0 }),
            ("blood_oxygen",              "SpO₂",   "%",    { $0 }),            // already 0-100
            ("heart_rate_variability",    "HRV",    "ms",   { $0 }),
            ("active_energy",             "Energy", "kcal", { $0 }),
            ("resting_heart_rate",        "RestHR", "bpm",  { $0 }),
            ("respiratory_rate",          "Resp",   "br",   { $0 }),
            ("walking_heart_rate_avg",    "WalkHR", "bpm",  { $0 }),
            ("vo2max",                    "VO₂",    "",     { $0 }),
            ("exercise_time",             "Exer",   "min",  { $0 }),
            ("stand_time",                "Stand",  "min",  { $0 }),
            ("flights_climbed",           "Floors", "",     { $0 }),
            ("distance",                  "Dist",   "km",   { $0 / 1000 }),     // m → km
            ("sleeping_wrist_temp",       "Temp",   "°C",   { $0 }),
        ]
        var byKey: [String: MetricSeries] = [:]
        for category in metricCategories {
            for series in category.series {
                byKey[series.feature] = series
            }
        }
        var samples: [HimeWidgetMetric] = []
        for entry in priority {
            guard let series = byKey[entry.key], let raw = series.latestValue else { continue }
            let value = entry.xform(raw)
            let formatted: String
            if value >= 100 {
                formatted = String(format: "%.0f", value)
            } else if value >= 10 {
                formatted = value.rounded() == value ? String(format: "%.0f", value) : String(format: "%.1f", value)
            } else {
                formatted = String(format: "%.1f", value)
            }
            samples.append(HimeWidgetMetric(name: entry.name, value: formatted, unit: entry.unit))
        }
        HimeWidgetStore.update { snap in
            snap.metrics = samples
        }
    }

    /// Publish the newest agent report into the widget snapshot.
    func publishLatestReportToWidget() {
        guard let report = reports.first else {
            HimeWidgetStore.update { snap in
                snap.latestReportTitle = nil
                snap.latestReportPreview = nil
                snap.latestReportLevel = nil
            }
            return
        }
        // Large widget can show ~16 lines × ~40 chars = up to ~600 chars.
        let preview = String(report.content.prefix(600))
        HimeWidgetStore.update { snap in
            snap.latestReportTitle = report.title ?? "HiMe Insight"
            snap.latestReportPreview = preview
            snap.latestReportLevel = report.alert_level
        }
    }
}
