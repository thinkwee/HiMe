import HealthKit
import UIKit
import Combine
@preconcurrency import BackgroundTasks

// MARK: - RecentSample (UI display only)

struct RecentSample: Identifiable {
    let id = UUID()
    let feature: String
    let value: Double
    let timestamp: Date
    var isSynced: Bool = false

    var emoji: String {
        switch feature {
        case "heart_rate": return "💗"
        case "resting_heart_rate": return "❤️"
        case "walking_heart_rate_avg": return "🏃"
        case "heart_rate_variability": return "📈"
        case "blood_oxygen": return "🩸"
        case "respiratory_rate": return "🌬️"
        case "vo2max": return "🫁"
        case "steps": return "👟"
        case "distance": return "📍"
        case "active_energy": return "🔥"
        case "resting_energy": return "⚡️"
        case "exercise_time": return "🏋️"
        case "stand_time": return "🧍"
        case "flights_climbed": return "🪜"
        case "body_mass": return "⚖️"
        case "body_mass_index": return "📊"
        case "sleep_in_bed", "sleep_asleep", "sleep_core", "sleep_deep", "sleep_rem": return "😴"
        case "sleep_awake": return "👁️"
        case "walking_speed": return "🚶"
        case "running_speed": return "🏃‍♂️"
        case "environmental_audio": return "🔊"
        case "headphone_audio": return "🎧"
        case "mindful_session": return "🧘"
        case "water": return "💧"
        case "sleeping_wrist_temp": return "🌡️"
        case "time_in_daylight": return "☀️"
        case "walking_steadiness": return "⚖️"
        case "walking_asymmetry": return "👣"
        case "walking_step_length": return "📏"
        case "walking_double_support": return "👯"
        case "stair_ascent_speed", "stair_descent_speed": return "🪜"
        case "heart_rate_recovery": return "🔄"
        case "atrial_fibrillation_burden": return "💓"
        case "running_power", "cycling_power": return "⚡️"
        case "running_stride_length": return "📐"
        case "running_vertical_oscillation": return "🦘"
        case "running_ground_contact": return "⏱️"
        case "uv_index": return "☀️"
        case "low_heart_rate_event", "high_heart_rate_event", "irregular_heart_rhythm_event": return "⚠️"
        case _ where feature.hasPrefix("workout_"): return "🏋️"
        default: return "📡"
        }
    }

    var formattedValue: String {
        switch feature {
        case "heart_rate", "resting_heart_rate", "walking_heart_rate_avg", "respiratory_rate", "heart_rate_recovery":
            return "\(Int(value)) bpm"
        case "blood_oxygen", "walking_steadiness", "walking_asymmetry", "walking_double_support", "atrial_fibrillation_burden":
            return String(format: "%.1f%%", value * 100)
        case "steps", "flights_climbed":
            return "\(Int(value))"
        case "distance", "distance_cycling", "six_minute_walk", "walking_step_length", "running_stride_length":
            // Input is in meters; display in km if > 1000m for readability
            if value >= 1000 {
                return String(format: "%.2f km", value / 1000.0)
            } else {
                return String(format: "%.1f m", value)
            }
        case "active_energy", "resting_energy":
            return "\(Int(value)) kcal"
        case "exercise_time", "stand_time", "time_in_daylight", "mindful_session":
            // Input is in SECONDS
            return "\(Int(value / 60)) min"
        case _ where feature.hasPrefix("sleep_"):
            // Input is in SECONDS
            return "\(Int(value / 60)) min"
        case "heart_rate_variability":
            return String(format: "%.1f ms", value)
        case "body_mass":
            return String(format: "%.1f kg", value)
        case "body_mass_index":
            return String(format: "%.1f", value)
        case "vo2max":
            return String(format: "%.1f ml/kg·min", value)
        case "walking_speed", "stair_ascent_speed", "stair_descent_speed", "cycling_speed":
            return String(format: "%.2f m/s", value)
        case "running_speed":
            // HealthKit gives meters/second; convert to km/h for display
            return String(format: "%.1f km/h", value * 3.6)
        case "environmental_audio", "headphone_audio":
            return String(format: "%.0f dB", value)
        case "water":
            return "\(Int(value)) mL"
        case "sleeping_wrist_temp":
            return String(format: "%.1f °C", value)
        case "running_power", "cycling_power":
            return "\(Int(value)) W"
        case "running_vertical_oscillation":
            // Input is in centimeters (standardized below)
            return String(format: "%.1f cm", value)
        case "running_ground_contact":
            return "\(Int(value)) ms"
        case "uv_index":
            return String(format: "%.1f", value)
        case _ where feature.hasPrefix("workout_") && feature.hasSuffix("_duration"):
            return String(format: "%.1f min", value / 60.0)
        case _ where feature.hasPrefix("workout_") && feature.hasSuffix("_distance"):
            return String(format: "%.2f km", value / 1000.0)
        case _ where feature.hasPrefix("workout_") && feature.hasSuffix("_energy"):
            return "\(Int(value)) kcal"
        default:
            return String(format: "%.1f", value)
        }
    }

    var displayName: String {
        feature.replacingOccurrences(of: "_", with: " ").capitalized
    }
}

// MARK: - HealthKitManager

@MainActor
final class HealthKitManager: ObservableObject {
    static let shared = HealthKitManager()

    @Published var authStatus: String = "Not authorized"
    @Published var lastSync: String = "Never"
    @Published var recentSamples: [RecentSample] = []

    @Published var isBurstModeEnabled: Bool = UserDefaults.standard.bool(forKey: "burstModeEnabled") {
        didSet {
            UserDefaults.standard.set(isBurstModeEnabled, forKey: "burstModeEnabled")
            Task { @MainActor in self.toggleWorkoutSession() }
        }
    }

    nonisolated private let store = HKHealthStore()
    private var workoutSession: HKWorkoutSession?
    private let maxRecentSamples = 1000

    /// Cumulative metrics that must use HKStatisticsCollectionQuery for proper
    /// source-priority deduplication.  HKAnchoredObjectQuery returns raw samples
    /// from every source (iPhone + Apple Watch), causing double-counting for these.
    static let cumulativeFeatures: Set<String> = [
        "steps", "distance", "flights_climbed", "exercise_time",
        "stand_time", "active_energy", "resting_energy", "water"
    ]

    let quantityMetrics: [(String, HKQuantityTypeIdentifier, HKUnit)] = [
        ("steps", .stepCount, .count()),
        ("distance", .distanceWalkingRunning, .meter()), // Standardized to Meters
        ("flights_climbed", .flightsClimbed, .count()),
        ("exercise_time", .appleExerciseTime, .second()), // Standardized to Seconds
        ("stand_time", .appleStandTime, .second()),      // Standardized to Seconds
        ("active_energy", .activeEnergyBurned, .kilocalorie()),
        ("resting_energy", .basalEnergyBurned, .kilocalorie()),
        ("water", .dietaryWater, .literUnit(with: .milli)),
        ("heart_rate", .heartRate, HKUnit(from: "count/min")),
        ("resting_heart_rate", .restingHeartRate, HKUnit(from: "count/min")),
        ("walking_heart_rate_avg", .walkingHeartRateAverage, HKUnit(from: "count/min")),
        ("heart_rate_variability", .heartRateVariabilitySDNN, .secondUnit(with: .milli)),
        ("heart_rate_recovery", .heartRateRecoveryOneMinute, HKUnit(from: "count/min")),
        ("blood_oxygen", .oxygenSaturation, .percent()),
        ("vo2max", .vo2Max, HKUnit(from: "ml/kg*min")),
        ("respiratory_rate", .respiratoryRate, HKUnit(from: "count/min")),
        ("walking_speed", .walkingSpeed, HKUnit(from: "m/s")),
        ("walking_steadiness", .appleWalkingSteadiness, .percent()),
        ("walking_asymmetry", .walkingAsymmetryPercentage, .percent()),
        ("walking_step_length", .walkingStepLength, .meter()),
        ("walking_double_support", .walkingDoubleSupportPercentage, .percent()),
        ("six_minute_walk", .sixMinuteWalkTestDistance, .meter()),
        ("stair_ascent_speed", .stairAscentSpeed, HKUnit(from: "m/s")),
        ("stair_descent_speed", .stairDescentSpeed, HKUnit(from: "m/s")),
        ("body_mass", .bodyMass, .gramUnit(with: .kilo)),
        ("body_mass_index", .bodyMassIndex, .count()),
        ("sleeping_wrist_temp", .appleSleepingWristTemperature, .degreeCelsius()),
        ("time_in_daylight", .timeInDaylight, .second()), // Standardized to Seconds
        ("uv_index", .uvExposure, .count()),
        ("atrial_fibrillation_burden", .atrialFibrillationBurden, .percent()),
        ("running_power", .runningPower, .watt()),
        ("running_stride_length", .runningStrideLength, .meter()),
        ("running_vertical_oscillation", .runningVerticalOscillation, .meterUnit(with: .centi)),
        ("running_ground_contact", .runningGroundContactTime, .secondUnit(with: .milli)),
        ("cycling_speed", .cyclingSpeed, HKUnit(from: "m/s")),
        ("cycling_cadence", .cyclingCadence, HKUnit(from: "count/min")),
        ("cycling_power", .cyclingPower, .watt()),
    ]

    /// Internal sentinel used for sleep analysis — parseCategorySample maps this to
    /// concrete feature names (sleep_in_bed, sleep_asleep, sleep_core, sleep_deep,
    /// sleep_rem, sleep_awake). Never stored in payloads.
    nonisolated static let sleepPlaceholder = "__sleep__"

    let categoryMetrics: [(String, HKCategoryTypeIdentifier)] = [
        ("mindful_session", .mindfulSession),
        ("audio_exposure_event", .environmentalAudioExposureEvent),
        ("high_heart_rate_event", .highHeartRateEvent),
        ("low_heart_rate_event", .lowHeartRateEvent),
        ("irregular_heart_rhythm_event", .irregularHeartRhythmEvent),
        (HealthKitManager.sleepPlaceholder, .sleepAnalysis),
    ]

    // Workout types to track
    private let workoutTypes: [HKWorkoutActivityType] = [
        .running, .cycling, .swimming, .walking, .hiking,
        .yoga, .functionalStrengthTraining, .traditionalStrengthTraining,
        .highIntensityIntervalTraining, .elliptical, .rowing,
        .coreTraining, .flexibility, .cooldown,
    ]

    nonisolated private static func workoutTypeName(_ type: HKWorkoutActivityType) -> String {
        switch type {
        case .running: return "running"
        case .cycling: return "cycling"
        case .swimming: return "swimming"
        case .walking: return "walking"
        case .hiking: return "hiking"
        case .yoga: return "yoga"
        case .functionalStrengthTraining: return "strength"
        case .traditionalStrengthTraining: return "strength"
        case .highIntensityIntervalTraining: return "hiit"
        case .elliptical: return "elliptical"
        case .rowing: return "rowing"
        case .coreTraining: return "core"
        case .flexibility: return "flexibility"
        case .cooldown: return "cooldown"
        default: return "other"
        }
    }

    private init() {}

    func setup() async {
        guard HKHealthStore.isHealthDataAvailable() else {
            await MainActor.run { authStatus = "HealthKit not available on this device" }
            HealthKitManager.bgLog("HealthKit not available (iPad/Simulator)")
            return
        }

        var readTypes = Set<HKObjectType>()
        for (_, id, _) in quantityMetrics {
            if let type = HKQuantityType.quantityType(forIdentifier: id) {
                readTypes.insert(type)
            }
        }
        for (_, id) in categoryMetrics {
            if let type = HKObjectType.categoryType(forIdentifier: id) {
                readTypes.insert(type)
            }
        }

        readTypes.insert(HKWorkoutType.workoutType())

        do {
            try await store.requestAuthorization(toShare: [], read: readTypes)
            await MainActor.run { authStatus = "Authorized" }
        } catch {
            let msg = "Auth failed: \(error.localizedDescription)"
            await MainActor.run { authStatus = msg }
            HealthKitManager.bgLog("HealthKit auth failed: \(error.localizedDescription)")
            return
        }

        // Cumulative metrics use HKStatisticsCollectionQuery for source-priority
        // deduplication — this matches iOS Health's numbers exactly.
        // Watch no longer syncs cumulative metrics to server (only instantaneous),
        // so there is no double-counting.
        for (f, id, unit) in quantityMetrics {
            if HealthKitManager.cumulativeFeatures.contains(f) {
                registerCumulativeObserver(feature: f, quantityType: HKQuantityType(id), unit: unit)
            } else {
                registerObserver(feature: f, sampleType: HKQuantityType(id), unit: unit)
            }
        }
        for (f, id) in categoryMetrics {
            registerObserver(feature: f, sampleType: HKCategoryType(id), unit: nil)
        }

        // Register workout observer
        registerWorkoutObserver()
    }

    private func registerObserver(feature: String, sampleType: HKSampleType, unit: HKUnit?) {
        store.enableBackgroundDelivery(for: sampleType, frequency: .immediate) { _, _ in }

        let observer = HKObserverQuery(sampleType: sampleType, predicate: nil) { [weak self] _, completion, error in
            if let error {
                HealthKitManager.bgLog("📱 HK-OBSERVER: \(feature) fired with error: \(error.localizedDescription)")
                completion()
                return
            }
            guard let self else { completion(); return }

            let stateStr: String = DispatchQueue.main.sync {
                let s = UIApplication.shared.applicationState
                return s == .active ? "active" : s == .background ? "background" : "inactive"
            }
            HealthKitManager.bgLog("📱 HK-OBSERVER: \(feature) fired (appState=\(stateStr))")

            let taskID: UIBackgroundTaskIdentifier = DispatchQueue.main.sync {
                UIApplication.shared.beginBackgroundTask(withName: "HKFetch-\(feature)") {
                    // expiration handler — taskID captured after assignment
                }
            }
            guard taskID != .invalid else {
                HealthKitManager.bgLog("📱 HK-OBSERVER: \(feature) — beginBackgroundTask returned .invalid, aborting")
                completion()
                return
            }

            // Always route observer-triggered uploads via fgSession. bgSession
            // (URLSessionConfiguration.background) hands tasks to nsurlsessiond,
            // which can silently stall for tens of seconds — indefinitely during
            // first-install backfill when HK fires a burst with applicationState
            // != .active. beginBackgroundTask above grants ~30s of guaranteed
            // execution, plenty for a 500-record POST even on a background wake.
            Task {
                await self.fetchAndStore(feature: feature, sampleType: sampleType, unit: unit, appState: "foreground")
                completion()
                await MainActor.run { UIApplication.shared.endBackgroundTask(taskID) }
            }
        }
        store.execute(observer)
    }

    private func registerCumulativeObserver(feature: String, quantityType: HKQuantityType, unit: HKUnit) {
        store.enableBackgroundDelivery(for: quantityType, frequency: .immediate) { _, _ in }

        let observer = HKObserverQuery(sampleType: quantityType, predicate: nil) { [weak self] _, completion, error in
            if let error {
                HealthKitManager.bgLog("📱 HK-OBSERVER: \(feature) (cumulative) fired with error: \(error.localizedDescription)")
                completion()
                return
            }
            guard let self else { completion(); return }

            let stateStr: String = DispatchQueue.main.sync {
                let s = UIApplication.shared.applicationState
                return s == .active ? "active" : s == .background ? "background" : "inactive"
            }
            HealthKitManager.bgLog("📱 HK-OBSERVER: \(feature) (cumulative) fired (appState=\(stateStr))")

            let taskID: UIBackgroundTaskIdentifier = DispatchQueue.main.sync {
                UIApplication.shared.beginBackgroundTask(withName: "HKStats-\(feature)") {
                    // expiration handler
                }
            }
            guard taskID != .invalid else {
                HealthKitManager.bgLog("📱 HK-OBSERVER: \(feature) (cumulative) — beginBackgroundTask returned .invalid, aborting")
                completion()
                return
            }

            Task {
                await self.fetchAndStoreCumulative(
                    feature: feature, quantityType: quantityType, unit: unit, appState: "foreground"
                )
                completion()
                await MainActor.run { UIApplication.shared.endBackgroundTask(taskID) }
            }
        }
        store.execute(observer)
    }

    private func registerWorkoutObserver() {
        let workoutType = HKWorkoutType.workoutType()
        store.enableBackgroundDelivery(for: workoutType, frequency: .immediate) { _, _ in }

        let observer = HKObserverQuery(sampleType: workoutType, predicate: nil) { [weak self] _, completion, error in
            guard error == nil, let self else { completion(); return }

            let taskID: UIBackgroundTaskIdentifier = DispatchQueue.main.sync {
                UIApplication.shared.beginBackgroundTask(withName: "HKFetch-workouts") { }
            }
            guard taskID != .invalid else { completion(); return }

            Task {
                await self.fetchWorkouts(appState: "foreground")
                completion()
                await MainActor.run { UIApplication.shared.endBackgroundTask(taskID) }
            }
        }
        store.execute(observer)
    }

    // MARK: - Workout Fetch

    nonisolated func fetchWorkouts(appState: String = "foreground") async {
        let anchorKey = "anchor_workouts"
        let anchor = HealthKitManager.loadAnchorStatic(anchorKey)

        let thirtyDaysAgo = Calendar.current.date(byAdding: .day, value: -30, to: Date())!
        let predicate = HKQuery.predicateForSamples(withStart: thirtyDaysAgo, end: nil, options: .strictStartDate)

        let healthStore = await MainActor.run { HealthKitManager.shared.store }
        let payloads: [HealthPayload] = await withCheckedContinuation { continuation in
            let query = HKAnchoredObjectQuery(
                type: HKWorkoutType.workoutType(), predicate: predicate, anchor: anchor,
                limit: HKObjectQueryNoLimit
            ) { _, rawSamples, _, newAnchor, _ in
                var results: [HealthPayload] = []

                if let workouts = rawSamples as? [HKWorkout] {
                    for w in workouts {
                        let typeName = HealthKitManager.workoutTypeName(w.workoutActivityType)
                        let ts = w.startDate.timeIntervalSince1970
                        let duration = w.duration // seconds

                        results.append(HealthPayload(ts: ts, value: duration, feature: "workout_\(typeName)_duration"))

                        if let distance = w.totalDistance?.doubleValue(for: .meter()), distance > 0 {
                            results.append(HealthPayload(ts: ts, value: distance, feature: "workout_\(typeName)_distance"))
                        }
                        if let energy = w.statistics(for: HKQuantityType(.activeEnergyBurned))?.sumQuantity()?.doubleValue(for: .kilocalorie()), energy > 0 {
                            results.append(HealthPayload(ts: ts, value: energy, feature: "workout_\(typeName)_energy"))
                        }
                    }
                }

                if let newAnchor {
                    HealthKitManager.saveAnchorStatic(newAnchor, key: anchorKey)
                }
                continuation.resume(returning: results)
            }
            healthStore.execute(query)
        }

        guard !payloads.isEmpty else { return }

        await PendingStore.shared.append(payloads)

        let ts = Date().formatted(date: .omitted, time: .standard)
        let recents = payloads.map {
            RecentSample(feature: $0.f, value: $0.v, timestamp: Date(timeIntervalSince1970: $0.ts), isSynced: false)
        }

        await MainActor.run {
            let m = HealthKitManager.shared
            m.lastSync = ts
            m.recentSamples.insert(contentsOf: recents.reversed(), at: 0)
            if m.recentSamples.count > 1000 {
                m.recentSamples = Array(m.recentSamples.prefix(1000))
            }
        }

        await WebSocketClient.shared.flushPendingAndWait(appState: appState)
    }

    // MARK: - Cumulative Fetch (deduplicated via HKStatisticsCollectionQuery)

    /// Fetches cumulative metrics (steps, distance, energy, etc.) using
    /// HKStatisticsCollectionQuery with .cumulativeSum.  This query automatically
    /// deduplicates overlapping samples from multiple sources (iPhone + Apple Watch)
    /// using Apple's source-priority algorithm — the same logic the Health app uses.
    nonisolated func fetchAndStoreCumulative(
        feature: String,
        quantityType: HKQuantityType,
        unit: HKUnit,
        appState: String = "foreground"
    ) async {
        let hwmKey = "stats_hwm_\(feature)"
        let lastSent = UserDefaults.standard.double(forKey: hwmKey)

        // Lookback window: always re-query the last 6 hours of buckets
        // from the HWM so that cumulative totals (steps, energy, etc.)
        // that HealthKit updated after the initial send are re-sent
        // with the corrected value.  The server uses UPSERT so the
        // latest value wins.
        let lookbackSeconds: TimeInterval = 6 * 3600

        let thirtyDaysAgo = Calendar.current.date(byAdding: .day, value: -30, to: Date())!
        let startDate: Date
        if lastSent > 0 {
            let hwmDate = Date(timeIntervalSince1970: lastSent)
            let lookbackDate = hwmDate.addingTimeInterval(-lookbackSeconds)
            startDate = max(lookbackDate, thirtyDaysAgo)
        } else {
            startDate = thirtyDaysAgo
        }

        // Align bucket boundaries to midnight for predictable hourly intervals.
        let calendar = Calendar.current
        let anchorDate = calendar.startOfDay(for: startDate)
        let interval = DateComponents(hour: 1)
        let now = Date()

        let predicate = HKQuery.predicateForSamples(
            withStart: startDate, end: now, options: .strictStartDate
        )

        let healthStore = await MainActor.run { HealthKitManager.shared.store }
        let (localPayloads, latestEndTs): ([HealthPayload], Double) = await withCheckedContinuation { continuation in
            let query = HKStatisticsCollectionQuery(
                quantityType: quantityType,
                quantitySamplePredicate: predicate,
                options: .cumulativeSum,
                anchorDate: anchorDate,
                intervalComponents: interval
            )

            query.initialResultsHandler = { _, collection, error in
                guard let collection = collection, error == nil else {
                    let errMsg = error?.localizedDescription ?? "nil"
                    let isProtected = errMsg.contains("Protected") || errMsg.contains("inaccessible")
                    HealthKitManager.bgLog("📱 HK-STATS: \(feature) query FAILED — \(errMsg)\(isProtected ? " [DEVICE LOCKED]" : "")")
                    continuation.resume(returning: ([], 0))
                    return
                }

                var payloads: [HealthPayload] = []
                var maxEndTs: Double = 0
                collection.enumerateStatistics(from: startDate, to: now) { stats, _ in
                    // Only emit buckets where HealthKit has actual data.
                    // nil sumQuantity means no samples exist for this bucket —
                    // skip it to avoid flooding the server with empty future buckets.
                    guard let sum = stats.sumQuantity() else { return }
                    let value = sum.doubleValue(for: unit)
                    payloads.append(HealthPayload(
                        ts: stats.startDate.timeIntervalSince1970,
                        value: value,
                        feature: feature
                    ))
                    let endTs = stats.endDate.timeIntervalSince1970
                    if endTs > maxEndTs { maxEndTs = endTs }
                }
                continuation.resume(returning: (payloads, maxEndTs))
            }

            healthStore.execute(query)
        }

        guard !localPayloads.isEmpty else {
            HealthKitManager.bgLog("📱 HK-STATS: \(feature) — 0 new buckets (appState=\(appState))")
            return
        }

        HealthKitManager.bgLog("📱 HK-STATS: \(feature) — \(localPayloads.count) new buckets → PendingStore (appState=\(appState))")

        await PendingStore.shared.append(localPayloads)

        // Advance high-water mark to the latest bucket END time so we don't re-fetch it.
        if latestEndTs > 0 {
            UserDefaults.standard.set(latestEndTs, forKey: hwmKey)
        }

        let ts = Date().formatted(date: .omitted, time: .standard)
        let recentSlice = localPayloads.suffix(1000)
        let recents = recentSlice.map {
            RecentSample(
                feature: $0.f,
                value: $0.v,
                timestamp: Date(timeIntervalSince1970: $0.ts),
                isSynced: false
            )
        }

        await MainActor.run {
            let m = HealthKitManager.shared
            m.lastSync = ts
            m.recentSamples.insert(contentsOf: recents.reversed(), at: 0)
            if m.recentSamples.count > 1000 {
                m.recentSamples = Array(m.recentSamples.prefix(1000))
            }
        }

        await WebSocketClient.shared.flushPendingAndWait(appState: appState)
    }

    // MARK: - Core Fetch (instantaneous metrics only)

    // FIX: Converted from semaphore-based sync to async/await via CheckedContinuation.
    // This avoids potential deadlocks when called from a context that cannot be blocked
    // (e.g. the main thread or a Task on the cooperative thread pool).
    nonisolated func fetchAndStore(
        feature: String,
        sampleType: HKSampleType,
        unit: HKUnit?,
        appState: String = "foreground"
    ) async {
        let anchorKey = "anchor_\(sampleType.identifier)"
        let anchor = HealthKitManager.loadAnchorStatic(anchorKey)

        // Always cap queries to the last 30 days — prevents historical data flood
        // after app reinstall (anchor lost) and keeps incremental queries bounded.
        let thirtyDaysAgo = Calendar.current.date(byAdding: .day, value: -30, to: Date())!
        let predicate = HKQuery.predicateForSamples(withStart: thirtyDaysAgo, end: nil, options: .strictStartDate)

        let healthStore = await MainActor.run { HealthKitManager.shared.store }
        let localPayloads: [HealthPayload] = await withCheckedContinuation { continuation in
            let query = HKAnchoredObjectQuery(
                type: sampleType, predicate: predicate, anchor: anchor,
                limit: HKObjectQueryNoLimit
            ) { _, rawSamples, _, newAnchor, _ in
                var payloads: [HealthPayload] = []

                if let samples = rawSamples as? [HKQuantitySample], let unit {
                    for s in samples {
                        payloads.append(HealthPayload(
                            ts: s.startDate.timeIntervalSince1970,
                            value: s.quantity.doubleValue(for: unit),
                            feature: feature
                        ))
                    }
                } else if let samples = rawSamples as? [HKCategorySample] {
                    HealthKitManager.bgLog("Category \(feature): got \(samples.count) samples")
                    for s in samples {
                        let (feat, val) = HealthKitManager.parseCategorySample(s, baseFeature: feature)
                        // For duration-based categories (sleep, mindful), use endDate as ts
                        // so the agent can derive startDate = ts - duration.
                        // This matches Health app's date attribution (overnight sleep → next day).
                        let isDuration = feature == HealthKitManager.sleepPlaceholder || feature == "mindful_session"
                        let sampleTs = isDuration ? s.endDate.timeIntervalSince1970 : s.startDate.timeIntervalSince1970
                        payloads.append(HealthPayload(
                            ts: sampleTs,
                            value: val,
                            feature: feat
                        ))
                    }
                }

                if let newAnchor {
                    HealthKitManager.saveAnchorStatic(newAnchor, key: anchorKey)
                }
                continuation.resume(returning: payloads)
            }
            healthStore.execute(query)
        }

        guard !localPayloads.isEmpty else {
            HealthKitManager.bgLog("📱 HK-FETCH: \(feature) — 0 new samples (appState=\(appState))")
            return
        }

        HealthKitManager.bgLog("📱 HK-FETCH: \(feature) — \(localPayloads.count) new samples → PendingStore (appState=\(appState))")

        await PendingStore.shared.append(localPayloads)

        let ts = Date().formatted(date: .omitted, time: .standard)
        let recentSlice = localPayloads.suffix(1000)
        let recents = recentSlice.map {
            RecentSample(
                feature: $0.f,
                value: $0.v,
                timestamp: Date(timeIntervalSince1970: $0.ts),
                isSynced: false
            )
        }

        await MainActor.run {
            let m = HealthKitManager.shared
            m.lastSync = ts
            m.recentSamples.insert(contentsOf: recents.reversed(), at: 0)
            if m.recentSamples.count > 1000 {
                m.recentSamples = Array(m.recentSamples.prefix(1000))
            }
        }

        await WebSocketClient.shared.flushPendingAndWait(appState: appState)
    }

    // MARK: - UI Sync Status

    /// Marks the N oldest samples as synced in the UI.
    /// Already isolated to @MainActor via the class annotation.
    func markOldestAsSynced(count: Int) {
        guard count > 0, !recentSamples.isEmpty else { return }
        var remaining = count
        // recentSamples is newest-first; iterate from the end to find the oldest unsynced.
        // Snapshot the count to avoid issues if the array is mutated during iteration.
        let upperBound = recentSamples.count
        for i in stride(from: upperBound - 1, through: 0, by: -1) {
            if remaining <= 0 { break }
            guard i < recentSamples.count else { continue }
            if !recentSamples[i].isSynced {
                recentSamples[i].isSynced = true
                remaining -= 1
            }
        }
    }

    // MARK: - Force Fetch

    func forceFetch() {
        HealthKitManager.bgLog("HK: Force sweep started...")
        var bgTaskID: UIBackgroundTaskIdentifier = .invalid
        bgTaskID = UIApplication.shared.beginBackgroundTask(withName: "HKForceFetch") {
            UIApplication.shared.endBackgroundTask(bgTaskID)
        }
        guard bgTaskID != .invalid else { return }
        // FIX: Also fetches categoryMetrics (sleep, mindful, heart events) which was missing before.
        Task {
            for (f, id, unit) in self.quantityMetrics {
                if HealthKitManager.cumulativeFeatures.contains(f) {
                    await self.fetchAndStoreCumulative(
                        feature: f, quantityType: HKQuantityType(id), unit: unit, appState: "foreground"
                    )
                } else if let type = HKQuantityType.quantityType(forIdentifier: id) {
                    await self.fetchAndStore(feature: f, sampleType: type, unit: unit, appState: "foreground")
                }
            }
            for (f, id) in self.categoryMetrics {
                if let type = HKObjectType.categoryType(forIdentifier: id) {
                    await self.fetchAndStore(feature: f, sampleType: type, unit: nil, appState: "foreground")
                }
            }
            await self.fetchWorkouts(appState: "foreground")
            HealthKitManager.bgLog("HK: Force sweep completed.")
            UIApplication.shared.endBackgroundTask(bgTaskID)
        }
    }

    // MARK: - Burst Mode (Workout Session)

    private func toggleWorkoutSession() {
        if isBurstModeEnabled {
            startWorkoutSession()
        } else {
            stopWorkoutSession()
        }
    }

    private func startWorkoutSession() {
        guard workoutSession == nil else { return }

        if #available(iOS 26.0, *) {
            let config = HKWorkoutConfiguration()
            config.activityType = .other

            do {
                let session = try HKWorkoutSession(healthStore: store, configuration: config)
                self.workoutSession = session
                session.startActivity(with: Date())
                HealthKitManager.bgLog("Burst Mode: ON")
            } catch {
                HealthKitManager.bgLog("Burst Mode ERR: \(error.localizedDescription)")
                isBurstModeEnabled = false
            }
        } else {
            HealthKitManager.bgLog("Burst Mode: not available on this iOS version")
            isBurstModeEnabled = false
        }
    }

    private func stopWorkoutSession() {
        workoutSession?.end()
        workoutSession = nil
        HealthKitManager.bgLog("Burst Mode: OFF")
    }

    // MARK: - Helpers

    nonisolated private static func parseCategorySample(_ s: HKCategorySample, baseFeature: String) -> (String, Double) {
        if baseFeature == sleepPlaceholder {
            var feat = "sleep_unknown"
            switch HKCategoryValueSleepAnalysis(rawValue: s.value) {
            case .inBed:             feat = "sleep_in_bed"
            case .asleepUnspecified: feat = "sleep_asleep"
            case .asleepCore:        feat = "sleep_core"
            case .asleepDeep:        feat = "sleep_deep"
            case .asleepREM:         feat = "sleep_rem"
            case .awake:             feat = "sleep_awake"
            default: break
            }
            return (feat, s.endDate.timeIntervalSince(s.startDate))
        }
        if baseFeature == "mindful_session" {
            return (baseFeature, s.endDate.timeIntervalSince(s.startDate))
        }
        // Heart rate events and audio exposure events are presence indicators —
        // store duration (seconds) so the agent knows how long the event lasted.
        // Falls back to 1.0 for zero-length events.
        let duration = s.endDate.timeIntervalSince(s.startDate)
        return (baseFeature, duration > 0 ? duration : 1.0)
    }

    nonisolated private static func loadAnchorStatic(_ key: String) -> HKQueryAnchor? {
        UserDefaults.standard.data(forKey: key).flatMap {
            try? NSKeyedUnarchiver.unarchivedObject(ofClass: HKQueryAnchor.self, from: $0)
        }
    }

    nonisolated private static func saveAnchorStatic(_ anchor: HKQueryAnchor, key: String) {
        if let data = try? NSKeyedArchiver.archivedData(withRootObject: anchor, requiringSecureCoding: true) {
            UserDefaults.standard.set(data, forKey: key)
        }
    }

    nonisolated static func bgLog(_ msg: String) {
        Task { @MainActor in
            LogManager.shared.log(msg)
        }
    }

    // MARK: - Background Tasks

    nonisolated static func scheduleBackgroundRefresh() {
        let request = BGAppRefreshTaskRequest(identifier: "com.hime.healthkit.refresh")
        request.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60)
        do {
            try BGTaskScheduler.shared.submit(request)
            bgLog("📱 BG-REFRESH: scheduled next refresh in 15 min")
        } catch {
            bgLog("📱 BG-REFRESH: submit ERR: \(error.localizedDescription)")
        }
    }

    nonisolated func handleBackgroundRefresh(task: BGAppRefreshTask) {
        HealthKitManager.bgLog("📱 BG-REFRESH: handleBackgroundRefresh started (pending=\(PendingStore.shared.count))")
        HealthKitManager.scheduleBackgroundRefresh()

        task.expirationHandler = {
            HealthKitManager.bgLog("📱 BG-REFRESH: task EXPIRED before completion")
            task.setTaskCompleted(success: false)
        }

        Task { @MainActor in
            let m = HealthKitManager.shared

            let priorityFeatures: Set<String> = [
                "heart_rate", "blood_oxygen", "heart_rate_variability",
                "resting_heart_rate", "respiratory_rate", "steps",
                "active_energy", "exercise_time", HealthKitManager.sleepPlaceholder
            ]

            // Fetch priority metrics first
            for (f, id, unit) in m.quantityMetrics where priorityFeatures.contains(f) {
                if HealthKitManager.cumulativeFeatures.contains(f) {
                    await m.fetchAndStoreCumulative(
                        feature: f, quantityType: HKQuantityType(id), unit: unit, appState: "background"
                    )
                } else if let type = HKQuantityType.quantityType(forIdentifier: id) {
                    await m.fetchAndStore(feature: f, sampleType: type, unit: unit, appState: "background")
                }
            }
            for (f, id) in m.categoryMetrics where priorityFeatures.contains(f) {
                if let type = HKObjectType.categoryType(forIdentifier: id) {
                    await m.fetchAndStore(feature: f, sampleType: type, unit: nil, appState: "background")
                }
            }

            // Fetch workouts (priority — captures structured exercise sessions)
            await m.fetchWorkouts(appState: "background")

            // Then remaining metrics
            for (f, id, unit) in m.quantityMetrics where !priorityFeatures.contains(f) {
                if HealthKitManager.cumulativeFeatures.contains(f) {
                    await m.fetchAndStoreCumulative(
                        feature: f, quantityType: HKQuantityType(id), unit: unit, appState: "background"
                    )
                } else if let type = HKQuantityType.quantityType(forIdentifier: id) {
                    await m.fetchAndStore(feature: f, sampleType: type, unit: unit, appState: "background")
                }
            }
            for (f, id) in m.categoryMetrics where !priorityFeatures.contains(f) {
                if let type = HKObjectType.categoryType(forIdentifier: id) {
                    await m.fetchAndStore(feature: f, sampleType: type, unit: nil, appState: "background")
                }
            }

            HealthKitManager.bgLog("📱 BG-REFRESH: completed (pending=\(PendingStore.shared.count))")
            task.setTaskCompleted(success: true)
        }
    }
}
