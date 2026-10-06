import SwiftUI

/// What is it doing, and when? Coming up first, then what it did and what that produced, in plain words.
struct ScheduleView: View {
    @Environment(EngineStore.self) private var store
    var go: (Page, String?) -> Void
    @State private var showDone = false

    var body: some View {
        ScrollView {
            if let project = store.project, let dashboard = store.dashboard {
                VStack(alignment: .leading, spacing: 20) {
                    HStack(alignment: .firstTextBaseline) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("What it does and when").font(.largeTitle.weight(.semibold))
                            Text("Everything the engine does for \(project.name) on its own. Nothing here posts on your behalf; Reddit replies always wait for you.")
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Change the schedule") { go(.edit, "schedule") }
                    }
                    Picker("", selection: $showDone) {
                        Text("Coming up").tag(false)
                        Text("Done recently").tag(true)
                    }
                    .pickerStyle(.segmented).labelsHidden().frame(width: 280)

                    if showDone {
                        DoneList(items: dashboard.activity.filter { $0.scope == project.id || $0.scope == "engine" })
                    } else {
                        ComingUp(project: project, upcoming: dashboard.upcoming.filter { $0.scope == project.id || $0.scope == "engine" },
                                 engineJobs: dashboard.engine.jobs)
                    }
                }
                .padding(28)
                .frame(maxWidth: 980, alignment: .leading)
            }
        }
    }
}

struct ComingUp: View {
    @Environment(EngineStore.self) private var store
    var project: Project
    var upcoming: [Upcoming]
    var engineJobs: [Job]

    var body: some View {
        let jobs = project.jobs + engineJobs
        let off = jobs.filter { !$0.enabled }
        VStack(alignment: .leading, spacing: 14) {
            if project.paused || store.killed {
                EmptyNote(symbol: "pause.circle", title: store.killed ? "The engine is stopped" : "\(project.name) is paused",
                          text: "Nothing below runs until you resume it under Safety.")
            }
            ForEach(groups(), id: \.0) { day, items in
                VStack(alignment: .leading, spacing: 0) {
                    Text(day).font(.headline).padding(.bottom, 6)
                    ForEach(items) { item in
                        TaskRow(time: time(item.at), job: jobs.first { $0.id == item.job }, fallbackName: item.name, scope: item.scope == "engine" ? "engine" : project.id)
                        Divider()
                    }
                }
            }
            if upcoming.isEmpty {
                EmptyNote(symbol: "moon.zzz", title: "Nothing scheduled", text: "Every task is switched off. Turn tasks on under Edit project, Schedule.")
            }
            if !off.isEmpty {
                DisclosureGroup("Switched off (\(off.count))") {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(off) { job in TaskRow(time: "off", job: job, fallbackName: job.name, scope: engineJobs.contains { $0.id == job.id } ? "engine" : project.id); Divider() }
                    }
                }
                .foregroundStyle(.secondary)
            }
        }
    }

    private func groups() -> [(String, [Upcoming])] {
        var order: [String] = []
        var by: [String: [Upcoming]] = [:]
        for item in upcoming {
            let day = dayName(item.at)
            if by[day] == nil { order.append(day) }
            by[day, default: []].append(item)
        }
        return order.map { ($0, by[$0]!) }
    }

    private func dayName(_ at: String) -> String {
        guard let d = Fmt.date(at) else { return "Later" }
        let cal = Calendar.current
        if cal.isDateInToday(d) { return "Today" }
        if cal.isDateInTomorrow(d) { return "Tomorrow" }
        return d.formatted(.dateTime.weekday(.wide).day().month(.wide))
    }

    private func time(_ at: String) -> String { Fmt.date(at)?.formatted(date: .omitted, time: .shortened) ?? "" }
}

/// One task: when, what it does in plain words, and on demand the details and "Run now".
struct TaskRow: View {
    @Environment(EngineStore.self) private var store
    var time: String
    var job: Job?
    var fallbackName: String
    var scope: String
    @State private var open = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 14) {
                Text(time).font(.body).figures().foregroundStyle(.secondary).frame(width: 64, alignment: .leading)
                VStack(alignment: .leading, spacing: 2) {
                    Text(job?.name ?? fallbackName).font(.body.weight(.medium))
                    if let does = job?.does, !does.isEmpty { Text(does).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
                }
                Spacer()
                if job?.usesAi == "required" { Text("uses AI").font(.caption).foregroundStyle(.secondary) }
                Button { withAnimation(.easeOut(duration: 0.18)) { open.toggle() } } label: {
                    Image(systemName: "chevron.down").rotationEffect(.degrees(open ? 0 : -90))
                }
                .buttonStyle(.borderless).help(open ? "Hide details" : "Details")
            }
            if open, let job {
                VStack(alignment: .leading, spacing: 6) {
                    LabeledContent("How often", value: job.when)
                    LabeledContent("Produces", value: job.produces)
                    if let last = job.last {
                        LabeledContent("Last time") {
                            Text("\(last.status == "ok" ? "Done" : last.status.capitalized) \(Fmt.ago(last.at)): \(last.summary)")
                                .foregroundStyle(Theme.status(last.status)).lineLimit(3)
                        }
                    }
                    HStack {
                        Spacer()
                        Button("Run now") { Task { await store.runNow(scope, job.id) } }
                            .disabled(!job.enabled || store.killed || store.working.contains("run:\(scope)/\(job.id)"))
                            .help("Runs once now, with the same checks as on schedule")
                    }
                }
                .font(.callout)
                .padding(.leading, 78)
            }
        }
        .padding(.vertical, 10)
    }
}

struct DoneList: View {
    var items: [Activity]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if items.isEmpty {
                EmptyNote(symbol: "clock", title: "Nothing ran yet", text: "Tasks appear here with what they produced once they run.")
            }
            ForEach(items.prefix(40)) { item in
                HStack(alignment: .firstTextBaseline, spacing: 14) {
                    Text(Fmt.clock(item.at)).font(.callout).figures().foregroundStyle(.secondary).frame(width: 150, alignment: .leading)
                    StatusDot(color: Theme.status(item.status))
                    VStack(alignment: .leading, spacing: 2) {
                        Text(item.name).font(.body.weight(.medium))
                        Text(result(item)).font(.callout).foregroundStyle(item.status == "ok" ? .secondary : Theme.status(item.status)).lineLimit(2)
                    }
                    Spacer()
                }
                .padding(.vertical, 9)
                Divider()
            }
        }
    }

    private func result(_ item: Activity) -> String {
        switch item.status {
        case "ok": return item.summary.isEmpty ? "Done." : "Done: \(item.summary)"
        case "missed": return "Skipped: \(item.summary)"
        default: return "\(item.status.capitalized): \(item.summary)"
        }
    }
}
