import Charts
import SwiftUI

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
                    Text("of \(Fmt.number(goal.target)) sign-ups wanted")
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
                    figure("Per day lately", Fmt.number(goal.avgPerDay7))
                    figure("Needed per day", Fmt.number(goal.neededPerDay))
                    figure("Invites that joined, per person", goal.kFactor.map { Fmt.number($0, digits: 2) } ?? "–")
                    Spacer()
                    Button("Goal details", action: onOpen).buttonStyle(.link)
                }
            } else {
                EmptyNote(symbol: "flag", title: "No goal set",
                          text: "Set how many sign-ups you want, and by when, under Edit project, Basics.")
            }
        }
    }

    private func sentence(_ g: Goal) -> String {
        var parts: [String] = []
        if let gap = g.gapToPlan { parts.append(gap >= 0 ? "\(Fmt.number(gap)) more than an even pace would have" : "\(Fmt.number(-gap)) fewer than an even pace would have") }
        if let projection = g.projection { parts.append("at this pace you reach \(Fmt.number(projection))") }
        if let left = g.daysLeft, let deadline = g.deadline {
            parts.append("\(left) days left, deadline \(Fmt.day(deadline).formatted(.dateTime.day().month(.wide)))")
        }
        if parts.isEmpty, let status = g.status { parts.append(status) }
        if let zone = g.zone { parts.append("counting \(zone)") }
        if let from = g.numbersFrom, from != "waitlist ledger" { parts.append("numbers from \(from)") }
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
            case .some(false): return ("Behind pace", Theme.warn)
            case .none: return (goal.start == nil || goal.start == "" ? "Not started" : "Starts \(goal.start ?? "")", .secondary)
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
