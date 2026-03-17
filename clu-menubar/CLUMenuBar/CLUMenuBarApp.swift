import SwiftUI
import Charts

// MARK: - API Model

struct UsageSample: Codable, Identifiable {
    let ts: Int
    let fiveH: Double
    let sevenD: Double
    let tok: Int

    var id: Int { ts }
    var date: Date { Date(timeIntervalSince1970: TimeInterval(ts)) }

    enum CodingKeys: String, CodingKey {
        case ts
        case fiveH = "5h"
        case sevenD = "7d"
        case tok
    }
}

struct ModelInfo: Codable {
    let utilization: Double?
    let resets_at: String?
}

struct ExtraUsage: Codable {
    let enabled: Bool?
    let limit: Double?
    let used: Double?
    let utilization: Double?
}

struct APIResponse: Codable {
    let pct_5h: Double?
    let pct_7d: Double?
    let reset_5h_iso: String?
    let reset_7d_iso: String?
    let reset_5h_secs: Int?
    let reset_7d_secs: Int?
    let tokens_5h: Int?
    let pace_pct: Double?
    let elapsed_ratio: Double?
    let plan: String?
    let is_promo: Bool?
    let promo_label: String?
    let models: [String: ModelInfo]?
    let extra_usage: ExtraUsage?
    let history: [UsageSample]?
    let last_fetch: Int?
    let error: String?
}

// MARK: - Data Service

@Observable
class UsageService {
    var data: APIResponse?
    var isLoading = false
    var lastUpdate: Date?

    private var timer: Timer?
    private let baseURL: String

    init(baseURL: String = "http://localhost:8765") {
        self.baseURL = baseURL
    }

    func startPolling(interval: TimeInterval = 30) {
        fetch()
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            self?.fetch()
        }
    }

    func fetch() {
        guard let url = URL(string: "\(baseURL)/api") else { return }
        isLoading = true
        URLSession.shared.dataTask(with: url) { [weak self] responseData, _, error in
            DispatchQueue.main.async {
                self?.isLoading = false
                guard let responseData = responseData, error == nil else { return }
                if let response = try? JSONDecoder().decode(APIResponse.self, from: responseData) {
                    self?.data = response
                    self?.lastUpdate = Date()
                }
            }
        }.resume()
    }

    func triggerRefresh() {
        guard let url = URL(string: "\(baseURL)/api/refresh") else { return }
        URLSession.shared.dataTask(with: url) { _, _, _ in
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
                self?.fetch()
            }
        }.resume()
    }
}

// MARK: - Colors (adaptive for light/dark)

extension Color {
    static let cluViolet = Color(red: 0.65, green: 0.55, blue: 0.98)
    static let cluCyan = Color(red: 0.25, green: 0.78, blue: 0.92)
    static let cluGreen = Color(red: 0.20, green: 0.78, blue: 0.55)
    static let cluOrange = Color(red: 0.96, green: 0.55, blue: 0.22)
    static let cluRed = Color(red: 0.94, green: 0.36, blue: 0.36)
    static let cluAmber = Color(red: 0.96, green: 0.72, blue: 0.15)

    static func forPct(_ pct: Double) -> Color {
        if pct >= 90 { return .cluRed }
        if pct >= 70 { return .cluOrange }
        if pct >= 40 { return .cluAmber }
        return .cluGreen
    }
}

// MARK: - Format Helpers

func formatReset(seconds: Int?) -> String {
    guard let secs = seconds, secs > 0 else { return "—" }
    let d = secs / 86400
    let h = (secs % 86400) / 3600
    let m = (secs % 3600) / 60
    if d > 0 { return "Resets in \(d)d \(h)h" }
    if h > 0 { return "Resets in \(h)h \(String(format: "%02d", m))m" }
    return "Resets in \(m)m"
}

func formatTimeAgo(_ date: Date?) -> String {
    guard let date = date else { return "never" }
    let secs = Int(Date().timeIntervalSince(date))
    if secs < 5 { return "just now" }
    if secs < 60 { return "\(secs)s ago" }
    if secs < 3600 { return "\(secs / 60)m ago" }
    return "\(secs / 3600)h ago"
}

func parseISO(_ iso: String?) -> Date? {
    guard let iso = iso else { return nil }
    let fmt = ISO8601DateFormatter()
    fmt.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let d = fmt.date(from: iso) { return d }
    fmt.formatOptions = [.withInternetDateTime]
    return fmt.date(from: iso)
}

// MARK: - Usage Bar

struct UsageBar: View {
    let label: String
    let pct: Double
    let resetSecs: Int?

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .lastTextBaseline) {
                Text(label)
                    .font(.system(size: 13, weight: .medium))
                Spacer()
                Text("\(Int(pct))%")
                    .font(.system(size: 20, weight: .bold, design: .rounded))
                    .foregroundStyle(Color.forPct(pct))
            }

            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 5)
                        .fill(.quaternary)
                    RoundedRectangle(cornerRadius: 5)
                        .fill(Color.forPct(pct))
                        .frame(width: max(0, geo.size.width * pct / 100))
                        .animation(.easeInOut(duration: 0.6), value: pct)
                }
            }
            .frame(height: 10)

            Text(formatReset(seconds: resetSecs))
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
        }
    }
}

struct DashedLine: Shape {
    func path(in rect: CGRect) -> Path {
        Path { p in
            p.move(to: CGPoint(x: 0, y: rect.midY))
            p.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
        }
    }
}

// MARK: - Window Chart

struct WindowChartView: View {
    let title: String
    let history: [UsageSample]
    let keyPath: KeyPath<UsageSample, Double>
    let resetISO: String?
    let windowSecs: Double
    let color: Color

    private var windowStart: Date {
        if let reset = parseISO(resetISO) {
            return reset.addingTimeInterval(-windowSecs)
        }
        return Date().addingTimeInterval(-windowSecs)
    }

    private var windowEnd: Date {
        windowStart.addingTimeInterval(windowSecs)
    }

    private var budgetPoints: [(date: Date, pct: Double)] {
        stride(from: 0.0, through: 1.0, by: 0.05).map { ratio in
            (windowStart.addingTimeInterval(ratio * windowSecs), ratio * 100)
        }
    }

    private var usagePoints: [(date: Date, pct: Double)] {
        // Only show samples from the current window — filter out previous cycles
        history
            .filter { $0.date >= windowStart && $0.date <= windowEnd }
            .map { ($0.date, $0[keyPath: keyPath]) }
    }

    /// Human-readable time format for x-axis depending on window size
    private var xAxisFormat: Date.FormatStyle {
        if windowSecs <= 86400 {
            return .dateTime.hour().minute()         // "2 PM" / "14:30"
        } else {
            return .dateTime.weekday(.abbreviated)   // "Mon", "Tue"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            // Title row with percentage inline
            HStack(alignment: .lastTextBaseline, spacing: 6) {
                Text(title)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.primary)
                if let last = usagePoints.last {
                    Text("\(Int(last.pct))%")
                        .font(.system(size: 14, weight: .bold, design: .rounded))
                        .foregroundStyle(color)
                }
                Spacer()
            }

            Chart {
                // Budget diagonal
                ForEach(Array(budgetPoints.enumerated()), id: \.offset) { _, pt in
                    LineMark(
                        x: .value("Time", pt.date),
                        y: .value("Budget", pt.pct),
                        series: .value("S", "budget")
                    )
                    .foregroundStyle(.secondary.opacity(0.5))
                    .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [5, 4]))
                    .interpolationMethod(.linear)
                }

                // Gradient fill under actual
                ForEach(Array(usagePoints.enumerated()), id: \.offset) { _, pt in
                    AreaMark(
                        x: .value("Time", pt.date),
                        y: .value("U", pt.pct)
                    )
                    .foregroundStyle(
                        LinearGradient(
                            colors: [color.opacity(0.2), color.opacity(0.02)],
                            startPoint: .top, endPoint: .bottom
                        )
                    )
                    .interpolationMethod(.catmullRom)
                }

                // Actual usage line
                ForEach(Array(usagePoints.enumerated()), id: \.offset) { _, pt in
                    LineMark(
                        x: .value("Time", pt.date),
                        y: .value("Usage", pt.pct),
                        series: .value("S", "actual")
                    )
                    .foregroundStyle(color)
                    .lineStyle(StrokeStyle(lineWidth: 2.5))
                    .interpolationMethod(.catmullRom)
                }
            }
            .chartXScale(domain: windowStart...windowEnd)
            .chartYScale(domain: 0...100)
            .chartYAxis {
                AxisMarks(position: .trailing, values: [0, 25, 50, 75, 100]) { val in
                    AxisValueLabel {
                        Text("\(val.as(Int.self) ?? 0)%")
                            .font(.system(size: 10, weight: .medium))
                            .foregroundStyle(.secondary)
                    }
                    AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5))
                        .foregroundStyle(.secondary.opacity(0.2))
                }
            }
            .chartXAxis {
                AxisMarks(values: .automatic(desiredCount: windowSecs <= 86400 ? 5 : 4)) { val in
                    AxisValueLabel(format: xAxisFormat)
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(.secondary)
                    AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5))
                        .foregroundStyle(.secondary.opacity(0.15))
                }
            }
            .chartLegend(.hidden)
            .frame(height: 110)

            // Legend
            HStack(spacing: 14) {
                HStack(spacing: 5) {
                    RoundedRectangle(cornerRadius: 1).fill(color).frame(width: 16, height: 3)
                    Text("actual").font(.system(size: 10)).foregroundStyle(.secondary)
                }
                HStack(spacing: 5) {
                    DashedLine()
                        .stroke(.secondary.opacity(0.5), style: StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
                        .frame(width: 16, height: 2)
                    Text("even pace").font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
        }
        .padding(10)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10))
    }
}

// MARK: - Popover Content

struct PopoverView: View {
    var service: UsageService
    @Environment(\.colorScheme) var colorScheme

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                // Header
                HStack(alignment: .center) {
                    Text("Claude Usage")
                        .font(.system(size: 17, weight: .bold))
                    Spacer()
                    if let plan = service.data?.plan, !plan.isEmpty {
                        Text(plan.capitalized)
                            .font(.system(size: 10, weight: .semibold))
                            .padding(.horizontal, 8)
                            .padding(.vertical, 3)
                            .background(Color.cluViolet.opacity(0.15))
                            .foregroundStyle(Color.cluViolet)
                            .clipShape(Capsule())
                    }
                    if service.data?.is_promo == true, let label = service.data?.promo_label {
                        Text("\u{26A1} \(label)")
                            .font(.system(size: 10, weight: .bold))
                            .padding(.horizontal, 8)
                            .padding(.vertical, 3)
                            .background(Color.yellow.opacity(0.15))
                            .foregroundStyle(.yellow)
                            .clipShape(Capsule())
                    }
                }
                .padding(.horizontal, 16)
                .padding(.top, 16)
                .padding(.bottom, 12)

                if let data = service.data {
                    // Promo banner
                    if data.is_promo == true, let label = data.promo_label {
                        HStack(spacing: 8) {
                            Image(systemName: "bolt.fill")
                                .font(.system(size: 14))
                            Text("\(label.uppercased()) USAGE PROMOTION")
                                .font(.system(size: 12, weight: .bold))
                            Spacer()
                            Text("ACTIVE")
                                .font(.system(size: 9, weight: .heavy))
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(.white.opacity(0.2))
                                .clipShape(Capsule())
                        }
                        .foregroundStyle(.white)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 10)
                        .background(
                            LinearGradient(
                                colors: [Color(red: 0.85, green: 0.55, blue: 0.0), Color(red: 0.95, green: 0.70, blue: 0.1)],
                                startPoint: .leading, endPoint: .trailing
                            )
                        )
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .padding(.horizontal, 14)
                        .padding(.bottom, 10)
                    }

                    // Usage bars
                    UsageBar(label: "5-Hour Window", pct: data.pct_5h ?? 0, resetSecs: data.reset_5h_secs)
                        .padding(.horizontal, 16)
                        .padding(.bottom, 14)

                    UsageBar(label: "7-Day Window", pct: data.pct_7d ?? 0, resetSecs: data.reset_7d_secs)
                        .padding(.horizontal, 16)
                        .padding(.bottom, 14)

                    // Pace
                    if let pace = data.pace_pct {
                        let paceColor = pace <= 100 ? Color.cluGreen : pace <= 150 ? Color.cluOrange : Color.cluRed
                        let paceIcon = pace <= 100 ? "checkmark.circle.fill" : pace <= 150 ? "exclamationmark.triangle.fill" : "flame.fill"
                        let paceLabel = pace <= 100 ? "under budget" : pace <= 150 ? "ahead of budget" : "burning fast"

                        HStack(spacing: 6) {
                            Image(systemName: paceIcon)
                                .font(.system(size: 12))
                                .foregroundStyle(paceColor)
                            Text("Pace \(Int(pace))%")
                                .font(.system(size: 13, weight: .semibold, design: .rounded))
                                .foregroundStyle(paceColor)
                            Text(paceLabel)
                                .font(.system(size: 11))
                                .foregroundStyle(.secondary)
                        }
                        .padding(.horizontal, 16)
                        .padding(.bottom, 12)
                    }

                    // Charts
                    if let history = data.history, history.count >= 2 {
                        VStack(spacing: 8) {
                            WindowChartView(
                                title: "5-Hour Window",
                                history: history,
                                keyPath: \.fiveH,
                                resetISO: data.reset_5h_iso,
                                windowSecs: 5 * 3600,
                                color: .cluCyan
                            )
                            WindowChartView(
                                title: "7-Day Window",
                                history: history,
                                keyPath: \.sevenD,
                                resetISO: data.reset_7d_iso,
                                windowSecs: 7 * 86400,
                                color: .cluOrange
                            )
                        }
                        .padding(.horizontal, 10)
                        .padding(.bottom, 8)
                    }

                    Divider()
                        .padding(.horizontal, 16)

                    // Footer
                    HStack {
                        Text("Updated \(formatTimeAgo(service.lastUpdate))")
                            .font(.system(size: 10))
                            .foregroundStyle(.tertiary)
                        Spacer()
                        Button("Refresh") { service.triggerRefresh() }
                            .buttonStyle(.borderless)
                            .font(.system(size: 11))
                        Button("Quit") { NSApplication.shared.terminate(nil) }
                            .buttonStyle(.borderless)
                            .font(.system(size: 11))
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)

                } else {
                    // Loading state
                    VStack(spacing: 10) {
                        ProgressView()
                            .controlSize(.regular)
                        Text("Connecting to clu --serve...")
                            .font(.system(size: 12))
                            .foregroundStyle(.secondary)
                        Text("clu --serve")
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(.tertiary)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 4)
                            .background(.quaternary, in: RoundedRectangle(cornerRadius: 6))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(40)
                }
            }
        }
        .frame(width: 340, height: service.data != nil ? 620 : 200)
    }
}

// MARK: - App

@main
struct CLUMenuBarApp: App {
    @State private var service = UsageService()
    @State private var started = false

    var body: some Scene {
        MenuBarExtra {
            PopoverView(service: service)
                .onAppear {
                    if !started {
                        started = true
                        service.startPolling(interval: 30)
                    }
                }
        } label: {
            if let data = service.data {
                let pct = data.pct_5h ?? 0
                let icon = pct >= 90 ? "\u{1F534}" : pct >= 70 ? "\u{1F7E0}" : pct >= 40 ? "\u{1F7E1}" : "\u{1F7E2}"
                let promo = data.is_promo == true ? "\u{26A1}" : ""
                Text("\(promo)\(icon) \(Int(pct))%")
            } else {
                Text("clu \u{00B7}\u{00B7}\u{00B7}")
            }
        }
        .menuBarExtraStyle(.window)
    }
}
