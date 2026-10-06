import Charts
import SwiftUI

struct GoalView: View {
    @Environment(EngineStore.self) private var store

    var body: some View {
        ScrollView {
            if let project = store.project {
                VStack(alignment: .leading, spacing: 18) {
                    GoalSummary(project: project, onOpen: {})
                    if let goal = project.goal, !goal.daily.isEmpty {
                        Panel {
                            SectionTitle("Sign-ups per day", trailing: goal.neededPerDay.map { "Needed from now on: \(Fmt.number($0)) a day" })
                            DailyChart(points: goal.daily, needed: goal.neededPerDay).frame(height: 180)
                        }
                        HStack(alignment: .top, spacing: 18) {
                            Panel {
                                SectionTitle("Where they came from", trailing: "all time")
                                BreakdownList(rows: goal.channels.map { ($0.source.capitalized, $0.confirmed, true) })
                            }
                            Panel {
                                SectionTitle("Countries", trailing: goal.zone.map { "only \($0) count" })
                                BreakdownList(rows: goal.countries.map { ($0.country, $0.confirmed, $0.inZone) })
                                if let outside = goal.outsideZone, outside > 0 {
                                    Text("\(Fmt.number(outside)) sign-ups from other countries are not counted toward the goal.")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                }
                .padding(24)
                .frame(maxWidth: 1180, alignment: .leading)
            }
        }
    }
}

struct DailyChart: View {
    var points: [DayPoint]
    var needed: Double?

    var body: some View {
        Chart {
            ForEach(points) { p in
                BarMark(x: .value("Day", p.day, unit: .day), y: .value("Confirmed", p.confirmed))
                    .foregroundStyle(Theme.accent.opacity(0.85))
                    .cornerRadius(2)
            }
            if let needed {
                RuleMark(y: .value("Needed", needed))
                    .foregroundStyle(Theme.plan)
                    .lineStyle(StrokeStyle(lineWidth: 1.2, dash: [4, 4]))
                    .annotation(position: .top, alignment: .leading) {
                        Text("needed per day").font(.caption2).foregroundStyle(.secondary)
                    }
            }
        }
        .chartXAxis { AxisMarks(values: .stride(by: .day, count: 7)) { _ in AxisGridLine(); AxisValueLabel(format: .dateTime.day().month(.abbreviated)) } }
        .accessibilityLabel("Confirmed sign-ups per day for the last 30 days")
    }
}

/// A ranked list with proportional bars; rows outside the goal's zone are drawn quieter.
struct BreakdownList: View {
    var rows: [(String, Int, Bool)]

    var body: some View {
        let top = max(rows.map(\.1).max() ?? 1, 1)
        if rows.isEmpty {
            Text("No sign-ups yet.").font(.callout).foregroundStyle(.secondary)
        } else {
            VStack(spacing: 8) {
                ForEach(Array(rows.prefix(8).enumerated()), id: \.offset) { _, row in
                    HStack(spacing: 10) {
                        Text(row.0).font(.callout).frame(width: 96, alignment: .leading).lineLimit(1)
                        GeometryReader { geo in
                            RoundedRectangle(cornerRadius: 3)
                                .fill(row.2 ? Theme.accent.opacity(0.8) : Theme.plan.opacity(0.5))
                                .frame(width: max(3, geo.size.width * CGFloat(row.1) / CGFloat(top)))
                        }
                        .frame(height: 10)
                        Text(Fmt.number(row.1)).font(.callout).figures().frame(width: 64, alignment: .trailing)
                    }
                    .opacity(row.2 ? 1 : 0.7)
                }
            }
        }
    }
}
