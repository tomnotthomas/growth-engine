import Foundation

// The control API's JSON (growth/control/snapshot.py), decoded with convertFromSnakeCase.

struct Dashboard: Decodable {
    var generatedAt: String
    var demo: Bool
    var engine: EngineInfo
    var attention: [Attention]
    var projects: [Project]
    var upcoming: [Upcoming]
    var activity: [Activity]
}

struct EngineInfo: Decodable {
    var host: String
    var version: String
    var commit: String
    var lastTick: String?
    var tickAgeSeconds: Double?
    var healthy: Bool
    var kill: KillState?
    var ai: AIBudget
    var rateLimits: [String: String]
    var jobs: [Job]
    var updates: [UpdateEntry]
    var audit: AuditHealth
}

struct KillState: Decodable, Equatable {
    var on: Bool
    var reason: String?
    var at: String?
    var by: String?
}

struct AIBudget: Decodable {
    var today: Int
    var maxPerDay: Int
    var week: Int
    var maxPerWeek: Int
    var perProjectPerWeek: Int?
    var window: [String]?
}

struct UpdateEntry: Decodable, Identifiable {
    var at: String
    var from: String?
    var to: String?
    var status: String
    var detail: String?
    var id: String { at + status }
}

struct AuditHealth: Decodable {
    var intact: Bool
    var entries: Int
    var problem: String
}

struct Attention: Decodable, Identifiable {
    var kind: String
    var project: String
    var id_: String?
    var title: String
    var detail: String
    var at: String?
    var id: String { "\(kind)|\(project)|\(id_ ?? title)|\(at ?? "")" }

    enum CodingKeys: String, CodingKey {
        case kind, project, title, detail, at
        case id_ = "id"
    }
}

struct Upcoming: Decodable, Identifiable {
    var at: String
    var scope: String
    var job: String
    var kind: String
    var id: String { "\(scope)/\(job)@\(at)" }
}

struct Activity: Decodable, Identifiable {
    var at: String?
    var scope: String
    var job: String
    var status: String
    var summary: String
    var id: String { "\(scope)/\(job)@\(at ?? "")/\(status)" }
}

struct Project: Decodable, Identifiable {
    var id: String
    var name: String
    var brandName: String
    var baseUrl: String
    var domainDecided: Bool
    var indexable: Bool
    var launched: Bool
    var paused: Bool
    var languages: [String]
    var goal: Goal?
    var jobs: [Job]
    var channels: [Channel]
    var drafts: [Draft]
    var problems: [Problem]
    var blocks: [Block]
    var deploy: DeployState
    var keywords: [Keyword]
}

struct Goal: Decodable {
    var name: String
    var target: Int
    var status: String?
    var confirmedTotal: Int?
    var pendingTotal: Int?
    var numbersFrom: String?
    var zone: String?
    var outsideZone: Int?
    var last7Days: Int?
    var avgPerDay7: Double?
    var referralShare7: Double?
    var start: String?
    var days: Int?
    var deadline: String?
    var daysElapsed: Int?
    var daysLeft: Int?
    var planToDate: Int?
    var gapToPlan: Int?
    var neededPerDay: Double?
    var projection: Int?
    var onTrack: Bool?
    var kFactor: Double?
    var fetchedAt: String?
    var daily: [DayPoint]
    var channels: [SourceCount]
    var countries: [CountryCount]
}

struct DayPoint: Decodable, Identifiable {
    var date: String
    var confirmed: Int
    var cumulative: Int
    var plan: Int?
    var id: String { date }
    var day: Date { Fmt.day(date) }
}

struct SourceCount: Decodable, Identifiable {
    var source: String
    var confirmed: Int
    var id: String { source }
}

struct CountryCount: Decodable, Identifiable {
    var country: String
    var confirmed: Int
    var inZone: Bool
    var id: String { country }
}

struct Job: Decodable, Identifiable {
    var id: String
    var kind: String
    var description: String
    var schedule: String
    var enabled: Bool
    var paused: Bool
    var channel: String?
    var usesAi: String?
    var last: LastRun?
    var nextAt: String?
}

struct LastRun: Decodable {
    var status: String
    var at: String?
    var summary: String
}

struct Channel: Decodable, Identifiable {
    var id: String
    var label: String
    var level: String
    var why: String
    var enabled: Bool
    var paused: Bool
    var rateLimit: String
    var usedLastDay: Int
}

struct Draft: Decodable, Identifiable {
    var id: String
    var channel: String
    var community: String
    var threadUrl: String
    var title: String
    var text: String
    var why: String
    var createdAt: String
    var status: String
}

struct Problem: Decodable, Identifiable {
    var job: String
    var status: String
    var at: String?
    var summary: String
    var id: String { "\(job)@\(at ?? "")" }
}

struct Block: Decodable, Identifiable {
    var scope: String
    var job: String
    var rule: String
    var message: String
    var at: String
    var id: String { "\(job)@\(at)/\(rule)" }
}

struct DeployState: Decodable {
    var check: DeployRecord?
    var production: DeployRecord?
    var lastGood: String?
    var history: [DeployRecord]?
}

struct DeployRecord: Decodable, Identifiable {
    var at: String?
    var stage: String?
    var status: String?
    var versionId: String?
    var url: String?
    var detail: String?
    var id: String { "\(at ?? "")\(stage ?? "")\(versionId ?? "")" }
}

struct Keyword: Codable, Identifiable, Hashable {
    var page: String
    var primary: String
    var lang: String?
    var id: String { "\(page)|\(primary)|\(lang ?? "")" }
}

struct AuditPage: Decodable {
    var intact: Bool
    var entries: Int
    var problem: String
    var items: [AuditEntry]
}

struct AuditEntry: Decodable, Identifiable {
    var seq: Int
    var at: String
    var actor: String
    var event: String
    var scope: String
    var detail: [String: JSONValue]
    var id: Int { seq }
}

enum JSONValue: Decodable, CustomStringConvertible {
    case string(String), number(Double), bool(Bool), object([String: JSONValue]), array([JSONValue]), null

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let v = try? c.decode(Bool.self) { self = .bool(v) }
        else if let v = try? c.decode(Double.self) { self = .number(v) }
        else if let v = try? c.decode(String.self) { self = .string(v) }
        else if let v = try? c.decode([JSONValue].self) { self = .array(v) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }

    var description: String {
        switch self {
        case .string(let s): return s
        case .number(let n): return n == n.rounded() ? String(Int(n)) : String(n)
        case .bool(let b): return b ? "on" : "off"
        case .null: return "–"
        case .array(let a): return a.map(\.description).joined(separator: ", ")
        case .object(let o): return o.keys.sorted().map { "\($0) \(o[$0]!.description)" }.joined(separator: ", ")
        }
    }
}

enum Fmt {
    private static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()
    private static let dayParser: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()
    private static let relative: RelativeDateTimeFormatter = {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .short
        return f
    }()

    static func date(_ text: String?) -> Date? { text.flatMap { iso.date(from: $0) } }
    static func day(_ text: String) -> Date { dayParser.date(from: text) ?? .distantPast }

    static func ago(_ text: String?, now: Date = Date()) -> String {
        guard let d = date(text) else { return "never" }
        if abs(d.timeIntervalSince(now)) < 60 { return d > now ? "in a moment" : "just now" }
        return relative.localizedString(for: d, relativeTo: now)
    }

    static func clock(_ text: String?) -> String {
        guard let d = date(text) else { return "–" }
        let cal = Calendar.current
        if cal.isDateInToday(d) { return d.formatted(date: .omitted, time: .shortened) }
        if cal.isDateInTomorrow(d) { return "Tomorrow " + d.formatted(date: .omitted, time: .shortened) }
        if cal.isDateInYesterday(d) { return "Yesterday " + d.formatted(date: .omitted, time: .shortened) }
        return d.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated).hour().minute())
    }

    static func number(_ n: Int?) -> String { n.map { $0.formatted(.number) } ?? "–" }
    static func number(_ n: Double?, digits: Int = 1) -> String {
        n.map { $0.formatted(.number.precision(.fractionLength(0...digits))) } ?? "–"
    }

    static func jobName(_ id: String) -> String {
        id.replacingOccurrences(of: "-", with: " ").capitalized(with: nil)
    }
}
