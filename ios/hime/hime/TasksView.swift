import SwiftUI
import Combine

// MARK: - Models

struct ScheduledTask: Identifiable, Decodable {
    let id: Int
    let cron_expr: String
    let prompt_goal: String
    let status: String
    let last_run_at: String?
    let created_at: String?
}

struct TriggerRule: Identifiable, Decodable {
    let id: Int
    let name: String
    let feature_type: String
    let condition: String
    let threshold: Double
    let window_minutes: Int
    let cooldown_minutes: Int
    let prompt_goal: String
    let status: String
    let trigger_count: Int
    let created_at: String?
}

private struct ScheduledTasksResponse: Decodable {
    let success: Bool
    let tasks: [ScheduledTask]
}

private struct TriggerRulesResponse: Decodable {
    let success: Bool
    let rules: [TriggerRule]
}

// MARK: - Tasks ViewModel

@MainActor
class TasksViewModel: ObservableObject {
    @Published var tasks: [ScheduledTask] = []
    @Published var rules: [TriggerRule] = []
    @Published var isLoadingTasks = false
    @Published var isLoadingRules = false

    private var refreshTimer: Timer?
    private var base: String { ServerConfig.load().apiBaseURL }

    func startPolling() {
        refreshTimer?.invalidate()
        fetchAll()
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor [weak self] in self?.fetchAll() }
        }
    }

    func stopPolling() {
        refreshTimer?.invalidate()
        refreshTimer = nil
    }

    func fetchAll() {
        Task { await fetchTasks() }
        Task { await fetchRules() }
    }

    func fetchTasks() async {
        guard let url = URL(string: "\(base)/api/agent/scheduled-tasks/LiveUser") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(for: APIClient.request(url))
            let decoded = try JSONDecoder().decode(ScheduledTasksResponse.self, from: data)
            tasks = decoded.tasks.filter { $0.status != "deleted" }
        } catch {
            // Network error — leave the list unchanged and let the next
            // poll retry.
        }
    }

    func fetchRules() async {
        guard let url = URL(string: "\(base)/api/agent/trigger-rules/LiveUser") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(for: APIClient.request(url))
            let decoded = try JSONDecoder().decode(TriggerRulesResponse.self, from: data)
            rules = decoded.rules.filter { $0.status != "deleted" }
        } catch {
            // See fetchTasks().
        }
    }

    func toggleTaskStatus(_ task: ScheduledTask) async {
        let newStatus = task.status == "active" ? "paused" : "active"
        guard let url = URL(string: "\(base)/api/agent/scheduled-tasks/LiveUser/\(task.id)") else { return }
        var req = APIClient.request(url, method: "PUT")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["status": newStatus])
        let _ = try? await URLSession.shared.data(for: req)
        await fetchTasks()
    }

    func toggleRuleStatus(_ rule: TriggerRule) async {
        let newStatus = rule.status == "active" ? "paused" : "active"
        guard let url = URL(string: "\(base)/api/agent/trigger-rules/LiveUser/\(rule.id)") else { return }
        var req = APIClient.request(url, method: "PUT")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["status": newStatus])
        let _ = try? await URLSession.shared.data(for: req)
        await fetchRules()
    }

    func deleteTask(_ task: ScheduledTask) async {
        guard let url = URL(string: "\(base)/api/agent/scheduled-tasks/LiveUser/\(task.id)") else { return }
        var req = APIClient.request(url, method: "PUT")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["status": "deleted"])
        let _ = try? await URLSession.shared.data(for: req)
        await fetchTasks()
    }

    func deleteRule(_ rule: TriggerRule) async {
        guard let url = URL(string: "\(base)/api/agent/trigger-rules/LiveUser/\(rule.id)") else { return }
        var req = APIClient.request(url, method: "PUT")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["status": "deleted"])
        let _ = try? await URLSession.shared.data(for: req)
        await fetchRules()
    }

    func createTask(cron: String, goal: String) async -> Bool {
        guard let url = URL(string: "\(base)/api/agent/scheduled-tasks/LiveUser") else { return false }
        var req = APIClient.request(url, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["cron_expr": cron, "prompt_goal": goal])
        do {
            let (data, _) = try await URLSession.shared.data(for: req)
            if let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               j["success"] as? Bool == true {
                await fetchTasks()
                return true
            }
        } catch {}
        return false
    }

    func createRule(name: String, featureType: String, condition: String, threshold: Double,
                    windowMinutes: Int, cooldownMinutes: Int, goal: String) async -> Bool {
        guard let url = URL(string: "\(base)/api/agent/trigger-rules/LiveUser") else { return false }
        var req = APIClient.request(url, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "name": name, "feature_type": featureType, "condition": condition,
            "threshold": threshold, "window_minutes": windowMinutes,
            "cooldown_minutes": cooldownMinutes, "prompt_goal": goal
        ]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        do {
            let (data, _) = try await URLSession.shared.data(for: req)
            if let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               j["success"] as? Bool == true {
                await fetchRules()
                return true
            }
        } catch {}
        return false
    }

    @Published var runningGoals: Set<String> = []

    func triggerAnalysis(goal: String?) async {
        let key = goal ?? ""
        runningGoals.insert(key)
        defer { runningGoals.remove(key) }

        guard let url = URL(string: "\(base)/api/agent/trigger-analysis/LiveUser") else { return }
        var req = APIClient.request(url, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["goal": goal as Any])
        let _ = try? await URLSession.shared.data(for: req)

        // Brief delay so user sees the "triggered" state
        try? await Task.sleep(nanoseconds: 1_500_000_000)
    }
}

// MARK: - Cron Humanizer

private func humanizeCron(_ expr: String) -> String {
    let parts = expr.split(separator: " ").map(String.init)
    guard parts.count == 5 else { return expr }
    let minute = parts[0]
    let hour = parts[1]
    let dayOfWeek = parts[4]

    var time = ""
    if let h = Int(hour), let m = Int(minute) {
        time = String(format: "%02d:%02d", h, m)
    } else {
        time = "\(hour):\(minute)"
    }

    if dayOfWeek == "*" && parts[2] == "*" && parts[3] == "*" {
        return "Daily \(time)"
    }

    let dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    if let d = Int(dayOfWeek), d >= 0, d < 7 {
        return "\(dayNames[d]) \(time)"
    }

    return "\(expr) (\(time))"
}

// MARK: - Condition Display

private let conditionLabels: [String: String] = [
    "gt": ">", "lt": "<", "gte": ">=", "lte": "<=",
    "avg_gt": "avg >", "avg_lt": "avg <",
    "spike": "spike", "drop": "drop",
    "delta_gt": "delta >", "absent": "absent"
]

// MARK: - Tasks Content (reusable, takes external VM)

struct TasksContentView: View {
    @ObservedObject var vm: TasksViewModel
    @State private var showNewTask = false
    @State private var showNewRule = false

    var body: some View {
        VStack(spacing: 20) {
            // ── Scheduled Tasks ──
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Label("Scheduled Tasks", systemImage: "clock.arrow.2.circlepath")
                        .font(.headline)
                    Spacer()
                    Button { showNewTask = true } label: {
                        Image(systemName: "plus.circle.fill")
                            .font(.system(size: 20))
                            .foregroundColor(.accentColor)
                    }
                }
                .padding(.horizontal)

                if vm.tasks.isEmpty {
                    Text("No scheduled tasks")
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 20)
                } else {
                    ForEach(vm.tasks) { task in
                        ScheduledTaskRow(task: task, vm: vm)
                    }
                }
            }

            Divider().padding(.horizontal)

            // ── Trigger Rules ──
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Label("Event Triggers", systemImage: "bolt.circle")
                        .font(.headline)
                    Spacer()
                    Button { showNewRule = true } label: {
                        Image(systemName: "plus.circle.fill")
                            .font(.system(size: 20))
                            .foregroundColor(.accentColor)
                    }
                }
                .padding(.horizontal)

                if vm.rules.isEmpty {
                    Text("No trigger rules")
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 20)
                } else {
                    ForEach(vm.rules) { rule in
                        TriggerRuleRow(rule: rule, vm: vm)
                    }
                }
            }
        }
        .sheet(isPresented: $showNewTask) { NewTaskSheet(vm: vm, isPresented: $showNewTask) }
        .sheet(isPresented: $showNewRule) { NewRuleSheet(vm: vm, isPresented: $showNewRule) }
    }
}

// MARK: - Tasks View (standalone tab — no longer used but kept for reference)

struct TasksView: View {
    @StateObject private var vm = TasksViewModel()

    var body: some View {
        ScrollView {
            TasksContentView(vm: vm)
                .padding(.vertical)
        }
        .onAppear { vm.startPolling() }
        .onDisappear { vm.stopPolling() }
        .refreshable { vm.fetchAll() }
    }
}

// MARK: - Scheduled Task Row

private struct ScheduledTaskRow: View {
    let task: ScheduledTask
    @ObservedObject var vm: TasksViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(humanizeCron(task.cron_expr))
                    .font(.system(size: 13, weight: .bold, design: .monospaced))
                    .foregroundColor(.primary)
                Spacer()
                StatusBadge(status: task.status)
            }

            Text(task.prompt_goal)
                .font(.system(size: 13))
                .foregroundColor(.secondary)
                .lineLimit(2)

            HStack(spacing: 16) {
                Button {
                    Task { await vm.toggleTaskStatus(task) }
                } label: {
                    Label(task.status == "active" ? "Pause" : "Resume",
                          systemImage: task.status == "active" ? "pause.circle" : "play.circle")
                        .font(.system(size: 12))
                }

                Button {
                    Task { await vm.triggerAnalysis(goal: task.prompt_goal) }
                } label: {
                    if vm.runningGoals.contains(task.prompt_goal) {
                        HStack(spacing: 4) {
                            ProgressView()
                                .scaleEffect(0.6)
                            Text("Running")
                                .font(.system(size: 12))
                        }
                    } else {
                        Label("Run Now", systemImage: "play.fill")
                            .font(.system(size: 12))
                    }
                }
                .tint(.orange)
                .disabled(vm.runningGoals.contains(task.prompt_goal))

                Spacer()

                Button(role: .destructive) {
                    Task { await vm.deleteTask(task) }
                } label: {
                    Image(systemName: "trash")
                        .font(.system(size: 12))
                }
            }
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous)
            .fill(Color(.secondarySystemGroupedBackground)))
        .padding(.horizontal)
    }
}

// MARK: - Trigger Rule Row

private struct TriggerRuleRow: View {
    let rule: TriggerRule
    @ObservedObject var vm: TasksViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(rule.name)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.primary)
                Spacer()
                StatusBadge(status: rule.status)
            }

            HStack(spacing: 8) {
                ConditionPill(featureType: rule.feature_type,
                              condition: rule.condition,
                              threshold: rule.threshold)
                Text("\(rule.window_minutes)m window")
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
                if rule.trigger_count > 0 {
                    Text("\(rule.trigger_count)x triggered")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(.orange)
                }
            }

            Text(rule.prompt_goal)
                .font(.system(size: 12))
                .foregroundColor(.secondary)
                .lineLimit(2)

            HStack(spacing: 16) {
                Button {
                    Task { await vm.toggleRuleStatus(rule) }
                } label: {
                    Label(rule.status == "active" ? "Pause" : "Resume",
                          systemImage: rule.status == "active" ? "pause.circle" : "play.circle")
                        .font(.system(size: 12))
                }

                Button {
                    Task { await vm.triggerAnalysis(goal: rule.prompt_goal) }
                } label: {
                    if vm.runningGoals.contains(rule.prompt_goal) {
                        HStack(spacing: 4) {
                            ProgressView()
                                .scaleEffect(0.6)
                            Text("Running")
                                .font(.system(size: 12))
                        }
                    } else {
                        Label("Run Now", systemImage: "play.fill")
                            .font(.system(size: 12))
                    }
                }
                .tint(.orange)
                .disabled(vm.runningGoals.contains(rule.prompt_goal))

                Spacer()

                Button(role: .destructive) {
                    Task { await vm.deleteRule(rule) }
                } label: {
                    Image(systemName: "trash")
                        .font(.system(size: 12))
                }
            }
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous)
            .fill(Color(.secondarySystemGroupedBackground)))
        .padding(.horizontal)
    }
}

// MARK: - Status Badge

private struct StatusBadge: View {
    let status: String

    var body: some View {
        Text(status.capitalized)
            .font(.system(size: 10, weight: .bold))
            .foregroundColor(status == "active" ? .green : .orange)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(Capsule().fill(
                (status == "active" ? Color.green : Color.orange).opacity(0.12)
            ))
    }
}

// MARK: - Condition Pill

private struct ConditionPill: View {
    let featureType: String
    let condition: String
    let threshold: Double

    var body: some View {
        let label = conditionLabels[condition] ?? condition
        let threshStr = threshold.truncatingRemainder(dividingBy: 1) == 0
            ? String(format: "%.0f", threshold)
            : String(format: "%.1f", threshold)

        Text("\(featureType) \(label) \(threshStr)")
            .font(.system(size: 11, weight: .medium, design: .monospaced))
            .foregroundColor(.cyan)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(Capsule().fill(Color.cyan.opacity(0.1)))
    }
}

// MARK: - New Task Sheet

private struct NewTaskSheet: View {
    @ObservedObject var vm: TasksViewModel
    @Binding var isPresented: Bool
    @State private var cronExpr = "0 8 * * *"
    @State private var goal = ""
    @State private var isCreating = false

    var body: some View {
        NavigationView {
            Form {
                Section("Schedule (Cron)") {
                    TextField("e.g. 0 8 * * *", text: $cronExpr)
                        .font(.system(.body, design: .monospaced))
                    Text(humanizeCron(cronExpr))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Section("Analysis Goal") {
                    TextEditor(text: $goal)
                        .frame(minHeight: 80)
                }
            }
            .navigationTitle("New Scheduled Task")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { isPresented = false }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Create") {
                        isCreating = true
                        Task {
                            let ok = await vm.createTask(cron: cronExpr, goal: goal)
                            isCreating = false
                            if ok { isPresented = false }
                        }
                    }
                    .disabled(goal.trimmingCharacters(in: .whitespaces).isEmpty || isCreating)
                }
            }
        }
    }
}

// MARK: - New Rule Sheet

private struct NewRuleSheet: View {
    @ObservedObject var vm: TasksViewModel
    @Binding var isPresented: Bool
    @State private var name = ""
    @State private var featureType = "heart_rate"
    @State private var condition = "gt"
    @State private var threshold = ""
    @State private var windowMinutes = "60"
    @State private var cooldownMinutes = "30"
    @State private var goal = ""
    @State private var isCreating = false

    private let conditions = ["gt", "lt", "gte", "lte", "avg_gt", "avg_lt", "spike", "drop", "delta_gt", "absent"]
    private let featureTypes = ["heart_rate", "heart_rate_variability", "blood_oxygen", "respiratory_rate",
                                "step_count", "active_energy", "exercise_time", "sleep_analysis"]

    var body: some View {
        NavigationView {
            Form {
                Section("Rule Name") {
                    TextField("e.g. High Heart Rate", text: $name)
                }
                Section("Condition") {
                    Picker("Feature", selection: $featureType) {
                        ForEach(featureTypes, id: \.self) { Text($0).tag($0) }
                    }
                    Picker("Condition", selection: $condition) {
                        ForEach(conditions, id: \.self) {
                            Text(conditionLabels[$0] ?? $0).tag($0)
                        }
                    }
                    TextField("Threshold", text: $threshold)
                        .keyboardType(.decimalPad)
                }
                Section("Timing") {
                    HStack {
                        Text("Window")
                        TextField("minutes", text: $windowMinutes)
                            .keyboardType(.numberPad)
                            .multilineTextAlignment(.trailing)
                        Text("min")
                    }
                    HStack {
                        Text("Cooldown")
                        TextField("minutes", text: $cooldownMinutes)
                            .keyboardType(.numberPad)
                            .multilineTextAlignment(.trailing)
                        Text("min")
                    }
                }
                Section("Analysis Goal") {
                    TextEditor(text: $goal)
                        .frame(minHeight: 80)
                }
            }
            .navigationTitle("New Trigger Rule")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { isPresented = false }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Create") {
                        isCreating = true
                        Task {
                            let ok = await vm.createRule(
                                name: name, featureType: featureType, condition: condition,
                                threshold: Double(threshold) ?? 0,
                                windowMinutes: Int(windowMinutes) ?? 60,
                                cooldownMinutes: Int(cooldownMinutes) ?? 30,
                                goal: goal
                            )
                            isCreating = false
                            if ok { isPresented = false }
                        }
                    }
                    .disabled(name.isEmpty || goal.isEmpty || threshold.isEmpty || isCreating)
                }
            }
        }
    }
}
