import AppKit
import SwiftUI

/// `GrowthEngine --snapshot <folder>` renders the main pages against the configured engine into PNGs and
/// quits: a quick visual check (used for design review and the README) without driving the screen.
@MainActor
enum Snapshot {
    static var folder: URL? {
        let args = CommandLine.arguments
        guard let i = args.firstIndex(of: "--snapshot"), i + 1 < args.count else { return nil }
        return URL(fileURLWithPath: args[i + 1], isDirectory: true)
    }

    static func run(into folder: URL) async {
        let store = EngineStore()
        guard await store.refresh(), let dashboard = store.dashboard else {
            FileHandle.standardError.write(Data("snapshot: engine not reachable: \(store.link)\n".utf8))
            exit(1)
        }
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        guard let project = store.project, let goal = project.goal else { exit(1) }
        let pad: (AnyView) -> AnyView = { AnyView($0.padding(24)) }
        let pages: [(String, AnyView)] = [
            ("overview", pad(AnyView(VStack(alignment: .leading, spacing: 18) {
                GoalSummary(project: project, onOpen: {})
                HStack(alignment: .top, spacing: 18) {
                    WaitingPanel(items: dashboard.attention, go: { _ in })
                    NextUpPanel(items: dashboard.upcoming)
                }
                HealthStrip(engine: dashboard.engine, go: { _ in })
            }))),
            ("goal", pad(AnyView(VStack(alignment: .leading, spacing: 18) {
                Panel {
                    SectionTitle("Confirmed per day", trailing: "Needed: \(Fmt.number(goal.neededPerDay)) a day")
                    DailyChart(points: goal.daily, needed: goal.neededPerDay).frame(height: 180)
                }
                HStack(alignment: .top, spacing: 18) {
                    Panel { SectionTitle("By channel"); BreakdownList(rows: goal.channels.map { ($0.source.capitalized, $0.confirmed, true) }) }
                    Panel { SectionTitle("By country", trailing: goal.zone); BreakdownList(rows: goal.countries.map { ($0.country, $0.confirmed, $0.inZone) }) }
                }
            }))),
            ("queue", pad(AnyView(VStack(spacing: 16) { ForEach(project.drafts) { DraftCard(project: project.id, draft: $0) } }))),
            ("jobs", pad(AnyView(JobTable(scope: project.id, jobs: project.jobs)))),
            ("menubar", AnyView(MenuBarView())),
        ]
        for scheme in [ColorScheme.light, .dark] {
            for (name, view) in pages {
                let size = name == "menubar" ? CGSize(width: 320, height: 260) : CGSize(width: 1000, height: name == "overview" ? 860 : 640)
                let content = view
                    .environment(store)
                    .frame(width: size.width, height: size.height, alignment: .topLeading)
                    .background(scheme == .dark ? Color(white: 0.11) : Color.white)
                    .environment(\.colorScheme, scheme)
                let renderer = ImageRenderer(content: content)
                renderer.scale = 2
                if let image = renderer.nsImage, let tiff = image.tiffRepresentation,
                   let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:]) {
                    try? png.write(to: folder.appendingPathComponent("\(name)-\(scheme == .dark ? "dark" : "light").png"))
                }
            }
        }
        exit(0)
    }
}
