import SwiftUI
import WebKit
import Combine

// MARK: - Personalised Page Model

struct PersonalisedPage: Identifiable, Decodable {
    var id: String { page_id }
    let page_id: String
    let display_name: String
    let description: String?
    let backend_route: String
    let frontend_asset: String
    let created_at: String?
}

private struct PersonalisedPagesResponse: Decodable {
    let success: Bool
    let pages: [PersonalisedPage]
}

// MARK: - Personalised Pages Store

@MainActor
class PersonalisedPagesStore: ObservableObject {
    @Published var pages: [PersonalisedPage] = []
    @Published var isLoading: Bool = false

    private var refreshTimer: Timer?

    func fetchPages() async {
        isLoading = true
        defer { isLoading = false }
        let base = ServerConfig.load().apiBaseURL
        guard let url = URL(string: "\(base)/api/personalised-pages/list") else { return }
        do {
            let (data, _) = try await URLSession.shared.data(for: APIClient.request(url))
            let decoded = try JSONDecoder().decode(PersonalisedPagesResponse.self, from: data)
            pages = decoded.pages
        } catch {
            // Silently ignore — server may not be running
        }
    }

    /// Start a background timer that re-fetches pages every 30 seconds.
    func startPeriodicRefresh() {
        stopPeriodicRefresh()
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in
                await self.fetchPages()
            }
        }
    }

    func stopPeriodicRefresh() {
        refreshTimer?.invalidate()
        refreshTimer = nil
    }
}

// MARK: - WKWebView Wrapper

struct PersonalisedPageWebView: UIViewRepresentable {
    let urlString: String

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.scrollView.bounces = true
        // Load once at creation time.
        if let url = URL(string: urlString) {
            webView.load(APIClient.request(url))
        }
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        // SwiftUI calls updateUIView whenever the parent view re-evaluates, which happens
        // frequently (HealthKit samples, WebSocket ticks, pages-list refresh every 30s).
        // Only reload if the target URL actually changed — otherwise we'd kill in-flight
        // Chart.js rendering, leak canvas contexts, and eventually get jetsam-killed by
        // the Web Content process, terminating the whole app.
        guard let url = URL(string: urlString), webView.url != url else { return }
        webView.load(APIClient.request(url))
    }
}

// MARK: - Personalised Page Screen

struct PersonalisedPageScreen: View {
    let page: PersonalisedPage

    /// The WebView sets an Authorization header for the HTML load itself, but the
    /// page's own fetch of /data is issued by its inline JS, which cannot set
    /// headers. `hime-ui.js` forwards a ?token= from its own location when it is
    /// not framed, so pass it through here — otherwise every page 401s on its
    /// data request against an auth-enabled backend.
    private var frontendURL: String {
        let base = "\(ServerConfig.load().apiBaseURL)/api/personalised-pages/\(page.page_id)/"
        let token = ServerConfig.authToken
        guard !token.isEmpty else { return base }
        let encoded = token.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? token
        return "\(base)?token=\(encoded)"
    }

    var body: some View {
        PersonalisedPageWebView(urlString: frontendURL)
    }
}

// MARK: - Content View

struct ContentView: View {
    @EnvironmentObject var hk: HealthKitManager
    @EnvironmentObject var ws: WebSocketClient

    @StateObject private var pagesStore = PersonalisedPagesStore()
    @ObservedObject private var router = AppRouter.shared

    // Tab order: Dashboard(0), Collected Data(1), Cat Home(2, default), Pages(3..3+n-1), Placeholder(3+n)
    @State private var selectedTab = 2
    /// Root navigation stack — driven programmatically so a tapped notification
    /// can deep-link straight into Chat.
    @State private var path = NavigationPath()

    /// 3 built-in + personalised pages + 1 placeholder
    private var totalTabs: Int { 3 + pagesStore.pages.count + 1 }

    /// Keep the selection inside the range of tags the TabView actually renders.
    private func clampSelectedTab(_ candidate: Int) {
        let maxTab = totalTabs - 1
        if candidate > maxTab { selectedTab = maxTab }
        else if candidate < 0 { selectedTab = 0 }
    }

    var body: some View {
        mainContent
    }

    @ViewBuilder
    private var mainContent: some View {
        NavigationStack(path: $path) {
            VStack(spacing: 0) {
                // Page indicator dots
                HStack(spacing: 6) {
                    ForEach(0..<totalTabs, id: \.self) { idx in
                        Circle()
                            .fill(selectedTab == idx ? Color(red: 0.95, green: 0.70, blue: 0.35) : Color.gray.opacity(0.3))
                            .frame(width: selectedTab == idx ? 9 : 7, height: selectedTab == idx ? 9 : 7)
                            .animation(.spring(response: 0.3), value: selectedTab)
                    }
                }
                .padding(.top, 8)
                .animation(.spring(), value: totalTabs)

                TabView(selection: $selectedTab) {
                    // Tab 0: Dashboard (includes Reports + Tasks via segmented control)
                    DashboardView()
                        .tag(0)

                    // Tab 1: Collected Data
                    DataListView(hk: hk)
                        .tag(1)

                    // Tab 2 (center/default): Cat Main View
                    CatMainView()
                        .tag(2)

                    // Tab 3+: Personalised page screens (right of Cat)
                    ForEach(Array(pagesStore.pages.enumerated()), id: \.element.id) { idx, page in
                        PersonalisedPageScreen(page: page)
                            .tag(3 + idx)
                    }

                    // Last tab: Placeholder for next page
                    PagePlaceholderView()
                        .tag(3 + pagesStore.pages.count)
                }
                .tabViewStyle(.page(indexDisplayMode: .never))
                .ignoresSafeArea(edges: .bottom)
                .onChange(of: selectedTab) { _, newValue in
                    clampSelectedTab(newValue)
                }
                // The tab count shrinks whenever the server drops a personalised
                // page. Clamping only on selectedTab changes left the TabView
                // parked on a tag that no longer exists — a blank screen the user
                // can't swipe out of — so watch the count too.
                .onChange(of: totalTabs) { _, _ in
                    clampSelectedTab(selectedTab)
                }
            }
            .background(selectedTab == 2
                ? Color(red: 0.82, green: 0.78, blue: 0.72)
                : Color(.systemGroupedBackground))
            .navigationTitle(navTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(.hidden, for: .navigationBar)
            .animation(.easeInOut(duration: 0.2), value: selectedTab)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Link(destination: URL(string: "https://github.com/thinkwee/HiMe")!) {
                        GitHubIcon()
                            .frame(width: 20, height: 20)
                    }
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    NavigationLink(destination: SettingsView()) {
                        Image(systemName: "gearshape")
                            .foregroundColor(.primary)
                    }
                }
            }
            .navigationDestination(for: AppRoute.self) { route in
                switch route {
                case .chat: ChatView()
                // .report is handled by switching tabs (not a pushed
                // destination), so this branch is never reached — kept for
                // exhaustiveness.
                case .report: DashboardView()
                }
            }
            .task {
                await pagesStore.fetchPages()
                pagesStore.startPeriodicRefresh()
                // A goal survey captured during onboarding (before the auth
                // token was entered) is stashed locally; flush it now that the
                // app is up and a token may exist.
                await ServerConfig.flushPendingSurvey()
            }
            .onDisappear {
                pagesStore.stopPeriodicRefresh()
            }
            .refreshable {
                await pagesStore.fetchPages()
            }
        }
        // A tapped notification asks the router to open Chat. onReceive (not
        // onChange) so a value set before this view subscribes — e.g. a cold
        // launch from the notification — still routes.
        .onReceive(router.$pendingRoute) { route in
            guard let route else { return }
            switch route {
            case .chat:
                selectedTab = 2
                path = NavigationPath()
                path.append(AppRoute.chat)
            case .report:
                // Reports live inside the Dashboard tab. First pop any pushed
                // screen (e.g. the Chat view this "view full report" tap came
                // from) so the TabView becomes frontmost, THEN switch to the
                // Dashboard tab on a later runloop tick once the pop has settled.
                //
                // Why deferred: applied in the SAME state update as the pop, the
                // PageTabViewStyle's underlying UIPageViewController — still
                // covered by the popping Chat view — silently drops the
                // selection change and stays on Hime Home (tab 2), so the user
                // had to swipe several pages over to reach the report. Deferring
                // until the pop settles lets the page controller honour the jump
                // (without animation, so it's a clean cut, not a scroll through
                // the Data tab). `pendingReportId` then drives the Reports
                // section + row auto-expand once DashboardView appears.
                path = NavigationPath()
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
                    var tx = Transaction()
                    tx.disablesAnimations = true
                    withTransaction(tx) { selectedTab = 0 }
                }
            }
            router.pendingRoute = nil
        }
    }

    private var navTitle: String {
        switch selectedTab {
        case 0: return String(localized: "Dashboard")
        case 1: return String(localized: "Collected Data")
        case 2: return String(localized: "Hime Home")
        default:
            let pageIdx = selectedTab - 3
            if pageIdx >= 0 && pageIdx < pagesStore.pages.count {
                return pagesStore.pages[pageIdx].display_name
            }
            return String(localized: "Pages")
        }
    }
}

// MARK: - Page Placeholder View

struct PagePlaceholderView: View {
    var body: some View {
        VStack(spacing: 24) {
            Spacer()

            Image(systemName: "sparkles.rectangle.stack")
                .font(.system(size: 56))
                .foregroundStyle(
                    LinearGradient(
                        colors: [Color(red: 0.95, green: 0.70, blue: 0.35),
                                 Color(red: 0.85, green: 0.50, blue: 0.80)],
                        startPoint: .topLeading, endPoint: .bottomTrailing
                    )
                )

            VStack(spacing: 10) {
                Text("Your Next Page Lives Here")
                    .font(.system(size: 20, weight: .bold, design: .rounded))
                    .foregroundColor(.primary)

                Text("Infinite possibilities, one conversation away.")
                    .font(.system(size: 15, weight: .medium, design: .rounded))
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
            }

            Text("Ask your agent to create a personalised page\nand it will appear right here.")
                .font(.system(size: 13))
                .foregroundColor(.secondary.opacity(0.7))
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)

            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

// MARK: - Components from original ContentView

// MARK: - Feature Grouped Sample (one entry per feature type)

struct FeatureGroup: Identifiable {
    let id = UUID()
    let feature: String
    let samples: [RecentSample]

    var count: Int { samples.count }
    var syncedCount: Int { samples.filter { $0.isSynced }.count }
    var syncPercentage: Int {
        guard count > 0 else { return 0 }
        return Int(Double(syncedCount) / Double(count) * 100)
    }
    var emoji: String { samples.first?.emoji ?? "📡" }
    var displayName: String { samples.first?.displayName ?? feature }
    /// Most recent sample timestamp
    var latestTimestamp: Date { samples.first?.timestamp ?? .distantPast }
}

func groupSamplesByFeature(_ samples: [RecentSample]) -> [FeatureGroup] {
    let grouped = Dictionary(grouping: samples) { $0.feature }
    return grouped.map { feature, arr in
        FeatureGroup(
            feature: feature,
            samples: arr.sorted { $0.timestamp > $1.timestamp }  // newest first
        )
    }.sorted { $0.latestTimestamp > $1.latestTimestamp }  // features with newest data first
}

// MARK: - Recent Samples Section

struct RecentSamplesSection: View {
    @ObservedObject var hk: HealthKitManager

    private var featureGroups: [FeatureGroup] {
        groupSamplesByFeature(hk.recentSamples)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Recent Collected Data")
                    .font(.headline)
                    .foregroundColor(.primary)
                Spacer()
                if !hk.recentSamples.isEmpty {
                    Text("\(hk.recentSamples.count) readings")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
            .padding(.horizontal)

            if hk.recentSamples.isEmpty {
                EmptyRecentView()
            } else {
                ScrollView {
                    LazyVStack(spacing: 8) {
                        ForEach(featureGroups) { group in
                            NavigationLink(destination: FeatureDetailView(group: group)) {
                                FeatureGroupRow(group: group)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.horizontal)
                }
            }
        }
    }
}

struct FeatureGroupRow: View {
    let group: FeatureGroup

    var body: some View {
        HStack(spacing: 12) {
            Text(group.emoji)
                .font(.title2)
                .frame(width: 36, height: 36)
                .background(Color(.tertiarySystemGroupedBackground))
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))

            VStack(alignment: .leading, spacing: 2) {
                Text(group.displayName)
                    .font(.subheadline)
                    .foregroundColor(.primary)
                Text(group.latestTimestamp.formatted(date: .abbreviated, time: .shortened))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            Spacer()

            VStack(alignment: .trailing, spacing: 2) {
                Text("\(group.count) readings")
                    .font(.subheadline.weight(.medium))
                    .foregroundColor(.primary)

                HStack(spacing: 4) {
                    if group.syncPercentage == 100 {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.system(size: 10))
                            .foregroundColor(.green)
                    }
                    Text("\(group.syncPercentage)% synced")
                        .font(.caption2.weight(.bold))
                        .foregroundColor(group.syncPercentage == 100 ? .green : .orange)
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(Color(.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

struct FeatureDetailView: View {
    let group: FeatureGroup

    var body: some View {
        List {
            Section {
                ForEach(group.samples) { sample in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(sample.timestamp, style: .time)
                                .font(.subheadline.monospacedDigit())
                                .foregroundColor(.primary)
                            Text(sample.timestamp, style: .date)
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 2) {
                            Text(sample.formattedValue)
                                .font(.subheadline.weight(.medium).monospacedDigit())
                                .foregroundColor(.primary)
                            if sample.isSynced {
                                Image(systemName: "checkmark.circle.fill")
                                    .font(.system(size: 10))
                                    .foregroundColor(.green)
                            } else {
                                Image(systemName: "arrow.up.circle")
                                    .font(.system(size: 10))
                                    .foregroundColor(.orange)
                            }
                        }
                    }
                }
            } header: {
                Text("\(group.displayName) · \(group.count) readings")
            }
        }
        .navigationTitle(group.displayName)
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct EmptyRecentView: View {
    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "waveform.path.ecg")
                .font(.system(size: 40))
                .foregroundColor(.secondary.opacity(0.4))
            Text("No data collected yet")
                .font(.subheadline)
                .foregroundColor(.secondary)
            Text("Connect and wear your Apple Watch to start collecting health data.")
                .font(.caption)
                .foregroundColor(.secondary.opacity(0.7))
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 48)
    }
}
