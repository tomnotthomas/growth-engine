import SwiftUI

/// How do I edit it? One place, five tabs, each with one Save.
struct EditView: View {
    @Environment(EngineStore.self) private var store
    @Binding var tab: String

    var body: some View {
        if let project = store.project {
            VStack(spacing: 0) {
                Picker("", selection: $tab) {
                    Text("Basics").tag("basics")
                    Text("Website text").tag("text")
                    Text("Schedule").tag("schedule")
                    Text("Connections").tag("connections")
                    Text("Launch").tag("launch")
                }
                .pickerStyle(.segmented).labelsHidden()
                .frame(maxWidth: 560)
                .padding(.top, 18).padding(.bottom, 4)
                Group {
                    switch tab {
                    case "text": WebsiteTextEditor(project: project)
                    case "schedule": ScheduleEditor(project: project)
                    case "connections": ConnectionsEditor(project: project)
                    case "launch": LaunchEditor(project: project)
                    default: BasicsEditor(project: project)
                    }
                }
                .id("\(project.id)/\(tab)")
            }
        }
    }
}

// MARK: Basics: name, address, goal, legal notice

struct BasicsEditor: View {
    @Environment(EngineStore.self) private var store
    var project: Project
    @State private var name = ""
    @State private var address = ""
    @State private var final = false
    @State private var target = 5000
    @State private var hasStart = false
    @State private var start = Date()
    @State private var days = 46
    @State private var countries = ""
    @State private var legal: [String: String] = [:]

    private let legalFields = [("name", "Your name or company"), ("street", "Street and number"), ("postcode_city", "Postcode and city"),
                               ("country", "Country"), ("email", "Contact email")]

    var body: some View {
        Form {
            Section {
                TextField("Product name", text: $name)
                TextField("Web address", text: $address, prompt: Text("https://my-product.com"))
                Toggle("This address is final", isOn: $final)
            } header: { Text("Product") } footer: {
                Text("Search engines and the launch wait until the address is final.").foregroundStyle(.secondary)
            }
            Section {
                Stepper(value: $target, in: 10...1_000_000, step: target < 1000 ? 10 : 500) {
                    LabeledContent("Sign-ups wanted", value: Fmt.number(target))
                }
                Toggle("The clock has started", isOn: $hasStart)
                if hasStart { DatePicker("Start (launch day)", selection: $start, displayedComponents: .date) }
                Stepper(value: $days, in: 7...365) { LabeledContent("Days to reach it", value: "\(days)") }
                TextField("Countries that count", text: $countries, prompt: Text("DE, AT, CH (empty: all)"))
            } header: { Text("Sign-up goal") } footer: {
                Text("Results compares the real sign-ups with an even pace from the start to the deadline.").foregroundStyle(.secondary)
            }
            Section {
                ForEach(legalFields, id: \.0) { key, label in
                    TextField(label, text: Binding(get: { legal[key] ?? "" }, set: { legal[key] = $0 }))
                }
            } header: { Text("Legal notice (Impressum)") } footer: {
                Text("Shown on the legal page. German law needs it before the site collects sign-ups.").foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .safeAreaInset(edge: .bottom) {
            SaveBar(label: "Save basics", busy: store.working.contains("settings:\(project.id)") || store.working.contains("goal:\(project.id)")) {
                Task { await save() }
            }
        }
        .onAppear(perform: load)
    }

    private func load() {
        name = project.brandName
        address = project.baseUrl
        final = project.domainDecided
        target = project.goal?.target ?? 5000
        if let s = project.goal?.start, !s.isEmpty { hasStart = true; start = Fmt.day(s) }
        days = project.goal?.days ?? 46
        countries = project.goal?.zone ?? ""
        legal = project.legal
    }

    private func save() async {
        var settings: [String: Any] = [:]
        if name != project.brandName { settings["brand_name"] = name }
        if address != project.baseUrl { settings["base_url"] = address }
        if final != project.domainDecided { settings["domain_decided"] = final }
        if !settings.isEmpty, !(await store.setSettings(project.id, settings)) { return }
        let day = hasStart ? start.formatted(.iso8601.year().month().day()) : ""
        let zone = countries.split(whereSeparator: { $0 == "," || $0 == " " }).map { String($0).uppercased() }
        guard await store.setGoal(project.id, ["target": target, "days": days, "start": day, "zone": zone]) else { return }
        let changed = legal.filter { project.legal[$0.key] != $0.value }
        if !changed.isEmpty { _ = await store.setLegal(project.id, changed) }
    }
}

// MARK: Website text

struct WebsiteTextEditor: View {
    @Environment(EngineStore.self) private var store
    var project: Project
    @State private var selected: String?
    @State private var draft: PageText?

    var body: some View {
        HStack(spacing: 0) {
            List(selection: $selected) {
                ForEach(project.pages) { page in
                    VStack(alignment: .leading, spacing: 1) {
                        Text(page.title.replacingOccurrences(of: " | {brand}", with: "")).lineLimit(1)
                        Text("\(page.path) · \(page.lang.uppercased())").font(.caption).foregroundStyle(.secondary)
                    }
                    .tag(page.id)
                }
            }
            .frame(width: 260)
            Divider()
            if let binding = Binding($draft) {
                Form {
                    Section {
                        TextField("Title (browser tab and search results)", text: binding.title)
                        TextField("Description (search results)", text: binding.description, axis: .vertical).lineLimit(2...4)
                    } header: { Text("How it shows up in search") }
                    Section {
                        TextField("Main heading", text: binding.headline, axis: .vertical).lineLimit(1...3)
                        TextField("Line under the heading", text: binding.sub, axis: .vertical).lineLimit(1...3)
                    } header: { Text("Top of the page") } footer: {
                        Text("{brand} is replaced by the product name. A heading in two lines: one line each.").foregroundStyle(.secondary)
                    }
                }
                .formStyle(.grouped)
                .safeAreaInset(edge: .bottom) {
                    SaveBar(label: "Save page text", busy: store.working.contains("page:\(project.id)/\(binding.wrappedValue.id)"),
                            disabled: draft == project.pages.first { $0.id == draft?.id }) {
                        Task { if let d = draft { _ = await store.setPageText(project.id, d) } }
                    }
                }
            } else {
                EmptyNote(symbol: "doc.text", title: "Pick a page", text: "Choose a page on the left to edit its texts. Changes go live with the next rebuild.")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .onChange(of: selected) { draft = project.pages.first { $0.id == selected } }
        .onAppear { if selected == nil { selected = project.pages.first?.id } }
    }
}

// MARK: Schedule

struct ScheduleEditor: View {
    @Environment(EngineStore.self) private var store
    var project: Project

    var body: some View {
        Form {
            Section {
                ForEach(project.jobs) { job in ScheduleRow(scope: project.id, job: job) }
            } header: { Text("Tasks for \(project.name)") } footer: {
                Text("Changes apply right away. Tasks that act outside (publishing, submissions) still pass every safety check.").foregroundStyle(.secondary)
            }
            if let engine = store.dashboard?.engine {
                Section("For all projects") {
                    ForEach(engine.jobs) { job in ScheduleRow(scope: "engine", job: job) }
                }
            }
        }
        .formStyle(.grouped)
    }
}

struct ScheduleRow: View {
    @Environment(EngineStore.self) private var store
    var scope: String
    var job: Job
    @State private var kind = "daily"
    @State private var hours = 6
    @State private var time = Date()
    @State private var weekday = "mon"

    private let weekdays = [("mon", "Monday"), ("tue", "Tuesday"), ("wed", "Wednesday"), ("thu", "Thursday"), ("fri", "Friday"), ("sat", "Saturday"), ("sun", "Sunday")]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(job.name).font(.body.weight(.medium))
                    Text(job.does).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                Toggle("", isOn: Binding(get: { job.enabled }, set: { on in Task { await store.setJob(scope, job.id, enabled: on) } }))
                    .toggleStyle(.switch).labelsHidden()
                    .disabled(store.working.contains("job:\(scope)/\(job.id)"))
                    .accessibilityLabel(job.enabled ? "Switch \(job.name) off" : "Switch \(job.name) on")
            }
            if job.enabled {
                HStack(spacing: 8) {
                    Picker("", selection: $kind) {
                        Text("Every few hours").tag("every")
                        Text("Every day").tag("daily")
                        Text("Every week").tag("weekly")
                    }
                    .labelsHidden().frame(width: 150)
                    if kind == "every" {
                        Stepper("every \(hours) h", value: $hours, in: 1...24)
                    } else {
                        if kind == "weekly" {
                            Picker("", selection: $weekday) { ForEach(weekdays, id: \.0) { Text($0.1).tag($0.0) } }.labelsHidden().frame(width: 120)
                        }
                        Text("at")
                        DatePicker("", selection: $time, displayedComponents: .hourAndMinute).labelsHidden()
                    }
                    Spacer()
                    if text != job.schedule {
                        Button("Apply") { Task { _ = await store.setSchedule(scope, job.id, text) } }
                            .disabled(store.working.contains("schedule:\(scope)/\(job.id)"))
                    }
                }
                .font(.callout)
            }
        }
        .padding(.vertical, 4)
        .onAppear(perform: load)
    }

    /// The engine's schedule text for the picked values.
    private var text: String {
        let c = Calendar.current.dateComponents([.hour, .minute], from: time)
        let clock = String(format: "%02d:%02d", c.hour ?? 0, c.minute ?? 0)
        switch kind {
        case "every": return "every \(hours)h"
        case "weekly": return "weekly \(weekday) \(clock)"
        default: return "daily \(clock)"
        }
    }

    private func load() {
        let parts = job.schedule.split(separator: " ").map(String.init)
        guard let first = parts.first else { return }
        kind = first
        if first == "every", parts.count == 2 {
            let n = Int(parts[1].dropLast()) ?? 6
            hours = parts[1].hasSuffix("m") ? 1 : parts[1].hasSuffix("d") ? 24 : n
            if parts[1].hasSuffix("m") { kind = "every" }
        }
        let clock = parts.last ?? "04:00"
        if first == "weekly", parts.count == 3 { weekday = parts[1] }
        let hm = clock.split(separator: ":").compactMap { Int($0) }
        if hm.count == 2 { time = Calendar.current.date(bySettingHour: hm[0], minute: hm[1], second: 0, of: Date()) ?? Date() }
    }
}

// MARK: Connections (secrets go into the engine's encrypted store; never shown again)

struct ConnectionsEditor: View {
    @Environment(EngineStore.self) private var store
    var project: Project

    private let help: [String: (String, String)] = [
        "CLOUDFLARE_API_TOKEN": ("Cloudflare token", "Cloudflare, My Profile, API Tokens, Create Token, Custom: Account · Workers Scripts · Edit and Account · D1 · Edit, nothing else."),
        "CLOUDFLARE_ACCOUNT_ID": ("Cloudflare account id", "On the Cloudflare dashboard's home page, right column."),
        "GROWTH_DIGEST_WEBHOOK": ("Weekly report notification (optional)", "An ntfy.sh topic address, to get the weekly report on your phone."),
        "GITHUB_TOKEN": ("GitHub token (optional)", "A read-only token; lets updates check GitHub more often."),
    ]

    var body: some View {
        Form {
            Section {
                ForEach(project.connections) { c in
                    SecretRow(project: project.id, connection: c, label: help[c.name]?.0 ?? label(c.name), hint: help[c.name]?.1 ?? hint(c.name))
                }
            } header: { Text("Connections") } footer: {
                Text("Each value is stored encrypted on the engine's computer and never shown again. Paste a new value to replace it.")
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
    }

    private func label(_ name: String) -> String {
        name.hasSuffix("_STATS_TOKEN") ? "Waitlist numbers token" : "Analytics key (PostHog)"
    }

    private func hint(_ name: String) -> String {
        name.hasSuffix("_STATS_TOKEN") ? "The same long random text as the waitlist's STATS_TOKEN; lets Results show real sign-ups."
                                      : "A PostHog personal API key with read access, for visits in the weekly report."
    }
}

struct SecretRow: View {
    @Environment(EngineStore.self) private var store
    var project: String
    var connection: Connection
    var label: String
    var hint: String
    @State private var value = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(label).font(.body.weight(.medium))
                Spacer()
                Label(connection.set ? "Connected" : "Not set", systemImage: connection.set ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(connection.set ? Theme.good : .secondary).font(.callout)
            }
            Text(hint).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                SecureField(connection.set ? "Paste a new value to replace it" : "Paste the value", text: $value)
                Button("Save") { Task { if await store.setSecret(project, connection.name, value) { value = "" } } }
                    .disabled(value.isEmpty || store.working.contains("secret:\(connection.name)"))
            }
        }
        .padding(.vertical, 4)
    }
}

// MARK: Launch

struct LaunchEditor: View {
    @Environment(EngineStore.self) private var store
    var project: Project
    @State private var confirm = false

    var body: some View {
        Form {
            Section { LaunchRow(project: project, confirm: $confirm) } header: { Text("Launch") } footer: {
                if !project.launched, !project.domainDecided {
                    Text("To launch, first mark the web address as final under Basics.").foregroundStyle(Theme.warn)
                }
            }
            Section("Publishing so far") { DeploySummary(deploy: project.deploy, launched: project.launched) }
        }
        .formStyle(.grouped)
        .confirmationDialog("Launch \(project.name)?", isPresented: $confirm) {
            Button("Launch: put the website online") { Task { _ = await store.setSettings(project.id, ["launched": true]) } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("From the next publish on, \(project.baseUrl) is online and updates on its own. You can stop it again here.")
        }
    }
}

struct SaveBar: View {
    var label: String
    var busy: Bool
    var disabled: Bool = false
    var action: () -> Void

    var body: some View {
        HStack {
            Spacer()
            if busy { ProgressView().controlSize(.small) }
            Button(label, action: action)
                .buttonStyle(.borderedProminent).tint(Theme.accent)
                .keyboardShortcut("s")
                .disabled(busy || disabled)
        }
        .padding(.horizontal, 20).padding(.vertical, 12)
        .background(.bar)
    }
}
