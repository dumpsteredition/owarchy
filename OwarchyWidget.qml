import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// One compact bar item: the circle is the direct on/off switch; the adjacent
// gear opens this plugin's native Omarchy panel.
BarWidget {
  id: root
  moduleName: "io.github.dumpsteredition.owarchy"

  property var savedState: ({ enabled: false, effects: [], rotation_seconds: 45 })
  property var availableEffects: []
  property var pendingCommands: []
  property bool stateLoaded: false
  property string statusMessage: ""

  readonly property string helperPath: decodeURIComponent(
    String(Qt.resolvedUrl("control_state.py")).replace(/^file:\/\//, ""))
  readonly property string toggleTooltip: !stateLoaded
    ? "OWARCHY screensaver — loading state"
    : (savedState.enabled
      ? (savedState.effects.length > 0
        ? "OWARCHY screensaver on — click to turn off"
        : "OWARCHY is on, but no effects are selected — click to turn off")
      : "OWARCHY screensaver off — click to turn on")

  implicitWidth: buttons.implicitWidth
  implicitHeight: barSize

  function setLocal(patch) {
    var next = {
      enabled: savedState.enabled === true,
      effects: Array.isArray(savedState.effects) ? savedState.effects.slice() : [],
      rotation_seconds: Number(savedState.rotation_seconds) || 45
    }
    for (var key in patch) next[key] = patch[key]
    savedState = next
  }

  function acceptResponse(rawText) {
    try {
      var response = JSON.parse(String(rawText || "").trim())
      if (response.state) savedState = response.state
      availableEffects = Array.isArray(response.available_effects) ? response.available_effects : []
      stateLoaded = true
      statusMessage = response.error || response.warning || ""
      if (response.ok !== true && !statusMessage) statusMessage = "Settings could not be updated."
    } catch (error) {
      statusMessage = "Could not read OWARCHY settings."
    }
  }

  function enqueue(command) {
    var next = pendingCommands.slice()
    next.push(command)
    pendingCommands = next
    launchNext()
  }

  function launchNext() {
    if (controlProcess.running || pendingCommands.length === 0) return
    var next = pendingCommands.slice()
    var command = next.shift()
    pendingCommands = next
    controlProcess.command = ["/usr/bin/python3", helperPath].concat(command)
    controlProcess.running = true
  }

  function refresh() { enqueue(["get"]) }

  function toggleEnabled() {
    setLocal({ enabled: savedState.enabled !== true })
    enqueue(["toggle"])
  }

  function openSettings() {
    if (bar && bar.shell && typeof bar.shell.toggle === "function")
      bar.shell.toggle(moduleName, "{}")
    else
      statusMessage = "Omarchy's plugin panel API is not available."
  }

  function mainButtonPressed(button) {
    if (button === Qt.LeftButton) root.toggleEnabled()
  }

  function settingsButtonPressed(button) {
    if (button === Qt.LeftButton) root.openSettings()
  }

  function testGeometry() {
    var window = root.QsWindow.window
    var screen = window ? window.screen : null
    var origin = window && window.contentItem
      ? root.mapToItem(window.contentItem, 0, 0) : Qt.point(0, 0)
    return JSON.stringify({
      widget: { x: origin.x, y: origin.y, width: root.width, height: root.height },
      barSize: root.barSize,
      screen: { width: screen ? screen.width : 0, height: screen ? screen.height : 0 }
    })
  }

  function captureForTest() {
    if (Quickshell.env("OWARCHY_CONTROLS_TEST") !== "1") return
    var directory = Quickshell.env("OWARCHY_CONTROLS_SCREENSHOTS")
    if (!directory) return
    var name = savedState.enabled ? "bar-switch-on.png" : "bar-switch-off.png"
    root.grabToImage(function(result) {
      root.statusMessage = result.saveToFile(directory + "/" + name)
        ? "captured " + name : "component capture failed"
    }, Qt.size(Math.ceil(root.width), Math.ceil(root.height)))
  }

  IpcHandler {
    enabled: Quickshell.env("OWARCHY_CONTROLS_TEST") === "1"
    target: root.moduleName + ".controls-test"
    function clickSwitch(): void { root.mainButtonPressed(Qt.LeftButton) }
    function openSettings(): void { root.settingsButtonPressed(Qt.LeftButton) }
    function geometry(): string { return root.testGeometry() }
    function capture(): void { root.captureForTest() }
  }

  Process {
    id: controlProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.acceptResponse(text)
    }
    onExited: function(exitCode) {
      if (exitCode !== 0 && root.statusMessage === "")
        root.statusMessage = "Could not update OWARCHY settings."
      Qt.callLater(root.launchNext)
    }
  }

  Timer {
    interval: 3000
    running: true
    repeat: true
    onTriggered: root.refresh()
  }

  Row {
    id: buttons
    anchors.verticalCenter: parent.verticalCenter
    spacing: 0

    WidgetButton {
      id: toggleButton
      bar: root.bar
      text: root.savedState.enabled ? "●" : "○"
      fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
      fontSize: 16
      fixedWidth: Style.bar.statusSlot
      horizontalMargin: 0
      verticalPadding: 0
      tooltipText: root.toggleTooltip
      active: root.savedState.enabled === true
      activeColor: Commons.Color.accent
      useActiveColor: true
      Accessible.role: Accessible.Button
      Accessible.name: root.savedState.enabled ? "Turn OWARCHY off" : "Turn OWARCHY on"
      onPressed: function(button) { root.mainButtonPressed(button) }
    }

    WidgetButton {
      id: settingsButton
      bar: root.bar
      text: "⚙"
      fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
      fontSize: Style.font.bodySmall
      fixedWidth: Style.bar.statusSlot
      horizontalMargin: 0
      verticalPadding: 0
      tooltipText: "OWARCHY settings"
      Accessible.role: Accessible.Button
      Accessible.name: "Open OWARCHY settings"
      onPressed: function(button) { root.settingsButtonPressed(button) }
    }
  }

  Component.onCompleted: refresh()
}
