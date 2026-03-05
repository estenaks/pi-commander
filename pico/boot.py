# boot.py — runs automatically before main.py on every Pico startup
import ota

ota.check_and_update()