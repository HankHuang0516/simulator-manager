import SwiftUI
import AppKit
import Foundation
import Combine
import Darwin

let dashboardShowNotification = Notification.Name("com.hankhuang.simulator-manager.dashboard.show")

final class DashboardSingleton {
    private let descriptor: Int32
    private init(descriptor: Int32) { self.descriptor = descriptor }
    static func acquire() -> DashboardSingleton? {
        let support = FileManager.default.urls(for:.applicationSupportDirectory,in:.userDomainMask).first!
        let directory = support.appendingPathComponent("simulator-manager",isDirectory:true)
        try? FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true,
                                                 attributes:[.posixPermissions:0o700])
        let path = directory.appendingPathComponent("dashboard-ui.lock").path
        let fd = Darwin.open(path,O_CREAT | O_RDWR,S_IRUSR | S_IWUSR)
        guard fd >= 0 else { return nil }
        var request = flock(); request.l_type = Int16(F_WRLCK); request.l_whence = Int16(SEEK_SET)
        guard Darwin.fcntl(fd,F_SETLK,&request) != -1 else { Darwin.close(fd); return nil }
        return DashboardSingleton(descriptor:fd)
    }
    deinit {
        var request = flock(); request.l_type = Int16(F_UNLCK); request.l_whence = Int16(SEEK_SET)
        _ = Darwin.fcntl(descriptor,F_SETLK,&request); Darwin.close(descriptor)
    }
}

func revealExistingDashboard(showGuide: Bool) {
    DistributedNotificationCenter.default().post(name:dashboardShowNotification,object:nil,
                                                   userInfo:["showGuide":showGuide])
    for application in NSRunningApplication.runningApplications(withBundleIdentifier:"com.hankhuang.simulator-manager.dashboard") {
        application.activate(options:[.activateIgnoringOtherApps])
    }
}

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
struct Event: Decodable, Identifiable {
    let seq: Int; let time: Double; let event: String; let session: String?; let resource: String?
    let project: String?; let pool: String?; let started_at: Double?; let duration_seconds: Double?
    var id: Int { seq }
}
struct ComplianceFinding: Decodable, Identifiable {
    let fingerprint: String; let session: String; let project: String; let platform: String
    let kind: String; let first_seen: Double; let last_seen: Double; let active: Int; let guidance: String
    var id: String { fingerprint }
}
struct AuditResult: Decodable {
    let observed_at: Double; let registered_sessions: Int; let observable_sessions: Int
    let active_findings: Int; let findings: [ComplianceFinding]; let enforcement: String; let safety: String
}
struct Policy: Decodable { let max_renewals: Int }
struct Snapshot: Decodable { let policy: Policy; let scheduler: Scheduler; let leases: [Lease]; let queue: [Waiter]; let sessions: [Session]; let environments: [Environment]; let events: [Event]; let compliance: [ComplianceFinding]?; let config_error: String? }

enum AppLanguage: String, CaseIterable, Identifiable {
    case automatic, english, chinese
    var id: String { rawValue }
}

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

func readAudit(cli: String, state: String) throws -> AuditResult {
    let p = Process(); p.executableURL = URL(fileURLWithPath:cli); p.arguments = ["audit","--state-dir",state,"--json"]
    p.currentDirectoryURL = URL(fileURLWithPath:cli).deletingLastPathComponent()
    let out = Pipe(), err = Pipe(), bytes = Bytes(), errors = Bytes()
    p.standardOutput = out; p.standardError = err
    try p.run()
    let reads = DispatchGroup()
    reads.enter(); DispatchQueue.global().async { bytes.append(out.fileHandleForReading.readDataToEndOfFile()); reads.leave() }
    reads.enter(); DispatchQueue.global().async { errors.append(err.fileHandleForReading.readDataToEndOfFile()); reads.leave() }
    let deadline = Date().addingTimeInterval(8)
    while p.isRunning && Date() < deadline { Thread.sleep(forTimeInterval:0.05) }
    if p.isRunning {
        p.terminate()
        let grace = Date().addingTimeInterval(1)
        while p.isRunning && Date() < grace { Thread.sleep(forTimeInterval:0.05) }
        if p.isRunning { kill(p.processIdentifier,SIGKILL) }
        p.waitUntilExit(); throw NSError(domain:"Monitor scan timed out",code:1)
    }
    p.waitUntilExit()
    guard reads.wait(timeout:.now()+2) == .success else { throw NSError(domain:"Monitor stream did not finish",code:1) }
    guard p.terminationStatus == 0 else { throw NSError(domain:String(data:errors.value(),encoding:.utf8) ?? "Monitor unavailable",code:Int(p.terminationStatus)) }
    return try JSONDecoder().decode(AuditResult.self,from:bytes.value())
}

@MainActor final class Model: ObservableObject {
    @Published var snapshot: Snapshot?; @Published var error: String?; @Published var updated: Date?
    @Published var pinned = true; @Published var selection = "Overview"; @Published var showGuide = false; @Published var showMonitor = false
    @Published var auditResult: AuditResult?; @Published var auditError: String?; @Published var auditInFlight = false
    @Published var language: AppLanguage { didSet { UserDefaults.standard.set(language.rawValue,forKey:"SimulatorManagerLanguage") } }
    var refreshInFlight = false
    let cli: String; let state: String; let demo: String?
    init() {
        language = AppLanguage(rawValue:UserDefaults.standard.string(forKey:"SimulatorManagerLanguage") ?? "") ?? .automatic
        let args = CommandLine.arguments
        func arg(_ key: String) -> String? { guard let i = args.firstIndex(of: key), i+1 < args.count else { return nil }; return args[i+1] }
        let root = Bundle.main.bundleURL.deletingLastPathComponent()
        cli = arg("--cli") ?? Bundle.main.object(forInfoDictionaryKey:"SimulatorManagerCLI") as? String ?? root.appendingPathComponent("bin/sim-manager").path
        state = arg("--state-dir") ?? ProcessInfo.processInfo.environment["SIM_MANAGER_STATE_DIR"] ?? Bundle.main.object(forInfoDictionaryKey:"SimulatorManagerState") as? String ?? NSHomeDirectory()+"/Library/Application Support/simulator-manager"
        demo = arg("--demo")
        showGuide = args.contains("--onboarding") || !UserDefaults.standard.bool(forKey:"SimulatorManagerOnboardingComplete")
    }
    var usesChinese: Bool {
        switch language {
        case .chinese: return true
        case .english: return false
        case .automatic: return Locale.preferredLanguages.first?.lowercased().hasPrefix("zh") == true
        }
    }
    func text(_ english: String, _ chinese: String) -> String { usesChinese ? chinese : english }
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
    func auditNow() async {
        guard !auditInFlight else { return }
        showMonitor = true; auditInFlight = true; auditError = nil
        defer { auditInFlight = false }
        if demo != nil, let s = snapshot {
            let findings = s.compliance ?? []
            auditResult = AuditResult(observed_at:Date().timeIntervalSince1970,registered_sessions:s.sessions.count,
                                      observable_sessions:s.sessions.count,active_findings:findings.count,
                                      findings:findings,enforcement:"guidance-only",
                                      safety:"No process, task, simulator, emulator, or adb server is stopped by compliance coaching.")
            return
        }
        let cli = self.cli, state = self.state
        let result = await Task.detached { () -> Result<AuditResult,Error> in
            do { return .success(try readAudit(cli:cli,state:state)) } catch { return .failure(error) }
        }.value
        switch result {
        case .success(let report): auditResult = report; await refresh()
        case .failure(let error): auditError = error.localizedDescription
        }
    }
}

struct GuideCopy {
    let title: String; let body: String; let bullets: [String]; let symbol: String
}
func localizedGuide(_ zh: Bool) -> [GuideCopy] {
    if zh { return [
        GuideCopy(title:"歡迎使用 Simulator Manager",body:"這個小浮框是所有 Codex task 共用的模擬器中轉站。關閉視窗只會隱藏；可從選單列或「應用程式」再次打開。",bullets:["安裝完成後自動啟動","常駐選單列，隨時查看排程","不會清除任何模擬器資料"],symbol:"square.stack.3d.up.fill"),
        GuideCopy(title:"先完成不需要模擬器的檢查",body:"Codex 應先執行編譯、靜態檢查與主機單元測試；只有畫面、導覽、手勢、生命週期或執行期行為才進入分配流程。",bullets:["文件與純邏輯通常不需模擬器","需要 runtime/UI 驗證才提出請求","避免浪費啟動與佔用時間"],symbol:"hammer.fill"),
        GuideCopy(title:"讓 Tool 取得精確裝置",body:"在 Codex task 說「Use simulator-manager for this project.」。Tool 會排隊、啟動指定裝置、執行測試，並在成功、失敗、逾時或中斷後自動釋放。",bullets:["不得直接選擇 booted 裝置","不得使用未指定的 adb target","每個 task 都只能 release，必須保留暖機裝置"],symbol:"play.circle.fill"),
        GuideCopy(title:"公平使用與安全讓位",body:"建立、開機、安裝、測試與匯出共用同一個 40 分鐘上限；有人等待時，仍須在 2 分鐘安全切點完成並回到隊尾。",bullets:["1800 秒 soak 可在無人等待時完成","總佔用上限包含開機與匯出","禁止 simctl shutdown、adb emu kill 或關閉 emulator"],symbol:"person.2.fill"),
        GuideCopy(title:"偏離規則時會主動教學",body:"監督程式只讀取已登記 task 的程序關係。發現直接使用 simctl、adb、emulator 或未租用的模擬器測試時，會標記該 task 並產生修正指引。",bullets:["只記錄動作種類，不保存完整命令","不會終止 task 或裝置","已採用 Tool 的 task 會在下次互動收到專屬指引"],symbol:"graduationcap.fill")
    ] }
    return [
        GuideCopy(title:"Welcome to Simulator Manager",body:"This floating dashboard is the shared control plane between Codex tasks and mobile simulators. Closing the panel only hides it; reopen it from the menu bar or Applications.",bullets:["Opens after installation","Lives in the menu bar","Never erases simulator data"],symbol:"square.stack.3d.up.fill"),
        GuideCopy(title:"Run host checks first",body:"Codex should finish builds, static checks, and host unit tests before requesting a device. Enter the managed lane only for UI or runtime behavior.",bullets:["Docs and pure logic usually need no simulator","Request only for runtime or UI validation","Avoid unnecessary boot and occupancy time"],symbol:"hammer.fill"),
        GuideCopy(title:"Let the Tool assign one exact device",body:"Tell a Codex task “Use simulator-manager for this project.” The Tool queues, boots, runs, and releases after success, failure, interruption, or timeout.",bullets:["Never target booted implicitly","Never use an unspecified adb target","Every task must release and leave the verified device warm"],symbol:"play.circle.fill"),
        GuideCopy(title:"Share fairly and yield safely",body:"Creation, boot, installation, testing, and export share one 40-minute cap. When someone waits, checkpoint at the 2-minute boundary and rejoin at the back of the queue.",bullets:["A 1800-second soak fits while nobody waits","Boot and export count toward occupancy","Never shut down a device; the manager reuses or retires it"],symbol:"person.2.fill"),
        GuideCopy(title:"Coaching appears when a task drifts",body:"The watcher reads process relationships for registered tasks. Direct simctl, adb, emulator, or unleased simulator tests create targeted guidance.",bullets:["Stores the action type, never the full command","Never kills a task or device","Tool-enabled tasks receive their lesson on the next interaction"],symbol:"graduationcap.fill")
    ]
}

struct QuickStartGuide: View {
    @ObservedObject var model: Model; @State private var page = 0
    var body: some View {
        let pages = localizedGuide(model.usesChinese)
        VStack(spacing:22) {
            HStack { Text(model.text("QUICK START","快速開始")).font(.system(size:10,weight:.bold)).tracking(1.4).foregroundStyle(lavender); Spacer(); Text("\(page+1) / \(pages.count)").font(.system(size:11,design:.rounded)).foregroundStyle(.secondary) }
            ZStack { Circle().fill(LinearGradient(colors:[lavender.opacity(0.18),mint.opacity(0.10)],startPoint:.topLeading,endPoint:.bottomTrailing)).frame(width:92,height:92); Image(systemName:pages[page].symbol).font(.system(size:38,weight:.light)).foregroundStyle(lavender) }
            VStack(spacing:10) { Text(pages[page].title).font(.system(size:24,weight:.semibold,design:.rounded)).multilineTextAlignment(.center); Text(pages[page].body).font(.system(size:13)).foregroundStyle(.secondary).multilineTextAlignment(.center).lineSpacing(3) }
            VStack(alignment:.leading,spacing:10) { ForEach(pages[page].bullets,id:\.self) { item in Label(item,systemImage:"checkmark.circle.fill").font(.system(size:12)).foregroundStyle(ink).symbolRenderingMode(.palette).foregroundStyle(mint,ink) } }.frame(maxWidth:.infinity,alignment:.leading).padding(16).background(.white.opacity(0.65),in:RoundedRectangle(cornerRadius:18))
            HStack(spacing:10) {
                if page > 0 { Button(model.text("Back","上一步")) { page -= 1 }.buttonStyle(.bordered) }
                Spacer()
                Button(page == pages.count-1 ? model.text("Start sharing","開始共用") : model.text("Next","下一步")) {
                    if page == pages.count-1 { UserDefaults.standard.set(true,forKey:"SimulatorManagerOnboardingComplete"); model.showGuide=false } else { page += 1 }
                }.buttonStyle(.borderedProminent).tint(lavender)
            }
        }.padding(28).frame(width:440).foregroundStyle(ink).background(LinearGradient(colors:[Color(red:0.95,green:0.96,blue:1),Color(red:0.97,green:0.99,blue:0.98)],startPoint:.topLeading,endPoint:.bottomTrailing))
    }
}

struct BypassMonitor: View {
    @ObservedObject var model: Model
    var body: some View {
        let zh = model.usesChinese
        let report = model.auditResult
        let active = report?.findings.filter { $0.active == 1 } ?? []
        let covered = report?.observable_sessions ?? 0
        let registered = report?.registered_sessions ?? 0
        let gap = max(0,registered-covered)
        VStack(spacing:0) {
            HStack(spacing:12) {
                ZStack { Circle().fill((active.isEmpty ? mint : Color.orange).opacity(0.14)).frame(width:44,height:44); Image(systemName:active.isEmpty ? "eye.circle.fill" : "exclamationmark.shield.fill").font(.system(size:22)).foregroundStyle(active.isEmpty ? mint : .orange) }
                VStack(alignment:.leading,spacing:3) { Text(tr("Bypass monitor","繞道監視",zh)).font(.system(size:20,weight:.semibold,design:.rounded)); Text(tr("Read-only inspection of registered task process trees","只讀檢查已登記 task 的程序關係",zh)).font(.system(size:10)).foregroundStyle(.secondary) }
                Spacer()
                Button { model.showMonitor=false } label:{ Image(systemName:"xmark").foregroundStyle(.secondary).frame(width:28,height:28).background(.white.opacity(0.65),in:Circle()) }.buttonStyle(.plain)
            }.padding(22)
            ScrollView {
                VStack(alignment:.leading,spacing:14) {
                    if model.auditInFlight {
                        HStack(spacing:10) { ProgressView().controlSize(.small); Text(tr("Inspecting current simulator and emulator access…","正在檢查目前的模擬器使用狀況⋯",zh)).font(.system(size:12,weight:.medium)) }.padding(16).frame(maxWidth:.infinity,alignment:.leading).background(.white.opacity(0.65),in:RoundedRectangle(cornerRadius:16))
                    } else if let error = model.auditError {
                        Label(error,systemImage:"exclamationmark.triangle.fill").font(.system(size:11)).foregroundStyle(.orange).padding(16).frame(maxWidth:.infinity,alignment:.leading).background(.orange.opacity(0.08),in:RoundedRectangle(cornerRadius:16))
                    } else if let report {
                        VStack(alignment:.leading,spacing:8) {
                            Label(active.isEmpty ? tr("No attributable bypass detected now","目前未偵測到可歸因的繞道使用",zh) : tr("Managed-lane bypass detected","偵測到繞過管理通道",zh),systemImage:active.isEmpty ? "checkmark.shield.fill" : "exclamationmark.shield.fill").font(.system(size:14,weight:.semibold)).foregroundStyle(active.isEmpty ? mint : .orange)
                            Text(active.isEmpty ? tr("No registered, observable task is currently using a simulator or emulator outside a matching lease.","目前沒有已登記且可觀察的 task 在對應租約外使用模擬器。",zh) : tr("These actions can cause device contention, cross-task data corruption, unreliable tests, or ADB disruption.","這些行為可能造成裝置互搶、跨 task 資料污染、測試結果不可靠或 ADB 中斷。",zh)).font(.system(size:11)).foregroundStyle(.secondary).lineSpacing(2)
                        }.padding(16).background((active.isEmpty ? mint : Color.orange).opacity(0.08),in:RoundedRectangle(cornerRadius:17)).overlay(RoundedRectangle(cornerRadius:17).stroke((active.isEmpty ? mint : Color.orange).opacity(0.18)))
                        HStack(spacing:8) { Metric(name:tr("Findings","違規",zh),value:String(active.count),icon:"exclamationmark.shield"); Metric(name:tr("Observable","可觀察",zh),value:"\(covered)/\(registered)",icon:"eye"); Metric(name:tr("Coverage gaps","覆蓋缺口",zh),value:String(gap),icon:"questionmark.circle") }
                        if gap > 0 || registered == 0 {
                            VStack(alignment:.leading,spacing:7) {
                                Label(tr("Coverage boundary","監視範圍限制",zh),systemImage:"scope").font(.system(size:12,weight:.semibold)).foregroundStyle(lavender)
                                Text(registered == 0 ? tr("No task has registered with Simulator Manager. Enable the Tool in each Codex task before relying on attribution.","目前沒有 task 登記 Simulator Manager。每個 Codex task 都必須先啟用 Tool，才能可靠歸因。",zh) : tr("Process trees for \(gap) registered tasks are not currently observable. Unregistered or unavailable tasks cannot be safely attributed, so this result is not proof that the entire Mac has no unmanaged activity.","有 \(gap) 個已登記 task 的程序關係目前無法觀察；未登記或無法觀察的 task 不能安全歸因，因此這個結果不代表整台 Mac 絕對沒有繞道活動。",zh)).font(.system(size:10)).foregroundStyle(.secondary).lineSpacing(2)
                            }.padding(14).background(lavender.opacity(0.07),in:RoundedRectangle(cornerRadius:16))
                        }
                        if !active.isEmpty {
                            SectionTitle(title:tr("Attributed findings","已歸因的違規",zh),count:active.count)
                            ForEach(active) { finding in
                                VStack(alignment:.leading,spacing:8) {
                                    HStack { Image(systemName:"exclamationmark.triangle.fill").foregroundStyle(.orange); VStack(alignment:.leading,spacing:2) { Text(projectName(finding.project)).font(.system(size:12,weight:.semibold)); Text("\(finding.platform.uppercased()) · \(finding.kind.replacingOccurrences(of:"-",with:" ")) · Task \(finding.session)").font(.system(size:9,design:.monospaced)).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle) }; Spacer() }
                                    Text(guidanceText(finding,zh)).font(.system(size:10)).foregroundStyle(.secondary).lineSpacing(2)
                                }.padding(14).background(.orange.opacity(0.08),in:RoundedRectangle(cornerRadius:16))
                            }
                        }
                        Label(tr("This monitor records bounded guidance only. It never stops a task, simulator, emulator, or ADB server.","監視器只記錄有限的教學指引，不會停止 task、模擬器、Android Emulator 或 ADB。",zh),systemImage:"hand.raised.fill").font(.system(size:10)).foregroundStyle(.secondary).padding(14).frame(maxWidth:.infinity,alignment:.leading).background(.white.opacity(0.55),in:RoundedRectangle(cornerRadius:16))
                        Text("\(tr("Checked","檢查於",zh)) \(Date(timeIntervalSince1970:report.observed_at).formatted(date:.omitted,time:.standard))").font(.system(size:9)).foregroundStyle(.secondary).frame(maxWidth:.infinity,alignment:.trailing)
                    }
                }.padding(.horizontal,22).padding(.bottom,18)
            }
            HStack { Button { Task { await model.auditNow() } } label:{ Label(tr("Scan again","再次監視",zh),systemImage:"arrow.clockwise").frame(maxWidth:.infinity) }.buttonStyle(.borderedProminent).tint(lavender).disabled(model.auditInFlight) }.padding(18).background(.white.opacity(0.45))
        }.frame(width:500,height:620).foregroundStyle(ink).background(LinearGradient(colors:[Color(red:0.95,green:0.96,blue:1),Color(red:0.97,green:0.99,blue:0.98)],startPoint:.topLeading,endPoint:.bottomTrailing)).preferredColorScheme(.light)
    }
}

let ink = Color(red: 0.13, green: 0.16, blue: 0.26)
let lavender = Color(red: 0.40, green: 0.34, blue: 0.88)
let mint = Color(red: 0.08, green: 0.61, blue: 0.48)
func projectName(_ path: String) -> String { URL(fileURLWithPath: path).lastPathComponent }
func symbol(_ pool: String) -> String { pool == "ios" ? "iphone" : pool == "android" ? "smartphone" : "display" }
func duration(_ seconds: Double) -> String { let n = max(0, Int(seconds)); return String(format: "%02d:%02d", n/60, n%60) }
func activityDuration(_ seconds: Double) -> String {
    let n = max(0,Int(seconds)); return n >= 3600 ? String(format:"%d:%02d:%02d",n/3600,(n%3600)/60,n%60) : String(format:"%02d:%02d",n/60,n%60)
}
func activityClock(_ seconds: Double) -> String { Date(timeIntervalSince1970:seconds).formatted(date:.omitted,time:.standard) }
func tr(_ english: String, _ chinese: String, _ zh: Bool) -> String { zh ? chinese : english }
func pressureName(_ value: String, _ zh: Bool) -> String {
    if !zh { return value.capitalized }
    return ["healthy":"正常","elevated":"偏高","high":"高壓","critical":"嚴重"].first { $0.key == value.lowercased() }?.value ?? value
}
func phaseName(_ value: String, _ zh: Bool) -> String {
    if !zh { return value.capitalized }
    return ["ready":"就緒","creating":"建立中","stopping":"退役中","failed":"失敗"].first { $0.key == value.lowercased() }?.value ?? value
}
func operationName(_ value: String?, _ zh: Bool) -> String {
    guard let value else { return tr("Reserved","已保留",zh) }
    if !zh { return value.capitalized }
    return ["work":"工作","boot":"開機","manual":"手動操作"].first { $0.key == value.lowercased() }?.value ?? value
}
func eventName(_ value: String, _ zh: Bool) -> String {
    if !zh { return value.replacingOccurrences(of:"-",with:" ").capitalized }
    let names = ["queued":"加入排隊","acquired":"取得租約","released":"已釋放","idle-stopped":"閒置退役","mode-transition":"模式切換","session-enabled":"Task 已啟用","compliance-guidance":"使用指引"]
    return names[value] ?? value.replacingOccurrences(of:"-",with:" ")
}
func guidanceText(_ finding: ComplianceFinding, _ zh: Bool) -> String {
    guard zh else { return finding.guidance }
    if finding.kind == "unsafe-shutdown" {
        if finding.platform == "ios" {
            return "此 task 嘗試關閉 iOS Simulator。這會破壞暖機重用並拖慢所有等待中的 task。即使持有有效租約，測試完成也只能 release，禁止 simctl shutdown 或關機 cleanup trap；只有管理器可以依規則退役已釋放的裝置。"
        }
        return "此 task 嘗試關閉 Android Emulator。這會破壞暖機重用並拖慢所有等待中的 task。即使持有有效租約，測試完成也只能 release，禁止關閉 emulator、adb emu kill 或關機 cleanup trap；只有管理器可以依規則退役已釋放的裝置。"
    }
    if finding.platform == "ios" {
        return "此 task 在 Simulator Manager 租約外使用 iOS Simulator。請先完成編譯與主機測試；需要 runtime／UI 驗證時，使用 simulator_manager_run 或 sim-manager run ios，只操作分配的 SIM_MANAGER_UDID。完成後只 release 使用權，不執行 simctl shutdown，讓管理器決定暖機重用或安全退役。"
    }
    return "此 task 在 Simulator Manager 租約外使用 Android Emulator。請先完成編譯與主機測試；需要 runtime／UI 驗證時，使用 simulator_manager_run 或 sim-manager run android，只操作分配的 SIM_MANAGER_SERIAL。完成後只 release 使用權，不關閉 emulator、不執行 adb emu kill，也不停止共用 ADB。"
}

struct LanguagePicker: View {
    @ObservedObject var model: Model
    var body: some View {
        Menu {
            Picker(model.text("Language","語言"),selection:$model.language) {
                Text(model.text("Automatic (Device)","自動（裝置語系）")).tag(AppLanguage.automatic)
                Text("English").tag(AppLanguage.english)
                Text("中文").tag(AppLanguage.chinese)
            }
        } label: {
            Image(systemName:"globe").font(.system(size:12,weight:.semibold)).foregroundStyle(lavender).frame(width:27,height:27).background(.white.opacity(0.6),in:Circle())
        }.menuStyle(.borderlessButton).frame(width:27).help(model.text("Change language","切換語言"))
    }
}

struct ActivityRow: View {
    let event: Event; let chinese: Bool
    var project: String { event.project.map(projectName) ?? (event.session.map { String($0.prefix(12)) } ?? "System") }
    var body: some View {
        VStack(alignment:.leading,spacing:6) {
            HStack(spacing:7) {
                Circle().fill(event.event == "acquired" ? lavender : event.event == "released" ? mint : Color.orange).frame(width:6,height:6)
                Text(eventName(event.event,chinese)).font(.system(size:11,weight:.semibold))
                Spacer()
                Text(activityClock(event.time)).font(.system(size:9)).foregroundStyle(.secondary)
            }
            HStack(spacing:6) {
                Image(systemName:event.pool.map(symbol) ?? "person.crop.square").foregroundStyle(lavender)
                Text(project).font(.system(size:10,weight:.medium)).lineLimit(1)
                Spacer()
                if let pool = event.pool { CapsuleLabel(text:pool.uppercased(),color:lavender) }
            }
            if let session = event.session {
                Text("Task \(session)").font(.system(size:9,design:.monospaced)).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
            }
            if let total = event.duration_seconds, event.event == "released" || event.event == "stale-reaped" || event.event == "acquired" {
                HStack(spacing:5) {
                    Image(systemName:"timer").foregroundStyle(mint)
                    Text(event.event == "acquired" ? tr("Elapsed","已使用",chinese) : tr("Total occupancy","總占用",chinese)).font(.system(size:9,weight:.semibold))
                    Text(activityDuration(total)).font(.system(size:10,weight:.semibold,design:.monospaced))
                    Spacer()
                    if event.event != "acquired", let started = event.started_at { Text("\(activityClock(started)) → \(activityClock(event.time))").font(.system(size:8)).foregroundStyle(.secondary) }
                }
            }
            if let resource = event.resource { Text(resource).font(.system(size:8,design:.monospaced)).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle) }
        }.padding(11).background(.white.opacity(0.58),in:RoundedRectangle(cornerRadius:14))
    }
}

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
    let lease: Lease; let renewalLimit: Int; let chinese: Bool
    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { tick in
            let now = tick.date.timeIntervalSince1970
            let remaining = max(0, lease.end-now)
            let progress = min(1, max(0, (now-lease.created)/max(1, lease.end-lease.created)))
            let color = lease.yield_by != nil || remaining < 30 ? Color.orange : lavender
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 11) {
                    Image(systemName: symbol(lease.pool)).font(.system(size: 19)).foregroundStyle(color).frame(width: 35,height: 35).background(color.opacity(0.10),in: RoundedRectangle(cornerRadius: 11))
                    VStack(alignment: .leading, spacing: 3) { Text(projectName(lease.project)).font(.system(size: 12, weight: .semibold)).lineLimit(1); Text("\(lease.pool.uppercased()) · \(operationName(lease.operation,chinese))").font(.system(size: 10)).foregroundStyle(.secondary) }
                    Spacer(); Text(duration(remaining)).font(.system(size: 18, weight: .semibold, design: .rounded)).monospacedDigit().foregroundStyle(color)
                }
                GeometryReader { g in ZStack(alignment: .leading) { Capsule().fill(color.opacity(0.10)); Capsule().fill(color).frame(width: g.size.width*progress) } }.frame(height: 4)
                HStack { Text("\(lease.session.prefix(8)) · \(lease.renewals)/\(renewalLimit) \(tr("renewals","次續租",chinese))"); Spacer(); Text(lease.yield_by != nil ? tr("Checkpoint & requeue","安全保存並重新排隊",chinese) : tr("Lease remaining","租約剩餘時間",chinese)) }.font(.system(size: 9)).foregroundStyle(.secondary)
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
        let zh = model.usesChinese
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                ZStack { RoundedRectangle(cornerRadius: 13).fill(LinearGradient(colors: [lavender, Color(red:0.54,green:0.66,blue:0.99)], startPoint:.topLeading,endPoint:.bottomTrailing)); Image(systemName:"square.stack.3d.up.fill").font(.system(size:19)).foregroundStyle(.white) }.frame(width: 39,height: 39)
                VStack(alignment:.leading,spacing:3) { Text("Simulator Manager").font(.system(size:17,weight:.semibold,design:.rounded)); HStack(spacing:5) { Circle().fill(model.error == nil && model.updated != nil ? mint : Color.orange).frame(width:5,height:5); Text(model.demo != nil ? tr("Preview · sample data","預覽 · 範例資料",zh) : model.error == nil && model.updated != nil ? tr("Live shared scheduling","即時共用排程",zh) : tr("Connecting to manager","正在連接管理器",zh)).font(.system(size:10)).foregroundStyle(.secondary) } }
                Spacer()
                Button { Task { await model.auditNow() } } label: { HStack(spacing:5) { Image(systemName:"eye.fill").font(.system(size:10,weight:.semibold)); Text(tr("Monitor","監視",zh)).font(.system(size:10,weight:.semibold)) }.foregroundStyle(model.snapshot?.compliance?.isEmpty == false ? Color.orange : mint).padding(.horizontal,9).frame(height:27).background(.white.opacity(0.68),in:Capsule()) }.buttonStyle(.plain).help(tr("Check for simulator use outside managed leases","檢查是否有繞過租約使用模擬器",zh)).accessibilityIdentifier("bypass-monitor-button")
                LanguagePicker(model:model)
                Button { model.showGuide = true } label: { Image(systemName:"questionmark").font(.system(size:12,weight:.semibold)).foregroundStyle(lavender).frame(width:27,height:27).background(.white.opacity(0.6),in:Circle()) }.buttonStyle(.plain).help(tr("Open the Quick Start guide","開啟 Quick Start 教學",zh))
                Button { model.pinned.toggle(); NSApp.windows.first?.level = model.pinned ? .floating : .normal } label: { Image(systemName: model.pinned ? "pin.fill" : "pin").font(.system(size:12)).foregroundStyle(model.pinned ? lavender : .secondary).frame(width:27,height:27).background(.white.opacity(0.6),in:Circle()) }.buttonStyle(.plain).help(tr("Keep window above other apps","讓視窗保持在其他 App 上方",zh))
                Button { NSApp.windows.first?.orderOut(nil) } label: { Image(systemName:"xmark").font(.system(size:10,weight:.semibold)).foregroundStyle(.secondary).frame(width:27,height:27).background(.white.opacity(0.6),in:Circle()) }.buttonStyle(.plain).help(tr("Hide dashboard; reopen from the menu bar","隱藏浮框；可從選單列重新開啟",zh))
            }.padding(.horizontal,20).padding(.top,22).padding(.bottom,17)
            ScrollView {
                VStack(alignment:.leading,spacing:17) {
                    if let error = model.error { Label(error,systemImage:"exclamationmark.triangle.fill").font(.system(size:11)).foregroundStyle(.orange).padding(12).frame(maxWidth:.infinity,alignment:.leading).background(.orange.opacity(0.08),in:RoundedRectangle(cornerRadius:14)) }
                    if let s = model.snapshot {
                        VStack(alignment:.leading,spacing:12) {
                            HStack { Text((zh ? ["Dynamic Pool","受限模式","降載中","傳統模式"] : ["Dynamic Pool","Constrained","Draining","Traditional Mode"])[min(3,max(0,s.scheduler.stage))]).font(.system(size:21,weight:.semibold,design:.rounded)); Spacer(); CapsuleLabel(text:pressureName(s.scheduler.pressure,zh),color:s.scheduler.pressure == "healthy" ? mint : .orange) }
                            Text(s.scheduler.creation_allowed ? tr("Private environments · up to \(s.scheduler.capacity) mobile allocations","私人環境 · 最多同時分配 \(s.scheduler.capacity) 個行動裝置",zh) : tr("New environments paused · existing work stays protected","已暫停建立新環境 · 現有工作仍受保護",zh)).font(.system(size:10)).foregroundStyle(.secondary)
                            HStack(spacing:5) { ForEach(0..<4) { stage in Capsule().fill(stage <= s.scheduler.stage ? (s.scheduler.stage == 0 ? mint : Color.orange) : ink.opacity(0.07)).frame(height:4) } }
                            HStack { Text(tr("Dynamic","動態",zh)); Spacer(); Text(tr("Traditional","傳統",zh)) }.font(.system(size:9)).foregroundStyle(.secondary)
                        }.padding(16).background(.white.opacity(0.7),in:RoundedRectangle(cornerRadius:20))
                        HStack(spacing:8) { Metric(name:tr("In use","使用中",zh),value:String(s.leases.count),icon:"bolt.fill"); Metric(name:tr("Waiting","排隊中",zh),value:String(s.queue.count),icon:"line.3.horizontal"); Metric(name:tr("Registered","已登記",zh),value:String(s.sessions.count),icon:"person.2.fill") }
                        if let findings = s.compliance, !findings.isEmpty {
                            SectionTitle(title:tr("Guidance center","教學指引中心",zh),count:findings.count)
                            ForEach(findings) { finding in
                                VStack(alignment:.leading,spacing:8) {
                                    HStack { Image(systemName:"graduationcap.fill").foregroundStyle(.orange); VStack(alignment:.leading,spacing:2) { Text(projectName(finding.project)).font(.system(size:12,weight:.semibold)); Text("\(finding.platform.uppercased()) · \(finding.kind.replacingOccurrences(of:"-",with:" ")) · \(finding.session.prefix(8))").font(.system(size:9)).foregroundStyle(.secondary) }; Spacer(); CapsuleLabel(text:tr("GUIDANCE","指引",zh),color:.orange) }
                                    Text(guidanceText(finding,zh)).font(.system(size:10)).foregroundStyle(.secondary).lineSpacing(2)
                                }.padding(14).background(.orange.opacity(0.08),in:RoundedRectangle(cornerRadius:18)).overlay(RoundedRectangle(cornerRadius:18).stroke(.orange.opacity(0.18)))
                            }
                        }
                        SectionTitle(title:tr("Active allocations","使用中的分配",zh),count:s.leases.count)
                        if s.leases.isEmpty { EmptyRow(icon:"checkmark.circle",title:tr("Resources are resting","資源目前閒置",zh),detail:tr("Runtime requests appear here automatically.","Runtime 請求會自動顯示在這裡。",zh)) } else { ForEach(s.leases) { LeaseRow(lease:$0,renewalLimit:s.policy.max_renewals,chinese:zh) } }
                        SectionTitle(title:tr("FIFO waiting line","FIFO 排隊",zh),count:s.queue.count)
                        if s.queue.isEmpty { EmptyRow(icon:"sparkles",title:tr("No requests waiting","目前無人排隊",zh),detail:tr("Build and unit work can continue independently.","編譯與單元測試可繼續獨立執行。",zh)) } else { ForEach(Array(s.queue.enumerated()),id:\.element.id) { index, waiter in WaitRow(waiter:waiter,position:index+1) } }
                        HStack(spacing:8) { Metric(name:tr("CPU load / core","每核心 CPU 負載",zh),value:s.scheduler.metrics.load_ratio.map { String(format:"%.0f%%",$0*100) } ?? "—",icon:"cpu"); Metric(name:tr("Memory free","可用記憶體",zh),value:s.scheduler.metrics.memory_free_percent.map { String(format:"%.0f%%",$0) } ?? "—",icon:"memorychip"); Metric(name:tr("Disk free","可用磁碟",zh),value:s.scheduler.metrics.disk_free_gib.map { String(format:"%.1f GB",$0) } ?? "—",icon:"externaldrive") }
                        DisclosureGroup {
                            VStack(spacing:8) { ForEach(s.sessions) { session in HStack { Circle().fill(s.leases.contains { $0.session == session.session } ? lavender : ink.opacity(0.15)).frame(width:6,height:6); VStack(alignment:.leading,spacing:2) { Text(projectName(session.project)).font(.system(size:11,weight:.medium)); Text(String(session.session.prefix(8))).font(.system(size:9)).foregroundStyle(.secondary) }; Spacer(); CapsuleLabel(text:s.leases.contains { $0.session == session.session } ? tr("Allocated","已分配",zh) : s.queue.contains { $0.session == session.session } ? tr("Queued","排隊中",zh) : tr("Registered","已登記",zh),color:s.leases.contains { $0.session == session.session } ? lavender : mint) }.padding(.vertical,5) } }.padding(.top,7)
                        } label: { SectionTitle(title:tr("Sessions","Tasks",zh),count:s.sessions.count) }.tint(lavender)
                        DisclosureGroup {
                            VStack(spacing:8) { ForEach(s.environments) { env in HStack { Image(systemName:symbol(env.pool)).foregroundStyle(lavender); Text(projectName(env.project)).font(.system(size:11)).lineLimit(1); Spacer(); CapsuleLabel(text:env.running == 1 ? tr("Running","執行中",zh) : phaseName(env.phase,zh)) }.padding(.vertical,5) } }.padding(.top,7)
                        } label: { SectionTitle(title:tr("Private environments","私人環境",zh),count:s.environments.count) }.tint(lavender)
                        if !s.events.isEmpty { DisclosureGroup {
                            VStack(alignment:.leading,spacing:8) { ForEach(Array(s.events.prefix(10))) { event in ActivityRow(event:event,chinese:zh) } }.padding(.top,8)
                        } label: { SectionTitle(title:tr("Recent activity","最近活動",zh),count:min(10,s.events.count)) }.tint(lavender) }
                    } else { EmptyRow(icon:"antenna.radiowaves.left.and.right",title:tr("Loading shared state","正在載入共用狀態",zh),detail:tr("The dashboard follows your local manager.","浮框會讀取本機 Simulator Manager。",zh)) }
                }.padding(.horizontal,20).padding(.bottom,18)
            }.scrollIndicators(.hidden)
            HStack { Text(tr("VIEW ONLY","僅供檢視",zh)).font(.system(size:8,weight:.semibold)).tracking(1).foregroundStyle(lavender); Spacer(); if let updated = model.updated { Text("\(tr("Updated","更新於",zh)) \(updated.formatted(date:.omitted,time:.standard))").font(.system(size:9)).foregroundStyle(.secondary) }; Button { Task { await model.refresh() } } label:{ Image(systemName:"arrow.clockwise").font(.system(size:11)).foregroundStyle(lavender) }.buttonStyle(.plain).help(tr("Refresh","重新整理",zh)) }.padding(.horizontal,21).padding(.vertical,13).background(.white.opacity(0.45))
        }.foregroundStyle(ink).background(LinearGradient(colors:[Color(red:0.94,green:0.95,blue:1),Color(red:0.96,green:0.98,blue:0.98)],startPoint:.topLeading,endPoint:.bottomTrailing)).background(.ultraThinMaterial).clipShape(RoundedRectangle(cornerRadius:26)).preferredColorScheme(.light)
        .sheet(isPresented:$model.showGuide) { QuickStartGuide(model:model) }
        .sheet(isPresented:$model.showMonitor) { BypassMonitor(model:model) }
        .task { while !Task.isCancelled { await model.refresh(); try? await Task.sleep(nanoseconds:2_000_000_000) } }
    }
}

@MainActor final class Delegate: NSObject, NSApplicationDelegate {
    var panel: NSPanel!; var item: NSStatusItem!; var languageObserver: AnyCancellable?; let model = Model()
    func applicationDidFinishLaunching(_ notification: Notification) {
        DistributedNotificationCenter.default().addObserver(self,selector:#selector(showFromSecondaryLaunch(_:)),
                                                             name:dashboardShowNotification,object:nil)
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
        item = NSStatusBar.system.statusItem(withLength:NSStatusItem.squareLength)
        item.button?.image = NSImage(systemSymbolName:"square.stack.3d.up",accessibilityDescription:"Simulator Manager")
        rebuildMenus()
        languageObserver = model.$language.dropFirst().sink { [weak self] _ in self?.rebuildMenus() }
        NSApp.activate(ignoringOtherApps:true)
    }
    func languageItem(_ title: String, action: Selector, selected: Bool) -> NSMenuItem {
        let entry = NSMenuItem(title:title,action:action,keyEquivalent:""); entry.target = self; entry.state = selected ? .on : .off; return entry
    }
    func rebuildMenus() {
        let mainMenu = NSMenu(), appMenu = NSMenu(), appItem = NSMenuItem()
        let quitItem = NSMenuItem(title:model.text("Quit Dashboard","結束浮框"),action:#selector(quit),keyEquivalent:"q")
        quitItem.target = self; appMenu.addItem(quitItem); appItem.submenu = appMenu; mainMenu.addItem(appItem); NSApp.mainMenu = mainMenu
        guard item != nil else { return }
        let menu = NSMenu()
        menu.addItem(withTitle:model.text("Show Dashboard","顯示浮框"),action:#selector(show),keyEquivalent:"")
        menu.addItem(withTitle:model.text("Quick Start Guide","Quick Start 教學"),action:#selector(guide),keyEquivalent:"")
        let language = NSMenuItem(title:model.text("Language","語言"),action:nil,keyEquivalent:"")
        let choices = NSMenu()
        choices.addItem(languageItem(model.text("Automatic (Device)","自動（裝置語系）"),action:#selector(useAutomaticLanguage),selected:model.language == .automatic))
        choices.addItem(languageItem("English",action:#selector(useEnglish),selected:model.language == .english))
        choices.addItem(languageItem("中文",action:#selector(useChinese),selected:model.language == .chinese))
        language.submenu = choices; menu.addItem(language)
        menu.addItem(.separator()); menu.addItem(withTitle:model.text("Quit Dashboard","結束浮框"),action:#selector(quit),keyEquivalent:"q")
        for child in menu.items where child.action != nil { child.target = self }; item.menu = menu
    }
    @objc func show() { panel.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps:true) }
    @objc func showFromSecondaryLaunch(_ notification: Notification) {
        if notification.userInfo?["showGuide"] as? Bool == true { model.showGuide = true }
        show()
    }
    @objc func guide() { model.showGuide = true; show() }
    @objc func useAutomaticLanguage() { model.language = .automatic }
    @objc func useEnglish() { model.language = .english }
    @objc func useChinese() { model.language = .chinese }
    @objc func quit() { NSApp.terminate(nil) }
    func applicationWillTerminate(_ notification: Notification) {
        DistributedNotificationCenter.default().removeObserver(self,name:dashboardShowNotification,object:nil)
    }
    func applicationShouldHandleReopen(_ sender:NSApplication,hasVisibleWindows:Bool)->Bool { show(); return true }
}
@main struct SimulatorManagerApp {
    @MainActor static func main() {
        guard let singleton = DashboardSingleton.acquire() else {
            revealExistingDashboard(showGuide:CommandLine.arguments.contains("--onboarding"))
            return
        }
        let app = NSApplication.shared
        let delegate = Delegate()
        app.delegate = delegate; app.setActivationPolicy(.regular)
        withExtendedLifetime(singleton) { withExtendedLifetime(delegate) { app.run() } }
    }
}
