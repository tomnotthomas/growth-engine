import SwiftUI

/// Safety and limits: stop everything, pause this project or one kind of activity, and cap how much it does.
struct SafetyView: View {
    @Environment(EngineStore.self) private var store
    @Binding var killSheet: Bool

    var body: some View {
        if let project = store.project, let engine = store.dashboard?.engine {
            Form {
                Section {
                    HStack(spacing: 14) {
                        Image(systemName: store.killed ? "stop.circle.fill" : "stop.circle").font(.title).foregroundStyle(Theme.bad)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(store.killed ? "The engine is stopped" : "Stop everything").font(.body.weight(.semibold))
                            Text(store.killed ? "Nothing runs, publishes, updates or posts until you resume."
                                              : "Halts every task, publish and update at once, for all projects. The website stays online.")
                                .font(.callout).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button(store.killed ? "Resume…" : "Stop everything…") { killSheet = true }
                            .tint(store.killed ? Theme.good : Theme.killFill)
                            .buttonStyle(.borderedProminent)
                    }
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(project.paused ? "\(project.name) is paused" : "Pause \(project.name)").font(.body.weight(.medium))
                            Text("Stops this project's tasks only.").font(.callout).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button(project.paused ? "Resume project" : "Pause project") { Task { await store.setPaused(project.id, !project.paused) } }
                            .disabled(store.working.contains("pause:\(project.id)"))
                    }
                } header: { Text("Stop") }

                Section {
                    ForEach(project.channels) { channel in ChannelRow(project: project.id, channel: channel) }
                } header: { Text("What it may do, and how often") } footer: {
                    Text("A limit like 20/24h means at most 20 actions in any 24 hours. Some things are never automated, whatever you set: posting on Reddit or forums, unrequested messages or emails, captchas, new accounts, buying anything.")
                        .foregroundStyle(.secondary)
                }

                Section {
                    BudgetEditor(engine: engine)
                } header: { Text("AI use (your Claude subscription)") }
            }
            .formStyle(.grouped)
        }
    }
}

struct BudgetEditor: View {
    @Environment(EngineStore.self) private var store
    var engine: EngineInfo
    @State private var perDay = 0
    @State private var perWeek = 0
    @State private var loaded = false

    var body: some View {
        Group {
            Stepper("At most \(perDay) AI runs a day", value: $perDay, in: 0...24)
            Stepper("At most \(perWeek) AI runs a week", value: $perWeek, in: 0...100)
            HStack {
                Text("Used: \(engine.ai.today) today, \(engine.ai.week) this week\(engine.ai.window.map { "; only between \($0.joined(separator: " and "))" } ?? "")")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Save") { Task { _ = await store.setBudget(["max_runs_per_day": perDay, "max_runs_per_week": perWeek]) } }
                    .disabled(store.working.contains("budget") || (perDay == engine.ai.maxPerDay && perWeek == engine.ai.maxPerWeek))
            }
        }
        .onAppear {
            guard !loaded else { return }
            loaded = true
            perDay = engine.ai.maxPerDay
            perWeek = engine.ai.maxPerWeek
        }
    }
}
