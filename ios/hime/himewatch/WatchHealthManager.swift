//
//  WatchHealthManager.swift
//  himeWatch Watch App
//

import HealthKit
import Combine
import os

private let watchLog = Logger(subsystem: "com.hime.watch", category: "HealthSync")

/// Log to both os.Logger and buffer for iPhone forwarding.
func watchHealthLog(_ msg: String) {
    watchLog.info("\(msg)")
    Task { @MainActor in
        WatchConnectivityManager.shared.bufferLog(msg)
    }
}

@MainActor
class WatchHealthManager: ObservableObject {
    static let shared = WatchHealthManager()

    @Published var lastSyncTime: String = "Never"
    @Published var samplesSynced: Int = 0
    @Published var isAuthorized: Bool = false

    // Latest values for display
    @Published var latestHeartRate: Double?
    @Published var latestHeartRateTime: Date?
    @Published var latestBloodOxygen: Double?
    @Published var latestBloodOxygenTime: Date?
    @Published var latestHRV: Double?
    @Published var latestHRVTime: Date?
    @Published var todaySteps: Double = 0
    @Published var todaySleepMinutes: Double = 0
    @Published var todayActiveEnergy: Double = 0

    private let store = HKHealthStore()

    /// Instantaneous metrics — synced to server from Watch (real-time, no dedup issue).
    private let instantMetrics: [(String, HKQuantityTypeIdentifier, HKUnit)] = [
        ("heart_rate", .heartRate, HKUnit.count().unitDivided(by: .minute())),
        ("heart_rate_variability", .heartRateVariabilitySDNN, .secondUnit(with: .milli)),
        ("blood_oxygen", .oxygenSaturation, .percent()),
        ("resting_heart_rate", .restingHeartRate, HKUnit.count().unitDivided(by: .minute())),
        ("respiratory_rate", .respiratoryRate, HKUnit.count().unitDivided(by: .minute())),
    ]

    /// Cumulative metrics — observed on Watch for UI display only, NOT synced to server.
    /// iPhone's HKStatisticsCollectionQuery is the authoritative source for these
    /// (uses Apple's source-priority deduplication, matches iOS Health exactly).
    private let cumulativeMetrics: [(String, HKQuantityTypeIdentifier, HKUnit)] = [
        ("steps", .stepCount, .count()),
        ("active_energy", .activeEnergyBurned, .kilocalorie()),
        ("exercise_time", .appleExerciseTime, .second()),
    ]

    private let categoryMetrics: [(String, HKCategoryTypeIdentifier)] = [
        ("__sleep__", .sleepAnalysis),
    ]

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
        guard HKHealthStore.isHealthDataAvailable() else { return }

        var readTypes = Set<HKObjectType>()
        for (_, id, _) in instantMetrics {
            if let type = HKQuantityType.quantityType(forIdentifier: id) {
                readTypes.insert(type)
            }
        }
        for (_, id, _) in cumulativeMetrics {
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
            isAuthorized = true
        } catch {
            isAuthorized = false
        }

        // Instantaneous metrics: observe + sync to server
        for (f, id, unit) in instantMetrics {
            registerObserver(feature: f, sampleType: HKQuantityType(id), unit: unit)
        }
        // Category metrics (sleep): observe + sync to server
        for (f, id) in categoryMetrics {
            registerObserver(feature: f, sampleType: HKCategoryType(id), unit: nil)
        }
        // Workouts: observe + sync to server
        registerWorkoutObserver()
        // Cumulative metrics: observe for local UI display only (no server sync)
        for (_, id, _) in cumulativeMetrics {
            registerLocalObserver(sampleType: HKQuantityType(id))
        }

        // Fetch initial today stats
        await fetchTodayStats()
    }

    /// Fetch today's cumulative stats via HKStatisticsQuery
    func fetchTodayStats() async {
        let calendar = Calendar.current
        let startOfDay = calendar.startOfDay(for: Date())
        let predicate = HKQuery.predicateForSamples(withStart: startOfDay, end: nil, options: .strictStartDate)

        // Steps
        if let stepsType = HKQuantityType.quantityType(forIdentifier: .stepCount) {
            let steps = await querySum(type: stepsType, unit: .count(), predicate: predicate)
            todaySteps = steps
        }

        // Active energy
        if let energyType = HKQuantityType.quantityType(forIdentifier: .activeEnergyBurned) {
            let energy = await querySum(type: energyType, unit: .kilocalorie(), predicate: predicate)
            todayActiveEnergy = energy
        }

        // Latest heart rate
        if let hrType = HKQuantityType.quantityType(forIdentifier: .heartRate) {
            if let (hr, time) = await queryLatest(type: hrType, unit: HKUnit.count().unitDivided(by: .minute())) {
                latestHeartRate = hr
                latestHeartRateTime = time
            }
        }

        // Latest blood oxygen
        if let o2Type = HKQuantityType.quantityType(forIdentifier: .oxygenSaturation) {
            if let (o2, time) = await queryLatest(type: o2Type, unit: .percent()) {
                latestBloodOxygen = o2 * 100
                latestBloodOxygenTime = time
            }
        }

        // Latest HRV
        if let hrvType = HKQuantityType.quantityType(forIdentifier: .heartRateVariabilitySDNN) {
            if let (hrv, time) = await queryLatest(type: hrvType, unit: .secondUnit(with: .milli)) {
                latestHRV = hrv
                latestHRVTime = time
            }
        }

        // Sleep (last 24h)
        let sleepStart = calendar.date(byAdding: .hour, value: -24, to: Date())!
        let sleepPred = HKQuery.predicateForSamples(withStart: sleepStart, end: nil, options: .strictStartDate)
        await fetchSleepTotal(predicate: sleepPred)

        // Push freshest HR + steps into the watch widget snapshot.
        WatchConnectivityManager.shared.publishWatchSnapshot(
            heartRate: latestHeartRate,
            steps: todaySteps
        )
    }

    private nonisolated func querySum(type: HKQuantityType, unit: HKUnit, predicate: NSPredicate) async -> Double {
        let healthStore = await MainActor.run { self.store }
        return await withCheckedContinuation { continuation in
            let query = HKStatisticsQuery(quantityType: type, quantitySamplePredicate: predicate, options: .cumulativeSum) { _, stats, _ in
                let value = stats?.sumQuantity()?.doubleValue(for: unit) ?? 0
                continuation.resume(returning: value)
            }
            healthStore.execute(query)
        }
    }

    private nonisolated func queryLatest(type: HKQuantityType, unit: HKUnit) async -> (Double, Date)? {
        let healthStore = await MainActor.run { self.store }
        return await withCheckedContinuation { continuation in
            let sort = NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: false)
            let query = HKSampleQuery(sampleType: type, predicate: nil, limit: 1, sortDescriptors: [sort]) { _, samples, _ in
                if let sample = samples?.first as? HKQuantitySample {
                    continuation.resume(returning: (sample.quantity.doubleValue(for: unit), sample.startDate))
                } else {
                    continuation.resume(returning: nil)
                }
            }
            healthStore.execute(query)
        }
    }

    private nonisolated func fetchSleepTotal(predicate: NSPredicate) async {
        let healthStore = await MainActor.run { self.store }
        let total: Double = await withCheckedContinuation { continuation in
            guard let sleepType = HKObjectType.categoryType(forIdentifier: .sleepAnalysis) else {
                continuation.resume(returning: 0)
                return
            }
            let query = HKSampleQuery(sampleType: sleepType, predicate: predicate, limit: HKObjectQueryNoLimit, sortDescriptors: nil) { _, samples, _ in
                var minutes: Double = 0
                for s in (samples as? [HKCategorySample] ?? []) {
                    let val = HKCategoryValueSleepAnalysis(rawValue: s.value)
                    if val == .asleepCore || val == .asleepDeep || val == .asleepREM || val == .asleepUnspecified {
                        minutes += s.endDate.timeIntervalSince(s.startDate) / 60
                    }
                }
                continuation.resume(returning: minutes)
            }
            healthStore.execute(query)
        }
        await MainActor.run { self.todaySleepMinutes = total }
    }

    // MARK: - Local-only observer (UI refresh, no server sync)

    /// Observe cumulative metrics to refresh Watch UI (steps, energy display).
    /// Server sync for these is handled by iPhone's HKStatisticsCollectionQuery.
    private func registerLocalObserver(sampleType: HKSampleType) {
        let observer = HKObserverQuery(sampleType: sampleType, predicate: nil) { [weak self] _, completion, error in
            guard error == nil, let self else { completion(); return }
            completion()
            Task { await self.fetchTodayStats() }
        }
        store.execute(observer)
    }

    // MARK: - Observer-based sync to server

    private func registerObserver(feature: String, sampleType: HKSampleType, unit: HKUnit?) {
        store.enableBackgroundDelivery(for: sampleType, frequency: .immediate) { ok, err in
            if let err { watchHealthLog("⌚ BG-DELIVERY: enableBackgroundDelivery FAILED for \(feature): \(err.localizedDescription)") }
            else if ok { watchHealthLog("⌚ BG-DELIVERY: enabled for \(feature)") }
        }

        let observer = HKObserverQuery(sampleType: sampleType, predicate: nil) { [weak self] _, completion, error in
            if let error {
                watchHealthLog("⌚ OBSERVER: \(feature) fired with error: \(error.localizedDescription)")
                completion()
                return
            }
            guard let self else { completion(); return }
            watchHealthLog("⌚ OBSERVER: \(feature) fired — starting fetch")
            Task {
                await self.fetchAndTransfer(feature: feature, sampleType: sampleType, unit: unit)
                watchHealthLog("⌚ OBSERVER: \(feature) fetch+transfer done — calling completion()")
                completion()
            }
        }
        store.execute(observer)
    }

    nonisolated func fetchAndTransfer(
        feature: String,
        sampleType: HKSampleType,
        unit: HKUnit?
    ) async {
        let healthStore = await MainActor.run { self.store }
        let anchorKey = "watch_anchor_\(sampleType.identifier)"
        let anchor = Self.loadAnchor(anchorKey)

        let threeDaysAgo = Calendar.current.date(byAdding: .day, value: -3, to: Date())!
        let predicate = HKQuery.predicateForSamples(withStart: threeDaysAgo, end: nil, options: .strictStartDate)

        let payloads: [[String: Any]] = await withCheckedContinuation { continuation in
            let query = HKAnchoredObjectQuery(
                type: sampleType, predicate: predicate, anchor: anchor,
                limit: HKObjectQueryNoLimit
            ) { _, rawSamples, _, newAnchor, _ in
                var results: [[String: Any]] = []

                if let samples = rawSamples as? [HKQuantitySample], let unit {
                    for s in samples {
                        results.append(["ts": s.startDate.timeIntervalSince1970, "v": s.quantity.doubleValue(for: unit), "f": feature])
                    }
                } else if let samples = rawSamples as? [HKCategorySample] {
                    for s in samples {
                        let (feat, val) = Self.parseCategorySample(s, baseFeature: feature)
                        // Match iPhone: duration-based categories (sleep, mindful) use endDate
                        // as ts so cross-source duplicates collapse on the (timestamp,
                        // feature_type) primary key. Using startDate here previously caused
                        // every sleep block to be stored twice (once from Watch, once from
                        // iPhone), roughly doubling the nightly sleep total.
                        let isDuration = feature == "__sleep__" || feature == "mindful_session"
                        let ts = isDuration ? s.endDate.timeIntervalSince1970 : s.startDate.timeIntervalSince1970
                        results.append(["ts": ts, "v": val, "f": feat])
                    }
                }

                if let newAnchor {
                    Self.saveAnchor(newAnchor, key: anchorKey)
                }
                continuation.resume(returning: results)
            }
            healthStore.execute(query)
        }

        guard !payloads.isEmpty else {
            watchHealthLog("⌚ FETCH: \(feature) — 0 new samples (anchor up-to-date)")
            WatchConnectivityManager.shared.flushLogs()
            return
        }

        watchHealthLog("⌚ FETCH: \(feature) — \(payloads.count) new samples, sending via WC + HTTP")

        // Send via both paths concurrently:
        // 1. WatchConnectivity → iPhone → Server (backup, handles cat state sync etc.)
        // 2. Direct HTTP POST → Server (primary, works even when iPhone app is suspended)
        let wc = WatchConnectivityManager.shared
        async let wcResult: () = wc.sendHealthData(payloads)
        async let httpResult: () = wc.sendHealthDataHTTP(payloads)
        _ = await (wcResult, httpResult)

        watchHealthLog("⌚ SEND: \(feature) — both WC and HTTP paths completed")
        WatchConnectivityManager.shared.flushLogs()

        // Update latest display values from new samples
        await MainActor.run {
            let m = WatchHealthManager.shared
            m.samplesSynced += payloads.count
            m.lastSyncTime = Date().formatted(date: .omitted, time: .shortened)

            for p in payloads {
                guard let f = p["f"] as? String, let v = p["v"] as? Double, let ts = p["ts"] as? Double else { continue }
                let sampleTime = Date(timeIntervalSince1970: ts)
                switch f {
                case "heart_rate":
                    m.latestHeartRate = v
                    m.latestHeartRateTime = sampleTime
                case "blood_oxygen":
                    m.latestBloodOxygen = v * 100
                    m.latestBloodOxygenTime = sampleTime
                case "heart_rate_variability":
                    m.latestHRV = v
                    m.latestHRVTime = sampleTime
                default: break
                }
            }
        }
        // Re-fetch today's cumulative stats (steps, energy, sleep)
        await WatchHealthManager.shared.fetchTodayStats()
    }

    private func registerWorkoutObserver() {
        let workoutType = HKWorkoutType.workoutType()
        store.enableBackgroundDelivery(for: workoutType, frequency: .immediate) { ok, err in
            if let err { watchHealthLog("⌚ BG-DELIVERY: enableBackgroundDelivery FAILED for workouts: \(err.localizedDescription)") }
            else if ok { watchHealthLog("⌚ BG-DELIVERY: enabled for workouts") }
        }

        let observer = HKObserverQuery(sampleType: workoutType, predicate: nil) { [weak self] _, completion, error in
            if let error {
                watchHealthLog("⌚ OBSERVER: workouts fired with error: \(error.localizedDescription)")
                completion()
                return
            }
            guard let self else { completion(); return }
            watchHealthLog("⌚ OBSERVER: workouts fired — starting fetch")
            Task {
                await self.fetchWorkouts()
                watchHealthLog("⌚ OBSERVER: workouts fetch+transfer done — calling completion()")
                completion()
            }
        }
        store.execute(observer)
    }

    nonisolated func fetchWorkouts() async {
        let healthStore = await MainActor.run { self.store }
        let anchorKey = "watch_anchor_workouts"
        let anchor = Self.loadAnchor(anchorKey)

        let threeDaysAgo = Calendar.current.date(byAdding: .day, value: -3, to: Date())!
        let predicate = HKQuery.predicateForSamples(withStart: threeDaysAgo, end: nil, options: .strictStartDate)

        let payloads: [[String: Any]] = await withCheckedContinuation { continuation in
            let query = HKAnchoredObjectQuery(
                type: HKWorkoutType.workoutType(), predicate: predicate, anchor: anchor,
                limit: HKObjectQueryNoLimit
            ) { _, rawSamples, _, newAnchor, _ in
                var results: [[String: Any]] = []

                if let workouts = rawSamples as? [HKWorkout] {
                    for w in workouts {
                        let typeName = WatchHealthManager.workoutTypeName(w.workoutActivityType)
                        let ts = w.startDate.timeIntervalSince1970
                        results.append(["ts": ts, "v": w.duration, "f": "workout_\(typeName)_duration"])
                        if let distance = w.totalDistance?.doubleValue(for: .meter()), distance > 0 {
                            results.append(["ts": ts, "v": distance, "f": "workout_\(typeName)_distance"])
                        }
                        if let energy = w.statistics(for: HKQuantityType(.activeEnergyBurned))?.sumQuantity()?.doubleValue(for: .kilocalorie()), energy > 0 {
                            results.append(["ts": ts, "v": energy, "f": "workout_\(typeName)_energy"])
                        }
                    }
                }

                if let newAnchor {
                    Self.saveAnchor(newAnchor, key: anchorKey)
                }
                continuation.resume(returning: results)
            }
            healthStore.execute(query)
        }

        guard !payloads.isEmpty else {
            watchHealthLog("⌚ FETCH: workouts — 0 new samples")
            WatchConnectivityManager.shared.flushLogs()
            return
        }

        watchHealthLog("⌚ FETCH: workouts — \(payloads.count) new samples, sending via WC + HTTP")

        let wc = WatchConnectivityManager.shared
        async let wcResult: () = wc.sendHealthData(payloads)
        async let httpResult: () = wc.sendHealthDataHTTP(payloads)
        _ = await (wcResult, httpResult)

        watchHealthLog("⌚ SEND: workouts — both WC and HTTP paths completed")
        WatchConnectivityManager.shared.flushLogs()

        await MainActor.run {
            let m = WatchHealthManager.shared
            m.samplesSynced += payloads.count
            m.lastSyncTime = Date().formatted(date: .omitted, time: .shortened)
        }
    }

    // MARK: - Helpers

    nonisolated private static func parseCategorySample(_ s: HKCategorySample, baseFeature: String) -> (String, Double) {
        if baseFeature == "__sleep__" {
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
        return (baseFeature, Double(s.value))
    }

    nonisolated private static func loadAnchor(_ key: String) -> HKQueryAnchor? {
        UserDefaults.standard.data(forKey: key).flatMap {
            try? NSKeyedUnarchiver.unarchivedObject(ofClass: HKQueryAnchor.self, from: $0)
        }
    }

    nonisolated private static func saveAnchor(_ anchor: HKQueryAnchor, key: String) {
        if let data = try? NSKeyedArchiver.archivedData(withRootObject: anchor, requiringSecureCoding: true) {
            UserDefaults.standard.set(data, forKey: key)
        }
    }
}
