import SwiftUI

enum Page: String, CaseIterable, Identifiable, Hashable {
    case start, schedule, queue, results, edit, safety, health, audit
    var id: String { rawValue }

    var title: String {
        switch self {
        case .start: return "Start here"
        case .schedule: return "What it does and when"
        case .queue: return "Waiting for you"
        case .results: return "Results"
        case .edit: return "Edit project"
        case .safety: return "Safety and limits"
        case .health: return "Engine health"
        case .audit: return "Activity record"
        }
    }

    var short: String {
        switch self {
        case .schedule: return "Schedule"
        default: return title
        }
    }

    var symbol: String {
        switch self {
        case .start: return "flag"
        case .schedule: return "calendar.badge.clock"
        case .queue: return "tray.full"
        case .results: return "chart.line.uptrend.xyaxis"
        case .edit: return "pencil"
        case .safety: return "hand.raised"
        case .health: return "stethoscope"
        case .audit: return "list.bullet.rectangle"
        }
    }
}

struct ContentView: View {
    @Environment(EngineStore.self) private var store
    @State private var section: Page?
    @State private var editTab = "basics"
    @State private var killSheet = false
    @AppStorage("showMore") private var showMore = false

    init(initialPage: Page = .start) {
        _section = State(initialValue: initialPage)
    }

    var body: some View {
        NavigationSplitView {
            List(selection: $section) {
                Section {
                    ForEach([Page.start, .schedule, .queue, .results, .edit]) { row($0) }
                }
                Section("More", isExpanded: $showMore) {
                    ForEach([Page.safety, .health, .audit]) { row($0) }
                }
            }
            .safeAreaInset(edge: .top) { ProjectSwitcher().padding(.horizontal, 10).padding(.top, 6) }
            .navigationSplitViewColumnWidth(min: 210, ideal: 230)
            .safeAreaInset(edge: .bottom) { ConnectionFooter().padding(12) }
        } detail: {
            Group {
                if store.dashboard != nil {
                    detail
                } else {
                    FirstConnectView()
                }
            }
            .safeAreaInset(edge: .top, spacing: 0) { Banners(killSheet: $killSheet) }
            .navigationTitle(section?.short ?? "Growth Engine")
        }
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button { Task { await store.refresh() } } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                    .help("Fetch the latest state now (⌘R)")
            }
        }
        .sheet(isPresented: $killSheet) { KillSwitchSheet() }
        .alert(item: Binding(get: { store.failure }, set: { store.failure = $0 })) { failure in
            Alert(title: Text("That did not work"), message: Text(failure.message), dismissButton: .default(Text("OK")))
        }
    }

    private func go(_ page: Page, _ tab: String?) {
        if let tab { editTab = tab }
        if [.safety, .health, .audit].contains(page) { showMore = true }
        section = page
    }

    private func row(_ item: Page) -> some View {
        Label(item.short, systemImage: item.symbol)
            .badge(item == .queue ? store.waitingCount : 0)
            .tag(item)
    }

    @ViewBuilder
    private var detail: some View {
        let dimmed = !store.link.isOnline
        Group {
            switch section ?? .start {
            case .start: StartHereView(go: go)
            case .schedule: ScheduleView(go: go)
            case .queue: QueueView()
            case .results: GoalView()
            case .edit: EditView(tab: $editTab)
            case .safety: SafetyView(killSheet: $killSheet)
            case .health: EngineView()
            case .audit: AuditView()
            }
        }
        .disabled(dimmed && ![.start, .schedule, .results].contains(section ?? .start))
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
                       title: "The engine is stopped. Nothing runs, publishes, updates or posts.",
                       detail: "Since \(Fmt.clock(kill.at)) by \(kill.by ?? "?")\(kill.reason.map { ": \($0)" } ?? "")",
                       action: ("Resume…", { killSheet = true }), ink: .white)
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
                HStack(spacing: 8) {
                    StatusDot(color: Theme.limeFill, size: 7)
                    Text("Demo: made-up example data. Changes here are thrown away.").font(.caption)
                    Spacer()
                }
                .padding(.horizontal, 18).padding(.vertical, 6)
                .background(Theme.panel)
                .overlay(alignment: .bottom) { Rectangle().fill(Theme.hairline).frame(height: 1) }
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
        case .online: return store.dashboard?.engine.healthy == true ? "\(store.settings.place): running" : "\(store.settings.place): scheduler idle"
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
