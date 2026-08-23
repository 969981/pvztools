"""Single-controller mutex (pvztools.process.lock) — Fix 4.

CreateMutexW / ReleaseMutex / CloseHandle are simulated via monkeypatch so the
tests run on any platform and never touch a real game.
"""
from __future__ import annotations

import importlib

import pytest

from pvztools.process import ControllerBusy, attach
from pvztools.process import lock as lock_mod

from test_process import BASE, FakeK32, ProcessInfo, install, make_pe_header  # noqa: F401

ERR_EXISTS = lock_mod.ERROR_ALREADY_EXISTS
PID = 4242
GAME = [ProcessInfo(PID, "popcapgame1.exe", None)]


class FakeMutex:
    """Kernel stand-in: one named mutex namespace for the whole 'machine'."""

    def __init__(self):
        self.held = {}          # name -> handle
        self.next_handle = 0x100
        self.created = []       # names passed to CreateMutexW
        self.released = []      # handles passed to ReleaseMutex
        self.closed = []        # handles passed to CloseHandle

    def create(self, name):
        self.created.append(name)
        if name in self.held:
            self.next_handle += 1
            return self.next_handle, ERR_EXISTS   # a handle IS returned on already-exists
        self.next_handle += 1
        self.held[name] = self.next_handle
        return self.next_handle, 0

    def release(self, h):
        self.released.append(h)
        for name, held in list(self.held.items()):
            if held == h:
                del self.held[name]

    def close(self, h):
        self.closed.append(h)


@pytest.fixture
def mutex(monkeypatch):
    m = FakeMutex()
    monkeypatch.setattr(lock_mod, "_create_mutex", m.create)
    monkeypatch.setattr(lock_mod, "_release_mutex", m.release)
    monkeypatch.setattr(lock_mod, "_close_handle", m.close)
    return m


# --------------------------------------------------------------------------- #
# lock module
# --------------------------------------------------------------------------- #
def test_mutex_name_is_per_pid():
    assert lock_mod.mutex_name(7) == "Local\\pvztools-controller-7"


def test_acquire_then_busy(mutex):
    lk = lock_mod.acquire(PID)
    assert not lk.released
    with pytest.raises(ControllerBusy) as ei:
        lock_mod.acquire(PID)
    assert ei.value.pid == PID
    assert "已有另一个 pvztools 实例正在控制这个游戏" in str(ei.value)
    assert "pid %d" % PID in str(ei.value)
    # the duplicate handle from the failed CreateMutexW is closed, not leaked
    assert mutex.closed == [mutex.next_handle]
    lk.release()
    assert lk.released
    assert lk.handle is None
    # released -> a new owner can take it; release is idempotent
    lk.release()
    lock_mod.acquire(PID).release()
    assert len(mutex.released) == 2


def test_other_pid_is_independent(mutex):
    a = lock_mod.acquire(1)
    b = lock_mod.acquire(2)
    assert mutex.created == ["Local\\pvztools-controller-1", "Local\\pvztools-controller-2"]
    a.release(); b.release()


def test_offwindows_noop(monkeypatch):
    monkeypatch.setattr(lock_mod, "_create_mutex", lambda name: (None, 0))
    called = []
    monkeypatch.setattr(lock_mod, "_release_mutex", lambda h: called.append(h))
    lk = lock_mod.acquire(PID)
    assert lk.handle is None
    lk.release()
    assert called == []


# --------------------------------------------------------------------------- #
# attach integration
# --------------------------------------------------------------------------- #
def test_second_write_attach_raises_busy(monkeypatch, mutex):
    install(monkeypatch, FakeK32(GAME))
    s1 = attach(write=True)
    assert s1.lock is not None and not s1.lock.released
    with pytest.raises(ControllerBusy):
        attach(write=True)
    s1.close()


def test_readonly_attach_does_not_lock(monkeypatch, mutex):
    install(monkeypatch, FakeK32(GAME))
    s_rw = attach(write=True)
    # read-only sessions coexist with the controller and never touch the mutex
    s_ro = attach()
    assert s_ro.lock is None
    assert len(mutex.created) == 1
    s_ro.close()
    s_rw.close()


def test_close_releases_mutex(monkeypatch, mutex):
    install(monkeypatch, FakeK32(GAME))
    s = attach(write=True)
    h = s.lock.handle
    s.close()
    assert mutex.released == [h]
    assert mutex.closed == [h]
    assert s.lock.released
    # and now a second controller may attach
    s2 = attach(write=True)
    s2.close()


def test_failed_attach_releases_mutex(monkeypatch, mutex):
    install(monkeypatch, FakeK32(GAME, wow64=False))
    with pytest.raises(Exception):
        attach(write=True)
    assert mutex.held == {}
    install(monkeypatch, FakeK32(GAME, deny=True))
    with pytest.raises(Exception):
        attach(write=True)
    assert mutex.held == {}


def test_lock_kwarg_overrides_write(monkeypatch, mutex):
    install(monkeypatch, FakeK32(GAME))
    s = attach(lock=True)          # e.g. input-only control: no VM_WRITE but still exclusive
    assert s.lock is not None
    with pytest.raises(ControllerBusy):
        attach(write=True)
    s.close()
    s2 = attach(write=True, lock=False)
    assert s2.lock is None
    s2.close()


def test_game_attach_locks_for_input_only(monkeypatch, mutex):
    """game.attach(input=True) controls the game (window messages) -> locks,
    even though the process is opened read-only; a second controller is refused
    and Game.close releases the mutex."""
    game_mod = importlib.import_module("pvztools.game")
    install(monkeypatch, FakeK32(GAME))
    from pvztools.addr import profile_for_stamp
    monkeypatch.setattr(game_mod, "_load_profile_arg",
                        lambda profile, session: profile_for_stamp(0x4D02B058))
    g = game_mod.attach(input=True)
    assert g.session.lock is not None and not g.session.writable
    with pytest.raises(ControllerBusy):
        game_mod.attach(input=True)
    with pytest.raises(ControllerBusy):
        attach(write=True)
    g.close()
    assert mutex.held == {}
    # plain read-only attach never locks
    g2 = game_mod.attach()
    assert g2.session.lock is None
    g2.close()


def test_game_attach_busy_closes_session(monkeypatch, mutex):
    game_mod = importlib.import_module("pvztools.game")
    k = FakeK32(GAME)
    install(monkeypatch, k)
    holder = lock_mod.acquire(PID)
    with pytest.raises(ControllerBusy):
        game_mod.attach(input=True)
    assert k.closed == [0x1234]      # the process handle was not leaked
    holder.release()


def test_cli_reports_busy_clearly(monkeypatch, mutex, capsys):
    cli = importlib.import_module("pvztools.cli.__main__")
    install(monkeypatch, FakeK32(GAME))
    holder = attach(write=True)
    try:
        def open_game(args, **kw):
            return attach(write=True)
        monkeypatch.setattr(cli, "_open_game", open_game)
        import argparse
        rc = cli._run_memory(argparse.Namespace(write=True), lambda g: 0)
        assert rc == cli.EXIT_GAME
        err = capsys.readouterr().err
        assert "已有另一个 pvztools 实例正在控制这个游戏" in err
        assert "hint:" in err
    finally:
        holder.close()
