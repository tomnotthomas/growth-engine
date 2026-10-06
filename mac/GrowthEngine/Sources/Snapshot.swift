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

    /// Whole-window renders through AppKit (real controls), one per page: the usability-test screenshots.
    static func windows(into folder: URL, store: EngineStore) async {
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        for page in Page.allCases {
            let view = NSHostingView(rootView: ContentView(initialPage: page).environment(store).frame(width: 1180, height: 780))
            let window = NSWindow(contentRect: NSRect(x: -4000, y: -4000, width: 1180, height: 780),
                                  styleMask: [.titled, .fullSizeContentView], backing: .buffered, defer: false)
            window.contentView = view
            window.orderFrontRegardless()
            window.alphaValue = 0.01
            try? await Task.sleep(for: .milliseconds(1500))
            view.layoutSubtreeIfNeeded()
            if let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) {
                view.cacheDisplay(in: view.bounds, to: rep)
                try? rep.representation(using: .png, properties: [:])?.write(to: folder.appendingPathComponent("window-\(page.rawValue).png"))
            }
            window.orderOut(nil)
        }
    }

    static func run(into folder: URL) async {
        let store = EngineStore.fromArguments()
        guard await store.refresh(), let dashboard = store.dashboard else {
            FileHandle.standardError.write(Data("snapshot: engine not reachable: \(store.link)\n".utf8))
            exit(1)
        }
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        await windows(into: folder, store: store)
        exit(0)
    }
}
