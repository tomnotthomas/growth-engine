import AppKit
import SwiftUI

/// The engine's own colours, carried into a native app: ink and paper from the system appearance, and
/// the lime of the generated sites as the single accent (state and selection only, never decoration).
enum Theme {
    static func dynamic(light: NSColor, dark: NSColor) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? dark : light
        })
    }

    static func rgb(_ hex: UInt32, _ alpha: CGFloat = 1) -> NSColor {
        NSColor(srgbRed: CGFloat((hex >> 16) & 0xff) / 255, green: CGFloat((hex >> 8) & 0xff) / 255,
                blue: CGFloat(hex & 0xff) / 255, alpha: alpha)
    }

    /// Lime on dark; a deep olive on light, where lime would fail contrast as text.
    static let accent = dynamic(light: rgb(0x55660E), dark: rgb(0xD4F53C))
    /// Lime as a fill behind ink text, in both appearances.
    static let limeFill = dynamic(light: rgb(0xD4F53C), dark: rgb(0xD4F53C))
    static let onLime = Color(nsColor: rgb(0x131313))
    static let plan = dynamic(light: rgb(0x131313, 0.45), dark: rgb(0xEFEFED, 0.45))
    static let hairline = dynamic(light: rgb(0x131313, 0.10), dark: rgb(0xEFEFED, 0.12))
    static let panel = dynamic(light: rgb(0xF4F4F2), dark: rgb(0x1B1B1B))
    static let good = dynamic(light: rgb(0x23863A), dark: rgb(0x5FD37A))
    static let warn = dynamic(light: rgb(0xA35A00), dark: rgb(0xF2A541))
    static let bad = dynamic(light: rgb(0xC0281E), dark: rgb(0xFF6B5E))
    static let killFill = dynamic(light: rgb(0xC0281E), dark: rgb(0xD7372B))

    static func status(_ status: String?) -> Color {
        switch status {
        case "ok", "updated", "promoted", "previewed": return good
        case "failed", "rolled-back", "interrupted", "blocked": return bad
        case "running", "deferred", "missed": return warn
        default: return .secondary
        }
    }
}

/// A small filled dot plus label, the app's one status vocabulary.
struct StatusDot: View {
    var color: Color
    var size: CGFloat = 8
    var body: some View {
        Circle().fill(color).frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}

struct SectionTitle: View {
    var title: String
    var trailing: String?
    init(_ title: String, trailing: String? = nil) {
        self.title = title
        self.trailing = trailing
    }
    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).font(.headline)
            Spacer()
            if let trailing { Text(trailing).font(.callout).foregroundStyle(.secondary) }
        }
    }
}

/// A grouped surface: one level of containment, a hairline, no shadow stacks.
struct Panel<Content: View>: View {
    var padding: CGFloat = 16
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 12) { content }
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.panel, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(Theme.hairline))
    }
}

struct EmptyNote: View {
    var symbol: String
    var title: String
    var text: String
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: symbol).foregroundStyle(.secondary).font(.title3)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.callout.weight(.medium))
                Text(text).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.vertical, 6)
    }
}

/// Numbers in tables and headers line up: tabular figures everywhere data is shown.
extension View {
    func figures() -> some View { monospacedDigit() }
}
