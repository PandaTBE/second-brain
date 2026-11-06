import SwiftUI
import AppKit
import SecondBrainCore

private final class QuickSearchPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
    var command: ((QuickPanelKeyCommand) -> Void)?
    override func keyDown(with event: NSEvent) {
        let marked = (firstResponder as? NSTextView)?.hasMarkedText() ?? false
        if let action = QuickPanelKeyCommand.resolve(keyCode: event.keyCode,
                modifiers: QuickShortcutModifiers(eventFlags: event.modifierFlags), hasMarkedText: marked) {
            command?(action)
        } else { super.keyDown(with: event) }
    }
}

@MainActor
final class QuickSearchController: NSObject, ObservableObject, NSWindowDelegate {
    @Published private(set) var shortcut: QuickSearchShortcut = .standard
    @Published private(set) var registrationError: String?
    let model: QuickSearchViewModel
    private let hotKey = QuickSearchHotKey()
    private let defaults: UserDefaults
    private let preferenceKey = "quickSearch.shortcut.v1"
    private var panel: QuickSearchPanel?
    private var settingsWindow: NSWindow?
    private var previousApplication: NSRunningApplication?
    private var configured = false
    private var showMaterial: ((MemoryRecord) -> Void)?
    init(memory: MemoryViewModel, defaults: UserDefaults = .standard) {
        model = QuickSearchViewModel(memory: memory); self.defaults = defaults
        super.init()
    }
    func configure(showMaterial: @escaping (MemoryRecord) -> Void) {
        self.showMaterial = showMaterial
        guard !configured else { return }
        configured = true
        if let data = defaults.data(forKey: preferenceKey), let saved = try? JSONDecoder().decode(QuickSearchShortcut.self, from: data) { shortcut = saved }
        hotKey.pressed = { [weak self] in self?.show() }
        model.closePanel = { [weak self] in self?.close() }
        model.openMaterial = { [weak self] record in
            guard let self else { return }
            close(restoreFocus: false); self.showMaterial?(record)
        }
        model.announce = { [weak self] text in self?.announce(text) }
        do { try hotKey.install(shortcut) } catch { registrationError = error.localizedDescription }
    }
    func show() {
        if panel?.isVisible == true { panel?.makeKeyAndOrderFront(nil); return }
        previousApplication = NSWorkspace.shared.frontmostApplication
        model.present()
        let window = QuickSearchPanel(contentRect: NSRect(x: 0, y: 0, width: 800, height: 620),
                                     styleMask: [.titled, .closable, .resizable, .nonactivatingPanel], backing: .buffered, defer: false)
        window.title = "Быстрый поиск памяти"; window.isReleasedWhenClosed = false
        window.level = .floating; window.hidesOnDeactivate = false; window.delegate = self
        window.collectionBehavior = [.moveToActiveSpace, .fullScreenAuxiliary]
        window.command = model.command
        window.contentView = NSHostingView(rootView: QuickSearchPanelView(model: model, settings: { [weak self] in self?.showSettings() }))
        window.minSize = NSSize(width: 800, height: 620)
        panel = window; window.center(); NSApp.activate(ignoringOtherApps: true); window.makeKeyAndOrderFront(nil)
        DispatchQueue.main.async { [weak self, weak window] in
            guard let self, let window, self.panel === window, self.model.state.isPresented else { return }
            func searchField(_ view: NSView) -> NSSearchField? {
                if let field = view as? NSSearchField { return field }
                return view.subviews.lazy.compactMap(searchField).first
            }
            if let content = window.contentView, let field = searchField(content) { window.makeFirstResponder(field) }
        }
    }
    func close(restoreFocus: Bool = true) {
        // Revocation is synchronous, before window disappearance or application activation.
        model.dismiss()
        let old = previousApplication; previousApplication = nil
        panel?.orderOut(nil); panel?.delegate = nil; panel = nil
        if restoreFocus, NSWorkspace.shared.frontmostApplication?.processIdentifier == ProcessInfo.processInfo.processIdentifier,
           let old, old.processIdentifier != ProcessInfo.processInfo.processIdentifier, !old.isTerminated {
            old.activate(options: [])
        }
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if sender === panel { close(); return false }
        if sender === settingsWindow { closeSettings(); return false }
        return true
    }
    func showSettings() {
        close(restoreFocus: false)
        if let settingsWindow { NSApp.activate(ignoringOtherApps: true); settingsWindow.makeKeyAndOrderFront(nil); return }
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 570, height: 360), styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Сочетание быстрого поиска"; window.isReleasedWhenClosed = false; window.delegate = self
        window.contentView = NSHostingView(rootView: QuickShortcutSettings(controller: self))
        settingsWindow = window; window.center(); NSApp.activate(ignoringOtherApps: true); window.makeKeyAndOrderFront(nil)
    }
    func closeSettings() { settingsWindow?.orderOut(nil); settingsWindow?.delegate = nil; settingsWindow = nil }
    func saveShortcut(_ candidate: QuickSearchShortcut) throws {
        let data = try JSONEncoder().encode(candidate)
        try hotKey.install(candidate)
        shortcut = candidate; registrationError = nil
        defaults.set(data, forKey: preferenceKey)
    }
    func stop() { close(restoreFocus: false); closeSettings(); hotKey.stop() }
    private func announce(_ text: String) {
        guard model.state.isPresented, let panel, panel.isKeyWindow else { return }
        NSAccessibility.post(element: panel, notification: .announcementRequested,
                             userInfo: [.announcement: text, .priority: NSAccessibilityPriorityLevel.medium.rawValue])
    }
}
