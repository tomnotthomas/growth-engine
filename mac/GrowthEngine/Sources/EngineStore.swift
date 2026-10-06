import Foundation
import Observation

/// How the app reaches the engine. The engine only listens on 127.0.0.1 on the GEEKOM; the app opens an
/// SSH tunnel with the existing key-based alias and fetches the login token over the same SSH login.
struct ConnectionSettings: Codable, Equatable {
    enum Mode: String, Codable, CaseIterable, Identifiable {
        case geekom, local
        var id: String { rawValue }
        var label: String { self == .geekom ? "GEEKOM over SSH" : "This Mac" }
    }

    var mode: Mode = .geekom
    var sshAlias: String = "geekom-wsl"
    var remotePort: Int = 8765
    var tunnelPort: Int = 18765
    var tokenCommand: String = "cd ~/growth-engine && python3 -m growth app-token"
    var localPort: Int = 8765
    var localToken: String = "demo"
    /// For an engine on this Mac: a command that prints its token (e.g. `growth app-token`); empty uses the token above.
    var localTokenCommand: String = ""

    static let key = "connection"

    static func load() -> ConnectionSettings {
        guard let data = UserDefaults.standard.data(forKey: key),
              let value = try? JSONDecoder().decode(ConnectionSettings.self, from: data) else { return ConnectionSettings() }
        return value
    }

    init() {}

    // Settings saved by an older version miss newer keys; every key falls back to its default.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let d = ConnectionSettings()
        mode = (try? c.decode(Mode.self, forKey: .mode)) ?? d.mode
        sshAlias = (try? c.decode(String.self, forKey: .sshAlias)) ?? d.sshAlias
        remotePort = (try? c.decode(Int.self, forKey: .remotePort)) ?? d.remotePort
        tunnelPort = (try? c.decode(Int.self, forKey: .tunnelPort)) ?? d.tunnelPort
        tokenCommand = (try? c.decode(String.self, forKey: .tokenCommand)) ?? d.tokenCommand
        localPort = (try? c.decode(Int.self, forKey: .localPort)) ?? d.localPort
        localToken = (try? c.decode(String.self, forKey: .localToken)) ?? d.localToken
        localTokenCommand = (try? c.decode(String.self, forKey: .localTokenCommand)) ?? d.localTokenCommand
    }

    func save() {
        if let data = try? JSONEncoder().encode(self) { UserDefaults.standard.set(data, forKey: Self.key) }
    }

    var place: String { mode == .geekom ? "GEEKOM" : "This Mac" }
}

enum LinkState: Equatable {
    case connecting
    case online
    case offline(String)
    case locked(String)

    var isOnline: Bool { self == .online }
}

struct ControlFailure: Identifiable, Equatable {
    let id = UUID()
    let message: String
}

@MainActor
@Observable
final class EngineStore {
    var settings = ConnectionSettings.load()
    var link: LinkState = .connecting
    var dashboard: Dashboard?
    var lastSeen: Date?
    var offlineSince: Date?
    var failure: ControlFailure?
    var working: Set<String> = []
    var windowOpen = false
    var selectedProjectID: String?

    private var tunnel: Process?
    private var token: String?
    private var loop: Task<Void, Never>?
    private var attempt = 0
    private let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 12
        config.httpCookieStorage = nil
        config.urlCache = nil
        return URLSession(configuration: config)
    }()

    var project: Project? {
        guard let d = dashboard else { return nil }
        return d.projects.first { $0.id == selectedProjectID } ?? d.projects.first
    }

    var killed: Bool { dashboard?.engine.kill?.on == true }
    var waitingCount: Int { dashboard?.attention.filter { $0.kind == "draft" }.count ?? 0 }

    private var port: Int { settings.mode == .geekom ? settings.tunnelPort : settings.localPort }

    // MARK: lifecycle

    func start() {
        guard loop == nil else { return }
        loop = Task { [weak self] in await self?.run() }
    }

    func reconnect() {
        loop?.cancel()
        loop = nil
        stopTunnel()
        token = nil
        attempt = 0
        link = .connecting
        start()
    }

    func apply(_ new: ConnectionSettings) {
        settings = new
        new.save()
        dashboard = nil
        reconnect()
    }

    func shutdown() {
        loop?.cancel()
        stopTunnel()
    }

    private func run() async {
        while !Task.isCancelled {
            let ok = await refresh()
            attempt = ok ? 0 : min(attempt + 1, 6)
            let pause: Double = ok ? (windowOpen ? 15 : 60) : min(60, 3 * pow(2, Double(attempt - 1)))
            try? await Task.sleep(for: .seconds(pause))
        }
    }

    /// One round: make sure the tunnel and token exist, then fetch the snapshot. False when offline.
    @discardableResult
    func refresh() async -> Bool {
        do {
            try await ensureConnected()
            let data = try await request("GET", "/api/dashboard")
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            dashboard = try decoder.decode(Dashboard.self, from: data)
            if selectedProjectID == nil { selectedProjectID = dashboard?.projects.first?.id }
            lastSeen = Date()
            offlineSince = nil
            link = .online
            return true
        } catch let error as EngineError {
            markOffline(error)
            return false
        } catch let error as DecodingError {
            markOffline(.refused("the engine sent data this app version does not understand (\(Self.describe(error)))"))
            return false
        } catch {
            markOffline(.unreachable(error.localizedDescription))
            return false
        }
    }

    static func describe(_ error: DecodingError) -> String {
        switch error {
        case .keyNotFound(let key, let ctx): return "missing \(key.stringValue) at \(ctx.codingPath.map(\.stringValue).joined(separator: "."))"
        case .typeMismatch(_, let ctx), .valueNotFound(_, let ctx), .dataCorrupted(let ctx):
            return "\(ctx.debugDescription) at \(ctx.codingPath.map(\.stringValue).joined(separator: "."))"
        @unknown default: return "\(error)"
        }
    }

    private func markOffline(_ error: EngineError) {
        if case .locked(let why) = error {
            link = .locked(why)
            token = nil
            return
        }
        if offlineSince == nil { offlineSince = Date() }
        link = .offline(error.message)
        if settings.mode == .geekom, tunnel?.isRunning != true { token = nil }
    }

    // MARK: tunnel and token

    private func ensureConnected() async throws {
        if settings.mode == .local {
            if token == nil {
                token = settings.localTokenCommand.isEmpty ? settings.localToken : try await runForToken(["/bin/zsh", "-lc", settings.localTokenCommand])
            }
            return
        }
        if tunnel?.isRunning != true {
            try startTunnel()
            try await Task.sleep(for: .milliseconds(900))
            guard tunnel?.isRunning == true else {
                throw EngineError.unreachable("\(settings.sshAlias) did not answer over SSH")
            }
        }
        if token == nil {
            token = try await fetchToken()
        }
    }

    private func startTunnel() throws {
        stopTunnel()
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/ssh")
        p.arguments = sshOptions + ["-N", "-T", "-o", "ExitOnForwardFailure=yes",
                                    "-L", "127.0.0.1:\(settings.tunnelPort):127.0.0.1:\(settings.remotePort)", settings.sshAlias]
        p.standardInput = FileHandle.nullDevice
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        try p.run()
        tunnel = p
    }

    private func stopTunnel() {
        if let t = tunnel, t.isRunning { t.terminate() }
        tunnel = nil
    }

    private var sshOptions: [String] {
        ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=2"]
    }

    private func fetchToken() async throws -> String {
        try await runForToken(["/usr/bin/ssh"] + sshOptions + [settings.sshAlias, settings.tokenCommand])
    }

    /// Run a command whose only output is the engine's login token.
    private func runForToken(_ argv: [String]) async throws -> String {
        return try await Task.detached {
            let p = Process()
            let out = Pipe()
            let err = Pipe()
            p.executableURL = URL(fileURLWithPath: argv[0])
            p.arguments = Array(argv.dropFirst())
            p.standardInput = FileHandle.nullDevice
            p.standardOutput = out
            p.standardError = err
            try p.run()
            let data = out.fileHandleForReading.readDataToEndOfFile()
            let errData = err.fileHandleForReading.readDataToEndOfFile()
            p.waitUntilExit()
            let token = String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
            guard p.terminationStatus == 0, !token.isEmpty, !token.contains(" ") else {
                let why = String(decoding: errData, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
                throw EngineError.locked(why.isEmpty ? "the engine did not hand out a login token" : String(why.prefix(200)))
            }
            return token
        }.value
    }

    // MARK: HTTP

    private func request(_ method: String, _ path: String, body: [String: Any]? = nil) async throws -> Data {
        guard let url = URL(string: "http://127.0.0.1:\(port)\(path)") else { throw EngineError.unreachable("bad address") }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue("Bearer \(token ?? "")", forHTTPHeaderField: "Authorization")
        if let body {
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: req)
        } catch {
            throw EngineError.unreachable(settings.mode == .geekom ? "the GEEKOM engine does not answer" : "no engine on this Mac (run: growth serve --demo)")
        }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        if status == 401 { throw EngineError.locked("the login token was refused") }
        guard (200..<300).contains(status) else {
            let message = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
            throw EngineError.refused(message ?? "the engine answered \(status)")
        }
        return data
    }

    // MARK: control actions

    /// Send one control action; every one is validated and audit-logged by the engine.
    @discardableResult
    func control(_ key: String, _ path: String, _ body: [String: Any]) async -> Bool {
        working.insert(key)
        defer { working.remove(key) }
        do {
            _ = try await request("POST", path, body: body)
            await refresh()
            return true
        } catch let error as EngineError {
            failure = ControlFailure(message: error.message)
            if case .unreachable = error { markOffline(error) }
            return false
        } catch {
            failure = ControlFailure(message: error.localizedDescription)
            return false
        }
    }

    func audit(limit: Int = 300) async -> AuditPage? {
        do {
            let data = try await request("GET", "/api/audit?limit=\(limit)")
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            return try decoder.decode(AuditPage.self, from: data)
        } catch {
            return nil
        }
    }

    func setKill(_ on: Bool, reason: String) async { await control("kill", "/api/kill", ["on": on, "reason": reason]) }
    func setJob(_ scope: String, _ job: String, enabled: Bool) async {
        await control("job:\(scope)/\(job)", "/api/jobs", ["scope": scope, "job": job, "enabled": enabled])
    }
    func runNow(_ scope: String, _ job: String) async { await control("run:\(scope)/\(job)", "/api/run", ["scope": scope, "job": job]) }
    func setPaused(_ project: String, _ paused: Bool) async {
        await control("pause:\(project)", "/api/projects/\(project)/pause", ["paused": paused])
    }
    func setChannel(_ project: String, _ channel: String, paused: Bool? = nil, rateLimit: String? = nil) async {
        var body: [String: Any] = ["channel": channel]
        if let paused { body["paused"] = paused }
        if let rateLimit { body["rate_limit"] = rateLimit }
        await control("channel:\(project)/\(channel)", "/api/projects/\(project)/channel", body)
    }
    func decide(_ project: String, _ draft: String, _ status: String) async {
        await control("draft:\(draft)", "/api/projects/\(project)/draft", ["id": draft, "status": status])
    }
    func setGoal(_ project: String, _ body: [String: Any]) async -> Bool {
        await control("goal:\(project)", "/api/projects/\(project)/goal", body)
    }
    func setSettings(_ project: String, _ body: [String: Any]) async -> Bool {
        await control("settings:\(project)", "/api/projects/\(project)/settings", body)
    }
    func setBudget(_ body: [String: Any]) async -> Bool { await control("budget", "/api/budget", body) }
    func setSchedule(_ project: String, _ job: String, _ schedule: String) async -> Bool {
        await control("schedule:\(project)/\(job)", "/api/projects/\(project)/schedule", ["job": job, "schedule": schedule])
    }
    func setPageText(_ project: String, _ page: PageText) async -> Bool {
        await control("page:\(project)/\(page.id)", "/api/projects/\(project)/page",
                      ["page": page.page, "lang": page.lang, "title": page.title, "description": page.description,
                       "headline": page.headline, "sub": page.sub])
    }
    func setLegal(_ project: String, _ values: [String: String]) async -> Bool {
        await control("legal:\(project)", "/api/projects/\(project)/legal", values)
    }
    func setSecret(_ project: String, _ name: String, _ value: String) async -> Bool {
        await control("secret:\(name)", "/api/projects/\(project)/secret", ["name": name, "value": value])
    }
    func addProject(id: String, name: String, address: String) async -> Bool {
        let ok = await control("add-project", "/api/projects", ["id": id, "name": name, "base_url": address])
        if ok { selectedProjectID = id }
        return ok
    }
    /// Refresh the product data and rebuild the website now (one engine process, under its lock).
    func buildNow(_ project: String) async {
        await control("build:\(project)", "/api/projects/\(project)/build", [:])
    }
}

enum EngineError: Error {
    case unreachable(String)
    case locked(String)
    case refused(String)

    var message: String {
        switch self {
        case .unreachable(let s), .locked(let s), .refused(let s): return s
        }
    }
}
