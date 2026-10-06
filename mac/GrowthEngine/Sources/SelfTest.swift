import AppKit
import Foundation

/// `GrowthEngine --selftest` drives every control of the app against the configured engine (a scratch home,
/// never a real one) through the same client code the buttons use, and checks that each change took effect.
/// Exit 0 when every step passed. CI runs it against a throwaway home.
@MainActor
enum SelfTest {
    static var requested: Bool { CommandLine.arguments.contains("--selftest") }

    static func run() async {
        let store = EngineStore.fromArguments()
        var failures: [String] = []
        func check(_ name: String, _ ok: Bool) {
            print("\(ok ? "ok  " : "FAIL") \(name)")
            if !ok { failures.append(name) }
        }
        func project() -> Project? { store.dashboard?.projects.first { $0.id == store.selectedProjectID } }

        check("connects and loads the dashboard", await store.refresh())
        guard let first = store.dashboard?.projects.first else { print("FAIL no project"); exit(1) }
        check("the engine is not demo data", store.dashboard?.demo == false)
        store.selectedProjectID = first.id
        let pid = first.id

        check("add a project", await store.addProject(id: "selftest-shop", name: "Selftest Shop", address: "https://selftest-shop.com"))
        check("the new project is selected", project()?.name == "Selftest Shop")
        store.selectedProjectID = pid

        check("save basics: name and address", await store.setSettings(pid, ["brand_name": "Kiln Test", "base_url": "https://kiln-test.com"]))
        check("basics took effect", project()?.brandName == "Kiln Test" && project()?.baseUrl == "https://kiln-test.com")
        check("save the goal", await store.setGoal(pid, ["target": 1200, "days": 30, "start": "2026-10-01", "zone": ["DE"]]))
        check("goal took effect", project()?.goal?.target == 1200 && project()?.goal?.days == 30)
        check("save the legal notice", await store.setLegal(pid, ["name": "Self Test GmbH", "street": "Teststr. 1"]))
        check("legal notice took effect", project()?.legal["name"] == "Self Test GmbH")

        if var page = project()?.pages.first {
            page.description = "Edited by the self-test."
            check("save website text", await store.setPageText(pid, page))
            check("website text took effect", project()?.pages.first { $0.id == page.id }?.description == "Edited by the self-test.")
        } else { check("the project has editable pages", false) }

        if let job = project()?.jobs.first(where: { $0.kind == "fetch-data" }) {
            check("change a schedule", await store.setSchedule(pid, job.id, "every 3h"))
            check("schedule took effect", project()?.jobs.first { $0.id == job.id }?.when == "Every 3 hours")
            await store.setJob(pid, job.id, enabled: false)
            check("switch a task off", project()?.jobs.first { $0.id == job.id }?.enabled == false)
            await store.setJob(pid, job.id, enabled: true)
            check("switch a task on", project()?.jobs.first { $0.id == job.id }?.enabled == true)
        }

        if let channel = project()?.channels.first {
            await store.setChannel(pid, channel.id, paused: true, rateLimit: "7/24h")
            check("pause a channel and set its limit", project()?.channels.first { $0.id == channel.id }.map { $0.paused && $0.rateLimit == "7/24h" } == true)
            await store.setChannel(pid, channel.id, paused: false, rateLimit: "")
        }
        check("save the AI budget", await store.setBudget(["max_runs_per_day": 2, "max_runs_per_week": 9]))
        check("budget took effect", store.dashboard?.engine.ai.maxPerDay == 2)
        check("store a connection secret", await store.setSecret(pid, "CLOUDFLARE_ACCOUNT_ID", "selftest-account"))
        check("secret shows as connected, value never returned", project()?.connections.first { $0.name == "CLOUDFLARE_ACCOUNT_ID" }?.set == true)

        await store.buildNow(pid)
        var built = false
        for _ in 0..<40 {
            try? await Task.sleep(for: .seconds(1))
            await store.refresh()
            if project()?.setup.first(where: { $0.id == "build" })?.done == true { built = true; break }
        }
        check("build the website now", built)

        await store.setPaused(pid, true)
        check("pause the project", project()?.paused == true)
        await store.setPaused(pid, false)
        await store.setKill(true, reason: "self-test")
        check("stop everything", store.killed)
        await store.setKill(false, reason: "")
        check("resume", !store.killed)
        let refused = await store.setSettings(pid, ["launched": true])
        check("launch is refused before the address is final", !refused)
        store.failure = nil
        let audit = await store.audit(limit: 100)
        check("every change is in the activity record", (audit?.items.count ?? 0) >= 15 && audit?.intact == true)

        print(failures.isEmpty ? "selftest passed" : "selftest FAILED: \(failures.joined(separator: "; "))")
        exit(failures.isEmpty ? 0 : 1)
    }
}

extension EngineStore {
    /// `--port N` and `--token-command "..."` point a self-test or snapshot run at an engine on this Mac
    /// without touching the app's saved connection.
    static func fromArguments() -> EngineStore {
        let store = EngineStore()
        let args = CommandLine.arguments
        func value(_ flag: String) -> String? { args.firstIndex(of: flag).flatMap { $0 + 1 < args.count ? args[$0 + 1] : nil } }
        if let port = value("--port").flatMap(Int.init) {
            var s = ConnectionSettings()
            s.mode = .local
            s.localPort = port
            s.localTokenCommand = value("--token-command") ?? ""
            s.localToken = value("--token") ?? ""
            store.settings = s
        }
        return store
    }
}
