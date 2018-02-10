#! /usr/bin/python3

import os
import sys
import syslog
import time
from gpiozero import OutputDevice, Button
from multiprocessing import Queue

GPIO_BTN = 4
GPIO_LBO = 22
GPIO_PWR = 23
GPIO_DEBOUNCE = 0.2
SHUTDOWN_WAIT = 20
LOGMSG_BTN = 'SN UPS: Activate poweroff by button press (GPIO {})'.format(GPIO_BTN)
LOGMSG_LBO = 'SN UPS: Activate poweroff due to low battery (GPIO {})'.format(GPIO_LBO)
EVENT_GPIO = 10
EVENT_CHILD_END = 20

###
### GPIO Handler. Put activated device on queue
###

def gpio_handler(device):
	global q
	q.put((EVENT_GPIO, device.pin.number))
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

if __name__ == '__main__':

    # Open logger
    syslog.openlog(logoption=syslog.LOG_PID, facility=syslog.LOG_USER)
    
    # Set CTRL and LBO pins as input pull-up and assign callback handler
    btn = Button(GPIO_BTN, pull_up=True, bounce_time=GPIO_DEBOUNCE)
    btn.when_pressed = gpio_handler
    lbo = Button(GPIO_LBO, pull_up=True, bounce_time=GPIO_DEBOUNCE)
    lbo.when_pressed = gpio_handler

    # Set PWR pin as input pull-up and assign callback handler
    pwr = Button(GPIO_PWR, pull_up=True, bounce_time=GPIO_DEBOUNCE)
    pwr.when_pressed = gpio_handler
    pwr.when_released = gpio_handler
    
    # Shutdown if LBO is already active
    if lbo.is_pressed:
        sn_shutdown(LOGMSG_LBO)
    
    # Create queue
    q = Queue()
    
    # Log a message and enter to a loop
    syslog.syslog(syslog.LOG_NOTICE, 'SN UPS: Installed handlers on GPIO: {} (BTN), {} (LBO), {} (PWR)'.format(
        GPIO_BTN, GPIO_LBO, GPIO_PWR))
    while True:
        
        # Receive event and parameter from queue
        (event, param) = q.get()
        
        if event == EVENT_GPIO:
            pin = param
            if pin == GPIO_BTN:
                sn_shutdown(LOGMSG_BTN)
            elif pin == GPIO_LBO:
                
                ## Double check for LBO is active
                if lbo.is_pressed:
                    sn_shutdown(LOGMSG_LBO)
            elif pin == GPIO_PWR:
                if pwr.is_pressed:
                    syslog.syslog(syslog.LOG_WARNING, 'SN UPS: Power failure')
                else:
                    syslog.syslog(syslog.LOG_WARNING, 'SN UPS: Power restored')
            else:
                syslog.syslog(syslog.LOG_NOTICE, 'SN UPS: Received event from unexpected pin {}'.format(pin))
                
        else:
            syslog.syslog(syslog.LOG_NOTICE, 'SN UPS: Received unexpected event {}'.format(event))
    
    # Unreachable point
    exit(0)
    
