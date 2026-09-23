"""The tray icon: whether prax is running, at a glance, and the menu to
open it, restart a role, or stop.

``prax up --tray`` runs the supervisor (``prax.up``) with the icon as its
face — the login entry on a desktop (``prax.autostart``) — and ``prax
tray`` alone puts an icon beside a supervisor already running (or offers
to start one). The icon is the mark on its tile; a red dot on it says a
role is down or nothing runs. The menu is read from the supervisor's
status file every two seconds and its commands go through the command
file, the same way ``prax up --status`` and ``--restart`` do, so the
tray is one more client of the supervisor, not a second supervisor.

The icon library (``pystray``, with Pillow for the image) is an optional
extra — ``pip install prax[tray]`` — because a board in a cupboard has no
tray. On macOS the icon must run on the main thread, so the supervisor
runs on a thread of its own here; on Windows and Linux either way works.
What is shown and what each menu entry does is decided by the plain
functions below (``title_of``, ``entries_of``), which the tests cover
without a display.
"""

from __future__ import annotations

import contextlib
import logging
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from prax import config, up

log = logging.getLogger("prax.tray")

ICON = Path(__file__).resolve().parent / "ui" / "prax-tray.png"
POLL = 2.0  # seconds between reads of the status file


def door_url() -> str:
    """Where the UI is: ``run.door`` in prax.yaml, on this machine."""
    section = config.setting("run") or {}
    door = section.get("door") if isinstance(section, dict) else None
    port = 8000
    if isinstance(door, dict) and door.get("port"):
        port = int(door["port"])
    return f"http://127.0.0.1:{port}/ui/"


def title_of(snap: dict[str, Any] | None) -> str:
    """The tooltip: each role and its state, or that nothing runs."""
    if snap is None:
        return "prax — not running"
    parts = []
    for name, r in (snap.get("roles") or {}).items():
        parts.append(f"{name} {r.get('state', '?')}")
    return "prax — " + (" · ".join(parts) if parts else "no roles")


def is_well(snap: dict[str, Any] | None) -> bool:
    """Every role up or paused on purpose: the icon without its dot."""
    if snap is None:
        return False
    roles = snap.get("roles") or {}
    return bool(roles) and all(
        r.get("state") in ("up", "paused") for r in roles.values()
    )


def entries_of(
    snap: dict[str, Any] | None, *, supervising: bool
) -> list[dict[str, Any]]:
    """The menu as data: ``{label, action, args, enabled, default,
    children}`` rows. ``action`` names what the tray does: ``open``,
    ``logs``, ``restart``, ``stop``, ``start``, ``start-all`` (a supervisor
    when none runs), ``stop-all`` (the one running, from a tray beside
    it), ``quit``."""
    rows: list[dict[str, Any]] = [
        {"label": "Open prax", "action": "open", "default": True, "enabled": True}
    ]
    if snap is None:
        rows.append({"label": "prax is not running", "action": None, "enabled": False})
        if not supervising:
            rows.append({"label": "Start prax", "action": "start-all", "enabled": True})
    else:
        for name, r in (snap.get("roles") or {}).items():
            state = r.get("state", "?")
            children = []
            if state == "paused":
                children.append({"label": "start", "action": "start", "args": name})
            else:
                children.append({"label": "restart", "action": "restart", "args": name})
                children.append({"label": "stop", "action": "stop", "args": name})
            rows.append(
                {
                    "label": f"{name}: {state}",
                    "action": None,
                    "enabled": True,
                    "children": children,
                }
            )
    rows.append({"label": "Logs folder", "action": "logs", "enabled": True})
    if not supervising and snap is not None:
        # beside a supervisor of its own: stop it all, the icon stays
        rows.append({"label": "Stop prax", "action": "stop-all", "enabled": True})
    rows.append(
        {
            "label": "Quit prax" if supervising else "Quit the tray",
            "action": "quit",
            "enabled": True,
        }
    )
    return rows


def _open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        subprocess.Popen(["explorer", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class Tray:
    """The icon and its loop; ``run`` blocks until quit."""

    def __init__(self, data_dir: Path, *, supervise: bool) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError as exc:
            raise up.UpError(
                "the tray needs pystray and Pillow: pip install prax[tray]"
            ) from exc
        self.pystray = pystray
        self.data_dir = data_dir
        self.supervise = supervise
        self.supervisor: up.Supervisor | None = None
        self.stopping = threading.Event()
        base = Image.open(ICON).convert("RGBA")
        self.well = base
        dotted = base.copy()
        d = ImageDraw.Draw(dotted)
        w, h = dotted.size
        r = max(4, w // 5)
        d.ellipse((w - r * 2 - 2, h - r * 2 - 2, w - 2, h - 2), fill=(196, 44, 32, 255))
        self.dotted = dotted
        self.snap: dict[str, Any] | None = None
        self.icon = pystray.Icon(
            "prax", self.dotted, title_of(None), menu=pystray.Menu(self._menu)
        )

    # -- the menu, built from the last status read
    def _menu(self) -> Any:
        Item = self.pystray.MenuItem
        Menu = self.pystray.Menu
        items = []
        for row in entries_of(self.snap, supervising=self.supervise):
            if row.get("children"):
                sub = Menu(
                    *[
                        Item(c["label"], self._act(c["action"], c.get("args")))
                        for c in row["children"]
                    ]
                )
                items.append(Item(row["label"], sub))
                continue
            items.append(
                Item(
                    row["label"],
                    self._act(row["action"], row.get("args"))
                    if row["action"]
                    else None,
                    enabled=row.get("enabled", True),
                    default=row.get("default", False),
                )
            )
        # a separator before the last two rows (logs, quit)
        return items[:-2] + [Menu.SEPARATOR] + items[-2:]

    def _act(self, action: str | None, args: Any = None) -> Any:
        def run(*_: Any) -> None:
            try:
                self.do(action, args)
            except Exception:
                log.exception("tray action %s failed", action)

        return run

    def do(self, action: str | None, args: Any = None) -> None:
        if action == "open":
            webbrowser.open(door_url())
        elif action == "logs":
            _open_folder(up.logs_dir(self.data_dir))
        elif action == "restart":
            up.restart(self.data_dir, str(args))
        elif action == "stop":
            up.command(self.data_dir, {"cmd": "stop", "name": str(args)})
        elif action == "start":
            up.command(self.data_dir, {"cmd": "start", "name": str(args)})
        elif action == "start-all":
            up.detach(self.data_dir, [])
        elif action == "stop-all":
            up.command(self.data_dir, {"cmd": "stop"})
        elif action == "quit":
            self.quit()

    def quit(self) -> None:
        self.stopping.set()
        if self.supervisor is not None:
            self.supervisor.stopping.set()
        with contextlib.suppress(Exception):
            self.icon.stop()

    # -- the loop
    def _watch(self) -> None:
        last_title = None
        while not self.stopping.is_set():
            snap = up.status(self.data_dir)
            self.snap = snap
            title = title_of(snap)
            well = is_well(snap)
            if title != last_title:
                self.icon.title = title
                self.icon.icon = self.well if well else self.dotted
                with contextlib.suppress(Exception):
                    self.icon.update_menu()
                last_title = title
            self.stopping.wait(POLL)

    def run(self) -> int:
        if self.supervise:
            roles = up.roles()
            if up.running_pid(self.data_dir) is not None:
                raise up.UpError("prax up is already running; prax tray attaches to it")
            up.attach_log(self.data_dir)
            self.supervisor = up.Supervisor(roles, data_dir=self.data_dir)
            # the supervisor's own handlers cannot be set from its thread:
            # a SIGTERM (launchd, systemd, a kill) or ctrl-c reaches this
            # one, which asks it to stop everything in order
            for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
                if hasattr(signal, name):
                    with contextlib.suppress(ValueError, OSError):
                        signal.signal(getattr(signal, name), self._on_signal)
            threading.Thread(
                target=self._supervise, name="prax-up", daemon=True
            ).start()
        threading.Thread(target=self._watch, name="prax-tray", daemon=True).start()
        try:
            if not self.stopping.is_set():  # a supervisor that failed at once
                self.icon.run()  # blocks on the main thread (macOS insists)
        finally:
            self.stopping.set()
            self._wait_for_supervisor()
        return 0

    def _on_signal(self, signum: int, _frame: Any) -> None:
        log.info("tray: signal %s", signum)
        self.quit()

    def _wait_for_supervisor(self) -> None:
        if self.supervisor is None:
            return
        self.supervisor.stopping.set()
        for _ in range(300):  # the roles stop in order; up to a minute
            if up.running_pid(self.data_dir) is None:
                break
            time.sleep(0.2)

    def _supervise(self) -> None:
        assert self.supervisor is not None
        try:
            self.supervisor.run()
        except Exception:
            # under the login entry's pythonw there is no stderr to see
            # it on: the log is the place
            log.exception("prax up failed")
        finally:
            # the supervisor ended (a signal, a stop from the menu or the
            # command file, a failure): the icon goes with it
            self.stopping.set()
            with contextlib.suppress(Exception):
                self.icon.stop()


def run(data_dir: Path, *, supervise: bool) -> int:
    return Tray(data_dir, supervise=supervise).run()
