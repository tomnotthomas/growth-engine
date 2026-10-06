import Charts
import SwiftUI

struct OverviewView: View {
    @Environment(EngineStore.self) private var store
    var dashboard: Dashboard
    var go: (Page) -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                if let project = store.project {
                    GoalSummary(project: project, onOpen: { go(.goal) })
                    HStack(alignment: .top, spacing: 18) {
                        WaitingPanel(items: dashboard.attention.filter { $0.project == project.id }, go: go)
                        NextUpPanel(items: dashboard.upcoming)
                    }
                }
                HealthStrip(engine: dashboard.engine, go: go)
            }
            .padding(24)
            .frame(maxWidth: 1180, alignment: .leading)
        }
    }
}

/// The goal as a line: where the sign-ups are against the plan, said in one sentence and drawn once.
struct GoalSummary: View {
    var project: Project
    var onOpen: () -> Void

    var body: some View {
        Panel(padding: 20) {
            if let goal = project.goal {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(Fmt.number(goal.confirmedTotal))
                        .font(.system(size: 34, weight: .semibold)).figures()
                    Text("of \(Fmt.number(goal.target)) \(goal.name)")
                        .font(.title3).foregroundStyle(.secondary)
                    Spacer()
                    PaceBadge(goal: goal)
                }
                Text(sentence(goal)).font(.callout).foregroundStyle(.secondary).figures()
                if !goal.daily.isEmpty {
                    CumulativeChart(points: goal.daily).frame(height: 190)
                }
                HStack(spacing: 28) {
                    figure("Last 7 days", Fmt.number(goal.last7Days))
                    figure("Per day now", Fmt.number(goal.avgPerDay7))
                    figure("Needed per day", Fmt.number(goal.neededPerDay))
                    figure("Referral k", goal.kFactor.map { Fmt.number($0, digits: 2) } ?? "–")
                    Spacer()
                    Button("Goal details", action: onOpen).buttonStyle(.link)
                }
            } else {
                EmptyNote(symbol: "flag", title: "No goal set",
                          text: "Add a target, a start date and the countries that count under Goal, and the tracker starts.")
            }
        }
    }

    private func sentence(_ g: Goal) -> String {
        var parts: [String] = []
        if let gap = g.gapToPlan { parts.append(gap >= 0 ? "\(Fmt.number(gap)) ahead of plan" : "\(Fmt.number(-gap)) behind plan") }
        if let projection = g.projection { parts.append("on pace for \(Fmt.number(projection))") }
        if let left = g.daysLeft, let deadline = g.deadline {
            parts.append("\(left) days left, deadline \(Fmt.day(deadline).formatted(.dateTime.day().month(.wide)))")
        }
        if parts.isEmpty, let status = g.status { parts.append(status) }
        if let zone = g.zone { parts.append("counting \(zone)") }
        if let from = g.numbersFrom { parts.append("numbers from \(from)") }
        return parts.joined(separator: " · ")
    }

    private func figure(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value).font(.title3.weight(.medium)).figures()
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct PaceBadge: View {
    var goal: Goal
    var body: some View {
        let (text, color): (String, Color) = {
            switch goal.onTrack {
            case .some(true): return ("On track", Theme.good)
            case .some(false): return ("Behind plan", Theme.warn)
            case .none: return (goal.start == nil || goal.start == "" ? "Clock not started" : "Starts \(goal.start ?? "")", .secondary)
            }
        }()
        HStack(spacing: 6) {
            StatusDot(color: color)
            Text(text).font(.callout.weight(.medium))
        }
        .padding(.horizontal, 10).padding(.vertical, 4)
        .background(color.opacity(0.12), in: Capsule())
    }
}

struct CumulativeChart: View {
    var points: [DayPoint]

    var body: some View {
        Chart {
            ForEach(points) { p in
                AreaMark(x: .value("Day", p.day, unit: .day), y: .value("Confirmed", p.cumulative))
                    .foregroundStyle(LinearGradient(colors: [Theme.accent.opacity(0.28), Theme.accent.opacity(0.02)], startPoint: .top, endPoint: .bottom))
                    .interpolationMethod(.monotone)
                LineMark(x: .value("Day", p.day, unit: .day), y: .value("Confirmed", p.cumulative), series: .value("Series", "Confirmed"))
                    .foregroundStyle(Theme.accent)
                    .lineStyle(StrokeStyle(lineWidth: 2.2))
                    .interpolationMethod(.monotone)
            }
            ForEach(points.filter { $0.plan != nil }) { p in
                LineMark(x: .value("Day", p.day, unit: .day), y: .value("Plan", p.plan ?? 0), series: .value("Series", "Plan"))
                    .foregroundStyle(Theme.plan)
                    .lineStyle(StrokeStyle(lineWidth: 1.2, dash: [4, 4]))
            }
        }
        .chartXAxis { AxisMarks(values: .stride(by: .day, count: 7)) { _ in AxisGridLine(); AxisValueLabel(format: .dateTime.day().month(.abbreviated)) } }
        .chartYAxis { AxisMarks(position: .leading) }
        .chartLegend(.hidden)
        .accessibilityLabel("Confirmed sign-ups over the last 30 days against the plan line")
    }
}

struct WaitingPanel: View {
    @Environment(EngineStore.self) private var store
    var items: [Attention]
    var go: (Page) -> Void

    var body: some View {
        Panel {
            SectionTitle("Waiting for you", trailing: items.isEmpty ? nil : "\(items.count)")
            if items.isEmpty {
                EmptyNote(symbol: "checkmark.circle", title: "Nothing needs you",
                          text: "Reddit drafts, failed jobs and blocked actions show up here.")
            } else {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(items.prefix(7)) { item in
                        Button { go(item.kind == "draft" ? .queue : .activity) } label: { row(item) }
                            .buttonStyle(.plain)
                        if item.id != items.prefix(7).last?.id { Divider() }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity)
    }

    private func row(_ item: Attention) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: symbol(item.kind)).foregroundStyle(color(item.kind)).frame(width: 18)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.title).font(.callout.weight(.medium)).lineLimit(1)
                Text(item.detail).font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
            Spacer()
            Text(Fmt.ago(item.at)).font(.caption).foregroundStyle(.secondary)
        }
        .padding(.vertical, 8)
        .contentShape(Rectangle())
    }

    private func symbol(_ kind: String) -> String {
        switch kind {
        case "draft": return "text.bubble"
        case "block": return "hand.raised"
        default: return "exclamationmark.triangle"
        }
    }

    private func color(_ kind: String) -> Color { kind == "draft" ? Theme.accent : kind == "block" ? Theme.warn : Theme.bad }
}

struct NextUpPanel: View {
    var items: [Upcoming]

    var body: some View {
        Panel {
            SectionTitle("Next up")
            if items.isEmpty {
                EmptyNote(symbol: "moon.zzz", title: "Nothing scheduled", text: "Every job is off or paused.")
            } else {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(items.prefix(7)) { item in
                        HStack {
                            Text(Fmt.clock(item.at)).font(.callout).figures().foregroundStyle(.secondary).frame(width: 130, alignment: .leading)
                            Text(Fmt.jobName(item.job)).font(.callout)
                            Spacer()
                            Text(item.scope).font(.caption).foregroundStyle(.secondary)
                        }
                        .padding(.vertical, 7)
                        if item.id != items.prefix(7).last?.id { Divider() }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity)
    }
}

struct HealthStrip: View {
    var engine: EngineInfo
    var go: (Page) -> Void

    var body: some View {
        Panel {
            SectionTitle("Engine health")
            HStack(alignment: .top, spacing: 32) {
                item(engine.healthy ? Theme.good : Theme.warn, "Scheduler",
                     engine.lastTick.map { "Last tick \(Fmt.ago($0))" } ?? "Never ticked")
                item(engine.ai.today < engine.ai.maxPerDay ? Theme.good : Theme.warn, "AI budget",
                     "\(engine.ai.today)/\(engine.ai.maxPerDay) today · \(engine.ai.week)/\(engine.ai.maxPerWeek) this week")
                item(engine.audit.intact ? Theme.good : Theme.bad, "Audit log",
                     engine.audit.intact ? "\(Fmt.number(engine.audit.entries)) entries, chain intact" : "Chain broken: \(engine.audit.problem)")
                item(Theme.status(engine.updates.last?.status), "Updates",
                     engine.updates.last.map { "\($0.status) \(Fmt.ago($0.at))" } ?? "Running \(engine.commit.prefix(7))")
                Spacer()
                Button("Details") { go(.engine) }.buttonStyle(.link)
            }
        }
    }

    private func item(_ color: Color, _ title: String, _ text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            StatusDot(color: color)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.callout.weight(.medium))
                Text(text).font(.caption).foregroundStyle(.secondary).figures()
            }
        }
    }
}
