
Daemon 'snups.py' monitors two GPIOs. One of them connected to LBO signal
and another to on/off button. Transition from HI to LO on any GPIO initiates
shutdown.

Shutdown script 'sncutpwr.sh' drives button GPIO low. UPS detects the
transition and cuts power in 5 seconds (the time determined by RC network).

