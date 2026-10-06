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
                            SectionTitle("Confirmed per day", trailing: goal.neededPerDay.map { "Needed: \(Fmt.number($0)) a day" })
                            DailyChart(points: goal.daily, needed: goal.neededPerDay).frame(height: 180)
                        }
                        HStack(alignment: .top, spacing: 18) {
                            Panel {
                                SectionTitle("By channel", trailing: "all time")
                                BreakdownList(rows: goal.channels.map { ($0.source.capitalized, $0.confirmed, true) })
                            }
                            Panel {
                                SectionTitle("By country", trailing: goal.zone.map { "counting \($0)" })
                                BreakdownList(rows: goal.countries.map { ($0.country, $0.confirmed, $0.inZone) })
                                if let outside = goal.outsideZone, outside > 0 {
                                    Text("\(Fmt.number(outside)) confirmed outside the zone are not counted toward the goal.")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                    GoalEditor(project: project)
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

struct GoalEditor: View {
    @Environment(EngineStore.self) private var store
    var project: Project
    @State private var target = ""
    @State private var start = ""
    @State private var days = ""
    @State private var countries = ""
    @State private var loadedFor = ""

    var body: some View {
        Panel {
            SectionTitle("Targets")
            Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 10) {
                GridRow {
                    Text("Target").foregroundStyle(.secondary)
                    TextField("5000", text: $target).frame(width: 120).figures()
                    Text("confirmed sign-ups").foregroundStyle(.secondary)
                }
                GridRow {
                    Text("Start").foregroundStyle(.secondary)
                    TextField("YYYY-MM-DD", text: $start).frame(width: 120).figures()
                    Text("the launch day; empty keeps the clock stopped").foregroundStyle(.secondary)
                }
                GridRow {
                    Text("Days").foregroundStyle(.secondary)
                    TextField("46", text: $days).frame(width: 120).figures()
                    Text("from the start to the deadline").foregroundStyle(.secondary)
                }
                GridRow {
                    Text("Countries").foregroundStyle(.secondary)
                    TextField("DE, AT, CH", text: $countries).frame(width: 120)
                    Text("only these count toward the goal; empty counts all").foregroundStyle(.secondary)
                }
            }
            .font(.callout)
            HStack {
                Spacer()
                Button("Save targets") { Task { await save() } }
                    .disabled(store.working.contains("goal:\(project.id)") || !store.link.isOnline)
                    .keyboardShortcut("s")
            }
        }
        .onAppear(perform: load)
        .onChange(of: project.id) { load() }
    }

    private func load() {
        guard loadedFor != project.id else { return }
        loadedFor = project.id
        target = project.goal.map { String($0.target) } ?? ""
        start = project.goal?.start ?? ""
        days = project.goal?.days.map(String.init) ?? ""
        countries = project.goal?.zone ?? ""
    }

    private func save() async {
        var body: [String: Any] = ["start": start.trimmingCharacters(in: .whitespaces)]
        if let t = Int(target.filter(\.isNumber)) { body["target"] = t }
        if let d = Int(days.filter(\.isNumber)) { body["days"] = d }
        body["zone"] = countries.split(whereSeparator: { $0 == "," || $0 == " " }).map { String($0).uppercased() }
        if await store.setGoal(project.id, body) { loadedFor = "" ; load() }
    }
}
