import AppKit
import SwiftUI

// MARK: Waiting for you (the Reddit queue)

struct QueueView: View {
    @Environment(EngineStore.self) private var store

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Replies the engine wrote for Reddit threads where your product helps. It never posts them: copy one, open the thread, adjust it, and post it yourself, saying you made the product.")
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

struct ChannelRow: View {
    @Environment(EngineStore.self) private var store
    var project: String
    var channel: Channel
    @State private var limit = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(channelName(channel)).font(.body.weight(.medium))
                Text(levelWords(channel.level)).font(.caption2.weight(.semibold)).padding(.horizontal, 6).padding(.vertical, 1)
                    .background(Theme.hairline, in: Capsule())
                Spacer()
                Toggle("Pause", isOn: Binding(get: { channel.paused }, set: { on in Task { await store.setChannel(project, channel.id, paused: on) } }))
                    .toggleStyle(.switch)
            }
            Text(channel.why).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                Text("At most").font(.callout)
                TextField("", text: $limit, prompt: Text("no limit"))
                    .labelsHidden().frame(width: 90).figures()
                    .onSubmit { Task { await store.setChannel(project, channel.id, rateLimit: limit) } }
                Text("(e.g. 20/24h, Return saves) · \(channel.usedLastDay) done in the last 24 hours")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .disabled(store.working.contains("channel:\(project)/\(channel.id)") || !store.link.isOnline)
        .padding(.vertical, 4)
        .onAppear { limit = channel.rateLimit }
        .onChange(of: channel.rateLimit) { limit = channel.rateLimit }
    }
}

func channelName(_ channel: Channel) -> String {
    switch channel.id {
    case "website": return "Your website"
    case "directory-submit": return "Directory submissions"
    case "indexnow": return "Notices to search engines"
    case "digest": return "Weekly report"
    case "reddit": return "Reddit replies"
    case "analytics": return "Reading your visitor numbers"
    case "waitlist-email": return "Waitlist emails"
    default: return channel.label
    }
}

func levelWords(_ level: String) -> String {
    switch level {
    case "auto": return "runs on its own"
    case "human-queue": return "you post"
    case "needs-approval": return "needs platform approval"
    default: return "never"
    }
}
