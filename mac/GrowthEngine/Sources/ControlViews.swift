import AppKit
import SwiftUI

// MARK: Waiting for you (the Reddit queue)

struct QueueView: View {
    @Environment(EngineStore.self) private var store

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("The engine never posts these. Read, edit in Reddit, post from your own account, and say you build it.")
                    .font(.callout).foregroundStyle(.secondary)
                if let project = store.project {
                    if project.drafts.isEmpty {
                        Panel {
                            EmptyNote(symbol: "tray", title: "No drafts waiting",
                                      text: "When a thread is worth answering, a draft appears here with the reason it was picked.")
                        }
                    }
                    ForEach(project.drafts) { draft in DraftCard(project: project.id, draft: draft) }
                }
            }
            .padding(24)
            .frame(maxWidth: 860, alignment: .leading)
        }
    }
}

struct DraftCard: View {
    @Environment(EngineStore.self) private var store
    var project: String
    var draft: Draft
    @State private var copied = false

    var body: some View {
        Panel(padding: 18) {
            HStack(alignment: .firstTextBaseline) {
                Text(draft.community).font(.caption.weight(.semibold)).foregroundStyle(Theme.accent)
                if draft.status == "approved" {
                    Text("Approved, waiting for you to post").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Text(Fmt.ago(draft.createdAt)).font(.caption).foregroundStyle(.secondary)
            }
            Text(draft.title).font(.title3.weight(.semibold))
            Text(draft.why).font(.callout).foregroundStyle(.secondary)
            Text(draft.text)
                .font(.body)
                .textSelection(.enabled)
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Theme.hairline))
            HStack(spacing: 10) {
                Button {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(draft.text, forType: .string)
                    if let url = URL(string: draft.threadUrl), url.scheme == "https" { NSWorkspace.shared.open(url) }
                    copied = true
                } label: { Label(copied ? "Copied" : "Copy and open thread", systemImage: copied ? "checkmark" : "doc.on.doc") }
                .buttonStyle(.borderedProminent).tint(Theme.accent)
                if draft.status == "open" {
                    Button("Approve") { Task { await store.decide(project, draft.id, "approved") } }
                }
                Button("Mark posted") { Task { await store.decide(project, draft.id, "posted") } }
                Spacer()
                Button("Reject", role: .destructive) { Task { await store.decide(project, draft.id, "rejected") } }
            }
            .disabled(store.working.contains("draft:\(draft.id)") || !store.link.isOnline)
        }
    }
}

// MARK: Jobs

struct JobsView: View {
    @Environment(EngineStore.self) private var store

    var body: some View {
        ScrollView {
            if let project = store.project {
                VStack(alignment: .leading, spacing: 16) {
                    Panel {
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(project.paused ? "\(project.name) is paused" : "\(project.name) is running").font(.headline)
                                Text(project.paused ? "No job of this project runs until you resume it. Missed slots follow each job's catch-up rule."
                                                    : "Pausing stops every job of this project; the kill switch stops the whole engine.")
                                    .font(.callout).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button(project.paused ? "Resume project" : "Pause project") {
                                Task { await store.setPaused(project.id, !project.paused) }
                            }
                            .disabled(store.working.contains("pause:\(project.id)"))
                        }
                    }
                    JobTable(scope: project.id, jobs: project.jobs)
                }
                .padding(24)
                .frame(maxWidth: 1180, alignment: .leading)
            }
        }
    }
}

struct JobTable: View {
    @Environment(EngineStore.self) private var store
    var scope: String
    var jobs: [Job]

    var body: some View {
        Panel(padding: 0) {
            VStack(spacing: 0) {
                ForEach(jobs) { job in
                    JobRow(scope: scope, job: job)
                    if job.id != jobs.last?.id { Divider().padding(.leading, 16) }
                }
            }
        }
    }
}

struct JobRow: View {
    @Environment(EngineStore.self) private var store
    var scope: String
    var job: Job

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            StatusDot(color: job.enabled ? Theme.status(job.last?.status) : .secondary).padding(.top, 6)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Text(Fmt.jobName(job.id)).font(.callout.weight(.semibold))
                    Text(job.schedule).font(.caption).foregroundStyle(.secondary)
                    if job.usesAi == "required" { Text("AI").font(.caption2.weight(.semibold)).foregroundStyle(Theme.accent) }
                }
                Text(job.description).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                if let last = job.last {
                    Text("\(last.status) \(Fmt.ago(last.at)): \(last.summary)").font(.caption).foregroundStyle(Theme.status(last.status)).lineLimit(2)
                }
            }
            Spacer(minLength: 20)
            VStack(alignment: .trailing, spacing: 3) {
                Text(job.nextAt.map { Fmt.clock($0) } ?? (job.paused ? "paused" : "off")).font(.callout).figures()
                Text("next").font(.caption2).foregroundStyle(.secondary)
            }
            .frame(width: 150, alignment: .trailing)
            Button("Run now") { Task { await store.runNow(scope, job.id) } }
                .disabled(!job.enabled || store.killed || store.working.contains("run:\(scope)/\(job.id)"))
                .help("Runs once now under the same lock, ledger and guards as the schedule")
            Toggle("", isOn: Binding(get: { job.enabled }, set: { on in Task { await store.setJob(scope, job.id, enabled: on) } }))
                .toggleStyle(.switch).labelsHidden()
                .disabled(store.working.contains("job:\(scope)/\(job.id)"))
                .help(job.enabled ? "Switch this job off" : "Switch this job on")
        }
        .padding(.horizontal, 16).padding(.vertical, 12)
    }
}

// MARK: Channels

struct ChannelsView: View {
    @Environment(EngineStore.self) private var store

    var body: some View {
        ScrollView {
            if let project = store.project {
                VStack(alignment: .leading, spacing: 16) {
                    Text("A channel's level is a platform fact; a project can only switch a channel on within it. Rate limits count outward actions; the never-automate checks run before every one.")
                        .font(.callout).foregroundStyle(.secondary)
                    Panel(padding: 0) {
                        VStack(spacing: 0) {
                            ForEach(project.channels) { channel in
                                ChannelRow(project: project.id, channel: channel)
                                if channel.id != project.channels.last?.id { Divider().padding(.leading, 16) }
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

struct ChannelRow: View {
    @Environment(EngineStore.self) private var store
    var project: String
    var channel: Channel
    @State private var limit = ""

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Text(channel.label).font(.callout.weight(.semibold))
                    Text(channel.level).font(.caption2.weight(.semibold)).padding(.horizontal, 6).padding(.vertical, 1)
                        .background(Theme.hairline, in: Capsule())
                }
                Text(channel.why).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Text("\(channel.usedLastDay) outward actions in the last 24 hours").font(.caption).foregroundStyle(.secondary).figures()
            }
            Spacer(minLength: 20)
            TextField("no limit", text: $limit)
                .frame(width: 90).figures()
                .onSubmit { Task { await store.setChannel(project, channel.id, rateLimit: limit) } }
                .help("Outward actions per window, e.g. 20/24h. Press Return to save; empty removes the limit.")
            Toggle("Paused", isOn: Binding(get: { channel.paused }, set: { on in Task { await store.setChannel(project, channel.id, paused: on) } }))
                .toggleStyle(.switch)
        }
        .disabled(store.working.contains("channel:\(project)/\(channel.id)") || !store.link.isOnline)
        .padding(.horizontal, 16).padding(.vertical, 12)
        .onAppear { limit = channel.rateLimit }
        .onChange(of: channel.rateLimit) { limit = channel.rateLimit }
    }
}

// MARK: Activity

struct ActivityView: View {
    var dashboard: Dashboard

    var body: some View {
        Table(dashboard.activity) {
            TableColumn("When") { a in Text(Fmt.clock(a.at)).figures().foregroundStyle(.secondary) }.width(min: 120, ideal: 150)
            TableColumn("Job") { a in Text("\(a.scope) / \(Fmt.jobName(a.job))") }.width(min: 160, ideal: 200)
            TableColumn("Result") { a in
                HStack(spacing: 6) { StatusDot(color: Theme.status(a.status)); Text(a.status) }
            }.width(min: 90, ideal: 100)
            TableColumn("Summary") { a in Text(a.summary).foregroundStyle(.secondary).help(a.summary) }
        }
    }
}
