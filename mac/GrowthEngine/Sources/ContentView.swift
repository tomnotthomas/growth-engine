import SwiftUI

enum Page: String, CaseIterable, Identifiable, Hashable {
    case overview, goal, queue, jobs, channels, activity, project, engine, audit
    var id: String { rawValue }

    var title: String {
        switch self {
        case .overview: return "Overview"
        case .goal: return "Goal"
        case .queue: return "Waiting for you"
        case .jobs: return "Jobs"
        case .channels: return "Channels"
        case .activity: return "Activity"
        case .project: return "Project settings"
        case .engine: return "Engine"
        case .audit: return "Audit log"
        }
    }

    var symbol: String {
        switch self {
        case .overview: return "square.grid.2x2"
        case .goal: return "chart.line.uptrend.xyaxis"
        case .queue: return "tray.full"
        case .jobs: return "clock.arrow.2.circlepath"
        case .channels: return "dot.radiowaves.left.and.right"
        case .activity: return "list.bullet.rectangle"
        case .project: return "slider.horizontal.3"
        case .engine: return "cpu"
        case .audit: return "checkmark.shield"
        }
    }
}

struct ContentView: View {
    @Environment(EngineStore.self) private var store
    @State private var section: Page? = .overview
    @State private var killSheet = false

    var body: some View {
        NavigationSplitView {
            List(selection: $section) {
                Section("Project") {
                    ForEach([Page.overview, .goal, .queue, .jobs, .channels, .activity, .project]) { item in
                        row(item)
                    }
                }
                Section("Engine") {
                    ForEach([Page.engine, .audit]) { item in row(item) }
                }
            }
            .navigationSplitViewColumnWidth(min: 190, ideal: 210)
            .safeAreaInset(edge: .bottom) { ConnectionFooter().padding(12) }
        } detail: {
            Group {
                if let dashboard = store.dashboard {
                    detail(dashboard)
                } else {
                    FirstConnectView()
                }
            }
            .safeAreaInset(edge: .top, spacing: 0) { Banners(killSheet: $killSheet) }
            .navigationTitle(section?.title ?? "Growth Engine")
            .navigationSubtitle(store.project?.name ?? "")
        }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                if let projects = store.dashboard?.projects, projects.count > 1 {
                    Picker("Project", selection: Binding(get: { store.selectedProjectID ?? "" }, set: { store.selectedProjectID = $0 })) {
                        ForEach(projects) { Text($0.name).tag($0.id) }
                    }
                    .pickerStyle(.menu)
                }
                Button { Task { await store.refresh() } } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                    .help("Fetch the latest state now (⌘R)")
                KillSwitchButton(killSheet: $killSheet)
            }
        }
        .sheet(isPresented: $killSheet) { KillSwitchSheet() }
        .alert(item: Binding(get: { store.failure }, set: { store.failure = $0 })) { failure in
            Alert(title: Text("The engine refused this"), message: Text(failure.message), dismissButton: .default(Text("OK")))
        }
    }

    private func row(_ item: Page) -> some View {
        Label(item.title, systemImage: item.symbol)
            .badge(item == .queue ? store.waitingCount : 0)
            .tag(item)
    }

    @ViewBuilder
    private func detail(_ dashboard: Dashboard) -> some View {
        let dimmed = !store.link.isOnline
        Group {
            switch section ?? .overview {
            case .overview: OverviewView(dashboard: dashboard, go: { section = $0 })
            case .goal: GoalView()
            case .queue: QueueView()
            case .jobs: JobsView()
            case .channels: ChannelsView()
            case .activity: ActivityView(dashboard: dashboard)
            case .project: ProjectSettingsView()
            case .engine: EngineView()
            case .audit: AuditView()
            }
        }
        .disabled(dimmed && section != .overview && section != .activity && section != .goal)
        .opacity(dimmed ? 0.62 : 1)
        .animation(.easeOut(duration: 0.2), value: dimmed)
    }
}

/// Offline, halted and demo states sit above every page, so they cannot be missed.
struct Banners: View {
    @Environment(EngineStore.self) private var store
    @Binding var killSheet: Bool

    var body: some View {
        VStack(spacing: 0) {
            if let kill = store.dashboard?.engine.kill, kill.on {
                banner(color: Theme.killFill, symbol: "stop.circle.fill",
                       title: "Kill switch is on. Nothing runs, deploys, updates or posts.",
                       detail: "Since \(Fmt.clock(kill.at)) by \(kill.by ?? "?")\(kill.reason.map { ": \($0)" } ?? "")",
                       action: ("Turn off…", { killSheet = true }), ink: .white)
            }
            switch store.link {
            case .offline(let why):
                banner(color: Theme.warn, symbol: "bolt.horizontal.circle",
                       title: "\(store.settings.place) is offline. \(why).",
                       detail: offlineDetail, action: ("Try again", { store.reconnect() }), ink: .white)
            case .locked(let why):
                banner(color: Theme.warn, symbol: "lock",
                       title: "Not logged in to the engine: \(why).",
                       detail: "The app gets its token over SSH from \(store.settings.sshAlias). Check the alias in Settings.",
                       action: ("Try again", { store.reconnect() }), ink: .white)
            default:
                EmptyView()
            }
            if store.dashboard?.demo == true {
                banner(color: Theme.limeFill, symbol: "theatermasks",
                       title: "Demo data. Nothing here is real.",
                       detail: "Served by `growth serve --demo` from the fictional example project.", action: nil, ink: Theme.onLime)
            }
        }
    }

    private var offlineDetail: String {
        var parts: [String] = []
        if let since = store.offlineSince { parts.append("Offline since \(since.formatted(date: .omitted, time: .shortened))") }
        if let seen = store.lastSeen { parts.append("showing what it reported at \(seen.formatted(date: .omitted, time: .shortened))") }
        else { parts.append("no data yet") }
        return parts.joined(separator: ", ") + ". The engine catches up on its own when the PC is back."
    }

    private func banner(color: Color, symbol: String, title: String, detail: String, action: (String, () -> Void)?, ink: Color) -> some View {
        HStack(alignment: .center, spacing: 12) {
            Image(systemName: symbol).font(.title3)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.callout.weight(.semibold))
                Text(detail).font(.caption).opacity(0.86)
            }
            Spacer()
            if let action {
                Button(action.0, action: action.1).buttonStyle(.bordered).tint(ink)
            }
        }
        .foregroundStyle(ink)
        .padding(.horizontal, 18)
        .padding(.vertical, 10)
        .background(color)
    }
}

struct ConnectionFooter: View {
    @Environment(EngineStore.self) private var store

    var body: some View {
        HStack(spacing: 8) {
            StatusDot(color: color)
            VStack(alignment: .leading, spacing: 0) {
                Text(title).font(.caption.weight(.medium))
                Text(subtitle).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 0)
            SettingsLink { Image(systemName: "gearshape") }.buttonStyle(.borderless).help("Connection settings")
        }
    }

    private var color: Color {
        switch store.link {
        case .online: return store.dashboard?.engine.healthy == true ? Theme.good : Theme.warn
        case .connecting: return .secondary
        case .offline, .locked: return Theme.bad
        }
    }

    private var title: String {
        switch store.link {
        case .online: return "\(store.settings.place) online"
        case .connecting: return "Connecting…"
        case .offline: return "\(store.settings.place) offline"
        case .locked: return "Not logged in"
        }
    }

    private var subtitle: String {
        if let d = store.dashboard, store.link.isOnline { return "\(d.engine.host) · \(d.engine.commit.prefix(7))" }
        if let seen = store.lastSeen { return "Last seen \(seen.formatted(date: .omitted, time: .shortened))" }
        return store.settings.mode == .geekom ? "ssh \(store.settings.sshAlias)" : "127.0.0.1:\(store.settings.localPort)"
    }
}

struct FirstConnectView: View {
    @Environment(EngineStore.self) private var store

    var body: some View {
        VStack(spacing: 14) {
            switch store.link {
            case .connecting:
                ProgressView().controlSize(.large)
                Text("Opening the tunnel to \(store.settings.place)…").foregroundStyle(.secondary)
            case .offline(let why), .locked(let why):
                Image(systemName: "bolt.horizontal.circle").font(.system(size: 40)).foregroundStyle(Theme.warn)
                Text("\(store.settings.place) is not reachable").font(.title2.weight(.semibold))
                Text(why).foregroundStyle(.secondary)
                Text(store.settings.mode == .geekom
                     ? "The app connects with `ssh \(store.settings.sshAlias)` and needs `growth serve` running there (the growth-app service)."
                     : "Start an engine on this Mac: `python3 -m growth serve --demo`.")
                    .font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 420)
                HStack {
                    Button("Try again") { store.reconnect() }.keyboardShortcut(.defaultAction)
                    SettingsLink { Text("Connection settings…") }
                }
            case .online:
                ProgressView()
            }
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
