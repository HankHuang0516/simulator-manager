import SwiftUI
import AppKit
import Foundation
import Darwin

struct Metrics: Decodable { let load_ratio: Double?; let disk_free_gib: Double?; let memory_free_percent: Double? }
struct Scheduler: Decodable { let stage: Int; let mode: String; let capacity: Int; let creation_allowed: Bool; let metrics: Metrics; let pressure: String }
struct Lease: Decodable, Identifiable {
    let resource_id: String; let pool: String; let session: String; let project: String
    let created: Double; let expires: Double; let hard_expires: Double; let yield_by: Double?; let renewals: Int; let operation: String?
    var id: String { resource_id }
    var end: Double { min(expires, hard_expires, yield_by ?? hard_expires) }
}
struct Waiter: Decodable, Identifiable { let seq: Int; let pool: String; let session: String; let project: String; let created: Double; var id: Int { seq } }
struct Session: Decodable, Identifiable { let session: String; let project: String; var id: String { session } }
struct Environment: Decodable, Identifiable { let resource: String; let pool: String; let project: String; let session: String; let phase: String; let running: Int; var id: String { resource } }
struct Event: Decodable, Identifiable { let seq: Int; let time: Double; let event: String; let session: String?; let resource: String?; var id: Int { seq } }
struct ComplianceFinding: Decodable, Identifiable {
    let fingerprint: String; let session: String; let project: String; let platform: String
    let kind: String; let first_seen: Double; let last_seen: Double; let active: Int; let guidance: String
    var id: String { fingerprint }
}
struct Policy: Decodable { let max_renewals: Int }
struct Snapshot: Decodable { let policy: Policy; let scheduler: Scheduler; let leases: [Lease]; let queue: [Waiter]; let sessions: [Session]; let environments: [Environment]; let events: [Event]; let compliance: [ComplianceFinding]?; let config_error: String? }

final class Bytes: @unchecked Sendable {
    private let lock = NSLock(); private var data = Data()
    func append(_ bytes: Data) { lock.lock(); defer { lock.unlock() }; if data.count < 2_000_000 { data.append(bytes) } }
    func value() -> Data { lock.lock(); defer { lock.unlock() }; return data }
}
func readStatus(cli: String, state: String) throws -> Snapshot {
    let p = Process(); p.executableURL = URL(fileURLWithPath: cli); p.arguments = ["status", "--state-dir", state, "--json"]
    // Native app processes start in `/`; Homebrew Python may scan that directory
    // during site initialization. Use the installed CLI directory so refreshes are
    // bounded and do not leave a Python process waiting on root-volume traversal.
    p.currentDirectoryURL = URL(fileURLWithPath: cli).deletingLastPathComponent()
    let out = Pipe(), err = Pipe(), bytes = Bytes(), errors = Bytes()
    p.standardOutput = out; p.standardError = err
    try p.run()
    let reads = DispatchGroup()
    reads.enter(); DispatchQueue.global().async { bytes.append(out.fileHandleForReading.readDataToEndOfFile()); reads.leave() }
    reads.enter(); DispatchQueue.global().async { errors.append(err.fileHandleForReading.readDataToEndOfFile()); reads.leave() }
    let deadline = Date().addingTimeInterval(8)
    while p.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
    if p.isRunning {
        p.terminate()
        let grace = Date().addingTimeInterval(1)
        while p.isRunning && Date() < grace { Thread.sleep(forTimeInterval: 0.05) }
        if p.isRunning { kill(p.processIdentifier, SIGKILL) }
        p.waitUntilExit()
        throw NSError(domain: "Status refresh timed out", code: 1)
    }
    p.waitUntilExit()
    guard reads.wait(timeout:.now()+2) == .success else { throw NSError(domain:"Status stream did not finish",code:1) }
    guard p.terminationStatus == 0 else {
        throw NSError(domain: String(data: errors.value(), encoding: .utf8) ?? "Manager unavailable", code: Int(p.terminationStatus))
    }
    return try JSONDecoder().decode(Snapshot.self, from: bytes.value())
}

@MainActor final class Model: ObservableObject {
    @Published var snapshot: Snapshot?; @Published var error: String?; @Published var updated: Date?
    @Published var pinned = true; @Published var selection = "Overview"; @Published var showGuide = false
    var refreshInFlight = false
    let cli: String; let state: String; let demo: String?
    init() {
        let args = CommandLine.arguments
        func arg(_ key: String) -> String? { guard let i = args.firstIndex(of: key), i+1 < args.count else { return nil }; return args[i+1] }
        let root = Bundle.main.bundleURL.deletingLastPathComponent()
        cli = arg("--cli") ?? Bundle.main.object(forInfoDictionaryKey:"SimulatorManagerCLI") as? String ?? root.appendingPathComponent("bin/sim-manager").path
        state = arg("--state-dir") ?? ProcessInfo.processInfo.environment["SIM_MANAGER_STATE_DIR"] ?? Bundle.main.object(forInfoDictionaryKey:"SimulatorManagerState") as? String ?? NSHomeDirectory()+"/Library/Application Support/simulator-manager"
        demo = arg("--demo")
        showGuide = args.contains("--onboarding") || !UserDefaults.standard.bool(forKey:"SimulatorManagerOnboardingComplete")
    }
    func refresh() async {
        guard !refreshInFlight else { return }; refreshInFlight = true; defer { refreshInFlight = false }
        let cli = self.cli, state = self.state, demo = self.demo
        let result = await Task.detached { () -> Result<Snapshot, Error> in
            do {
                if let demo { return .success(try JSONDecoder().decode(Snapshot.self, from: Data(contentsOf: URL(fileURLWithPath: demo)))) }
                return .success(try readStatus(cli: cli, state: state))
            } catch { return .failure(error) }
        }.value
        switch result {
        case .success(let s): snapshot = s; error = s.config_error; updated = Date()
        case .failure(let e): error = e.localizedDescription
        }
    }
}

struct GuideCopy {
    let title: String; let body: String; let bullets: [String]; let symbol: String
}
func localizedGuide() -> [GuideCopy] {
    let zh = Locale.preferredLanguages.first?.lowercased().hasPrefix("zh") == true
    if zh { return [
        GuideCopy(title:"歡迎使用 Simulator Manager",body:"這個小浮框是所有 Codex task 共用的模擬器中轉站。關閉視窗只會隱藏；可從選單列或「應用程式」再次打開。",bullets:["安裝完成後自動啟動","常駐選單列，隨時查看排程","不會清除任何模擬器資料"],symbol:"square.stack.3d.up.fill"),
        GuideCopy(title:"先完成不需要模擬器的檢查",body:"Codex 應先執行編譯、靜態檢查與主機單元測試；只有畫面、導覽、手勢、生命週期或執行期行為才進入分配流程。",bullets:["文件與純邏輯通常不需模擬器","需要 runtime/UI 驗證才提出請求","避免浪費啟動與佔用時間"],symbol:"hammer.fill"),
        GuideCopy(title:"讓 Tool 取得精確裝置",body:"在 Codex task 說「Use simulator-manager for this project.」。Tool 會排隊、啟動指定裝置、執行測試，並在成功、失敗或逾時後自動釋放。",bullets:["不得直接選擇 booted 裝置","不得使用未指定的 adb target","release 只歸還使用權，不關閉暖機裝置"],symbol:"play.circle.fill"),
        GuideCopy(title:"公平使用與安全讓位",body:"建立、開機與測試共用同一個 10 分鐘上限；有人等待時，工作必須在安全切點完成並回到隊尾。",bullets:["總佔用上限包含開機","續租次數有限","不得自行 shutdown；由管理器決定暖機重用或退役"],symbol:"person.2.fill"),
        GuideCopy(title:"偏離規則時會主動教學",body:"監督程式只讀取已登記 task 的程序關係。發現直接使用 simctl、adb、emulator 或未租用的模擬器測試時，會標記該 task 並產生修正指引。",bullets:["只記錄動作種類，不保存完整命令","不會終止 task 或裝置","已採用 Tool 的 task 會在下次互動收到專屬指引"],symbol:"graduationcap.fill")
    ] }
    return [
        GuideCopy(title:"Welcome to Simulator Manager",body:"This floating dashboard is the shared control plane between Codex tasks and mobile simulators. Closing the panel only hides it; reopen it from the menu bar or Applications.",bullets:["Opens after installation","Lives in the menu bar","Never erases simulator data"],symbol:"square.stack.3d.up.fill"),
        GuideCopy(title:"Run host checks first",body:"Codex should finish builds, static checks, and host unit tests before requesting a device. Enter the managed lane only for UI or runtime behavior.",bullets:["Docs and pure logic usually need no simulator","Request only for runtime or UI validation","Avoid unnecessary boot and occupancy time"],symbol:"hammer.fill"),
        GuideCopy(title:"Let the Tool assign one exact device",body:"Tell a Codex task “Use simulator-manager for this project.” The Tool queues, boots, runs, and releases after success, failure, interruption, or timeout.",bullets:["Never target booted implicitly","Never use an unspecified adb target","Release use rights; keep the verified device warm"],symbol:"play.circle.fill"),
        GuideCopy(title:"Share fairly and yield safely",body:"Creation, boot, and testing share one 10-minute cap. When someone waits, checkpoint safely and rejoin at the back of the queue.",bullets:["Boot time counts toward occupancy","Renewals are bounded","Never shut down a device; the manager reuses or retires it"],symbol:"person.2.fill"),
        GuideCopy(title:"Coaching appears when a task drifts",body:"The watcher reads process relationships for registered tasks. Direct simctl, adb, emulator, or unleased simulator tests create targeted guidance.",bullets:["Stores the action type, never the full command","Never kills a task or device","Tool-enabled tasks receive their lesson on the next interaction"],symbol:"graduationcap.fill")
    ]
}

struct QuickStartGuide: View {
    @ObservedObject var model: Model; @State private var page = 0
    private let pages = localizedGuide()
    var body: some View {
        VStack(spacing:22) {
            HStack { Text("QUICK START").font(.system(size:10,weight:.bold)).tracking(1.4).foregroundStyle(lavender); Spacer(); Text("\(page+1) / \(pages.count)").font(.system(size:11,design:.rounded)).foregroundStyle(.secondary) }
            ZStack { Circle().fill(LinearGradient(colors:[lavender.opacity(0.18),mint.opacity(0.10)],startPoint:.topLeading,endPoint:.bottomTrailing)).frame(width:92,height:92); Image(systemName:pages[page].symbol).font(.system(size:38,weight:.light)).foregroundStyle(lavender) }
            VStack(spacing:10) { Text(pages[page].title).font(.system(size:24,weight:.semibold,design:.rounded)).multilineTextAlignment(.center); Text(pages[page].body).font(.system(size:13)).foregroundStyle(.secondary).multilineTextAlignment(.center).lineSpacing(3) }
            VStack(alignment:.leading,spacing:10) { ForEach(pages[page].bullets,id:\.self) { item in Label(item,systemImage:"checkmark.circle.fill").font(.system(size:12)).foregroundStyle(ink).symbolRenderingMode(.palette).foregroundStyle(mint,ink) } }.frame(maxWidth:.infinity,alignment:.leading).padding(16).background(.white.opacity(0.65),in:RoundedRectangle(cornerRadius:18))
            HStack(spacing:10) {
                if page > 0 { Button("Back") { page -= 1 }.buttonStyle(.bordered) }
                Spacer()
                Button(page == pages.count-1 ? "Start sharing" : "Next") {
                    if page == pages.count-1 { UserDefaults.standard.set(true,forKey:"SimulatorManagerOnboardingComplete"); model.showGuide=false } else { page += 1 }
                }.buttonStyle(.borderedProminent).tint(lavender)
            }
        }.padding(28).frame(width:440).foregroundStyle(ink).background(LinearGradient(colors:[Color(red:0.95,green:0.96,blue:1),Color(red:0.97,green:0.99,blue:0.98)],startPoint:.topLeading,endPoint:.bottomTrailing))
    }
}

let ink = Color(red: 0.13, green: 0.16, blue: 0.26)
let lavender = Color(red: 0.40, green: 0.34, blue: 0.88)
let mint = Color(red: 0.08, green: 0.61, blue: 0.48)
func projectName(_ path: String) -> String { URL(fileURLWithPath: path).lastPathComponent }
func symbol(_ pool: String) -> String { pool == "ios" ? "iphone" : pool == "android" ? "smartphone" : "display" }
func duration(_ seconds: Double) -> String { let n = max(0, Int(seconds)); return String(format: "%02d:%02d", n/60, n%60) }

struct CapsuleLabel: View {
    let text: String; var color = mint
    var body: some View { Text(text).font(.system(size: 10, weight: .semibold)).foregroundStyle(color).padding(.horizontal, 9).padding(.vertical, 5).background(color.opacity(0.10), in: Capsule()) }
}
struct SectionTitle: View {
    let title: String; let count: Int
    var body: some View { HStack { Text(title).font(.system(size: 13, weight: .semibold)); Spacer(); Text(String(count)).font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary) }.foregroundStyle(ink) }
}
struct EmptyRow: View {
    let icon: String; let title: String; let detail: String
    var body: some View { HStack(spacing: 12) { Image(systemName: icon).font(.system(size: 21, weight: .light)).foregroundStyle(mint); VStack(alignment: .leading, spacing: 3) { Text(title).font(.system(size: 12, weight: .medium)); Text(detail).font(.system(size: 10)).foregroundStyle(.secondary) }; Spacer() }.padding(15).background(.white.opacity(0.65), in: RoundedRectangle(cornerRadius: 17)) }
}
struct LeaseRow: View {
    let lease: Lease; let renewalLimit: Int
    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { tick in
            let now = tick.date.timeIntervalSince1970
            let remaining = max(0, lease.end-now)
            let progress = min(1, max(0, (now-lease.created)/max(1, lease.end-lease.created)))
            let color = lease.yield_by != nil || remaining < 30 ? Color.orange : lavender
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 11) {
                    Image(systemName: symbol(lease.pool)).font(.system(size: 19)).foregroundStyle(color).frame(width: 35,height: 35).background(color.opacity(0.10),in: RoundedRectangle(cornerRadius: 11))
                    VStack(alignment: .leading, spacing: 3) { Text(projectName(lease.project)).font(.system(size: 12, weight: .semibold)).lineLimit(1); Text("\(lease.pool.uppercased()) · \(lease.operation ?? "Reserved")").font(.system(size: 10)).foregroundStyle(.secondary) }
                    Spacer(); Text(duration(remaining)).font(.system(size: 18, weight: .semibold, design: .rounded)).monospacedDigit().foregroundStyle(color)
                }
                GeometryReader { g in ZStack(alignment: .leading) { Capsule().fill(color.opacity(0.10)); Capsule().fill(color).frame(width: g.size.width*progress) } }.frame(height: 4)
                HStack { Text("\(lease.session.prefix(8)) · \(lease.renewals)/\(renewalLimit) renewals"); Spacer(); Text(lease.yield_by != nil ? "Checkpoint & requeue" : "Lease remaining") }.font(.system(size: 9)).foregroundStyle(.secondary)
            }.padding(14).background(.white.opacity(0.78), in: RoundedRectangle(cornerRadius: 18))
        }
    }
}
struct WaitRow: View {
    let waiter: Waiter; let position: Int
    var body: some View { HStack(spacing: 10) { Text(String(position)).font(.system(size: 12, weight: .semibold, design: .rounded)).foregroundStyle(lavender).frame(width: 27, height: 27).background(lavender.opacity(0.09), in: Circle()); VStack(alignment: .leading, spacing: 3) { Text(projectName(waiter.project)).font(.system(size: 12, weight: .medium)).lineLimit(1); Text("\(waiter.pool.uppercased()) · \(waiter.session.prefix(8))").font(.system(size: 9)).foregroundStyle(.secondary) }; Spacer(); TimelineView(.periodic(from: .now, by: 1)) { tick in Text(duration(tick.date.timeIntervalSince1970-waiter.created)).font(.system(size: 11, design: .rounded)).monospacedDigit().foregroundStyle(.secondary) } }.padding(12).background(.white.opacity(0.65), in: RoundedRectangle(cornerRadius: 15)) }
}
struct Metric: View {
    let name: String; let value: String; let icon: String
    var body: some View { VStack(alignment: .leading, spacing: 7) { Label(name, systemImage: icon).font(.system(size: 9)).foregroundStyle(.secondary); Text(value).font(.system(size: 15, weight: .semibold, design: .rounded)).foregroundStyle(ink) }.frame(maxWidth: .infinity, alignment: .leading).padding(12).background(.white.opacity(0.65), in: RoundedRectangle(cornerRadius: 14)) }
}
struct Dashboard: View {
    @ObservedObject var model: Model
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                ZStack { RoundedRectangle(cornerRadius: 13).fill(LinearGradient(colors: [lavender, Color(red:0.54,green:0.66,blue:0.99)], startPoint:.topLeading,endPoint:.bottomTrailing)); Image(systemName:"square.stack.3d.up.fill").font(.system(size:19)).foregroundStyle(.white) }.frame(width: 39,height: 39)
                VStack(alignment:.leading,spacing:3) { Text("Simulator Manager").font(.system(size:17,weight:.semibold,design:.rounded)); HStack(spacing:5) { Circle().fill(model.error == nil && model.updated != nil ? mint : Color.orange).frame(width:5,height:5); Text(model.demo != nil ? "Preview · sample data" : model.error == nil && model.updated != nil ? "Live shared scheduling" : "Connecting to manager").font(.system(size:10)).foregroundStyle(.secondary) } }
                Spacer()
                Button { model.showGuide = true } label: { Image(systemName:"questionmark").font(.system(size:12,weight:.semibold)).foregroundStyle(lavender).frame(width:27,height:27).background(.white.opacity(0.6),in:Circle()) }.buttonStyle(.plain).help("Open the Quick Start guide")
                Button { model.pinned.toggle(); NSApp.windows.first?.level = model.pinned ? .floating : .normal } label: { Image(systemName: model.pinned ? "pin.fill" : "pin").font(.system(size:12)).foregroundStyle(model.pinned ? lavender : .secondary).frame(width:27,height:27).background(.white.opacity(0.6),in:Circle()) }.buttonStyle(.plain).help("Keep window above other apps")
                Button { NSApp.windows.first?.orderOut(nil) } label: { Image(systemName:"xmark").font(.system(size:10,weight:.semibold)).foregroundStyle(.secondary).frame(width:27,height:27).background(.white.opacity(0.6),in:Circle()) }.buttonStyle(.plain).help("Hide dashboard; reopen from the menu bar")
            }.padding(.horizontal,20).padding(.top,22).padding(.bottom,17)
            ScrollView {
                VStack(alignment:.leading,spacing:17) {
                    if let error = model.error { Label(error,systemImage:"exclamationmark.triangle.fill").font(.system(size:11)).foregroundStyle(.orange).padding(12).frame(maxWidth:.infinity,alignment:.leading).background(.orange.opacity(0.08),in:RoundedRectangle(cornerRadius:14)) }
                    if let s = model.snapshot {
                        VStack(alignment:.leading,spacing:12) {
                            HStack { Text(["Dynamic Pool","Constrained","Draining","Traditional Mode"][min(3,max(0,s.scheduler.stage))]).font(.system(size:21,weight:.semibold,design:.rounded)); Spacer(); CapsuleLabel(text:s.scheduler.pressure.capitalized,color:s.scheduler.pressure == "healthy" ? mint : .orange) }
                            Text(s.scheduler.creation_allowed ? "Private environments · up to \(s.scheduler.capacity) mobile allocations" : "New environments paused · existing work stays protected").font(.system(size:10)).foregroundStyle(.secondary)
                            HStack(spacing:5) { ForEach(0..<4) { stage in Capsule().fill(stage <= s.scheduler.stage ? (s.scheduler.stage == 0 ? mint : Color.orange) : ink.opacity(0.07)).frame(height:4) } }
                            HStack { Text("Dynamic"); Spacer(); Text("Traditional") }.font(.system(size:9)).foregroundStyle(.secondary)
                        }.padding(16).background(.white.opacity(0.7),in:RoundedRectangle(cornerRadius:20))
                        HStack(spacing:8) { Metric(name:"In use",value:String(s.leases.count),icon:"bolt.fill"); Metric(name:"Waiting",value:String(s.queue.count),icon:"line.3.horizontal"); Metric(name:"Registered",value:String(s.sessions.count),icon:"person.2.fill") }
                        if let findings = s.compliance, !findings.isEmpty {
                            SectionTitle(title:"Guidance center",count:findings.count)
                            ForEach(findings) { finding in
                                VStack(alignment:.leading,spacing:8) {
                                    HStack { Image(systemName:"graduationcap.fill").foregroundStyle(.orange); VStack(alignment:.leading,spacing:2) { Text(projectName(finding.project)).font(.system(size:12,weight:.semibold)); Text("\(finding.platform.uppercased()) · \(finding.kind.replacingOccurrences(of:"-",with:" ")) · \(finding.session.prefix(8))").font(.system(size:9)).foregroundStyle(.secondary) }; Spacer(); CapsuleLabel(text:"GUIDANCE",color:.orange) }
                                    Text(finding.guidance).font(.system(size:10)).foregroundStyle(.secondary).lineSpacing(2)
                                }.padding(14).background(.orange.opacity(0.08),in:RoundedRectangle(cornerRadius:18)).overlay(RoundedRectangle(cornerRadius:18).stroke(.orange.opacity(0.18)))
                            }
                        }
                        SectionTitle(title:"Active allocations",count:s.leases.count)
                        if s.leases.isEmpty { EmptyRow(icon:"checkmark.circle",title:"Resources are resting",detail:"Runtime requests appear here automatically.") } else { ForEach(s.leases) { LeaseRow(lease:$0,renewalLimit:s.policy.max_renewals) } }
                        SectionTitle(title:"FIFO waiting line",count:s.queue.count)
                        if s.queue.isEmpty { EmptyRow(icon:"sparkles",title:"No requests waiting",detail:"Build and unit work can continue independently.") } else { ForEach(Array(s.queue.enumerated()),id:\.element.id) { index, waiter in WaitRow(waiter:waiter,position:index+1) } }
                        HStack(spacing:8) { Metric(name:"CPU load / core",value:s.scheduler.metrics.load_ratio.map { String(format:"%.0f%%",$0*100) } ?? "—",icon:"cpu"); Metric(name:"Memory free",value:s.scheduler.metrics.memory_free_percent.map { String(format:"%.0f%%",$0) } ?? "—",icon:"memorychip"); Metric(name:"Disk free",value:s.scheduler.metrics.disk_free_gib.map { String(format:"%.1f GB",$0) } ?? "—",icon:"externaldrive") }
                        DisclosureGroup {
                            VStack(spacing:8) { ForEach(s.sessions) { session in HStack { Circle().fill(s.leases.contains { $0.session == session.session } ? lavender : ink.opacity(0.15)).frame(width:6,height:6); VStack(alignment:.leading,spacing:2) { Text(projectName(session.project)).font(.system(size:11,weight:.medium)); Text(String(session.session.prefix(8))).font(.system(size:9)).foregroundStyle(.secondary) }; Spacer(); CapsuleLabel(text:s.leases.contains { $0.session == session.session } ? "Allocated" : s.queue.contains { $0.session == session.session } ? "Queued" : "Registered",color:s.leases.contains { $0.session == session.session } ? lavender : mint) }.padding(.vertical,5) } }.padding(.top,7)
                        } label: { SectionTitle(title:"Sessions",count:s.sessions.count) }.tint(lavender)
                        DisclosureGroup {
                            VStack(spacing:8) { ForEach(s.environments) { env in HStack { Image(systemName:symbol(env.pool)).foregroundStyle(lavender); Text(projectName(env.project)).font(.system(size:11)).lineLimit(1); Spacer(); CapsuleLabel(text:env.running == 1 ? "Running" : env.phase.capitalized) }.padding(.vertical,5) } }.padding(.top,7)
                        } label: { SectionTitle(title:"Private environments",count:s.environments.count) }.tint(lavender)
                        if !s.events.isEmpty { DisclosureGroup {
                            VStack(alignment:.leading,spacing:9) { ForEach(Array(s.events.prefix(8))) { event in HStack { Circle().fill(event.event == "acquired" ? lavender : mint).frame(width:5,height:5); Text(event.event.replacingOccurrences(of:"-",with:" ").capitalized).font(.system(size:10)); Spacer(); Text(Date(timeIntervalSince1970:event.time),style:.time).font(.system(size:9)).foregroundStyle(.secondary) } } }.padding(.top,8)
                        } label: { SectionTitle(title:"Recent activity",count:min(8,s.events.count)) }.tint(lavender) }
                    } else { EmptyRow(icon:"antenna.radiowaves.left.and.right",title:"Loading shared state",detail:"The dashboard follows your local manager.") }
                }.padding(.horizontal,20).padding(.bottom,18)
            }.scrollIndicators(.hidden)
            HStack { Text("VIEW ONLY").font(.system(size:8,weight:.semibold)).tracking(1).foregroundStyle(lavender); Spacer(); if let updated = model.updated { Text("Updated \(updated.formatted(date:.omitted,time:.standard))").font(.system(size:9)).foregroundStyle(.secondary) }; Button { Task { await model.refresh() } } label:{ Image(systemName:"arrow.clockwise").font(.system(size:11)).foregroundStyle(lavender) }.buttonStyle(.plain).help("Refresh") }.padding(.horizontal,21).padding(.vertical,13).background(.white.opacity(0.45))
        }.foregroundStyle(ink).background(LinearGradient(colors:[Color(red:0.94,green:0.95,blue:1),Color(red:0.96,green:0.98,blue:0.98)],startPoint:.topLeading,endPoint:.bottomTrailing)).background(.ultraThinMaterial).clipShape(RoundedRectangle(cornerRadius:26)).preferredColorScheme(.light)
        .sheet(isPresented:$model.showGuide) { QuickStartGuide(model:model) }
        .task { while !Task.isCancelled { await model.refresh(); try? await Task.sleep(nanoseconds:2_000_000_000) } }
    }
}

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    var panel: NSPanel!; var item: NSStatusItem!; let model = Model()
    func applicationDidFinishLaunching(_ notification: Notification) {
        panel = NSPanel(contentRect:NSRect(x:0,y:0,width:420,height:690),styleMask:[.borderless,.resizable],backing:.buffered,defer:false)
        panel.title = "Simulator Manager"; panel.isFloatingPanel = true; panel.level = .floating; panel.isMovableByWindowBackground = true
        panel.hidesOnDeactivate = false; panel.isOpaque = false; panel.backgroundColor = .clear; panel.hasShadow = true
        panel.collectionBehavior = [.canJoinAllSpaces,.fullScreenAuxiliary]; panel.minSize = NSSize(width:380,height:440); panel.maxSize = NSSize(width:560,height:1100)
        panel.contentView = NSHostingView(rootView:Dashboard(model:model)); panel.center()
        let args = CommandLine.arguments
        if let i = args.firstIndex(of:"--render-preview"), i+1 < args.count {
            let path = args[i+1]
            Task { await model.refresh(); try? await Task.sleep(nanoseconds:300_000_000)
                if let view = panel.contentView {
                    view.layoutSubtreeIfNeeded()
                    if let rep = view.bitmapImageRepForCachingDisplay(in:view.bounds) {
                        view.cacheDisplay(in:view.bounds,to:rep)
                        try? rep.representation(using:.png,properties:[:])?.write(to:URL(fileURLWithPath:path))
                    }
                }
                NSApp.terminate(nil)
            }
            return
        }
        panel.makeKeyAndOrderFront(nil)
        let mainMenu = NSMenu(), appMenu = NSMenu(), appItem = NSMenuItem()
        let quitItem = NSMenuItem(title:"Quit Dashboard",action:#selector(quit),keyEquivalent:"q")
        quitItem.target = self; appMenu.addItem(quitItem); appItem.submenu = appMenu
        mainMenu.addItem(appItem); NSApp.mainMenu = mainMenu
        item = NSStatusBar.system.statusItem(withLength:NSStatusItem.squareLength)
        item.button?.image = NSImage(systemSymbolName:"square.stack.3d.up",accessibilityDescription:"Simulator Manager")
        let menu = NSMenu(); menu.addItem(withTitle:"Show Dashboard",action:#selector(show),keyEquivalent:""); menu.addItem(withTitle:"Quick Start Guide",action:#selector(guide),keyEquivalent:""); menu.addItem(.separator()); menu.addItem(withTitle:"Quit Dashboard",action:#selector(quit),keyEquivalent:"q")
        for child in menu.items { child.target = self }; item.menu = menu
        NSApp.activate(ignoringOtherApps:true)
    }
    @objc func show() { panel.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps:true) }
    @objc func guide() { model.showGuide = true; show() }
    @objc func quit() { NSApp.terminate(nil) }
    func applicationShouldHandleReopen(_ sender:NSApplication,hasVisibleWindows:Bool)->Bool { show(); return true }
}
@main struct SimulatorManagerApp {
    @MainActor static func main() {
        let app = NSApplication.shared
        let delegate = Delegate()
        app.delegate = delegate; app.setActivationPolicy(.accessory)
        withExtendedLifetime(delegate) { app.run() }
    }
}
