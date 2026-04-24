import SwiftUI

// MARK: - Onboarding flow
//
// Shown once on first launch (or after the user taps "Replay Onboarding" in
// Settings). Walks the user through:
//
//   1. Welcome
//   2. HealthKit rationale + optional authorization
//   3. Server setup
//   4. Cat avatar explanation
//   5. Ready / Start
//
// Completion sets `UserDefaults["hime.hasOnboarded"] = true` via the
// `@AppStorage` binding the parent scene holds.

struct OnboardingView: View {
    @Binding var hasOnboarded: Bool

    @AppStorage("hime.hasConsentedToAIDataSharing") private var hasConsentedToAI: Bool = false

    @State private var pageIndex: Int = 0
    @State private var serverAddress: String = ServerConfig.defaultAddress
    @State private var serverTestState: ServerTestState = .idle
    @State private var didRequestHealthKit = false
    @State private var aiConsentChecked: Bool = false

    private let totalPages = 6

    enum ServerTestState: Equatable {
        case idle
        case testing
        case success(String)
        case failure(String)
    }

    var body: some View {
        VStack(spacing: 0) {
            TabView(selection: $pageIndex) {
                welcomePage.tag(0)
                healthKitPage.tag(1)
                serverPage.tag(2)
                aiDisclosurePage.tag(3)
                catPage.tag(4)
                readyPage.tag(5)
            }
            .tabViewStyle(.page(indexDisplayMode: .never))
            .indexViewStyle(.page(backgroundDisplayMode: .always))

            // Dot indicator + primary action
            VStack(spacing: 16) {
                HStack(spacing: 8) {
                    ForEach(0..<totalPages, id: \.self) { idx in
                        Circle()
                            .fill(idx == pageIndex
                                  ? Color(red: 0.95, green: 0.70, blue: 0.35)
                                  : Color.gray.opacity(0.3))
                            .frame(width: idx == pageIndex ? 9 : 7,
                                   height: idx == pageIndex ? 9 : 7)
                            .animation(.spring(response: 0.3), value: pageIndex)
                    }
                }

                primaryButton
                    .padding(.horizontal, 24)

                // Reserve space so the button row doesn't jump between pages.
                Text(" ").font(.footnote)
            }
            .padding(.bottom, 30)
            .padding(.top, 12)
        }
        .background(Color(.systemBackground).ignoresSafeArea())
    }

    // MARK: - Pages

    private var welcomePage: some View {
        OnboardingPage(
            icon: "sparkles",
            iconColor: Color(red: 0.95, green: 0.70, blue: 0.35),
            title: "Welcome to Hime",
            subtitle: "Health Intelligence Management Engine",
            bodyText: "As your personal AI health assistant, Hime understands your real-time health data, feels your heart beats, your breath, and your body's movements, and provides proactive insights so you never miss what matters. \n\nFully self-hosted, secure, and open-source. Say **Hi** to healthy **Me**!"
        )
    }

    private var healthKitPage: some View {
        OnboardingPage(
            icon: "heart.text.square.fill",
            iconColor: .pink,
            title: "Apple Health Access",
            subtitle: "Read-only and on-device",
            bodyText: "Hime reads wearable health data from Apple Health so the AI agent can analyse trends. Hime never writes to Health and you have the full control on how and where AI process the data.",
            extraContent: AnyView(
                VStack(spacing: 8) {
                    if didRequestHealthKit {
                        Label("Access requested", systemImage: "checkmark.circle.fill")
                            .foregroundColor(.green)
                            .font(.subheadline)
                    }
                }
            )
        )
    }

    private var serverPage: some View {
        OnboardingPage(
            icon: "server.rack",
            iconColor: .blue,
            title: "Connect Your Server",
            subtitle: "Self-hosted, full control on your data",
            bodyText: "Hime runs on your own server. Follow the Hime GitHub Repo to deploy your own server.",
            extraContent: AnyView(
                VStack(spacing: 12) {
                    HStack {
                        Image(systemName: "network")
                            .foregroundColor(.secondary)
                        TextField("192.168.1.100 or example.com", text: $serverAddress)
                            .keyboardType(.URL)
                            .autocorrectionDisabled()
                            .textInputAutocapitalization(.never)
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(Color(.secondarySystemBackground))
                    .clipShape(RoundedRectangle(cornerRadius: 10))

                    Button {
                        Task { await testServerConnection() }
                    } label: {
                        HStack(spacing: 6) {
                            if serverTestState == .testing {
                                ProgressView().scaleEffect(0.8)
                            }
                            Text(testButtonTitle)
                                .fontWeight(.medium)
                        }
                    }
                    .disabled(serverTestState == .testing || serverAddress.isEmpty)

                    if case .success(let msg) = serverTestState {
                        Label(msg, systemImage: "checkmark.circle.fill")
                            .foregroundColor(.green)
                            .font(.footnote)
                    } else if case .failure(let msg) = serverTestState {
                        Label(msg, systemImage: "xmark.octagon.fill")
                            .foregroundColor(.red)
                            .font(.footnote)
                    }

                    Link(destination: URL(string: "https://github.com/thinkwee/HiMe")!) {
                        Label("Setup Guide on GitHub", systemImage: "link")
                            .font(.footnote)
                    }
                }
                .padding(.horizontal, 24)
            )
        )
    }

    private var aiDisclosurePage: some View {
        VStack(spacing: 0) {
            // Compact fixed header so user immediately sees what the page is.
            VStack(spacing: 6) {
                Image(systemName: "brain.head.profile")
                    .font(.system(size: 40))
                    .foregroundColor(.purple)
                Text("AI Data Sharing")
                    .font(.system(size: 22, weight: .bold, design: .rounded))
                Text("Scroll to read all sections, then agree below")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .padding(.top, 16)
            .padding(.bottom, 8)

            ZStack(alignment: .bottom) {
                ScrollView(.vertical, showsIndicators: true) {
                    VStack(alignment: .leading, spacing: 14) {
                        Text(AIDisclosureContent.bodyText)
                            .font(.callout)
                            .foregroundColor(.primary.opacity(0.9))
                            .fixedSize(horizontal: false, vertical: true)

                        AIDisclosureDetails()

                        Toggle(isOn: $aiConsentChecked) {
                            Text("I understand and agree to share my data with the third-party AI service configured on my server.")
                                .font(.footnote)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .tint(.purple)
                        .padding(.top, 4)

                        // Bottom padding so the fade overlay never hides the toggle.
                        Color.clear.frame(height: 24)
                    }
                    .padding(.horizontal, 20)
                    .padding(.top, 8)
                    .padding(.bottom, 16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }

                // Fade + chevron hint at the bottom so it's visually obvious there's more.
                VStack(spacing: 2) {
                    LinearGradient(
                        colors: [Color(.systemBackground).opacity(0), Color(.systemBackground)],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                    .frame(height: 18)
                    Image(systemName: "chevron.compact.down")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundColor(.secondary)
                        .padding(.bottom, 2)
                }
                .allowsHitTesting(false)
            }
        }
    }

    private var catPage: some View {
        OnboardingPage(
            icon: "cat.fill",
            iconColor: Color(red: 0.95, green: 0.70, blue: 0.35),
            title: "Meet HiMeow",
            subtitle: "Your AI digital health avatar",
            bodyText: "HiMeow is a AI digital health avatar that lives on your home screen and reflects your real-time health state. It's happy when you are well-rested, concerned when something looks off. \nLong-press HiMeow anytime for an instant health report and quick AI analysis.",
            customIcon: AnyView(
                CatHeadView(catState: "happy")
                    .frame(width: 120, height: 120)
            )
        )
    }

    private var readyPage: some View {
        OnboardingPage(
            icon: "checkmark.seal.fill",
            iconColor: .green,
            title: "You're All Set",
            subtitle: "Let's get started",
            bodyText: "Hime is ready. \n Long press the HiMeow in the home tab and the AI agent will start analysing health trends and pushing reports automatically."
        )
    }

    // MARK: - Primary button

    @ViewBuilder
    private var primaryButton: some View {
        switch pageIndex {
        case 0:
            nextButton(title: "Get Started")
        case 1:
            Button {
                Task { await requestHealthKitAuth() }
            } label: {
                Text(didRequestHealthKit ? "Continue" : "Grant Access")
                    .fontWeight(.semibold)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .background(Color.pink)
                    .foregroundColor(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
        case 2:
            VStack(spacing: 8) {
                Button {
                    if canAdvanceFromServerPage {
                        advance()
                    }
                } label: {
                    Text("Continue")
                        .fontWeight(.semibold)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background(canAdvanceFromServerPage ? Color.blue : Color.gray.opacity(0.4))
                        .foregroundColor(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                }
                .disabled(!canAdvanceFromServerPage)

                Button("Skip for now") {
                    advance()
                }
                .font(.footnote)
                .foregroundColor(.secondary)
            }
        case 3:
            Button {
                if aiConsentChecked {
                    hasConsentedToAI = true
                    advance()
                }
            } label: {
                Text("I Agree & Continue")
                    .fontWeight(.semibold)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .background(aiConsentChecked ? Color.purple : Color.gray.opacity(0.4))
                    .foregroundColor(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            .disabled(!aiConsentChecked)
        case 4:
            nextButton(title: "Continue")
        case 5:
            Button {
                finishOnboarding()
            } label: {
                Text("Start")
                    .fontWeight(.semibold)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .background(Color.green)
                    .foregroundColor(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
        default:
            EmptyView()
        }
    }

    private func nextButton(title: LocalizedStringKey) -> some View {
        Button {
            advance()
        } label: {
            Text(title)
                .fontWeight(.semibold)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .background(Color(red: 0.95, green: 0.70, blue: 0.35))
                .foregroundColor(.white)
                .clipShape(RoundedRectangle(cornerRadius: 12))
        }
    }

    // MARK: - Logic

    private var canAdvanceFromServerPage: Bool {
        if case .success = serverTestState { return true }
        return false
    }

    private var testButtonTitle: LocalizedStringKey {
        switch serverTestState {
        case .testing: return "Testing..."
        case .success: return "Test Again"
        default: return "Test Connection"
        }
    }

    private func advance() {
        withAnimation { pageIndex = min(pageIndex + 1, totalPages - 1) }
    }

    private func finishOnboarding() {
        hasOnboarded = true
    }

    private func requestHealthKitAuth() async {
        await HealthKitManager.shared.setup()
        didRequestHealthKit = true
        advance()
    }

    private func testServerConnection() async {
        serverTestState = .testing
        let cleaned = ServerConfig.extractBase(from: serverAddress)
        serverAddress = cleaned
        let cfg = ServerConfig(baseAddress: cleaned)
        cfg.save()
        // Publish to the live singleton so existing WS/HTTP pipelines pick up
        // the new address immediately. Without this, the WebSocketClient keeps
        // pointing at whatever ServerConfig.load() returned at app launch
        // (usually the default 192.168.1.100), and every post-onboarding
        // upload silently targets a dead address — see SettingsView for the
        // same pattern.
        WebSocketClient.shared.serverConfig = cfg
        guard let url = URL(string: "\(cfg.apiBaseURL)/health") else {
            serverTestState = .failure(String(localized: "Invalid server address"))
            return
        }
        var req = URLRequest(url: url)
        req.timeoutInterval = 6
        do {
            let (_, response) = try await URLSession.shared.data(for: req)
            if let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) {
                let fmt = String(localized: "Connected to %@")
                serverTestState = .success(String(format: fmt, cfg.baseAddress))
                // Open the WebSocket now — the user still has several pages
                // to tap through (consent, HK auth, cat picker) before the
                // first observer burst. By the time HealthKit starts firing
                // samples, WS is already established and the initial 1000s
                // of records drain over WS instead of racing a
                // half-initialised HTTP fallback.
                WebSocketClient.shared.connect()
            } else {
                serverTestState = .failure(String(localized: "Server responded with an error"))
            }
        } catch {
            serverTestState = .failure(String(localized: "Could not reach server"))
        }
    }
}

// MARK: - Reusable page scaffold

private struct OnboardingPage: View {
    let icon: String
    let iconColor: Color
    let title: LocalizedStringKey
    let subtitle: LocalizedStringKey
    let bodyText: LocalizedStringKey
    var extraContent: AnyView? = nil
    var customIcon: AnyView? = nil

    var body: some View {
        VStack(spacing: 0) {
            Spacer(minLength: 40)

            if let custom = customIcon {
                custom.padding(.bottom, 24)
            } else {
                Image(systemName: icon)
                    .font(.system(size: 72))
                    .foregroundColor(iconColor)
                    .padding(.bottom, 24)
            }

            Text(title)
                .font(.system(size: 28, weight: .bold, design: .rounded))
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)

            Text(subtitle)
                .font(.subheadline)
                .foregroundColor(.secondary)
                .padding(.top, 4)

            Text(bodyText)
                .font(.body)
                .foregroundColor(.primary.opacity(0.85))
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
                .padding(.top, 20)
                .fixedSize(horizontal: false, vertical: true)

            if let extra = extraContent {
                extra.padding(.top, 24)
            }

            Spacer(minLength: 40)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

// MARK: - Shared AI disclosure content

enum AIDisclosureContent {
    static let bodyText: LocalizedStringKey = "Hime is self-hosted, but your server forwards your data to a third-party AI service that you choose and configure. Please review the details below before continuing."
}

struct AIDisclosureDetails: View {
    @State private var showFullPolicy = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            DisclosureRow(
                icon: "tray.full",
                title: "What is sent",
                text: "Health metrics from Apple Health (heart rate, HRV, SpO2, respiratory rate, sleep stages, steps, active energy, workouts, and related wearable data) and the text of your conversations with the AI agent."
            )
            DisclosureRow(
                icon: "person.2",
                title: "Who it is sent to",
                text: "The third-party AI provider you configure on your server. Supported providers include OpenAI, Anthropic, Google (Gemini / Vertex), Mistral, Groq, DeepSeek, xAI, OpenRouter, Perplexity, Amazon Bedrock, Azure OpenAI, ZhipuAI, MiniMax, or a self-hosted vLLM endpoint. The provider you select will receive your data under its own terms and privacy policy."
            )
            DisclosureRow(
                icon: "arrow.triangle.branch",
                title: "How it is sent",
                text: "Data travels from this app to the Hime server you deploy, and your server makes API calls to the AI provider you selected. Hime does not upload your data to any service operated by the app developer."
            )
            DisclosureRow(
                icon: "hand.raised",
                title: "Your control",
                text: "You can revoke consent any time in Settings → Data & Privacy. Revoking consent stops the app from sending data and returns you to onboarding."
            )
            Button {
                showFullPolicy = true
            } label: {
                Label("Read the full privacy policy", systemImage: "doc.text")
                    .font(.footnote)
            }
            .padding(.top, 2)
        }
        .sheet(isPresented: $showFullPolicy) {
            PrivacyPolicySheet()
        }
    }
}

// MARK: - Full Privacy Policy (embedded in app)

struct PrivacyPolicySheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    PolicyHeading("Hime Privacy Policy")
                    PolicyMeta("Last updated: 2026-04-15")

                    PolicySection(title: "1. Who we are") {
                        "Hime (Health Intelligence Management Engine) is an open-source, self-hosted AI health assistant. The iOS app is a client that communicates only to a backend server that you, the user, deploy and operate yourself. The app developer does not run any server, does not collect telemetry, and does not have access to your data."
                    }

                    PolicySection(title: "2. Data the app collects on your device") {
                        "With your permission, the app reads health and fitness data from Apple HealthKit, including but not limited to: heart rate, heart rate variability (HRV), blood oxygen (SpO2), respiratory rate, body temperature, steps, distance, active and basal energy, exercise minutes, stand hours, workouts, sleep analysis (in-bed / core / deep / REM / awake), mindful sessions, and related wearable measurements. The app also stores the text of your conversations with the AI agent and the configuration you enter (server address, optional auth token, app preferences). The app does NOT read contacts, photos, location, microphone, camera, or any other personal data outside HealthKit."
                    }

                    PolicySection(title: "3. How the app collects data") {
                        "Health data is read from Apple HealthKit using HealthKit observer queries that the user authorises on the \"Apple Health Access\" onboarding page. The app never writes to HealthKit. Conversation text is provided directly by the user when they message the AI agent. Configuration is entered manually by the user."
                    }

                    PolicySection(title: "4. How the app uses data") {
                        "Collected data is forwarded to the Hime backend server that you deploy and configure in the app's Settings. Your server stores the data locally and uses it to: (a) compute statistics for the on-device dashboard; (b) generate AI-driven health reports and chat replies; (c) trigger health alerts based on rules you define. The app developer never receives any of your data."
                    }

                    PolicySection(title: "5. Sharing with third-party AI services") {
                        "Your Hime server makes API calls to a third-party Large Language Model (LLM) provider that you select via the DEFAULT_LLM_PROVIDER setting on your server. Supported providers include: OpenAI, Anthropic, Google (Gemini / Vertex AI), Mistral, Groq, DeepSeek, xAI, OpenRouter, Perplexity, Amazon Bedrock, Azure OpenAI, ZhipuAI, MiniMax, or a self-hosted vLLM endpoint.\n\nWhen the AI agent runs, the following may be sent to the chosen provider: numeric health metrics (with timestamps and units), summaries derived from your data, the text of your messages, and prompts/instructions assembled by the agent. Each provider processes the data under its own terms of service and privacy policy. You can review the providers' policies on their respective websites. We require you to confirm that the provider you choose offers data protection equal to or stronger than what is described in this policy.\n\nHime does not share data with any other third party (no analytics SDKs, no advertising networks, no crash-reporting services that exfiltrate data)."
                    }

                    PolicySection(title: "6. Data storage and retention") {
                        "Data is stored on your iPhone (HealthKit-derived caches, conversation history, configuration in UserDefaults) and on the Hime server you operate (per-user SQLite databases, agent memory, generated reports). Retention is fully under your control: deleting the app removes all on-device data; resetting your server wipes all server-side data. Third-party AI providers may retain prompt/response logs according to their own policies."
                    }

                    PolicySection(title: "7. Your consent and your controls") {
                        "Before any data is shared with a third-party AI service, the app requires your explicit consent on the \"AI Data Sharing\" onboarding page. You can withdraw consent at any time in Settings → Data & Privacy → Revoke Consent. Revoking consent disconnects the app from your server and returns you to onboarding; no data is sent until you re-consent. You can also revoke HealthKit access in the iOS Settings app at any time."
                    }

                    PolicySection(title: "8. Security") {
                        "Communication between the app and your server uses HTTPS when you configure an HTTPS server URL. An optional bearer token (API_AUTH_TOKEN) authenticates every request when set. Server-side, dynamically generated personalised pages run under a strict Content Security Policy and a sandbox that blocks dangerous imports. SQL accepted from the agent is validated to prevent destructive statements against health data."
                    }

                    PolicySection(title: "9. Children") {
                        "Hime is not directed to children under 13. Do not use the app if you are under the minimum age of digital consent in your jurisdiction."
                    }

                    PolicySection(title: "10. Changes to this policy") {
                        "If material changes are made, the in-app version of this policy will be updated and the app will request fresh consent on next launch."
                    }

                    PolicySection(title: "11. Contact") {
                        "Questions about this policy or the project can be raised at https://github.com/thinkwee/HiMe/issues."
                    }
                }
                .padding(20)
            }
            .navigationTitle("Privacy Policy")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}

private struct PolicyHeading: View {
    let text: LocalizedStringKey
    init(_ text: LocalizedStringKey) { self.text = text }
    var body: some View {
        Text(text).font(.title2.bold())
    }
}

private struct PolicyMeta: View {
    let text: LocalizedStringKey
    init(_ text: LocalizedStringKey) { self.text = text }
    var body: some View {
        Text(text).font(.caption).foregroundColor(.secondary)
    }
}

private struct PolicySection: View {
    let title: LocalizedStringKey
    let bodyText: LocalizedStringKey
    init(title: LocalizedStringKey, content: () -> LocalizedStringKey) {
        self.title = title
        self.bodyText = content()
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.headline)
            Text(bodyText)
                .font(.callout)
                .foregroundColor(.primary.opacity(0.9))
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct DisclosureRow: View {
    let icon: String
    let title: LocalizedStringKey
    let text: LocalizedStringKey

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon)
                .foregroundColor(.purple)
                .frame(width: 22)
                .padding(.top, 2)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.footnote.weight(.semibold))
                Text(text)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
