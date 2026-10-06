import AppKit
import SwiftUI

@main
struct GrowthEngineApp: App {
    @State private var store = EngineStore()
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        Window("Growth Engine", id: "control") {
            ContentView()
                .environment(store)
                .frame(minWidth: 940, minHeight: 620)
                .onAppear {
                    delegate.store = store
                    store.windowOpen = true
                    store.start()
                }
                .onDisappear { store.windowOpen = false }
        }
        .defaultSize(width: 1180, height: 780)
        .commands {
            CommandGroup(after: .toolbar) {
                Button("Refresh") { Task { await store.refresh() } }.keyboardShortcut("r")
            }
        }

        MenuBarExtra {
            MenuBarView().environment(store)
                .onAppear { store.start() }
        } label: {
            MenuBarLabel(link: store.link, killed: store.killed, waiting: store.waitingCount)
        }
        .menuBarExtraStyle(.window)

        Settings {
            ConnectionSettingsView().environment(store)
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    var store: EngineStore?

    func applicationDidFinishLaunching(_ notification: Notification) {
        if MainActor.assumeIsolated({ SelfTest.requested }) {
            Task { @MainActor in await SelfTest.run() }
        } else if let folder = MainActor.assumeIsolated({ Snapshot.folder }) {
            Task { @MainActor in await Snapshot.run(into: folder) }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        MainActor.assumeIsolated { store?.shutdown() }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
}

/// The menu bar icon carries the engine's state at a glance: running, halted, offline, or waiting for you.
struct MenuBarLabel: View {
    var link: LinkState
    var killed: Bool
    var waiting: Int

    var body: some View {
        let symbol: String = {
            if killed { return "stop.circle.fill" }
            switch link {
            case .online: return waiting > 0 ? "gauge.with.needle.fill" : "gauge.with.needle"
            case .connecting: return "gauge.with.dots.needle.0percent"
            case .offline, .locked: return "bolt.horizontal.circle"
            }
        }()
        HStack(spacing: 3) {
            Image(systemName: symbol)
            if waiting > 0, link.isOnline, !killed { Text("\(waiting)").font(.caption.monospacedDigit()) }
        }
        .accessibilityLabel(killed ? "Growth engine halted" : link.isOnline ? "Growth engine running" : "Growth engine offline")
    }
}
