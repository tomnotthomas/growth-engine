import SwiftUI

// MARK: Project settings: brand, domain, keywords, the launch switch

struct ProjectSettingsView: View {
    @Environment(EngineStore.self) private var store
    @State private var brand = ""
    @State private var baseURL = ""
    @State private var domainDecided = false
    @State private var indexable = false
    @State private var keywords: [Keyword] = []
    @State private var loadedFor = ""
    @State private var confirmLaunch = false

    var body: some View {
        if let project = store.project {
            Form {
                Section {
                    LaunchRow(project: project, confirm: $confirmLaunch)
                } header: { Text("Launch") }

                Section {
                    TextField("Brand name", text: $brand)
                    TextField("Site address", text: $baseURL, prompt: Text("https://example.com"))
                    Toggle("Domain decided", isOn: $domainDecided)
                    Toggle("Search engines may index the site", isOn: $indexable)
                } header: { Text("Brand and domain") } footer: {
                    Text("Indexing needs the decided domain and the legal-notice and privacy pages; the engine refuses anything else.")
                        .foregroundStyle(.secondary)
                }

                Section {
                    ForEach($keywords) { $kw in
                        HStack {
                            Text(kw.page).foregroundStyle(.secondary).frame(width: 110, alignment: .leading)
                            TextField("Primary keyword", text: $kw.primary)
                        }
                    }
                } header: { Text("Keywords") } footer: {
                    Text("Each page's primary keyword must appear in that page's title and heading, or the save is refused.")
                        .foregroundStyle(.secondary)
                }

                Section {
                    DeploySummary(deploy: project.deploy, launched: project.launched)
                } header: { Text("Deploys") }
            }
            .formStyle(.grouped)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save settings") { Task { await save(project) } }
                        .disabled(!dirty(project) || store.working.contains("settings:\(project.id)") || !store.link.isOnline)
                        .keyboardShortcut("s")
                }
            }
            .onAppear { load(project) }
            .onChange(of: project.id) { load(project) }
            .confirmationDialog("Launch \(project.name)?", isPresented: $confirmLaunch) {
                Button("Launch: allow production deploys") { Task { _ = await store.setSettings(project.id, ["launched": true]) } }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("The next deploy that passes its local check goes live on \(project.baseUrl). This is the go the engine waits for; it is recorded in the audit log.")
            }
        }
    }

    private func load(_ p: Project) {
        guard loadedFor != p.id + p.brandName + p.baseUrl else { return }
        loadedFor = p.id + p.brandName + p.baseUrl
        brand = p.brandName
        baseURL = p.baseUrl
        domainDecided = p.domainDecided
        indexable = p.indexable
        keywords = p.keywords
    }

    private func dirty(_ p: Project) -> Bool {
        brand != p.brandName || baseURL != p.baseUrl || domainDecided != p.domainDecided || indexable != p.indexable || keywords != p.keywords
    }

    private func save(_ p: Project) async {
        var body: [String: Any] = [:]
        if brand != p.brandName { body["brand_name"] = brand }
        if baseURL != p.baseUrl { body["base_url"] = baseURL }
        if domainDecided != p.domainDecided { body["domain_decided"] = domainDecided }
        if indexable != p.indexable { body["indexable"] = indexable }
        if keywords != p.keywords {
            body["keywords"] = keywords.map { k -> [String: Any] in
                var row: [String: Any] = ["page": k.page, "primary": k.primary]
                if let lang = k.lang { row["lang"] = lang }
                return row
            }
        }
        if await store.setSettings(p.id, body) { loadedFor = "" }
    }
}

struct LaunchRow: View {
    @Environment(EngineStore.self) private var store
    var project: Project
    @Binding var confirm: Bool

    var body: some View {
        HStack(alignment: .center, spacing: 14) {
            Image(systemName: project.launched ? "airplane.departure" : "lock.shield")
                .font(.title2).foregroundStyle(project.launched ? Theme.accent : .secondary).frame(width: 30)
            VStack(alignment: .leading, spacing: 2) {
                Text(project.launched ? "Launched: production deploys are on" : "Not launched: nothing goes public").font(.callout.weight(.semibold))
                Text(project.launched ? "Deploys pass a local check, go live, and roll back on their own if the live checks fail."
                                      : "Deploys are only checked on the engine's own machine; nothing is uploaded. Production waits for this switch.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if project.launched {
                Button("Stop production deploys") { Task { _ = await store.setSettings(project.id, ["launched": false]) } }
            } else {
                Button("Launch…") { confirm = true }
                    .buttonStyle(.borderedProminent).tint(Theme.accent)
                    .disabled(!project.domainDecided)
                    .help(project.domainDecided ? "Allow production deploys" : "Decide the domain first")
            }
        }
        .disabled(store.working.contains("settings:\(project.id)") || !store.link.isOnline)
    }
}

struct DeploySummary: View {
    var deploy: DeployState
    var launched: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            row("Local check", deploy.check)
            row("Production", deploy.production)
            if deploy.check == nil && deploy.production == nil {
                Text("No deploy yet. The deploy job checks the site locally whenever the built site changes.")
                    .font(.callout).foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private func row(_ title: String, _ record: DeployRecord?) -> some View {
        if let record {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                StatusDot(color: Theme.status(record.status))
                Text(title).font(.callout.weight(.medium)).frame(width: 90, alignment: .leading)
                Text("\(record.status ?? "") \(Fmt.ago(record.at))").font(.callout).foregroundStyle(.secondary)
                if let url = record.url, let link = URL(string: url) { Link(url, destination: link).font(.caption) }
                Spacer()
            }
        }
    }
}

// MARK: Engine: health, budgets, updates

struct EngineView: View {
    @Environment(EngineStore.self) private var store
    @State private var perDay = 0
    @State private var perWeek = 0
    @State private var perProject = 0
    @State private var loaded = false

    var body: some View {
        if let engine = store.dashboard?.engine {
            Form {
                Section("Health") {
                    LabeledContent("Host", value: engine.host)
                    LabeledContent("Version", value: "\(engine.version) · \(engine.commit)")
                    LabeledContent("Last scheduler tick", value: engine.lastTick.map { "\(Fmt.clock($0)) (\(Fmt.ago($0)))" } ?? "never")
                    LabeledContent("Audit log", value: engine.audit.intact ? "\(engine.audit.entries) entries, chain intact" : "BROKEN: \(engine.audit.problem)")
                }
                Section {
                    Stepper("AI runs per day: \(perDay)", value: $perDay, in: 0...24)
                    Stepper("AI runs per week: \(perWeek)", value: $perWeek, in: 0...100)
                    Stepper("AI runs per project per week: \(perProject)", value: $perProject, in: 0...100)
                    HStack {
                        Text("Used: \(engine.ai.today) today, \(engine.ai.week) this week\(engine.ai.window.map { " · runs only \($0.joined(separator: "–"))" } ?? "")")
                            .font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Button("Save budget") {
                            Task { _ = await store.setBudget(["max_runs_per_day": perDay, "max_runs_per_week": perWeek, "per_project_runs_per_week": perProject]) }
                        }
                        .disabled(store.working.contains("budget"))
                    }
                } header: { Text("AI budget (Claude subscription)") }
                Section("Engine jobs") {
                    ForEach(engine.jobs) { job in JobRow(scope: "engine", job: job).padding(.horizontal, -16) }
                }
                Section("Updates") {
                    if engine.updates.isEmpty {
                        Text("No update yet. The engine moves to the newest main commit whose checks passed and whose signature GitHub verified, and rolls back if its self-check fails.")
                            .font(.callout).foregroundStyle(.secondary)
                    }
                    ForEach(engine.updates.reversed()) { u in
                        HStack(spacing: 8) {
                            StatusDot(color: Theme.status(u.status))
                            Text(u.status).font(.callout.weight(.medium))
                            Text("\(u.from ?? "?") → \(u.to ?? "?")").font(.callout.monospaced()).foregroundStyle(.secondary)
                            Spacer()
                            Text(Fmt.ago(u.at)).font(.caption).foregroundStyle(.secondary)
                        }
                        .help(u.detail ?? "")
                    }
                }
            }
            .formStyle(.grouped)
            .onAppear {
                guard !loaded else { return }
                loaded = true
                perDay = engine.ai.maxPerDay
                perWeek = engine.ai.maxPerWeek
                perProject = engine.ai.perProjectPerWeek ?? engine.ai.maxPerWeek
            }
        }
    }
}

// MARK: Audit log

struct AuditView: View {
    @Environment(EngineStore.self) private var store
    @State private var page: AuditPage?
    @State private var loading = false

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                if let page {
                    StatusDot(color: page.intact ? Theme.good : Theme.bad)
                    Text(page.intact ? "Hash chain intact: \(page.entries) entries, none changed or removed" : "Hash chain broken: \(page.problem)")
                        .font(.callout)
                }
                Spacer()
                Button { Task { await load() } } label: { Label("Reload", systemImage: "arrow.clockwise") }.disabled(loading)
            }
            .padding(.horizontal, 16).padding(.vertical, 10)
            Divider()
            Table(page?.items ?? []) {
                TableColumn("When") { e in Text(Fmt.clock(e.at)).figures().foregroundStyle(.secondary) }.width(min: 120, ideal: 150)
                TableColumn("Who") { e in Text(e.actor) }.width(min: 60, ideal: 80)
                TableColumn("What") { e in Text(e.event).font(.callout.monospaced()) }.width(min: 140, ideal: 180)
                TableColumn("Scope") { e in Text(e.scope).foregroundStyle(.secondary) }.width(min: 70, ideal: 90)
                TableColumn("Detail") { e in
                    let text = e.detail.keys.sorted().map { "\($0): \(e.detail[$0]!.description)" }.joined(separator: " · ")
                    Text(text).foregroundStyle(.secondary).help(text)
                }
            }
        }
        .task { await load() }
    }

    private func load() async {
        loading = true
        page = await store.audit()
        loading = false
    }
}
