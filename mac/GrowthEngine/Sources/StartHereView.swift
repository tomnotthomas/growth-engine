import SwiftUI

/// The first screen: what this is, the one next step, and the path from a new project to a launched one.
struct StartHereView: View {
    @Environment(EngineStore.self) private var store
    var go: (Page, String?) -> Void

    var body: some View {
        ScrollView {
            if let project = store.project, let dashboard = store.dashboard {
                VStack(alignment: .leading, spacing: 22) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Start here").font(.largeTitle.weight(.semibold))
                        Text("The engine grows the waitlist for \(project.name) on its own: it keeps the website up to date, finds places to list it, and reports every week. You set it up once, approve a few things, and look at the results.")
                            .font(.title3).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: 720, alignment: .leading)
                    }
                    NextStepCard(project: project, go: go)
                    SetupChecklist(project: project, go: go)
                    RightNow(project: project, dashboard: dashboard, go: go)
                }
                .padding(28)
                .frame(maxWidth: 900, alignment: .leading)
            }
        }
    }
}

/// The one primary action on this screen.
struct NextStepCard: View {
    @Environment(EngineStore.self) private var store
    var project: Project
    var go: (Page, String?) -> Void

    var body: some View {
        Panel(padding: 22) {
            if let step = project.nextStep {
                Text("Next step").font(.callout.weight(.semibold)).foregroundStyle(Theme.accent)
                Text(step.title).font(.title2.weight(.semibold))
                Text(step.why).font(.body).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                HStack {
                    Button { act(step) } label: { Text(label(step)).padding(.horizontal, 6) }
                        .buttonStyle(.borderedProminent).tint(Theme.accent).controlSize(.large)
                        .keyboardShortcut(.defaultAction)
                        .disabled(store.working.contains("build:\(project.id)") || !store.link.isOnline)
                    if store.working.contains("build:\(project.id)") { ProgressView().controlSize(.small) }
                    Spacer()
                    Text("Step \(project.setupDone + 1) of \(project.setup.count)").font(.callout).foregroundStyle(.secondary)
                }
            } else {
                Text("All set").font(.callout.weight(.semibold)).foregroundStyle(Theme.accent)
                Text("\(project.name) is launched and runs on its own.").font(.title2.weight(.semibold))
                Text("Check Waiting for you when the menu bar shows a number; the rest happens by itself.")
                    .foregroundStyle(.secondary)
                Button("See what it does and when") { go(.schedule, nil) }
                    .buttonStyle(.borderedProminent).tint(Theme.accent).controlSize(.large)
            }
        }
    }

    private func label(_ step: SetupStep) -> String {
        switch step.go {
        case "build": return "Build the website now"
        case "edit-connections": return "Connect Cloudflare"
        case "edit-schedule": return "Turn on publishing"
        case "edit-launch": return "Review and launch"
        default: return "Open \(step.id == "legal" ? "the legal notice" : "the basics")"
        }
    }

    private func act(_ step: SetupStep) {
        switch step.go {
        case "build": Task { await store.buildNow(project.id) }
        case "edit-connections": go(.edit, "connections")
        case "edit-schedule": go(.edit, "schedule")
        case "edit-launch": go(.edit, "launch")
        default: go(.edit, "basics")
        }
    }
}

struct SetupChecklist: View {
    var project: Project
    var go: (Page, String?) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionTitle("From new project to launch", trailing: "\(project.setupDone) of \(project.setup.count) done")
            VStack(spacing: 0) {
                ForEach(Array(project.setup.enumerated()), id: \.element.id) { index, step in
                    let current = step.id == project.nextStep?.id
                    Button { open(step) } label: {
                        HStack(alignment: .firstTextBaseline, spacing: 12) {
                            Image(systemName: step.done ? "checkmark.circle.fill" : current ? "circle.inset.filled" : "circle")
                                .foregroundStyle(step.done ? Theme.good : current ? Theme.accent : .secondary)
                                .font(.body)
                            VStack(alignment: .leading, spacing: 2) {
                                Text("\(index + 1). \(step.title)").font(.body.weight(current ? .semibold : .regular))
                                    .foregroundStyle(step.done ? .secondary : .primary)
                                if current { Text(step.why).font(.callout).foregroundStyle(.secondary) }
                            }
                            Spacer()
                            Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
                        }
                        .padding(.vertical, 9)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    if index < project.setup.count - 1 { Divider() }
                }
            }
        }
    }

    private func open(_ step: SetupStep) {
        switch step.go {
        case "build": go(.schedule, nil)
        case "edit-connections": go(.edit, "connections")
        case "edit-schedule": go(.edit, "schedule")
        case "edit-launch": go(.edit, "launch")
        default: go(.edit, "basics")
        }
    }
}

/// Three plain facts, each a way in: what waits for you, what runs next, how sign-ups are doing.
struct RightNow: View {
    var project: Project
    var dashboard: Dashboard
    var go: (Page, String?) -> Void

    var body: some View {
        let waiting = project.drafts.filter { $0.status == "open" }.count
        let next = dashboard.upcoming.first { $0.scope == project.id }
        VStack(alignment: .leading, spacing: 10) {
            SectionTitle("Right now")
            fact("tray", waiting == 0 ? "Nothing is waiting for you." : "\(waiting) Reddit repl\(waiting == 1 ? "y is" : "ies are") waiting for you to post.",
                 "Waiting for you") { go(.queue, nil) }
            fact("clock", next.map { "Next: \($0.name.lowercased()), \(Fmt.clock($0.at).lowercased())." } ?? "Nothing is scheduled.",
                 "Schedule") { go(.schedule, nil) }
            fact("person.2", signups, "Results") { go(.results, nil) }
        }
    }

    private var signups: String {
        guard let goal = project.goal else { return "No sign-up goal yet." }
        guard let total = goal.confirmedTotal else { return "No sign-up numbers yet: they appear once the waitlist is online." }
        return "\(Fmt.number(total)) people signed up of your goal of \(Fmt.number(goal.target))."
    }

    private func fact(_ symbol: String, _ text: String, _ link: String, action: @escaping () -> Void) -> some View {
        HStack(spacing: 12) {
            Image(systemName: symbol).foregroundStyle(.secondary).frame(width: 20)
            Text(text).font(.body)
            Spacer()
            Button(link, action: action).buttonStyle(.link)
        }
        .padding(.vertical, 2)
    }
}

/// Which project am I in, and how do I switch? Always at the top of the sidebar.
struct ProjectSwitcher: View {
    @Environment(EngineStore.self) private var store
    @State private var open = false
    @State private var adding = false

    var body: some View {
        Button { open.toggle() } label: {
            HStack(spacing: 10) {
                RoundedRectangle(cornerRadius: 6).fill(Theme.limeFill)
                    .frame(width: 28, height: 28)
                    .overlay(Text(String(store.project?.name.prefix(1) ?? "?")).font(.headline).foregroundStyle(Theme.onLime))
                VStack(alignment: .leading, spacing: 1) {
                    Text(store.project?.name ?? "No project").font(.headline).lineLimit(1)
                    Text(store.project.map { $0.baseUrl.replacingOccurrences(of: "https://", with: "") } ?? "Add one to start")
                        .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer(minLength: 4)
                Image(systemName: "chevron.up.chevron.down").font(.caption).foregroundStyle(.secondary)
            }
            .padding(8)
            .background(Theme.panel, in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Theme.hairline))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help("Switch project, or add one")
        .accessibilityLabel("Project: \(store.project?.name ?? "none"). Switch project")
        .popover(isPresented: $open, arrowEdge: .bottom) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Projects").font(.caption).foregroundStyle(.secondary).padding(.horizontal, 8).padding(.top, 4)
                ForEach(store.dashboard?.projects ?? []) { p in
                    Button {
                        store.selectedProjectID = p.id
                        open = false
                    } label: {
                        HStack {
                            VStack(alignment: .leading, spacing: 1) {
                                Text(p.name)
                                Text(p.baseUrl.replacingOccurrences(of: "https://", with: "")).font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            if p.id == store.project?.id { Image(systemName: "checkmark").foregroundStyle(Theme.accent) }
                        }
                        .padding(.horizontal, 8).padding(.vertical, 5)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
                Divider().padding(.vertical, 4)
                Button {
                    open = false
                    adding = true
                } label: { Label("Add a project…", systemImage: "plus").padding(.horizontal, 8).padding(.vertical, 4) }
                .buttonStyle(.plain)
            }
            .padding(8)
            .frame(width: 260)
        }
        .sheet(isPresented: $adding) { AddProjectSheet() }
    }
}

struct AddProjectSheet: View {
    @Environment(EngineStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var address = "https://"

    private var id: String {
        name.lowercased().map { $0.isLetter || $0.isNumber ? String($0) : "-" }.joined()
            .split(separator: "-").joined(separator: "-")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Add a project").font(.title2.weight(.semibold))
            Text("It starts from the example project's pages, which you then rewrite under Edit project. Nothing runs outward until you turn it on.")
                .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Form {
                TextField("Product name", text: $name, prompt: Text("My Product"))
                TextField("Web address", text: $address, prompt: Text("https://my-product.com"))
                LabeledContent("Project id", value: id.isEmpty ? "–" : id)
            }
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Add project") {
                    Task { if await store.addProject(id: id, name: name, address: address) { dismiss() } }
                }
                .buttonStyle(.borderedProminent).tint(Theme.accent)
                .keyboardShortcut(.defaultAction)
                .disabled(id.isEmpty || !address.hasPrefix("https://") || store.working.contains("add-project"))
            }
        }
        .padding(24)
        .frame(width: 460)
    }
}
