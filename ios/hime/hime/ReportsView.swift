import SwiftUI

// MARK: - Reports View

struct ReportsView: View {
    @StateObject private var viewModel = DashboardViewModel()
    @State private var hasStarted = false

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                ReportsListSection(viewModel: viewModel)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
        .background(Color(.systemGroupedBackground))
        .onAppear {
            if !hasStarted {
                hasStarted = true
            }
            viewModel.startRefreshing()
        }
        .onDisappear {
            viewModel.stopRefreshing()
        }
        .refreshable {
            viewModel.fetchAll()
        }
    }
}

// MARK: - Reports List Section

struct ReportsListSection: View {
    @ObservedObject var viewModel: DashboardViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Agent Reports")
                    .font(.headline)
                    .foregroundColor(.primary)
                Spacer()
                if !viewModel.reports.isEmpty {
                    Text("\(viewModel.reports.count)")
                        .font(.caption2.weight(.bold))
                        .foregroundColor(.secondary)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 2)
                        .background(Color(.tertiarySystemGroupedBackground))
                        .clipShape(Capsule())
                }
            }

            if viewModel.isAgentRunning {
                HStack(spacing: 6) {
                    Circle()
                        .fill(Color.green)
                        .frame(width: 6, height: 6)
                    Text("Agent running")
                        .font(.caption2)
                        .foregroundColor(.green)
                    if let model = viewModel.agentModel {
                        Text("(\(model))")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }
                }
            }

            if viewModel.reports.isEmpty && !viewModel.isLoadingReports {
                ReportsEmptyView()
            } else {
                ForEach(viewModel.reports) { report in
                    ReportRow(report: report, viewModel: viewModel)
                }
            }
        }
    }
}

// MARK: - Empty Reports View

struct ReportsEmptyView: View {
    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: "doc.text.magnifyingglass")
                .font(.system(size: 32))
                .foregroundColor(.secondary.opacity(0.4))
            Text("No reports yet")
                .font(.subheadline)
                .foregroundColor(.secondary)
            Text("Start the agent to generate health insights and reports.")
                .font(.caption)
                .foregroundColor(.secondary.opacity(0.7))
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 32)
    }
}

// MARK: - Report Row

struct ReportRow: View {
    let report: AgentReport
    @ObservedObject var viewModel: DashboardViewModel
    @State private var isExpanded: Bool = false
    @State private var showDeleteConfirm: Bool = false

    private var alertColor: Color {
        switch viewModel.alertColor(for: report.alert_level) {
        case "red":    return .red
        case "orange": return .orange
        case "blue":   return .blue
        default:       return .green
        }
    }

    private var alertIcon: String {
        switch report.alert_level?.lowercased() {
        case "critical", "high": return "exclamationmark.triangle.fill"
        case "warning", "medium": return "exclamationmark.circle.fill"
        case "info", "low":       return "info.circle.fill"
        default:                  return "checkmark.circle.fill"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                withAnimation(.easeInOut(duration: 0.2)) {
                    isExpanded.toggle()
                }
            } label: {
                HStack(spacing: 10) {
                    // Alert indicator
                    RoundedRectangle(cornerRadius: 2, style: .continuous)
                        .fill(alertColor)
                        .frame(width: 4, height: 36)

                    Image(systemName: alertIcon)
                        .font(.system(size: 14))
                        .foregroundColor(alertColor)

                    VStack(alignment: .leading, spacing: 2) {
                        Text(report.title ?? "Health Report")
                            .font(.subheadline.weight(.medium))
                            .foregroundColor(.primary)
                            .lineLimit(isExpanded ? nil : 1)

                        if let timestamp = report.created_at {
                            Text(formatTimestamp(timestamp))
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }
                    }

                    Spacer()

                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(.secondary)
                }
            }
            .buttonStyle(.plain)

            if isExpanded {
                Divider()

                Text(cleanMarkdown(report.content))
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)

                HStack {
                    Button {
                        showDeleteConfirm = true
                    } label: {
                        Label("Delete", systemImage: "trash")
                            .font(.caption.weight(.medium))
                            .foregroundColor(.red)
                    }
                    .buttonStyle(.plain)

                    Spacer()

                    if let level = report.alert_level {
                        Text(level.uppercased())
                            .font(.system(size: 9, weight: .bold))
                            .foregroundColor(alertColor)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 3)
                            .background(alertColor.opacity(0.12))
                            .clipShape(Capsule())
                    }
                }
            }
        }
        .padding(12)
        .background(Color(.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .contextMenu {
            Button(role: .destructive) {
                showDeleteConfirm = true
            } label: {
                Label("Delete Report", systemImage: "trash")
            }
        }
        .alert("Delete this report?", isPresented: $showDeleteConfirm) {
            Button("Cancel", role: .cancel) {}
            Button("Delete", role: .destructive) {
                Task { await viewModel.deleteReport(id: report.id) }
            }
        } message: {
            Text("This will remove the report from your server. This cannot be undone.")
        }
    }

    private func formatTimestamp(_ ts: String) -> String {
        // Backend sends UTC timestamps without a timezone suffix
        // (e.g. "2026-04-10T10:22:54"). Append "Z" so ISO8601
        // parsers treat them as UTC rather than local time.
        let hasTZ = ts.hasSuffix("Z") || ts.contains("+") || (ts.count > 19 && ts.dropFirst(19).contains("-"))
        let utcTs = hasTZ ? ts : ts + "Z"

        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: utcTs) {
            let relative = RelativeDateTimeFormatter()
            relative.unitsStyle = .abbreviated
            return relative.localizedString(for: date, relativeTo: Date())
        }
        // Try without fractional seconds
        formatter.formatOptions = [.withInternetDateTime]
        if let date = formatter.date(from: utcTs) {
            let relative = RelativeDateTimeFormatter()
            relative.unitsStyle = .abbreviated
            return relative.localizedString(for: date, relativeTo: Date())
        }
        // Fallback: try common SQLite format "YYYY-MM-DD HH:MM:SS"
        let sqlFormatter = DateFormatter()
        sqlFormatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        sqlFormatter.timeZone = TimeZone(identifier: "UTC")
        if let date = sqlFormatter.date(from: ts) {
            let relative = RelativeDateTimeFormatter()
            relative.unitsStyle = .abbreviated
            return relative.localizedString(for: date, relativeTo: Date())
        }
        return String(ts.prefix(16))
    }

    /// Simple markdown cleanup: convert **bold** to plain text, keep bullet points
    private func cleanMarkdown(_ text: String) -> String {
        var result = text
        // Remove ** bold markers
        result = result.replacingOccurrences(of: "**", with: "")
        // Remove # headers markers but keep text
        let lines = result.split(separator: "\n", omittingEmptySubsequences: false)
        result = lines.map { line in
            var l = String(line)
            while l.hasPrefix("#") {
                l = String(l.dropFirst())
            }
            if l.hasPrefix(" ") { l = String(l.dropFirst()) }
            return l
        }.joined(separator: "\n")
        // Convert - bullets to bullet character
        result = result.replacingOccurrences(of: "\n- ", with: "\n\u{2022} ")
        if result.hasPrefix("- ") {
            result = "\u{2022} " + String(result.dropFirst(2))
        }
        return result.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
