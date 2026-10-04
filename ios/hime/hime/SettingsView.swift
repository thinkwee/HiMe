import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var hk: HealthKitManager
    @EnvironmentObject var ws: WebSocketClient
    @StateObject private var lm = LogManager.shared

    @State private var showClearLogsConfirm = false
    @State private var showCopiedToast = false
    @State private var serverAddress: String = ServerConfig.load().baseAddress
    @State private var authToken: String = ServerConfig.authToken
    @State private var saveTask: Task<Void, Never>? = nil
    @State private var tokenSaveTask: Task<Void, Never>? = nil

    @AppStorage("hime.hasOnboarded") private var hasOnboarded: Bool = false
    @AppStorage("hime.hasConsentedToAIDataSharing") private var hasConsentedToAI: Bool = false
    @State private var connectionTestResult: String? = nil
    @State private var connectionTestSuccess: Bool = false
    @State private var isTestingConnection: Bool = false
    @State private var showAIDisclosureSheet = false
    @State private var showRevokeConsentConfirm = false
    @State private var showPlanSurvey = false

    var body: some View {
        Form {
            // MARK: - Logo Header
            Section {
                VStack(spacing: 12) {
                    Image("AppLogo")
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .frame(height: 80)
                        .padding(.vertical, 10)

                    VStack(spacing: 4) {
                        Text("Hime")
                            .font(.system(size: 24, weight: .bold, design: .rounded))
                            .tracking(4)

                        Text("Health Intelligence Management Engine")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                            .tracking(1)
                    }
                }
                .frame(maxWidth: .infinity)
                .listRowBackground(Color.clear)
            }

            // MARK: - Self-hosted Server Guide
            Section {
                Link(destination: URL(string: "https://github.com/thinkwee/HiMe")!) {
                    HStack {
                        GitHubIcon()
                            .frame(width: 20, height: 20)
                        Text("Deploy Your Own Server")
                        Spacer()
                        Text("GitHub")
                            .foregroundColor(.secondary)
                            .font(.subheadline)
                        Image(systemName: "arrow.up.right.square")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }
            } footer: {
                Text("Hime requires a self-hosted server. Follow the setup guide on GitHub to deploy your backend and connect this app.")
            }

            // MARK: - Server
            Section {
                HStack {
                    Label("Server Address", systemImage: "network")
                    Spacer()
                    TextField("192.168.1.100 or example.com", text: $serverAddress)
                        .multilineTextAlignment(.trailing)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                        .foregroundColor(.secondary)
                        .onSubmit {
                            saveTask?.cancel()
                            serverAddress = applyServerAddress()
                        }
                        .onChange(of: serverAddress) {
                            saveTask?.cancel()
                            saveTask = Task { @MainActor in
                                try? await Task.sleep(for: .milliseconds(500))
                                guard !Task.isCancelled else { return }
                                applyServerAddress()
                            }
                        }
                }

                HStack {
                    Label("Auth Token", systemImage: "lock")
                    Spacer()
                    SecureField("Optional", text: $authToken)
                        .multilineTextAlignment(.trailing)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                        .foregroundColor(.secondary)
                        .onSubmit {
                            tokenSaveTask?.cancel()
                            Task { await applyAuthToken(authToken) }
                        }
                        .onChange(of: authToken) { _, newValue in
                            // Debounced: this fires on every keystroke, and
                            // saving / reconnecting / flushing the survey per
                            // character is what made the single-flight guard
                            // race in the first place.
                            tokenSaveTask?.cancel()
                            tokenSaveTask = Task {
                                try? await Task.sleep(nanoseconds: 500_000_000)
                                guard !Task.isCancelled else { return }
                                await applyAuthToken(newValue)
                            }
                        }
                }

                HStack {
                    Label {
                        Text("Status")
                    } icon: {
                        Image(systemName: ws.isConnected ? "checkmark.circle.fill" : "xmark.circle.fill")
                            .foregroundColor(ws.isConnected ? .green : .red)
                    }
                    Spacer()
                    Text(ws.isConnected ? LocalizedStringKey("Connected") : LocalizedStringKey("Disconnected"))
                        .foregroundColor(.secondary)
                }

                Button {
                    ws.isSyncActive ? ws.disconnect() : ws.connect()
                } label: {
                    Label(ws.isSyncActive ? LocalizedStringKey("Disconnect") : LocalizedStringKey("Connect"), systemImage: ws.isSyncActive ? "wifi.slash" : "wifi")
                }
                .foregroundColor(ws.isSyncActive ? .red : .accentColor)

                Button {
                    Task { await testServerConnection() }
                } label: {
                    HStack {
                        Label("Test Server Connection", systemImage: "bolt.horizontal.circle")
                        Spacer()
                        if isTestingConnection {
                            ProgressView().scaleEffect(0.8)
                        }
                    }
                }
                .disabled(isTestingConnection)

                if let result = connectionTestResult {
                    Text(result)
                        .font(.footnote)
                        .foregroundColor(connectionTestSuccess ? .green : .red)
                }

            } header: {
                Text("Server")
            } footer: {
                Text("Enter your server address. Auth Token must match the API_AUTH_TOKEN in your server's .env file (leave empty for local use).")
            }

            // MARK: - Data & Privacy
            Section {
                HStack {
                    Label {
                        Text("AI Data Sharing Consent")
                    } icon: {
                        Image(systemName: hasConsentedToAI ? "checkmark.shield.fill" : "xmark.shield.fill")
                            .foregroundColor(hasConsentedToAI ? .green : .red)
                    }
                    Spacer()
                    Text(hasConsentedToAI ? LocalizedStringKey("Granted") : LocalizedStringKey("Not granted"))
                        .foregroundColor(.secondary)
                        .font(.subheadline)
                }

                Button {
                    showAIDisclosureSheet = true
                } label: {
                    Label("Review Disclosure", systemImage: "doc.text.magnifyingglass")
                }
                .foregroundColor(.accentColor)

                if hasConsentedToAI {
                    Button(role: .destructive) {
                        showRevokeConsentConfirm = true
                    } label: {
                        Label("Revoke Consent", systemImage: "hand.raised.slash")
                    }
                    .confirmationDialog(
                        "Revoke consent to share data with third-party AI?",
                        isPresented: $showRevokeConsentConfirm,
                        titleVisibility: .visible
                    ) {
                        Button("Revoke", role: .destructive) {
                            hasConsentedToAI = false
                            // Stop everything that moves health data off the
                            // phone: HealthKit observers + queued samples, the
                            // WebSocket (not a persisted user "Disconnect", so
                            // re-granting consent reconnects), and push.
                            hk.stopCollection()
                            ws.disconnect(userInitiated: false)
                            DeviceTokenUploader.shared.unregister()
                            hasOnboarded = false
                        }
                    } message: {
                        Text("The app will disconnect from your server and return to onboarding. You can re-grant consent there.")
                    }
                }
            } header: {
                Text("Data & Privacy")
            } footer: {
                Text("Hime sends Apple Health data and chat messages to the third-party AI provider configured on your server. Your explicit consent is required before any data is shared.")
            }

            // MARK: - Onboarding
            Section {
                Button {
                    showPlanSurvey = true
                } label: {
                    Label("Redesign My Plan", systemImage: "wand.and.stars")
                }
                .foregroundColor(.accentColor)

                Button {
                    hasOnboarded = false
                } label: {
                    Label("Replay Onboarding", systemImage: "arrow.counterclockwise")
                }
                .foregroundColor(.accentColor)
            } header: {
                Text("Plan & Onboarding")
            } footer: {
                Text("Retake the goal survey and let HiMe redesign your scheduled check-ins and plan.")
            }

            // MARK: - HealthKit
            Section {
                HStack {
                    Label("Authorization", systemImage: "heart.text.square")
                    Spacer()
                    // iOS never discloses READ authorization status, so
                    // hk.authStatus can only say the prompt completed. Colour
                    // by whether samples have actually arrived — that is the
                    // only observable "it's working" signal.
                    Text(hk.recentSamples.isEmpty ? hk.authStatus : "Receiving data")
                        .foregroundColor(hk.recentSamples.isEmpty ? .orange : .green)
                        .font(.subheadline)
                }

                HStack {
                    Label("Last Sync", systemImage: "clock.arrow.circlepath")
                    Spacer()
                    Text(hk.lastSync)
                        .foregroundColor(.secondary)
                }

                Toggle(isOn: $hk.isBurstModeEnabled) {
                    Label("Burst Mode (High Priority)", systemImage: "bolt.fill")
                        .foregroundColor(hk.isBurstModeEnabled ? .yellow : .secondary)
                }

                Button {
                    hk.forceFetch(fullResync: true)
                } label: {
                    Label("Force Fetch All Data Now", systemImage: "arrow.clockwise")
                }
                .foregroundColor(.accentColor)

            } header: {
                Text("HealthKit")
            } footer: {
                Text("Hime monitors 50+ health metrics from your Apple Watch in the background using HealthKit observer queries.")
            }

            // MARK: - Activity Log
            Section {
                if lm.logs.isEmpty {
                    Text("No recent activity.")
                        .foregroundColor(.secondary)
                        .font(.subheadline)
                } else {
                    ScrollViewReader { proxy in
                        ScrollView {
                            LazyVStack(alignment: .leading, spacing: 2) {
                                ForEach(lm.logs) { entry in
                                    Text(entry.text)
                                        .font(.system(size: 11, design: .monospaced))
                                        .foregroundColor(.secondary)
                                        .padding(.horizontal, 16)
                                        .padding(.vertical, 1)
                                        .id(entry.id)
                                }
                            }
                        }
                        .frame(height: 260)
                        // LogManager keeps logs newest-first, so the latest
                        // line is at the TOP of the list.
                        .onChange(of: lm.logs.first?.id) {
                            if let first = lm.logs.first {
                                withAnimation {
                                    proxy.scrollTo(first.id, anchor: .top)
                                }
                            }
                        }
                    }
                    .listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 4, trailing: 0))
                }

                if !lm.logs.isEmpty {
                    Button {
                        // Logs are stored newest-first; copy oldest-first.
                        UIPasteboard.general.string = lm.logs.reversed().map(\.text).joined(separator: "\n")
                        showCopiedToast = true
                        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                            showCopiedToast = false
                        }
                    } label: {
                        Label("Copy Logs", systemImage: "doc.on.doc")
                    }
                    .foregroundColor(.accentColor)

                    Button(role: .destructive) {
                        showClearLogsConfirm = true
                    } label: {
                        Label("Clear Logs", systemImage: "trash")
                    }
                    .confirmationDialog("Clear all activity logs?", isPresented: $showClearLogsConfirm, titleVisibility: .visible) {
                        Button("Clear", role: .destructive) { lm.logs.removeAll() }
                    }
                }
            } header: {
                Text("Activity Log")
            }

            // MARK: - About
            Section {
                HStack {
                    Text("Version")
                    Spacer()
                    Text(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "—")
                        .foregroundColor(.secondary)
                }
                HStack {
                    Text("Build")
                    Spacer()
                    Text(Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "—")
                        .foregroundColor(.secondary)
                }
            } header: {
                Text("About")
            }
        }
        .navigationTitle("Settings")
        .navigationBarTitleDisplayMode(.inline)
        .overlay {
            if showCopiedToast {
                Text("Copied!")
                    .font(.subheadline)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 8)
                    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 8))
                    .transition(.opacity)
            }
        }
        .animation(.easeInOut(duration: 0.2), value: showCopiedToast)
        .sheet(isPresented: $showAIDisclosureSheet) {
            AIDisclosureSheet()
        }
        .sheet(isPresented: $showPlanSurvey) {
            PlanSurveySheet()
        }
    }

    // MARK: - Test Connection

    private func testServerConnection() async {
        isTestingConnection = true
        connectionTestResult = nil
        connectionTestSuccess = false
        defer { isTestingConnection = false }

        // Commit what is typed (the fields save on a debounce) so the probe
        // runs with the token and address the user is actually looking at.
        tokenSaveTask?.cancel()
        saveTask?.cancel()
        let cfg = ServerConfig(baseAddress: applyServerAddress())
        await applyAuthToken(authToken)

        switch await APIClient.probe(cfg) {
        case .ok:
            let fmt = String(localized: "OK — connected to %@")
            connectionTestResult = String(format: fmt, cfg.baseAddress)
            connectionTestSuccess = true
        case .unauthorized:
            connectionTestResult = String(localized: "Server rejected the auth token (401)")
        case .invalidAddress:
            connectionTestResult = String(localized: "Invalid address")
        case .serverError:
            connectionTestResult = String(localized: "Server returned an error")
        case .unreachable:
            connectionTestResult = String(localized: "Could not reach server")
        }
    }

    // MARK: - Apply settings

    /// Persist the typed server address and, if it actually changed, reconnect
    /// the WebSocket and refresh the watch / APNs registration. An empty
    /// address falls back to the default instead of producing "wss://watch.".
    /// Returns the normalised address.
    @discardableResult
    private func applyServerAddress() -> String {
        let cleaned = ServerConfig.normalized(ServerConfig.extractBase(from: serverAddress))
        guard cleaned != ws.serverConfig.baseAddress else { return cleaned }
        let cfg = ServerConfig(baseAddress: cleaned)
        cfg.save()
        ws.serverConfig = cfg
        ws.serverSettingsDidChange()
        return cleaned
    }

    /// Persist the auth token; on a real change reconnect (the WS handshake
    /// carries the token), push it to the watch, retry the APNs token upload,
    /// and flush any survey that was waiting for it.
    private func applyAuthToken(_ raw: String) async {
        let token = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if token != ServerConfig.authToken {
            ServerConfig.authToken = token
            ws.serverSettingsDidChange()
        }
        // A survey captured during onboarding (before the token existed) is
        // stashed locally — flush it now.
        await ServerConfig.flushPendingSurvey()
    }
}

// MARK: - AI Disclosure Sheet

struct AIDisclosureSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    HStack(spacing: 12) {
                        Image(systemName: "brain.head.profile")
                            .font(.system(size: 32))
                            .foregroundColor(.purple)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("AI Data Sharing")
                                .font(.title2.bold())
                            Text("How Hime shares your data with third-party AI")
                                .font(.footnote)
                                .foregroundColor(.secondary)
                        }
                    }

                    Text(AIDisclosureContent.bodyText)
                        .font(.callout)
                        .foregroundColor(.primary.opacity(0.9))
                        .fixedSize(horizontal: false, vertical: true)

                    AIDisclosureDetails()
                }
                .padding(20)
            }
            .navigationTitle("Data & Privacy")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}
