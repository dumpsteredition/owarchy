import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Commons as Commons

// Loaded and opened through Omarchy's native plugin panel dispatcher. Its card
// sits just inside the edge occupied by the active bar, not in a separate app.
Item {
  id: root

  property bool opened: false
  property var shell: null
  property var service: null
  property var savedState: ({ enabled: false, effects: [], rotation_seconds: 45 })
  property var availableEffects: []
  property var pendingCommands: []
  property bool stateLoaded: false
  property string statusMessage: ""

  readonly property string pluginId: "io.github.dumpsteredition.owarchy"
  readonly property string helperPath: decodeURIComponent(
    String(Qt.resolvedUrl("control_state.py")).replace(/^file:\/\//, ""))
  readonly property var panelScreen: Quickshell.screens.length > 0 ? Quickshell.screens[0] : null
  readonly property string barPosition: shell && shell.bar ? String(shell.bar.position || "top") : "top"
  readonly property int barSize: shell && shell.bar && Number(shell.bar.barSize) > 0
    ? Number(shell.bar.barSize) : Style.bar.sizeHorizontal
  readonly property int cardWidth: Math.min(348, Math.max(280, panelWindow.width - 16))
  readonly property int cardHeight: Math.min(520, Math.max(300, panelWindow.height - barSize - 16))
  readonly property int cardX: Math.max(8, Math.min(panelWindow.width - cardWidth - 8,
    barPosition === "left" ? barSize + 8
      : (barPosition === "right" ? panelWindow.width - barSize - cardWidth - 8
        : panelWindow.width - cardWidth - 8)))
  readonly property int cardY: Math.max(8, Math.min(panelWindow.height - cardHeight - 8,
    barPosition === "bottom" ? panelWindow.height - barSize - cardHeight - 8
      : (barPosition === "top" ? barSize + 8 : 8)))

  function open(payload) {
    opened = true
    refresh()
    Qt.callLater(function() { if (root.opened) panelFocus.forceActiveFocus() })
  }

  function close() { opened = false }
  function toggle() { opened ? close() : open("") }

  function status() {
    return JSON.stringify({
      opened: root.opened,
      stateLoaded: root.stateLoaded,
      state: root.savedState,
      available_effects: root.availableEffects,
      warning: root.statusMessage,
      panel: {
        x: root.cardX,
        y: root.cardY,
        width: root.cardWidth,
        height: root.cardHeight,
        screenWidth: panelWindow.width,
        screenHeight: panelWindow.height,
        barPosition: root.barPosition,
        barSize: root.barSize
      }
    })
  }

  function captureForTest() {
    if (Quickshell.env("OWARCHY_CONTROLS_TEST") !== "1") return
    var directory = Quickshell.env("OWARCHY_CONTROLS_SCREENSHOTS")
    if (!directory) return
    card.grabToImage(function(result) {
      root.statusMessage = result.saveToFile(directory + "/rotation-settings.png")
        ? "captured rotation-settings.png" : "component capture failed"
    }, Qt.size(Math.ceil(card.width), Math.ceil(card.height)))
  }

  function effectLabel(commandName) {
    var name = String(commandName || "")
    return name.length ? name.charAt(0).toUpperCase() + name.slice(1) : "Unknown effect"
  }

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

  function setEnabled(value) {
    setLocal({ enabled: value })
    enqueue(["set-enabled", value ? "on" : "off"])
  }

  function toggleEffect(commandName) {
    var effects = Array.isArray(savedState.effects) ? savedState.effects.slice() : []
    var index = effects.indexOf(commandName)
    var selected = index !== -1
    if (selected) effects.splice(index, 1)
    else effects.push(commandName)
    setLocal({ effects: effects })
    enqueue(["set-effect", commandName, selected ? "off" : "on"])
  }

  function stepRotation(delta) {
    var seconds = Number(savedState.rotation_seconds) || 45
    seconds = Math.max(5, Math.min(3600, seconds + Number(delta)))
    setLocal({ rotation_seconds: seconds })
    enqueue(["set-rotation-seconds", String(seconds)])
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
    interval: 2000
    running: root.opened
    repeat: true
    onTriggered: root.refresh()
  }

  PanelWindow {
    id: panelWindow
    screen: root.panelScreen
    visible: root.opened
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "io.github.dumpsteredition.owarchy-settings"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: root.opened ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
    anchors { top: true; bottom: true; left: true; right: true }

    MouseArea {
      anchors.fill: parent
      enabled: root.opened
      acceptedButtons: Qt.LeftButton | Qt.RightButton
      onClicked: root.close()
    }

    Rectangle {
      id: card
      x: root.cardX
      y: root.cardY
      width: root.cardWidth
      height: root.cardHeight
      radius: Style.cornerRadius
      color: Commons.Color.popups.background
      border.width: Math.max(1, Style.spacing.hairline)
      border.color: Commons.Color.popups.border
      clip: true

      // The card consumes outside-dismiss clicks; the actual controls below
      // are later siblings and receive pointer/keyboard events normally.
      MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
      }

      FocusScope {
        id: panelFocus
        anchors.fill: parent
        focus: true
        Keys.onPressed: function(event) {
          if (event.key === Qt.Key_Escape) {
            root.close()
            event.accepted = true
          }
        }

        Column {
          id: content
          anchors.fill: parent
          anchors.margins: 16
          spacing: 10

          Row {
            width: parent.width
            height: 40
            spacing: 8

            Column {
              width: parent.width - closeButton.width - 8
              anchors.verticalCenter: parent.verticalCenter
              spacing: 2
              Text {
                width: parent.width
                text: "OWARCHY"
                color: Commons.Color.popups.text
                font.family: Style.font.family
                font.pixelSize: Style.font.subtitle
                font.weight: Font.DemiBold
                elide: Text.ElideRight
              }
              Text {
                width: parent.width
                text: "Choose which effects rotate."
                color: Commons.Color.muted
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
              }
            }

            Rectangle {
              id: closeButton
              width: 28
              height: 28
              radius: 7
              color: closeMouse.containsMouse ? Commons.Color.menu.selectedBackground : "transparent"
              border.width: 1
              border.color: Commons.Color.popups.border
              Accessible.role: Accessible.Button
              Accessible.name: "Close OWARCHY settings"
              Text {
                anchors.centerIn: parent
                text: "×"
                color: Commons.Color.popups.text
                font.family: Style.font.family
                font.pixelSize: Style.font.title
              }
              MouseArea {
                id: closeMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.close()
              }
            }
          }

          Text {
            width: parent.width
            height: 34
            text: root.savedState.enabled
              ? (root.savedState.effects.length > 0
                ? "OWARCHY is on · " + root.savedState.effects.length + " effect" + (root.savedState.effects.length === 1 ? "" : "s") + " selected"
                : "OWARCHY is on, but no effects are selected.")
              : "OWARCHY is off · use the bar's circle to switch it on."
            color: root.savedState.enabled && root.savedState.effects.length === 0
              ? Commons.Color.urgent : Commons.Color.popups.text
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
            verticalAlignment: Text.AlignVCenter
          }

          Rectangle {
            width: parent.width
            height: Style.spacing.hairline
            color: Commons.Color.popups.border
            opacity: 0.8
          }

          Row {
            width: parent.width
            height: 34
            spacing: 8
            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: "Rotate every"
              color: Commons.Color.popups.text
              font.family: Style.font.family
              font.pixelSize: Style.font.bodySmall
            }
            Item { width: Math.max(0, parent.width - 208); height: 1 }
            Rectangle {
              id: decrementButton
              width: 28
              height: 28
              anchors.verticalCenter: parent.verticalCenter
              radius: 7
              color: decrementMouse.containsMouse ? Commons.Color.menu.selectedBackground : "transparent"
              border.width: 1
              border.color: Commons.Color.popups.border
              Accessible.role: Accessible.Button
              Accessible.name: "Decrease rotation time by five seconds"
              Text { anchors.centerIn: parent; text: "−"; color: Commons.Color.popups.text; font.pixelSize: Style.font.body }
              MouseArea {
                id: decrementMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.stepRotation(-5)
              }
            }
            Text {
              width: 48
              anchors.verticalCenter: parent.verticalCenter
              horizontalAlignment: Text.AlignHCenter
              text: String(root.savedState.rotation_seconds) + "s"
              color: Commons.Color.popups.text
              font.family: Style.font.family
              font.pixelSize: Style.font.body
              font.weight: Font.DemiBold
            }
            Rectangle {
              id: incrementButton
              width: 28
              height: 28
              anchors.verticalCenter: parent.verticalCenter
              radius: 7
              color: incrementMouse.containsMouse ? Commons.Color.menu.selectedBackground : "transparent"
              border.width: 1
              border.color: Commons.Color.popups.border
              Accessible.role: Accessible.Button
              Accessible.name: "Increase rotation time by five seconds"
              Text { anchors.centerIn: parent; text: "+"; color: Commons.Color.popups.text; font.pixelSize: Style.font.body }
              MouseArea {
                id: incrementMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.stepRotation(5)
              }
            }
          }

          Row {
            width: parent.width
            height: 20
            spacing: 6
            Text {
              id: effectListHeading
              anchors.verticalCenter: parent.verticalCenter
              text: "AVAILABLE TT-FX EFFECTS"
              color: Commons.Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.letterSpacing: 0.8
              font.weight: Font.DemiBold
            }
            Item {
              width: Math.max(0, parent.width - effectListHeading.implicitWidth - effectCount.implicitWidth - parent.spacing * 2)
              height: 1
            }
            Text {
              id: effectCount
              anchors.verticalCenter: parent.verticalCenter
              text: root.savedState.effects.length + " selected"
              color: Commons.Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }

          Item {
            id: effectViewport
            width: parent.width
            height: 222
            clip: true
            visible: root.availableEffects.length > 0

            Flickable {
              anchors.fill: parent
              contentWidth: width
              contentHeight: effectRows.implicitHeight
              clip: true
              boundsBehavior: Flickable.StopAtBounds
              interactive: contentHeight > height

              Column {
                id: effectRows
                width: effectViewport.width
                spacing: 2

                Repeater {
                  model: root.availableEffects

                  Rectangle {
                    id: effectRow
                    required property string modelData
                    required property int index
                    readonly property bool selected: root.savedState.effects.indexOf(modelData) !== -1
                    width: effectRows.width
                    height: 30
                    radius: 6
                    color: effectMouse.containsMouse || activeFocus
                      ? Commons.Color.menu.selectedBackground : "transparent"
                    focus: true
                    activeFocusOnTab: true
                    Accessible.role: Accessible.CheckBox
                    Accessible.name: root.effectLabel(modelData)
                    Accessible.checked: selected
                    Keys.onSpacePressed: root.toggleEffect(modelData)

                    Row {
                      anchors.fill: parent
                      anchors.leftMargin: 8
                      anchors.rightMargin: 8
                      spacing: 9
                      Rectangle {
                        width: 15
                        height: 15
                        anchors.verticalCenter: parent.verticalCenter
                        radius: 4
                        color: effectRow.selected ? Commons.Color.accent : "transparent"
                        border.width: 1
                        border.color: effectRow.selected ? Commons.Color.accent : Commons.Color.muted
                        Text {
                          anchors.centerIn: parent
                          visible: effectRow.selected
                          text: "✓"
                          color: Commons.Color.popups.background
                          font.family: Style.font.family
                          font.pixelSize: 11
                          font.weight: Font.Bold
                        }
                      }
                      Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: root.effectLabel(effectRow.modelData)
                        color: Commons.Color.popups.text
                        font.family: Style.font.family
                        font.pixelSize: Style.font.bodySmall
                      }
                    }

                    MouseArea {
                      id: effectMouse
                      anchors.fill: parent
                      hoverEnabled: true
                      cursorShape: Qt.PointingHandCursor
                      onClicked: root.toggleEffect(effectRow.modelData)
                    }
                  }
                }
              }
            }
          }

          Text {
            width: parent.width
            height: 38
            visible: root.availableEffects.length === 0
            text: root.statusMessage || "No effect commands were found in `ttfx --help`."
            color: Commons.Color.urgent
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
            verticalAlignment: Text.AlignVCenter
          }

          Text {
            width: parent.width
            height: 34
            text: root.savedState.effects.length === 0
              ? "None selected — rotation is paused until you choose an effect."
              : (root.savedState.effects.length + " selected · rotates every " + root.savedState.rotation_seconds + " seconds")
            color: root.savedState.effects.length === 0 ? Commons.Color.urgent : Commons.Color.muted
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
            verticalAlignment: Text.AlignVCenter
          }
        }
      }
    }
  }

  Component.onCompleted: refresh()
}
