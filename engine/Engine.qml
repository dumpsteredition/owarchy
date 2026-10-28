import QtQuick
import Quickshell
import Quickshell.Io

// The supervisor is persistent; it creates GTK/VTE layers only while the shared
// selection is enabled and non-empty. It owns one native host and never signals
// processes by name.
Item {
  id: root

  property bool stopping: false
  readonly property string supervisorPath: decodeURIComponent(
    String(Qt.resolvedUrl("manager.py")).replace(/^file:\/\//, ""))

  Process {
    id: supervisor
    command: ["/usr/bin/python3", "-u", root.supervisorPath]
    running: true

    stdout: SplitParser {
      onRead: function(line) { console.info("OWARCHY: " + line) }
    }
    stderr: SplitParser {
      onRead: function(line) { console.warn("OWARCHY: " + line) }
    }
    onExited: function(code) {
      if (!root.stopping) {
        console.warn("OWARCHY: supervisor exited " + code + "; restarting")
        restartTimer.restart()
      }
    }
  }

  Timer {
    id: restartTimer
    interval: 1000
    repeat: false
    onTriggered: if (!root.stopping) supervisor.running = true
  }

  Component.onDestruction: {
    root.stopping = true
    restartTimer.stop()
    supervisor.running = false
  }
}
