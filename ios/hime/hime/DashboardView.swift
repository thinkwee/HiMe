import SwiftUI
import Charts

// MARK: - Dashboard View

struct DashboardView: View {
    @StateObject private var viewModel = DashboardViewModel()
    @StateObject private var tasksVM = TasksViewModel()
    @State private var hasStarted = false
    @State private var selectedSection = 0  // 0=Overview, 1=Reports, 2=Tasks

    var body: some View {
        VStack(spacing: 0) {
            // Section picker
            Picker("Section", selection: $selectedSection) {
                Text("Overview").tag(0)
                Text("Reports").tag(1)
                Text("Tasks").tag(2)
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 16)
            .padding(.vertical, 8)

            ScrollView {
                VStack(spacing: 16) {
                    switch selectedSection {
                    case 0:
                        AgentStatusCard(viewModel: viewModel)
                        HealthChartSection(viewModel: viewModel)
                    case 1:
                        ReportsListSection(viewModel: viewModel)
                    case 2:
                        TasksContentView(vm: tasksVM)
                    default:
                        EmptyView()
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
        }
        .background(Color(.systemGroupedBackground))
        .onAppear {
            if !hasStarted {
                hasStarted = true
            }
            viewModel.startRefreshing()
            tasksVM.startPolling()
        }
        .onDisappear {
            viewModel.stopRefreshing()
            tasksVM.stopPolling()
        }
        .refreshable {
            HealthKitManager.shared.forceFetch()
            viewModel.fetchAll()
            tasksVM.fetchAll()
        }
    }
}

// MARK: - Agent Status Card

private struct AgentStatusCard: View {
    @ObservedObject var viewModel: DashboardViewModel

    private var stateIcon: String {
        let s = viewModel.analysisState.lowercased()
        if s.contains("think") { return "brain" }
        if s.contains("execut") { return "gearshape.fill" }
        if s.contains("sleep") { return "moon.zzz.fill" }
        if s.contains("quick") { return "bolt.fill" }
        if s == "idle" { return "pause.circle" }
        return "circle.fill"
    }

    private var stateColor: Color {
        let s = viewModel.analysisState.lowercased()
        if s.contains("think") { return .purple }
        if s.contains("execut") { return .orange }
        if s.contains("sleep") { return .indigo }
        if s.contains("quick") { return .pink }
        return .secondary
    }

    private func formatTokens(_ n: Int) -> String {
        if n >= 1_000_000 { return String(format: "%.1fM", Double(n) / 1_000_000) }
        if n >= 1_000 { return String(format: "%.1fK", Double(n) / 1_000) }
        return "\(n)"
    }

    var body: some View {
        VStack(spacing: 12) {
            HStack {
                Image(systemName: viewModel.isAgentRunning ? "brain.fill" : "brain")
                    .font(.title2)
                    .foregroundColor(viewModel.isAgentRunning ? .green : .secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Agent")
                        .font(.headline)
                    Text(viewModel.agentModel ?? String(localized: "Not configured"))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer()
                // State badge
                HStack(spacing: 4) {
                    Image(systemName: stateIcon)
                        .font(.system(size: 10))
                    Text(viewModel.analysisState.replacingOccurrences(of: "_", with: " ").capitalized)
                        .font(.caption2.weight(.medium))
                }
                .foregroundColor(stateColor)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(stateColor.opacity(0.1))
                .clipShape(Capsule())
            }

            // Stats row
            HStack(spacing: 0) {
                StatCell(label: "Cycles", value: "\(viewModel.analysisCycles)")
                Divider().frame(height: 28)
                StatCell(label: "Records", value: viewModel.totalRecords > 0 ? "\(viewModel.totalRecords / 1000)K" : "0")
                Divider().frame(height: 28)
                StatCell(label: "Last Analysis",
                         value: viewModel.lastAnalysisTime != nil ? formatRelativeTime(viewModel.lastAnalysisTime!) : "Never")
            }
            .padding(.vertical, 4)

            // Token usage row
            if viewModel.promptTokens + viewModel.completionTokens > 0 {
                HStack(spacing: 0) {
                    TokenCell(label: "Input", value: formatTokens(viewModel.promptTokens), color: .blue)
                    TokenCell(label: "Thinking", value: formatTokens(viewModel.thoughtsTokens), color: .purple)
                    TokenCell(label: "Output", value: formatTokens(viewModel.completionTokens), color: .green)
                }
                .padding(.vertical, 4)
                .background(Color(.tertiarySystemGroupedBackground))
                .clipShape(RoundedRectangle(cornerRadius: 8))
            }
        }
        .padding(12)
        .background(Color(.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    private func formatRelativeTime(_ iso: String) -> String {
        // Backend sends UTC timestamps without a timezone suffix — append "Z"
        let hasTZ = iso.hasSuffix("Z") || iso.contains("+") || (iso.count > 19 && iso.dropFirst(19).contains("-"))
        let utcIso = hasTZ ? iso : iso + "Z"
        let f1 = ISO8601DateFormatter()
        f1.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let f2 = ISO8601DateFormatter()
        f2.formatOptions = [.withInternetDateTime]
        guard let date = f1.date(from: utcIso) ?? f2.date(from: utcIso) else {
            return "—"
        }

        let now = Date()
        let seconds = Int(now.timeIntervalSince(date))

        if seconds < 60        { return "just now" }
        if seconds < 3600      { return "\(seconds / 60)m ago" }
        if seconds < 86400     { return "\(seconds / 3600)h ago" }
        if seconds < 86400 * 7 { return "\(seconds / 86400)d ago" }

        let df = DateFormatter()
        df.dateFormat = "M/d HH:mm"
        return df.string(from: date)
    }
}

private struct StatCell: View {
    let label: LocalizedStringKey
    let value: String
    var body: some View {
        VStack(spacing: 2) {
            Text(value)
                .font(.system(size: 15, weight: .semibold, design: .rounded).monospacedDigit())
                .foregroundColor(.primary)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            Text(label)
                .font(.caption2)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity)
    }
}

private struct TokenCell: View {
    let label: LocalizedStringKey
    let value: String
    let color: Color
    var body: some View {
        VStack(spacing: 2) {
            Text(value)
                .font(.system(size: 13, weight: .semibold, design: .rounded).monospacedDigit())
                .foregroundColor(color)
            Text(label)
                .font(.system(size: 9))
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity)
    }
}

// MARK: - Health Chart Section

private struct HealthChartSection: View {
    @ObservedObject var viewModel: DashboardViewModel
    @State private var expandedMetricId: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Health Summary")
                .font(.headline)
                .foregroundColor(.primary)

            let hasAnyData = viewModel.metricCategories.contains { !$0.series.isEmpty }

            if !hasAnyData && !viewModel.isLoadingMetrics {
                EmptyMetricsView()
            } else {
                ForEach(viewModel.metricCategories) { categoryData in
                    MetricCategorySection(
                        categoryData: categoryData,
                        expandedMetricId: $expandedMetricId
                    )
                }
            }
        }
    }
}

private struct EmptyMetricsView: View {
    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: "heart.text.square")
                .font(.system(size: 32))
                .foregroundColor(.secondary.opacity(0.4))
            Text("Waiting for data...")
                .font(.subheadline)
                .foregroundColor(.secondary)
            Text("Wear your Apple Watch and sync data to see metrics here.")
                .font(.caption)
                .foregroundColor(.secondary.opacity(0.7))
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 32)
    }
}

// MARK: - Metric Category Section

private struct MetricCategorySection: View {
    let categoryData: MetricCategoryData
    @Binding var expandedMetricId: String?

    private var categoryColor: Color {
        switch categoryData.category.color {
        case "amber":  return .orange
        case "orange": return .orange
        case "red":    return .red
        case "indigo": return .indigo
        case "teal":   return .teal
        case "yellow": return .yellow
        case "purple": return .purple
        case "green":  return .green
        case "cyan":   return .cyan
        default:       return .gray
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            // Category header
            HStack(spacing: 6) {
                Image(systemName: categoryData.category.icon)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(categoryColor)

                Text(categoryData.category.rawValue)
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(.primary)

                Spacer()

                if categoryData.series.isEmpty {
                    Text("No data")
                        .font(.caption2)
                        .foregroundColor(.secondary.opacity(0.6))
                }
            }

            if categoryData.series.isEmpty {
                HStack {
                    Spacer()
                    VStack(spacing: 4) {
                        Image(systemName: "chart.line.downtrend.xyaxis")
                            .font(.system(size: 20))
                            .foregroundColor(.secondary.opacity(0.3))
                        Text("No data yet")
                            .font(.caption2)
                            .foregroundColor(.secondary.opacity(0.5))
                    }
                    .padding(.vertical, 12)
                    Spacer()
                }
            } else {
                // Small cards grid - 3 per row
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 3), spacing: 8) {
                    ForEach(categoryData.series) { series in
                        SmallMetricCard(
                            series: series,
                            category: categoryData.category,
                            accentColor: categoryColor,
                            isExpanded: expandedMetricId == series.id
                        )
                        .onTapGesture {
                            withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) {
                                if expandedMetricId == series.id {
                                    expandedMetricId = nil
                                } else {
                                    expandedMetricId = series.id
                                }
                            }
                        }
                    }
                }

                // Expanded chart card below the grid
                if let expandedSeries = categoryData.series.first(where: { $0.id == expandedMetricId }) {
                    MetricChartCard(
                        series: expandedSeries,
                        category: categoryData.category,
                        accentColor: categoryColor
                    )
                    .transition(.opacity.combined(with: .move(edge: .top)))
                }
            }
        }
        .padding(12)
        .background(Color(.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

// MARK: - Small Metric Card (compact, 3 per row)

private struct SmallMetricCard: View {
    let series: MetricSeries
    let category: MetricCategory
    let accentColor: Color
    let isExpanded: Bool

    private var trendColor: Color {
        switch series.trend.color {
        case "green": return .green
        case "red":   return .red
        case "blue":  return .blue
        default:      return .secondary
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(series.displayName)
                .font(.system(size: 10, weight: .medium))
                .foregroundColor(.secondary)
                .lineLimit(1)
                .minimumScaleFactor(0.7)

            if let latestRaw = series.latestValue {
                HStack(spacing: 2) {
                    Text(category.formatValue(latestRaw, key: series.feature))
                        .font(.system(size: 16, weight: .bold, design: .rounded).monospacedDigit())
                        .foregroundColor(.primary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.6)

                    if !series.unit.isEmpty {
                        Text(series.unit)
                            .font(.system(size: 8))
                            .foregroundColor(.secondary)
                    }
                }

                Image(systemName: series.trend.icon)
                    .font(.system(size: 8, weight: .bold))
                    .foregroundColor(trendColor)
            } else {
                Text("--")
                    .font(.system(size: 16, weight: .bold, design: .rounded))
                    .foregroundColor(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(8)
        .background(isExpanded ? accentColor.opacity(0.1) : Color(.tertiarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(isExpanded ? accentColor.opacity(0.4) : Color.clear, lineWidth: 1.5)
        )
    }
}

// MARK: - Metric Chart Card (expanded detail view)

private struct MetricChartCard: View {
    let series: MetricSeries
    let category: MetricCategory
    let accentColor: Color

    private var trendColor: Color {
        switch series.trend.color {
        case "green": return .green
        case "red":   return .red
        case "blue":  return .blue
        default:      return .secondary
        }
    }

    private var dataSpanHours: Double {
        guard let first = series.dataPoints.first?.date,
              let last = series.dataPoints.last?.date else { return 0 }
        return last.timeIntervalSince(first) / 3600.0
    }

    private var xTickCount: Int {
        if dataSpanHours > 168 { return 4 }     // >7d
        if dataSpanHours > 48 { return 5 }      // 2-7d
        if dataSpanHours > 24 { return 3 }      // 1-2d
        return 4
    }

    private var xAxisFormat: Date.FormatStyle {
        if series.chartStyle == .bar && dataSpanHours > 48 {
            // Bar charts with daily data: just show day
            return .dateTime.month(.abbreviated).day(.defaultDigits)
        }
        if dataSpanHours > 168 {
            return .dateTime.month(.abbreviated).day(.defaultDigits)
        } else if dataSpanHours > 48 {
            return .dateTime.weekday(.abbreviated).day(.defaultDigits)
        } else if dataSpanHours > 24 {
            return .dateTime.weekday(.abbreviated).hour(.twoDigits(amPM: .omitted)).minute(.twoDigits)
        } else {
            return .dateTime.hour(.twoDigits(amPM: .omitted)).minute(.twoDigits)
        }
    }

    /// Compute adaptive Y-axis domain from data + config.
    private var yDomain: ClosedRange<Double> {
        let values = series.dataPoints.map(\.value)
        guard let dMin = values.min(), let dMax = values.max() else { return 0...1 }
        let (lo, hi) = series.yAxisConfig.resolve(dataMin: dMin, dataMax: dMax)
        return lo...hi
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            // Header
            HStack(spacing: 6) {
                Text(series.displayName)
                    .font(.caption.weight(.medium))
                    .foregroundColor(.primary)

                Spacer()

                if let latestRaw = series.latestValue {
                    Text(category.formatValue(latestRaw, key: series.feature))
                        .font(.system(size: 15, weight: .semibold, design: .rounded).monospacedDigit())
                        .foregroundColor(.primary)

                    if !series.unit.isEmpty {
                        Text(series.unit)
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                    }

                    Image(systemName: series.trend.icon)
                        .font(.system(size: 10, weight: .bold))
                        .foregroundColor(trendColor)
                }
            }

            // Sleep: stacked bar timeline
            if series.chartStyle == .sleepBar && !series.sleepBlocks.isEmpty {
                SleepStageTimeline(blocks: series.sleepBlocks)
            }
            // Charts: need at least 2 points (or 1 for bar)
            else if series.dataPoints.count >= 2 || (series.chartStyle == .bar && !series.dataPoints.isEmpty) {
                chartContent
                    .frame(height: 140)
            } else if series.dataPoints.count == 1 {
                HStack {
                    Spacer()
                    Text("1 data point recorded")
                        .font(.caption2)
                        .foregroundColor(.secondary.opacity(0.6))
                    Spacer()
                }
                .frame(height: 30)
            }
        }
        .padding(10)
        .background(Color(.tertiarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    }

    @ViewBuilder
    private var chartContent: some View {
        let yLabel = series.unit.isEmpty ? "Value" : series.unit

        Chart(series.dataPoints) { point in
            if series.chartStyle == .bar {
                BarMark(
                    x: .value("Time", point.date, unit: .day),
                    y: .value(yLabel, point.value)
                )
                .foregroundStyle(accentColor.gradient)
                .cornerRadius(3)
            } else if series.chartStyle == .point {
                PointMark(
                    x: .value("Time", point.date),
                    y: .value(yLabel, point.value)
                )
                .foregroundStyle(accentColor)
                .symbolSize(20)
            } else {
                LineMark(
                    x: .value("Time", point.date),
                    y: .value(yLabel, point.value)
                )
                .foregroundStyle(accentColor.gradient)
                .lineStyle(StrokeStyle(lineWidth: 1.5))

                AreaMark(
                    x: .value("Time", point.date),
                    y: .value(yLabel, point.value)
                )
                .foregroundStyle(
                    LinearGradient(
                        colors: [accentColor.opacity(0.2), accentColor.opacity(0.02)],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
            }
        }
        .chartXAxis {
            AxisMarks(values: .automatic(desiredCount: xTickCount)) { _ in
                AxisGridLine(stroke: StrokeStyle(lineWidth: 0.3))
                    .foregroundStyle(Color.secondary.opacity(0.3))
                AxisValueLabel(format: xAxisFormat)
                    .font(.system(size: 7))
                    .foregroundStyle(Color.secondary)
            }
        }
        .chartYAxis {
            AxisMarks(values: .automatic(desiredCount: 4)) { _ in
                AxisGridLine(stroke: StrokeStyle(lineWidth: 0.3))
                    .foregroundStyle(Color.secondary.opacity(0.3))
                AxisValueLabel()
                    .font(.system(size: 8))
                    .foregroundStyle(Color.secondary)
            }
        }
        .chartYScale(domain: yDomain)
    }
}

// MARK: - Sleep Stage Timeline (horizontal stacked bar per night)

private struct SleepStageTimeline: View {
    let blocks: [SleepBlock]

    private static func stageColor(_ stage: SleepStage) -> Color {
        switch stage {
        case .deep:  return Color(red: 0.25, green: 0.15, blue: 0.65)  // deep purple
        case .core:  return Color(red: 0.40, green: 0.55, blue: 0.95)  // soft blue
        case .rem:   return Color(red: 0.30, green: 0.80, blue: 0.85)  // teal
        case .awake: return Color(red: 0.95, green: 0.60, blue: 0.25)  // warm orange
        case .inBed: return Color(white: 0.30)                         // dim gray
        }
    }

    /// Group blocks into calendar days.
    /// A sleep session ending before 18:00 is attributed to that calendar day
    /// (so overnight sleep 23:00→07:00 belongs to the day it ends).
    /// This naturally handles naps: a 14:00 nap on Apr 3 stays on Apr 3.
    private var nights: [(date: Date, blocks: [SleepBlock])] {
        let calendar = Calendar.current
        var grouped: [Date: [SleepBlock]] = [:]

        for block in blocks where block.stage != .inBed {
            // Attribute to the day the sleep block ends
            let endDate = block.start.addingTimeInterval(block.durationMinutes * 60)
            let day = calendar.startOfDay(for: endDate)
            grouped[day, default: []].append(block)
        }

        return grouped.sorted { $0.key > $1.key }
            .prefix(3)
            .map { (date: $0.key, blocks: $0.value.sorted { $0.start < $1.start }) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            ForEach(Array(nights.enumerated()), id: \.offset) { _, night in
                NightBarView(night: night, stageColor: Self.stageColor)
            }

            // Legend
            HStack(spacing: 10) {
                ForEach([SleepStage.deep, .core, .rem, .awake], id: \.rawValue) { stage in
                    HStack(spacing: 4) {
                        RoundedRectangle(cornerRadius: 2.5)
                            .fill(Self.stageColor(stage))
                            .frame(width: 12, height: 12)
                        Text(stage.rawValue)
                            .font(.system(size: 10))
                            .foregroundColor(.secondary)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .center)
            .padding(.top, 2)
        }
    }
}

// MARK: - Single Night Bar

private struct NightBarView: View {
    let night: (date: Date, blocks: [SleepBlock])
    let stageColor: (SleepStage) -> Color

    private var nightLabel: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "E, MMM d"
        return formatter.string(from: night.date)
    }

    private var sleepBlocks: [SleepBlock] {
        night.blocks.filter { $0.stage != .inBed }
    }

    /// Split blocks into sessions separated by >2h gaps.
    private var sessions: [[SleepBlock]] {
        let sorted = sleepBlocks.sorted { $0.start < $1.start }
        guard !sorted.isEmpty else { return [] }
        var result: [[SleepBlock]] = [[sorted[0]]]
        for i in 1..<sorted.count {
            let prevEnd = result[result.count - 1].last!.start.addingTimeInterval(
                result[result.count - 1].last!.durationMinutes * 60
            )
            if sorted[i].start.timeIntervalSince(prevEnd) > 7200 {
                result.append([sorted[i]])
            } else {
                result[result.count - 1].append(sorted[i])
            }
        }
        return result
    }

    private var totalSleepMinutes: Int {
        Int(sleepBlocks.filter { $0.stage != .awake }.reduce(0) { $0 + $1.durationMinutes })
    }

    /// Time range label per session (e.g. "23:00–07:00" or "23:00–07:00 + 14:00–15:30")
    private var timeRangeLabel: String {
        let fmt = DateFormatter()
        fmt.dateFormat = "HH:mm"
        let parts = sessions.compactMap { session -> String? in
            guard let first = session.first, let last = session.last else { return nil }
            let end = last.start.addingTimeInterval(last.durationMinutes * 60)
            return "\(fmt.string(from: first.start))–\(fmt.string(from: end))"
        }
        return parts.joined(separator: "  +  ")
    }

    /// Total filled duration across all blocks (for proportion-based bar).
    private var totalFilledMinutes: Double {
        sleepBlocks.reduce(0) { $0 + $1.durationMinutes }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline) {
                Text(nightLabel)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(.primary)
                Spacer()
                let h = totalSleepMinutes / 60
                let m = totalSleepMinutes % 60
                Text("\(h)h \(m)m")
                    .font(.system(size: 12, weight: .bold, design: .rounded))
                    .foregroundColor(.primary)
            }

            Text(timeRangeLabel)
                .font(.system(size: 9))
                .foregroundColor(.secondary)

            // Proportion-based bar: each block's width = duration / total_duration.
            // Sessions are separated by a thin gap.
            GeometryReader { geo in
                let sess = sessions
                let sessionGapWidth: CGFloat = sess.count > 1 ? 3 : 0
                let totalGaps = CGFloat(max(sess.count - 1, 0)) * sessionGapWidth
                let barWidth = geo.size.width - totalGaps
                let total = max(totalFilledMinutes, 1)

                HStack(spacing: sessionGapWidth) {
                    ForEach(Array(sess.enumerated()), id: \.offset) { _, session in
                        let sessionDur = session.reduce(0.0) { $0 + $1.durationMinutes }
                        let sessionWidth = (sessionDur / total) * barWidth
                        HStack(spacing: 0.5) {
                            ForEach(session) { block in
                                let w = max((block.durationMinutes / sessionDur) * sessionWidth, 1.5)
                                RoundedRectangle(cornerRadius: 2)
                                    .fill(stageColor(block.stage))
                                    .frame(width: w)
                            }
                        }
                        .clipShape(RoundedRectangle(cornerRadius: 3))
                    }
                }
            }
            .frame(height: 22)

            // Stage breakdown
            HStack(spacing: 8) {
                ForEach(stageSummary(), id: \.0) { stage, mins in
                    HStack(spacing: 3) {
                        Circle()
                            .fill(stageColor(stage))
                            .frame(width: 6, height: 6)
                        Text("\(stage.rawValue) \(mins)m")
                            .font(.system(size: 9))
                            .foregroundColor(.secondary)
                    }
                }
            }
        }
    }

    private func stageSummary() -> [(SleepStage, Int)] {
        var totals: [SleepStage: Double] = [:]
        for b in sleepBlocks where b.stage != .inBed {
            totals[b.stage, default: 0] += b.durationMinutes
        }
        return [SleepStage.deep, .core, .rem, .awake].compactMap { stage in
            guard let mins = totals[stage], mins > 0 else { return nil }
            return (stage, Int(mins))
        }
    }
}
