
Daemon 'snups.py' monitors three GPIOs. One of them connected to LBO signal
and another to on/off button. Transition from HI to LO on any GPIO initiates
shutdown. The third GPIO connected to MAINS +5V power (via zener diode in order
to limit voltage to safe +3.3V). Transition on the GPIO leads to sending
notification by email.

Shutdown script 'sncutpwr.sh' drives button GPIO low. UPS detects the
transition and cuts power in 5 seconds (the time determined by RC network).
Usually the script should be run by systemd near to actual halt.
