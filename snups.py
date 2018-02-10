#! /usr/bin/python3

import os
import sys
import syslog
import time
from gpiozero import OutputDevice, Button
import queue

GPIO_BTN = 4
GPIO_LBO = 22
GPIO_DEBOUNCE = 0.2
SHUTDOWN_WAIT = 20
LOGMSG_BTN = 'SN UPS: Activate poweroff by button press (GPIO {})'.format(GPIO_BTN)
LOGMSG_LBO = 'SN UPS: Activate poweroff due to low battery (GPIO {})'.format(GPIO_LBO)

###
### GPIO Handler. Put activated device on queue
###

def gpio_handler(device):
	global q
	q.put(device)
	return
	
###
### Shutdown routine
###

def sn_shutdown(logmsg):
	
	# Warn logged users
	syslog.syslog(syslog.LOG_WARNING, logmsg)
	cmd = 'sudo wall "System shutting down in {} seconds"'.format(SHUTDOWN_WAIT)
	os.system(cmd)
	
	# Wait specified number of seconds before shutdown
	time.sleep(SHUTDOWN_WAIT)
	
	# Call system shutdown
	cmd = 'sudo shutdown now'
	os.system(cmd)
	exit(0)

###
### Main program starts here
###

# Open logger
syslog.openlog(logoption=syslog.LOG_PID, facility=syslog.LOG_USER)

# Create queue
q = queue.Queue()

# Set CTRL and LBO pins as input pull-up and assign callback handler
btn = Button(GPIO_BTN, pull_up=True, bounce_time=GPIO_DEBOUNCE)
btn.when_pressed = gpio_handler
lbo = Button(GPIO_LBO, pull_up=True, bounce_time=GPIO_DEBOUNCE)
lbo.when_pressed = gpio_handler

# Shutdown if LBO is already active
if lbo.is_pressed:
	sn_shutdown(LOGMSG_LBO)

# Log a message and enter to a loop
syslog.syslog(syslog.LOG_NOTICE, 'SN UPS: Installed handlers on GPIO: {} (BTN), {} (LBO)'.format(GPIO_BTN, GPIO_LBO))
while True:
	
	# Receive device from queue and call shutdown routine if correct pin activated
	device = q.get()
	pin = device.pin.number
	if pin == GPIO_BTN:
		sn_shutdown(LOGMSG_BTN)
	elif pin == GPIO_LBO:
		
		## Double check for LBO is active
		if lbo.is_pressed:
			sn_shutdown(LOGMSG_LBO)
	else:
		print("SN UPS: Received event from unexpected pin {}".format(pin))

# Unreachable point
exit(0)
