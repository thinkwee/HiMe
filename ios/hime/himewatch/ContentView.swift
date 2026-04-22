//
//  ContentView.swift
//  himeWatch Watch App
//

import SwiftUI
import Combine

// MARK: - ContentView

struct ContentView: View {
    @StateObject private var health = WatchHealthManager.shared
    @StateObject private var connectivity = WatchConnectivityManager.shared

    var body: some View {
        NavigationStack {
        ScrollView {
            VStack(spacing: 8) {
                // Cat head pixel art
                WatchCatHead(catState: connectivity.catState)
                    .frame(width: 120, height: 120)

                // State label
                Text(connectivity.catState.capitalized)
                    .font(.system(size: 12, weight: .semibold, design: .rounded))
                    .foregroundColor(stateColor)

                // Cat message from AI
                if !connectivity.catMessage.isEmpty {
                    Text(connectivity.catMessage)
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)
                        .lineLimit(2)
                        .padding(.horizontal, 4)
                }

                // Health metrics 2x2 grid
                LazyVGrid(columns: [GridItem(.flexible(), spacing: 6),
                                    GridItem(.flexible(), spacing: 6)], spacing: 6) {
                    HealthCard(
                        icon: "heart.fill",
                        color: .red,
                        value: health.latestHeartRate.map { "\(Int($0))" },
                        unit: "bpm",
                        label: String(localized: "Heart Rate"),
                        timestamp: health.latestHeartRateTime
                    )
                    HealthCard(
                        icon: "figure.walk",
                        color: .green,
                        value: health.todaySteps > 0 ? formatSteps(health.todaySteps) : nil,
                        unit: "",
                        label: String(localized: "Steps"),
                        subtitle: String(localized: "Today")
                    )
                    HealthCard(
                        icon: "lungs.fill",
                        color: .blue,
                        value: health.latestBloodOxygen.map { String(format: "%.0f", $0) },
                        unit: "%",
                        label: String(localized: "Blood O\u{2082}"),
                        timestamp: health.latestBloodOxygenTime
                    )
                    HealthCard(
                        icon: "bed.double.fill",
                        color: .indigo,
                        value: health.todaySleepMinutes > 0 ? formatSleep(health.todaySleepMinutes) : nil,
                        unit: "",
                        label: String(localized: "Sleep"),
                        subtitle: String(localized: "Today")
                    )
                }

                // Secondary metrics row
                HStack(spacing: 6) {
                    MiniCard(
                        label: String(localized: "HRV"),
                        value: health.latestHRV.map { "\(Int($0))" } ?? "--",
                        unit: "ms",
                        color: .purple,
                        timestamp: health.latestHRVTime
                    )
                    MiniCard(
                        label: String(localized: "Energy"),
                        value: health.todayActiveEnergy > 0 ? "\(Int(health.todayActiveEnergy))" : "--",
                        unit: "kcal",
                        color: .orange,
                        subtitle: String(localized: "Today")
                    )
                }

                // Sync status footer
                HStack(spacing: 4) {
                    Circle()
                        .fill(connectivity.isPhoneReachable ? Color.green : Color.orange)
                        .frame(width: 6, height: 6)
                    Text(connectivity.isPhoneReachable ? LocalizedStringKey("Syncing") : LocalizedStringKey("Queued"))
                        .font(.system(size: 9))
                        .foregroundColor(.secondary)
                    Spacer()
                    Text("\(health.samplesSynced)")
                        .font(.system(size: 9, weight: .medium, design: .monospaced))
                        .foregroundColor(.secondary)
                    Text("synced")
                        .font(.system(size: 9))
                        .foregroundColor(.secondary.opacity(0.7))
                }
                .padding(.horizontal, 4)
                .padding(.top, 2)

                // Notification
                if !connectivity.lastNotification.isEmpty {
                    Text(connectivity.lastNotification)
                        .font(.system(size: 9))
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)
                        .lineLimit(2)
                        .padding(.horizontal, 4)
                }
            }
            .padding(.horizontal, 4)
            .padding(.vertical, 4)
        }
        .navigationTitle("Hime")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await health.setup()
        }
        } // NavigationStack
    }

    private var stateColor: Color {
        watchStateColor(for: connectivity.catState)
    }

    private func formatSteps(_ steps: Double) -> String {
        if steps >= 10000 { return String(format: "%.1fK", steps / 1000) }
        return "\(Int(steps))"
    }

    private func formatSleep(_ minutes: Double) -> String {
        let hours = Int(minutes) / 60
        let mins = Int(minutes) % 60
        if hours > 0 { return "\(hours)h\(mins)m" }
        return "\(mins)m"
    }
}

// MARK: - Health Card

private struct HealthCard: View {
    let icon: String
    let color: Color
    let value: String?
    let unit: String
    let label: String
    var timestamp: Date? = nil
    var subtitle: String? = nil

    private var timeLabel: String? {
        guard let timestamp else { return nil }
        let mins = Int(-timestamp.timeIntervalSinceNow / 60)
        if mins < 1 { return "now" }
        if mins < 60 { return "\(mins)m ago" }
        let hours = mins / 60
        if hours < 24 { return "\(hours)h ago" }
        return nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Image(systemName: icon)
                .font(.system(size: 10))
                .foregroundColor(color)

            HStack(spacing: 1) {
                Text(value ?? "--")
                    .font(.system(size: 16, weight: .bold, design: .rounded).monospacedDigit())
                    .foregroundColor(.primary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.6)
                if !unit.isEmpty {
                    Text(unit)
                        .font(.system(size: 8))
                        .foregroundColor(.secondary)
                }
            }

            Text(label)
                .font(.system(size: 8))
                .foregroundColor(.secondary)
                .lineLimit(1)

            if let tl = timeLabel {
                Text(tl)
                    .font(.system(size: 7))
                    .foregroundColor(.secondary.opacity(0.6))
            } else if let subtitle {
                Text(subtitle)
                    .font(.system(size: 7))
                    .foregroundColor(.secondary.opacity(0.6))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(6)
        .background(Color(white: 0.15))
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

// MARK: - Mini Card (for secondary metrics)

private struct MiniCard: View {
    let label: String
    let value: String
    let unit: String
    let color: Color
    var timestamp: Date? = nil
    var subtitle: String? = nil

    private var timeLabel: String? {
        guard let timestamp else { return nil }
        let mins = Int(-timestamp.timeIntervalSinceNow / 60)
        if mins < 1 { return "now" }
        if mins < 60 { return "\(mins)m ago" }
        let hours = mins / 60
        if hours < 24 { return "\(hours)h ago" }
        return nil
    }

    var body: some View {
        HStack(spacing: 4) {
            VStack(alignment: .leading, spacing: 1) {
                Text(label)
                    .font(.system(size: 8))
                    .foregroundColor(.secondary)
                HStack(spacing: 1) {
                    Text(value)
                        .font(.system(size: 13, weight: .semibold, design: .rounded).monospacedDigit())
                        .foregroundColor(color)
                    Text(unit)
                        .font(.system(size: 7))
                        .foregroundColor(.secondary)
                }
                if let tl = timeLabel {
                    Text(tl)
                        .font(.system(size: 7))
                        .foregroundColor(.secondary.opacity(0.6))
                } else if let subtitle {
                    Text(subtitle)
                        .font(.system(size: 7))
                        .foregroundColor(.secondary.opacity(0.6))
                }
            }
            Spacer()
        }
        .padding(5)
        .background(Color(white: 0.15))
        .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
    }
}

#Preview {
    ContentView()
}
