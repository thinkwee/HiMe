//
//  HimeWidgets.swift
//  HimeWidgets
//
//  Widgets for home / lock screen, rendering live health state from
//  the host app via App Groups.
//

import WidgetKit
import SwiftUI

// MARK: - Snapshot model (Codable, shared shape with the host app)
//
// The host app writes the same JSON shape into the App Group container;
// the widget reads it. Both sides duplicate this struct intentionally —
// they're separate compile units, only the JSON wire format must match.

struct HimeSnapshot: Codable {
    var catStateRaw: String        // CatState rawValue, e.g. "happy"
    var catMessage: String
    var agentRunning: Bool
    var metrics: [HimeMetric]
    var latestReportTitle: String?
    var latestReportPreview: String?
    var latestReportLevel: String?

    // Derived display helpers (not stored)
    var catEmoji: String { Self.emoji(for: catStateRaw) }
    var catStateLabel: String { catStateRaw.prefix(1).uppercased() + catStateRaw.dropFirst() }
    var catStateColor: Color { Self.color(for: catStateRaw) }

    static let placeholder = HimeSnapshot(
        catStateRaw: "happy",
        catMessage: "Your sleep is trending up — keep it consistent!",
        agentRunning: true,
        metrics: [
            HimeMetric(name: "Heart",  value: "72",   unit: "bpm"),
            HimeMetric(name: "Steps",  value: "8421", unit: ""),
            HimeMetric(name: "Sleep",  value: "7.4",  unit: "h"),
            HimeMetric(name: "SpO₂",   value: "98",   unit: "%"),
            HimeMetric(name: "HRV",    value: "56",   unit: "ms"),
            HimeMetric(name: "Energy", value: "412",  unit: "kcal"),
        ],
        latestReportTitle: "Sleep quality trending up",
        latestReportPreview: "Deep sleep increased 18% this week — your consistent bedtime is paying off.",
        latestReportLevel: "info"
    )

    static func emoji(for state: String) -> String {
        switch state {
        case "energetic": return "⚡";  case "tired":      return "😴"
        case "stressed":  return "😾"; case "sad":        return "😿"
        case "relaxed":   return "😌"; case "curious":    return "🧐"
        case "happy":     return "😸"; case "focused":    return "🎯"
        case "sleepy":    return "💤"; case "recovering": return "🌿"
        case "sick":      return "🤒"; case "zen":        return "🧘"
        case "proud":     return "🏆"; case "alert":      return "🔔"
        case "adventurous": return "🌍"
        default: return "🐱"
        }
    }

    static func color(for state: String) -> Color {
        switch state {
        case "energetic":   return Color(red: 0.85, green: 0.50, blue: 0.10)
        case "tired":       return Color(red: 0.35, green: 0.30, blue: 0.60)
        case "stressed":    return Color(red: 0.75, green: 0.22, blue: 0.20)
        case "sad":         return Color(red: 0.25, green: 0.40, blue: 0.72)
        case "relaxed":     return Color(red: 0.22, green: 0.55, blue: 0.30)
        case "curious":     return Color(red: 0.15, green: 0.52, blue: 0.58)
        case "happy":       return Color(red: 0.78, green: 0.62, blue: 0.08)
        case "focused":     return Color(red: 0.50, green: 0.28, blue: 0.70)
        case "sleepy":      return Color(red: 0.42, green: 0.38, blue: 0.55)
        case "recovering":  return Color(red: 0.18, green: 0.52, blue: 0.48)
        case "sick":        return Color(red: 0.70, green: 0.32, blue: 0.32)
        case "zen":         return Color(red: 0.62, green: 0.55, blue: 0.25)
        case "proud":       return Color(red: 0.72, green: 0.55, blue: 0.12)
        case "alert":       return Color(red: 0.78, green: 0.48, blue: 0.10)
        case "adventurous": return Color(red: 0.20, green: 0.52, blue: 0.30)
        default: return Color.gray
        }
    }
}

struct HimeMetric: Codable, Identifiable {
    var id: String { name }
    let name: String
    let value: String
    let unit: String
}

// MARK: - App Group store (read-only on widget side)

enum HimeWidgetStore {
    static let appGroup: String = {
        guard let id = Bundle.main.bundleIdentifier,
              let range = id.range(of: ".hime", options: .backwards) else { return "" }
        return "group.\(id[id.startIndex..<range.upperBound])"
    }()
    static let fileName = "widget_snapshot.json"

    static var fileURL: URL? {
        FileManager.default
            .containerURL(forSecurityApplicationGroupIdentifier: appGroup)?
            .appendingPathComponent(fileName)
    }

    static func read() -> HimeSnapshot {
        guard let url = fileURL,
              let data = try? Data(contentsOf: url),
              let snap = try? JSONDecoder().decode(HimeSnapshot.self, from: data) else {
            return .placeholder
        }
        return snap
    }
}

// MARK: - Timeline provider

struct HimeEntry: TimelineEntry {
    let date: Date
    let snapshot: HimeSnapshot
}

struct HimeProvider: TimelineProvider {
    func placeholder(in context: Context) -> HimeEntry {
        HimeEntry(date: Date(), snapshot: .placeholder)
    }

    func getSnapshot(in context: Context, completion: @escaping (HimeEntry) -> Void) {
        let snap = context.isPreview ? .placeholder : HimeWidgetStore.read()
        completion(HimeEntry(date: Date(), snapshot: snap))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<HimeEntry>) -> Void) {
        let entry = HimeEntry(date: Date(), snapshot: HimeWidgetStore.read())
        // Host app calls reloadAllTimelines() on every write; .after is just a safety net.
        let next = Date().addingTimeInterval(30 * 60)
        completion(Timeline(entries: [entry], policy: .after(next)))
    }
}

// MARK: - Widget 1: Cat status

struct CatStatusWidget: Widget {
    let kind = "CatStatusWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: HimeProvider()) { entry in
            CatStatusView(snapshot: entry.snapshot)
                .containerBackground(for: .widget) {
                    LinearGradient(
                        colors: [entry.snapshot.catStateColor.opacity(0.25), Color.gray.opacity(0.05)],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                }
        }
        .configurationDisplayName("Himeow Mood")
        .description("Your AI cat companion's current state and message.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

struct CatStatusView: View {
    @Environment(\.widgetFamily) private var family
    let snapshot: HimeSnapshot

    var body: some View {
        if family == .systemSmall { small } else { medium }
    }

    private var small: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Text(snapshot.catEmoji).font(.system(size: 28))
                Text(snapshot.catStateLabel)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(snapshot.catStateColor)
                Spacer()
            }
            Text(snapshot.catMessage)
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
                .lineLimit(4)
            Spacer(minLength: 0)
        }
    }

    private var medium: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(spacing: 4) {
                Text(snapshot.catEmoji).font(.system(size: 44))
                Text(snapshot.catStateLabel)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(snapshot.catStateColor)
            }
            .frame(width: 70)

            VStack(alignment: .leading, spacing: 4) {
                Text("Himeow says")
                    .font(.system(size: 10, weight: .medium))
                    .foregroundStyle(.secondary)
                Text(snapshot.catMessage)
                    .font(.system(size: 13))
                    .lineLimit(5)
                Spacer(minLength: 0)
                HStack(spacing: 4) {
                    Circle()
                        .fill(snapshot.agentRunning ? Color.green : Color.gray)
                        .frame(width: 6, height: 6)
                    Text(snapshot.agentRunning ? "Agent active" : "Agent paused")
                        .font(.system(size: 9))
                        .foregroundStyle(.tertiary)
                }
            }
        }
    }
}

// MARK: - Widget 2: Health metrics

struct HealthMetricsWidget: Widget {
    let kind = "HealthMetricsWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: HimeProvider()) { entry in
            HealthMetricsView(snapshot: entry.snapshot)
                .containerBackground(.background, for: .widget)
        }
        .configurationDisplayName("Vitals Grid")
        .description("Latest readings from HiMe — heart, sleep, steps, SpO₂ and more.")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
    }
}

struct HealthMetricsView: View {
    @Environment(\.widgetFamily) private var family
    let snapshot: HimeSnapshot

    private var visible: [HimeMetric] {
        switch family {
        case .systemSmall:  return Array(snapshot.metrics.prefix(4))
        case .systemMedium: return Array(snapshot.metrics.prefix(6))
        case .systemLarge:  return Array(snapshot.metrics.prefix(8))
        default:            return Array(snapshot.metrics.prefix(4))
        }
    }

    private var columns: [GridItem] {
        let count: Int
        switch family {
        case .systemSmall:  count = 2
        case .systemMedium: count = 3
        case .systemLarge:  count = 2
        default:            count = 2
        }
        return Array(repeating: GridItem(.flexible(), spacing: 6), count: count)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Image(systemName: "heart.text.square.fill").foregroundStyle(.pink)
                Text("HiMe Health")
                    .font(.system(size: 12, weight: .semibold))
                Spacer()
                Text(snapshot.catEmoji)
            }

            LazyVGrid(columns: columns, spacing: 8) {
                ForEach(visible) { metric in
                    MetricCell(metric: metric)
                }
            }
            Spacer(minLength: 0)
        }
    }
}

private struct MetricCell: View {
    let metric: HimeMetric

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(metric.name.uppercased())
                .font(.system(size: 9, weight: .semibold))
                .foregroundStyle(.secondary)
                .lineLimit(1)
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(metric.value)
                    .font(.system(size: 18, weight: .bold, design: .rounded))
                if !metric.unit.isEmpty {
                    Text(metric.unit)
                        .font(.system(size: 9, weight: .medium))
                        .foregroundStyle(.secondary)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 4)
        .padding(.horizontal, 6)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(Color.secondary.opacity(0.1))
        )
    }
}

// MARK: - Widget 3: Latest report

struct LatestReportWidget: Widget {
    let kind = "LatestReportWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: HimeProvider()) { entry in
            LatestReportView(snapshot: entry.snapshot)
                .containerBackground(.background, for: .widget)
        }
        .configurationDisplayName("AI Insight")
        .description("The newest report from the HiMe agent.")
        .supportedFamilies([.systemMedium, .systemLarge])
    }
}

struct LatestReportView: View {
    @Environment(\.widgetFamily) private var family
    let snapshot: HimeSnapshot

    var body: some View {
        if let title = snapshot.latestReportTitle {
            VStack(alignment: .leading, spacing: family == .systemLarge ? 8 : 5) {
                HStack(spacing: 6) {
                    Image(systemName: alertIcon)
                        .foregroundStyle(alertColor)
                    Text("AI Insight")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.secondary)
                    Spacer()
                }
                Text(title)
                    .font(.system(size: family == .systemLarge ? 18 : 15, weight: .semibold))
                    .lineLimit(family == .systemLarge ? 3 : 2)
                    .minimumScaleFactor(0.85)
                Text(snapshot.latestReportPreview ?? "")
                    .font(.system(size: family == .systemLarge ? 14 : 12))
                    .foregroundStyle(.secondary)
                    .lineLimit(family == .systemLarge ? 16 : 4)
                    .minimumScaleFactor(0.9)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        } else {
            VStack(spacing: 6) {
                Image(systemName: "doc.text.magnifyingglass")
                    .font(.system(size: 28))
                    .foregroundStyle(.secondary)
                Text("No reports yet")
                    .font(.system(size: 13, weight: .semibold))
                Text("Open HiMe to start the agent.")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private var alertIcon: String {
        switch snapshot.latestReportLevel {
        case "critical", "alert": return "exclamationmark.triangle.fill"
        case "warning":           return "exclamationmark.circle.fill"
        case "info":              return "info.circle.fill"
        default:                  return "sparkles"
        }
    }

    private var alertColor: Color {
        switch snapshot.latestReportLevel {
        case "critical", "alert": return .red
        case "warning":           return .orange
        case "info":              return .blue
        default:                  return .purple
        }
    }
}

// MARK: - Previews

#Preview(as: .systemSmall) {
    CatStatusWidget()
} timeline: {
    HimeEntry(date: .now, snapshot: .placeholder)
}

#Preview(as: .systemMedium) {
    HealthMetricsWidget()
} timeline: {
    HimeEntry(date: .now, snapshot: .placeholder)
}

#Preview(as: .systemMedium) {
    LatestReportWidget()
} timeline: {
    HimeEntry(date: .now, snapshot: .placeholder)
}
