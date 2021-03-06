import asyncio
import os
import signal
import sys
import time

from gi.repository import GObject, Gtk, Gio, GLib

from pychess.external import gbulb
from pychess.System.Log import log


class SubProcess(GObject.GObject):
    __gsignals__ = {
        "line": (GObject.SignalFlags.RUN_FIRST, None, (str, )),
        "died": (GObject.SignalFlags.RUN_FIRST, None, ())
    }

    def __init__(self, path, args=[], warnwords=[], env=None, cwd="."):
        GObject.GObject.__init__(self)

        self.path = path
        self.args = args
        self.warnwords = warnwords
        self.env = env or os.environ

        self.defname = os.path.split(path)[1]
        self.defname = self.defname[:1].upper() + self.defname[1:].lower()
        cur_time = time.time()
        self.defname = (self.defname,
                        time.strftime("%H:%m:%%.3f", time.localtime(cur_time)) %
                        (cur_time % 60))
        log.debug(path, extra={"task": self.defname})

        argv = [str(u) for u in [self.path] + self.args]

        loop = asyncio.get_event_loop()
        self.proc = loop.run_until_complete(self.start(argv, env, cwd, loop))
        self.pid = self.proc.pid
        self.read_stdout_task = asyncio.async(self.read_stdout(self.proc.stdout))
        self.write_task = None

    @asyncio.coroutine
    def start(self, argv, env, cwd, loop):
        log.debug("SubProcess.start(): create_subprocess_exec...", extra={"task": self.defname})
        create = asyncio.create_subprocess_exec(* argv,
                                                stdin=asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.PIPE,
                                                env=env,
                                                cwd=cwd)
        proc = yield from create
        return proc

    def write(self, line):
        self.write_task = asyncio.async(self.write_stdin(self.proc.stdin, line))

    @asyncio.coroutine
    def write_stdin(self, writer, line):
        try:
            log.debug(line, extra={"task": self.defname})
            writer.write(line.encode())
            yield from writer.drain()
        except BrokenPipeError:
            log.debug('SubProcess.write_stdin(): BrokenPipeError', extra={"task": self.defname})
            self.emit("died")
            self.terminate()
        except ConnectionResetError:
            log.debug('SubProcess.write_stdin(): ConnectionResetError', extra={"task": self.defname})
            self.emit("died")
            self.terminate()
        except GLib.GError:
            log.debug("GLib.GError", extra={"task": self.defname})
            self.emit("died")
            self.terminate()

    @asyncio.coroutine
    def read_stdout(self, reader):
        while True:
            line = yield from reader.readline()
            if line:
                yield

                line = line.decode().rstrip()
                # Some engines send author names in different encodinds (f.e. spike)
                if not line or line.startswith("id author"):
                    continue

                for word in self.warnwords:
                    if word in line:
                        log.debug(line, extra={"task": self.defname})
                        break
                else:
                    log.debug(line, extra={"task": self.defname})
                self.emit("line", line)
            else:
                self.emit("died")
                break
        self.terminate()

    def terminate(self):
        if self.write_task is not None:
            self.write_task.cancel()
        self.read_stdout_task.cancel()
        try:
            self.proc.terminate()
        except ProcessLookupError:
            log.debug("SubProcess.terminate(): ProcessLookupError", extra={"task": self.defname})

    def pause(self):
        if sys.platform != "win32":
            self.proc.send_signal(signal.SIGSTOP)

    def resume(self):
        if sys.platform != "win32":
            self.proc.send_signal(signal.SIGCONT)


MENU_XML = """
<?xml version="1.0" encoding="UTF-8"?>
<interface>
  <menu id="app-menu">
    <section>
      <item>
        <attribute name="action">app.subprocess</attribute>
        <attribute name="label">Subprocess</attribute>
      </item>
      <item>
        <attribute name="action">app.quit</attribute>
        <attribute name="label">Quit</attribute>
    </item>
    </section>
  </menu>
</interface>
"""


class Application(Gtk.Application):
    def __init__(self):
        Gtk.Application.__init__(self, application_id="org.subprocess")
        self.window = None

    def do_startup(self):
        Gtk.Application.do_startup(self)

        action = Gio.SimpleAction.new("subprocess", None)
        action.connect("activate", self.on_subprocess)
        self.add_action(action)

        action = Gio.SimpleAction.new("quit", None)
        action.connect("activate", self.on_quit)
        self.add_action(action)

        builder = Gtk.Builder.new_from_string(MENU_XML, -1)
        self.set_app_menu(builder.get_object("app-menu"))

    def do_activate(self):
        if not self.window:
            self.window = Gtk.ApplicationWindow(application=self)

        self.window.present()

    def on_subprocess(self, action, param):
        proc = SubProcess("python", [os.path.expanduser("~") + "/pychess/lib/pychess/Players/PyChess.py", ])
        asyncio.async(self.parse_line(proc))
        print("xboard", file=proc)
        print("protover 2", file=proc)

    @asyncio.coroutine
    def parse_line(self, proc):
        while True:
            line = yield from gbulb.wait_signal(proc, 'line')
            if line:
                print('  parse_line:', line[1])
            else:
                print("no more lines")
                break

    def on_quit(self, action, param):
            self.quit()


if __name__ == "__main__":
    gbulb.install(gtk=True)
    app = Application()

    loop = asyncio.get_event_loop()
    loop.set_debug(enabled=True)
    loop.run_forever(application=app)
