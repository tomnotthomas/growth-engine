import AppKit
import SwiftUI

// MARK: Kill switch

struct KillSwitchButton: View {
    @Environment(EngineStore.self) private var store
    @Binding var killSheet: Bool

    var body: some View {
        Button { killSheet = true } label: {
            Label(store.killed ? "Stopped" : "Stop everything", systemImage: store.killed ? "stop.circle.fill" : "stop.circle")
        }
        .tint(Theme.killFill)
        .foregroundStyle(store.killed ? Theme.bad : .primary)
        .disabled(!store.link.isOnline)
        .help(store.killed ? "The engine is halted. Click to resume." : "Stop every job, deploy, update and outward action at once")
    }
}

/// Stopping and resuming the whole engine both ask once: stopping is cheap to undo, but resuming
/// lets every queued action go again.
struct KillSwitchSheet: View {
    @Environment(EngineStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var reason = ""

    var body: some View {
        let on = store.killed
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 12) {
                Image(systemName: on ? "play.circle.fill" : "stop.circle.fill").font(.system(size: 30)).foregroundStyle(on ? Theme.good : Theme.bad)
                Text(on ? "Resume the engine?" : "Stop everything?").font(.title2.weight(.semibold))
            }
            Text(on ? "Jobs start again on the next tick. Slots missed while halted follow each job's catch-up rule; nothing that already ran runs twice."
                    : "No job, deploy, engine update or outward action runs until you resume. Running work finishes its current step. The public site and the waitlist stay up.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if !on {
                TextField("Reason (goes into the audit log)", text: $reason)
            }
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }.keyboardShortcut(.cancelAction)
                Button(on ? "Resume engine" : "Stop everything") {
                    Task {
                        await store.setKill(!on, reason: reason)
                        dismiss()
                    }
                }
                .keyboardShortcut(.defaultAction)
                .tint(on ? Theme.good : Theme.killFill)
                .buttonStyle(.borderedProminent)
                .disabled(store.working.contains("kill"))
            }
        }
        .padding(24)
        .frame(width: 440)
    }
}

// MARK: Menu bar

struct MenuBarView: View {
    @Environment(EngineStore.self) private var store
    @Environment(\.openWindow) private var openWindow
    @State private var killSheet = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                StatusDot(color: dotColor, size: 9)
                Text(headline).font(.headline)
                Spacer()
            }
            if let project = store.project, let goal = project.goal, store.dashboard != nil {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(Fmt.number(goal.confirmedTotal)).font(.title2.weight(.semibold)).figures()
                        Text("of \(Fmt.number(goal.target))").foregroundStyle(.secondary)
                        Spacer()
                        PaceBadge(goal: goal)
                    }
                    ProgressView(value: Double(goal.confirmedTotal ?? 0), total: Double(max(goal.target, 1)))
                        .tint(Theme.accent)
                    Text("\(project.name) · \(Fmt.number(goal.last7Days)) in the last 7 days").font(.caption).foregroundStyle(.secondary)
                }
            }
            if store.waitingCount > 0 {
                Label("\(store.waitingCount) Reddit repl\(store.waitingCount == 1 ? "y" : "ies") waiting for you to post", systemImage: "text.bubble")
                    .font(.callout)
            }
            Divider()
            HStack {
                Button("Open Control Center") {
                    openWindow(id: "control")
                    NSApp.activate(ignoringOtherApps: true)
                }
                .keyboardShortcut("o")
                Spacer()
                Button(store.killed ? "Resume…" : "Stop everything…") { killSheet = true }
                    .tint(Theme.killFill)
                    .disabled(!store.link.isOnline)
            }
            HStack {
                SettingsLink { Text("Settings…") }.buttonStyle(.link)
                Spacer()
                Button("Quit") { NSApp.terminate(nil) }.buttonStyle(.link)
            }
            .font(.caption)
        }
        .padding(14)
        .frame(width: 320)
        .sheet(isPresented: $killSheet) { KillSwitchSheet().environment(store) }
    }

    private var dotColor: Color {
        if store.killed { return Theme.bad }
        switch store.link {
        case .online: return store.dashboard?.engine.healthy == true ? Theme.good : Theme.warn
        case .connecting: return .secondary
        default: return Theme.bad
        }
    }

    private var headline: String {
        if store.killed { return "Engine halted" }
        switch store.link {
        case .online: return store.dashboard?.engine.healthy == true ? "Engine running" : "Engine needs a look"
        case .connecting: return "Connecting to \(store.settings.place)…"
        case .offline: return "\(store.settings.place) offline" + (store.lastSeen.map { ", last seen \($0.formatted(date: .omitted, time: .shortened))" } ?? "")
        case .locked: return "Not logged in"
        }
    }
}

// MARK: Connection settings

struct ConnectionSettingsView: View {
    @Environment(EngineStore.self) private var store
    @State private var draft = ConnectionSettings()

    var body: some View {
        Form {
            Picker("Engine", selection: $draft.mode) {
                ForEach(ConnectionSettings.Mode.allCases) { Text($0.label).tag($0) }
            }
            .pickerStyle(.radioGroup)
            if draft.mode == .geekom {
                TextField("SSH alias", text: $draft.sshAlias)
                TextField("Engine port on the GEEKOM", value: $draft.remotePort, format: .number.grouping(.never))
                TextField("Local tunnel port", value: $draft.tunnelPort, format: .number.grouping(.never))
                TextField("Token command", text: $draft.tokenCommand)
                Text("The engine only listens on 127.0.0.1 there. The app opens `ssh -L` with your key and asks the engine for its login token over the same SSH login; nothing is stored on this Mac.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            } else {
                TextField("Port", value: $draft.localPort, format: .number.grouping(.never))
                SecureField("Token", text: $draft.localToken)
                TextField("Or a command that prints the token", text: $draft.localTokenCommand,
                          prompt: Text("cd ~/growth-engine && python3 -m growth app-token"))
                Text("For `python3 -m growth serve --demo` (token: demo), or an engine running on this Mac (use the token command).")
                    .font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                Spacer()
                Button("Connect") { store.apply(draft) }.keyboardShortcut(.defaultAction).disabled(draft == store.settings)
            }
        }
        .padding(20)
        .frame(width: 480)
        .onAppear { draft = store.settings }
    }
}
