// DwarfStar Workstation: native window for the local Workstation service.
//
// The application owns no model logic. It makes sure the local service
// (`dwarfstar serve`) is running and shows its user interface in a WebKit
// view. Profile selection, model lifecycle, telemetry and statistics all live
// in the service, so the window, the command line and tests behave the same.
//
// Command-line modes (no window):
//   --default-open-plan URL          how to open a URL in the user's default browser (JSON)
//   --headless ARGS...               run `dwarfstar ARGS...` and print its output
//   --metal-info                     the GPU's name and recommendedMaxWorkingSetSize (JSON)
//   --snapshot OUT.png [--view V] [--size WxH] [--wait S] [--script JS]
//                                    render one view off screen and save a PNG
import AppKit
import Metal
import WebKit

let servicePort = Int(ProcessInfo.processInfo.environment["DWARFSTAR_HTTP_PORT"] ?? "") ?? 8038
let serviceURL = URL(string: "http://127.0.0.1:\(servicePort)/")!

/// Repository root: environment, then the path recorded in the bundle at build time.
func dwarfstarHome() -> String? {
    if let value = ProcessInfo.processInfo.environment["DWARFSTAR_HOME"], !value.isEmpty { return value }
    if let value = Bundle.main.object(forInfoDictionaryKey: "DwarfStarHome") as? String, !value.isEmpty { return value }
    return nil
}

func emit(_ value: [String: Any]) {
    let data = (try? JSONSerialization.data(withJSONObject: value)) ?? Data("{}".utf8)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

func serviceReachable(timeout: TimeInterval = 1.0) -> Bool {
    var request = URLRequest(url: serviceURL.appendingPathComponent("api/catalog"))
    request.timeoutInterval = timeout
    let semaphore = DispatchSemaphore(value: 0)
    var ok = false
    URLSession.shared.dataTask(with: request) { _, response, _ in
        ok = (response as? HTTPURLResponse)?.statusCode == 200
        semaphore.signal()
    }.resume()
    _ = semaphore.wait(timeout: .now() + timeout + 0.5)
    return ok
}

/// Starts the service if nothing answers on its port. Returns the process when this app started it.
func ensureService() -> (process: Process?, error: String?) {
    if serviceReachable() { return (nil, nil) }
    guard let home = dwarfstarHome() else {
        return (nil, "DwarfStar's location is unknown. Rebuild the app from the repository or set DWARFSTAR_HOME.")
    }
    let launcher = home + "/bin/dwarfstar"
    guard FileManager.default.isExecutableFile(atPath: launcher) else { return (nil, "Missing \(launcher)") }
    let process = Process()
    process.executableURL = URL(fileURLWithPath: launcher)
    process.arguments = ["serve", "--port", String(servicePort)]
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    do { try process.run() } catch { return (nil, "Could not start the DwarfStar service: \(error.localizedDescription)") }
    let deadline = Date().addingTimeInterval(20)
    while Date() < deadline {
        if serviceReachable(timeout: 0.6) { return (process, nil) }
        if !process.isRunning { return (nil, "The DwarfStar service exited during start.") }
        Thread.sleep(forTimeInterval: 0.25)
    }
    process.terminate()
    return (nil, "The DwarfStar service did not answer on port \(servicePort).")
}

/// How to open a URL in the user's default browser without disturbing an automation browser.
func defaultOpenPlan(_ target: String) -> [String: Any] {
    guard let url = URL(string: target), let application = NSWorkspace.shared.urlForApplication(toOpen: url) else {
        return ["ok": false, "error": "No default application for this URL"]
    }
    let bundle = Bundle(url: application)?.bundleIdentifier ?? ""
    var automation: [Int32] = []
    for app in NSRunningApplication.runningApplications(withBundleIdentifier: bundle) {
        let ps = Process()
        ps.executableURL = URL(fileURLWithPath: "/bin/ps")
        ps.arguments = ["-p", String(app.processIdentifier), "-o", "command="]
        let pipe = Pipe()
        ps.standardOutput = pipe
        ps.standardError = FileHandle.nullDevice
        if (try? ps.run()) != nil {
            let text = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            ps.waitUntilExit()
            if text.contains("--headless") || text.contains("chrome-headless-shell") { automation.append(app.processIdentifier) }
        }
    }
    // LaunchServices can hand a URL to an already-running headless Chrome that shares the
    // bundle identifier. A fresh standard launch forwards it to the user's own browser instead.
    let collision = bundle == "com.google.Chrome" && !automation.isEmpty
    let command = collision ? ["/usr/bin/open", "-n", "-a", application.path, "--args", url.absoluteString] : ["/usr/bin/open", url.absoluteString]
    return ["ok": true, "command": command, "default_application": application.path, "default_bundle_id": bundle,
            "headless_bundle_collision": collision, "headless_pids": automation]
}

func argument(after flag: String) -> String? {
    guard let index = CommandLine.arguments.firstIndex(of: flag), index + 1 < CommandLine.arguments.count else { return nil }
    return CommandLine.arguments[index + 1]
}

// ----------------------------------------------------------------- headless modes

if CommandLine.arguments.count == 2 && CommandLine.arguments[1] == "--metal-info" {
    guard let device = MTLCreateSystemDefaultDevice() else { emit(["ok": false, "error": "No Metal device"]); exit(1) }
    emit(["ok": true, "device": device.name, "recommended_max_working_set_bytes": device.recommendedMaxWorkingSetSize,
          "unified_memory": device.hasUnifiedMemory, "physical_memory_bytes": ProcessInfo.processInfo.physicalMemory])
    exit(0)
}
if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--default-open-plan" {
    emit(defaultOpenPlan(CommandLine.arguments[2]))
    exit(0)
}
if CommandLine.arguments.count >= 3 && CommandLine.arguments[1] == "--headless" {
    guard let home = dwarfstarHome() else { emit(["ok": false, "error": "DWARFSTAR_HOME is not set"]); exit(2) }
    let process = Process()
    process.executableURL = URL(fileURLWithPath: home + "/bin/dwarfstar")
    process.arguments = Array(CommandLine.arguments.dropFirst(2))
    do { try process.run() } catch { emit(["ok": false, "error": error.localizedDescription]); exit(2) }
    process.waitUntilExit()
    exit(process.terminationStatus)
}

final class Shell: NSObject, NSApplicationDelegate, WKNavigationDelegate, NSWindowDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var service: Process?
    let snapshotPath = argument(after: "--snapshot")
    let snapshotView = argument(after: "--view") ?? "run"
    let snapshotScript = argument(after: "--script")
    let snapshotWait = Double(argument(after: "--wait") ?? "") ?? 2.5
    var size: NSSize = {
        if let text = argument(after: "--size") {
            let parts = text.lowercased().split(separator: "x").compactMap { Double($0) }
            if parts.count == 2 { return NSSize(width: parts[0], height: parts[1]) }
        }
        return NSSize(width: 1380, height: 900)
    }()

    func applicationDidFinishLaunching(_ notification: Notification) {
        let headless = snapshotPath != nil
        let style: NSWindow.StyleMask = [.titled, .closable, .miniaturizable, .resizable]
        window = NSWindow(contentRect: NSRect(origin: .zero, size: size), styleMask: style, backing: .buffered, defer: false)
        window.title = "DwarfStar Workstation"
        window.minSize = NSSize(width: 1040, height: 680)
        window.titlebarAppearsTransparent = true
        window.backgroundColor = NSColor(red: 0.043, green: 0.055, blue: 0.075, alpha: 1)
        window.appearance = NSAppearance(named: .darkAqua)
        window.delegate = self
        window.setFrameAutosaveName("DwarfStarWorkstationMain")
        web = WKWebView(frame: NSRect(origin: .zero, size: size), configuration: WKWebViewConfiguration())
        web.navigationDelegate = self
        web.autoresizingMask = [.width, .height]
        web.setValue(false, forKey: "drawsBackground")
        window.contentView = web
        if headless {
            window.setFrame(NSRect(x: -20000, y: -20000, width: size.width, height: size.height), display: false)
            window.orderBack(nil)
        } else {
            window.center()
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            buildMenu()
        }
        let started = ensureService()
        service = started.process
        if let message = started.error {
            if headless { emit(["ok": false, "error": message]); exit(1) }
            showError(message)
            return
        }
        var url = serviceURL
        if headless, var parts = URLComponents(url: serviceURL, resolvingAgainstBaseURL: false) {
            parts.fragment = snapshotView
            url = parts.url ?? serviceURL
        }
        web.load(URLRequest(url: url))
    }

    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About DwarfStar Workstation", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Reload", action: #selector(reload), keyEquivalent: "r")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit DwarfStar Workstation", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        let editItem = NSMenuItem()
        main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        NSApp.mainMenu = main
    }

    @objc func reload() { web.reload() }

    func showError(_ message: String) {
        let html = "<body style='background:#0b0e13;color:#dbe3ee;font:14px -apple-system;padding:40px'><h2>DwarfStar Workstation</h2><p>\(message)</p></body>"
        web.loadHTMLString(html, baseURL: nil)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard let path = snapshotPath else { return }
        let finish = { [self] in
            DispatchQueue.main.asyncAfter(deadline: .now() + snapshotWait) {
                let configuration = WKSnapshotConfiguration()
                configuration.rect = NSRect(origin: .zero, size: self.size)
                self.web.takeSnapshot(with: configuration) { image, error in
                    guard let image, let tiff = image.tiffRepresentation, let bitmap = NSBitmapImageRep(data: tiff),
                          let png = bitmap.representation(using: .png, properties: [:]) else {
                        emit(["ok": false, "error": error?.localizedDescription ?? "snapshot failed"])
                        exit(1)
                    }
                    do { try png.write(to: URL(fileURLWithPath: path)) } catch { emit(["ok": false, "error": error.localizedDescription]); exit(1) }
                    emit(["ok": true, "snapshot": path, "view": self.snapshotView, "width": bitmap.pixelsWide, "height": bitmap.pixelsHigh])
                    self.service?.terminate()
                    exit(0)
                }
            }
        }
        if let script = snapshotScript {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { self.web.evaluateJavaScript(script) { _, _ in finish() } }
        } else {
            finish()
        }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if snapshotPath != nil { emit(["ok": false, "error": error.localizedDescription]); exit(1) }
        showError("The DwarfStar service is not answering on port \(servicePort). \(error.localizedDescription)")
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        // The window shows only the local service. Anything else opens in the user's browser.
        if let url = action.request.url, let host = url.host, host != "127.0.0.1", host != "localhost" {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        // Stop the service this app started. A running model is left alone: it belongs to its controller.
        service?.terminate()
    }
}

let application = NSApplication.shared
let shell = Shell()
application.delegate = shell
application.setActivationPolicy(argument(after: "--snapshot") == nil ? .regular : .accessory)
application.run()
