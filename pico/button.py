# IRQ-driven button helper for MicroPython (RP2040)
# - Installs an IRQ on a button pin (default: pull-up, active-low)
# - Debounces in IRQ using a timestamp
# - Schedules the user callback to run in VM context via micropython.schedule if available
# - If schedule isn't available, falls back to setting a pollable flag (poll_pressed)
#
# Usage:
#   import button
#   button.init(pin_no=16, callback=my_callback, debounce_ms=200)
#   # If micropython.schedule is missing, in main loop:
#   if button.poll_pressed():
#       my_callback()

import machine
import time

# Try to import micropython.schedule; if unavailable we'll fall back to a poll flag.
try:
    import micropython
    _HAS_SCHEDULE = hasattr(micropython, "schedule")
except Exception:
    micropython = None
    _HAS_SCHEDULE = False

_button_pin = None
_cb = None
_last_ts = 0
_debounce_ms = 200
_busy = False
_pressed_flag = False

def _sched(_):
    """Runs in VM context via micropython.schedule"""
    global _busy, _cb
    if _cb is None:
        return
    if _busy:
        return
    _busy = True
    try:
        _cb()
    finally:
        _busy = False

def _irq(pin):
    """IRQ handler (runs in IRQ context) - keep tiny and safe."""
    global _last_ts, _debounce_ms, _busy, _pressed_flag

    now = time.ticks_ms()
    if time.ticks_diff(now, _last_ts) < _debounce_ms:
        return

    # If a run is already in progress, ignore presses
    if _busy:
        return

    _last_ts = now

    if _HAS_SCHEDULE:
        try:
            micropython.schedule(_sched, 0)
        except Exception:
            # If schedule fails for any reason, mark the flag to be polled by main loop.
            _pressed_flag = True
    else:
        # No schedule available on this port: set pollable flag.
        _pressed_flag = True

def init(pin_no=4, callback=None, debounce_ms=200, pull_up=True, trigger_falling=True):
    """
    Initialize the button IRQ.

    Args:
      pin_no (int): GPIO pin number for the button (placeholder - change for your board)
      callback (callable): function to call when button pressed; no args. Will run in VM context.
      debounce_ms (int): debounce interval in ms.
      pull_up (bool): use internal pull-up (typical button to ground).
      trigger_falling (bool): if True, triggers on falling edge (press).
    """
    global _button_pin, _cb, _debounce_ms, _last_ts, _pressed_flag

    _cb = callback
    _debounce_ms = debounce_ms
    _last_ts = 0
    _pressed_flag = False

    mode = machine.Pin.IN
    pull = machine.Pin.PULL_UP if pull_up else None
    pin = machine.Pin(pin_no, mode, pull)
    trig = machine.Pin.IRQ_FALLING if trigger_falling else machine.Pin.IRQ_RISING

    # detach any previous irq
    try:
        pin.irq(handler=None)
    except Exception:
        pass

    pin.irq(handler=_irq, trigger=trig)
    _button_pin = pin

def poll_pressed():
    """
    Pollable fallback for ports without micropython.schedule.
    Returns True once per press (clears the flag).
    """
    global _pressed_flag
    if _pressed_flag:
        _pressed_flag = False
        return True
    return False