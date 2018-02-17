#! /usr/bin/python3

import os
import sys
import syslog
import re
from subprocess import check_output
from time import sleep, time, localtime, strftime
from gpiozero import OutputDevice, Button
from multiprocessing import Process, Queue
from smtplib import SMTP, SMTPRecipientsRefused, SMTPSenderRefused
from ssl import create_default_context

GPIO_BTN = 4
GPIO_LBO = 22
GPIO_PWR = 23
GPIO_DEBOUNCE = 0.2
SHUTDOWN_WAIT = 20
MSG_PFX = 'SN UPS'
LOGMSG_BTN = '{}: Activate poweroff by button press (GPIO {})'.format(MSG_PFX, GPIO_BTN)
LOGMSG_LBO = '{}: Activate poweroff due to low battery (GPIO {})'.format(MSG_PFX, GPIO_LBO)
MAIL_TO = 'yooozh@gmail.com'
MAIL_FROM = 'yooozh@gmail.com'
MAIL_SUBJ = '{} event'.format(MSG_PFX)
MAIL_IN_PARALLEL = True
SMTP_TIMEOUT = 10
EVENT_GPIO = 10
EVENT_CHILD_END = 20

DNS_TIMEOUT = 10
DNS_ATTEMPTS = 3
DNS_SLEEP = 10
DNS_MX_MAX = 3
DNS_Q_PROG = '/usr/bin/host -t mx -W {}'.format(DNS_TIMEOUT)
DNS_Q_MATCH = r' mail is handled by ([0-9]+) (.+)\.$'
DNS_Q_NOMX = r' has no MX record$'
DNS_Q_NXDOMAIN = r'^Host .* not found: 3\(NXDOMAIN\)$'

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
    sleep(SHUTDOWN_WAIT)
    
    # Call system shutdown
    cmd = 'sudo shutdown now'
    os.system(cmd)
    exit(0)
    
###
### Make SMTP server list
###

def make_mx_list(a):
    
    mxd = {}
    
    # Request MX records for domain part of email address
    user, domain = a.split('@')
    dnsq = '{} {}'.format(DNS_Q_PROG, domain)
    nx = False
    try:
        o = check_output(dnsq, shell=True, universal_newlines=True)
    except:
        mxl = []
    else:
        
        ol = o.split('\n')
        for e in ol:
            
            # Extract MX and its priority and put them to dictionary
            # MX name as key, priority as value
            m = re.search(DNS_Q_MATCH, e)
            if m:
                prio = int(m.group(1))
                mx = m.group(2)
                mxd[mx] = prio
                continue
                
            # If domain has no MX record put it to dictionary and break loop
            m = re.search(DNS_Q_NOMX, e)
            if m:
                mxd = {domain: 0}
                break
                
            # If domain not existed
            m = re.search(DNS_Q_NXDOMAIN, e)
            if m:
                nx = True
                break
                
        # Make MX list sorted by priority
        mxl = sorted(mxd.keys(), key = lambda x: mxd[x])
        
    # truncate MX list to maximum allowed elements
    mxl = mxl[0:DNS_MX_MAX]
        
    return nx, mxl
    
###
### Send mail routine
###

def sendmail(q, t, froma, toa, subj, msg, sign):
    
    pid = os.getpid()
    ts = strftime('%Y-%m-%d %H:%M:%S', localtime(t))
    fmsg = 'From: {}\r\nTo: {}\r\nSubject: {}\r\n\r\n{} at {}\r\n\r\n--\r\nWBR,\r\n{}\r\n'.format(
        froma, toa, subj, msg, ts, sign)
        
    # Get MX list
    attempts = DNS_ATTEMPTS
    nx, mxl = make_mx_list(toa)
    while not nx and not mxl and attempts:
        sleep(DNS_SLEEP)
        attempts -= 1
        nx, mxl = make_mx_list(toa)
        
    # Error check
    if nx:
        syslog.syslog(syslog.LOG_NOTICE, '{}: Sending mail failed: domain not existed for {}'.format(MSG_PFX, toa))
    elif not mxl:
        syslog.syslog(syslog.LOG_NOTICE, '{}: Sending mail failed: error finding mail server for {}'.format(MSG_PFX, toa))
        
    # Try to send mail through MX server in MX list in order
    # Loop terminated when at least one MX server accept or refused mail
    else:
        success = False
        refused = False
        while not success and not refused and mxl:
            
            # Pick first MX server in a list and open SMTP connection
            mx = mxl.pop(0)
            smtp = None
            try:
                smtp = SMTP(host=mx, timeout=SMTP_TIMEOUT)
            except Exception as err:
                syslog.syslog(syslog.LOG_NOTICE, '{}: Opening SMTP connection to {} failed: {}'.format(MSG_PFX, mx, err))
                continue
                
            # Start TLS
            context = create_default_context()
            try:
                smtp.starttls(context=context)
            except Exception as err:
                syslog.syslog(syslog.LOG_NOTICE, '{}: Starting TLS with {} failed: {}'.format(MSG_PFX, mx, err))
                smtp.quit()
                continue
                
            # Send mail
            try:
                smtp.sendmail(froma, toa, fmsg)
            except SMTPRecipientsRefused as err:
                syslog.syslog(syslog.LOG_NOTICE, '{}: Recipient {} refused by {}: {}'.format(MSG_PFX, toa, mx, err))
                refused = True
            except SMTPSenderRefused as err:
                syslog.syslog(syslog.LOG_NOTICE, '{}: Sender {} refused by {}: {}'.format(MSG_PFX, froma, mx, err))
                refused = True
            except Exception as err:
                syslog.syslog(syslog.LOG_NOTICE, '{}: Sending mail via {} failed: {}'.format(MSG_PFX, mx, err))
            else:
                success = True
            finally:
                smtp.quit()
                
        # Log successful mail sending
        if success:
            syslog.syslog(syslog.LOG_NOTICE, '{}: Mail to {} sended successfully via {}'.format(MSG_PFX, toa, mx))
            
    q.put((EVENT_CHILD_END, pid))
    return

###
### Main program starts here
###

if __name__ == '__main__':
    
    # Dictionary of childs (instances of 'Process' class) indexed by PID
    children = {}

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
    
    # Send start notification by mail in separate process or inline
    msg = 'Monitor started'
    if MAIL_IN_PARALLEL:
        p = Process(target=sendmail, args=(q, time(), MAIL_FROM, MAIL_TO, MAIL_SUBJ, msg, MSG_PFX,))
        p.start()
        cpid = p.pid
        children[cpid] = p
    else:
        sendmail(q, time(), MAIL_FROM, MAIL_TO, MAIL_SUBJ, msg, MSG_PFX)

    # Log a message and enter to a loop
    syslog.syslog(syslog.LOG_NOTICE, '{}: Installed handlers on GPIO: {} (BTN), {} (LBO), {} (PWR)'.format(
        MSG_PFX, GPIO_BTN, GPIO_LBO, GPIO_PWR))
    while True:
        
        # Receive event and parameter from queue
        event, param = q.get()
        
        # State of some pin changed
        if event == EVENT_GPIO:
            pin = param
            
            # Button pressed, initiate shutdown
            if pin == GPIO_BTN:
                sn_shutdown(LOGMSG_BTN)
                
            # Low battery condition
            # Double check for LBO is active and initiate shutdown if so
            elif pin == GPIO_LBO:
                if lbo.is_pressed:
                    sn_shutdown(LOGMSG_LBO)
                    
            # Main power state changed (fail or restore)
            elif pin == GPIO_PWR:
                if pwr.is_pressed:
                    msg = 'Power failure'
                else:
                    msg = 'Power restored'
                syslog.syslog(syslog.LOG_WARNING, '{}: {}'.format(MSG_PFX, msg))
                
                # Send notification by mail in separate process or inline
                if MAIL_IN_PARALLEL:
                    p = Process(target=sendmail, args=(q, time(), MAIL_FROM, MAIL_TO, MAIL_SUBJ, msg, MSG_PFX,))
                    p.start()
                    cpid = p.pid
                    children[cpid] = p
                else:
                    sendmail(q, time(), MAIL_FROM, MAIL_TO, MAIL_SUBJ, msg, MSG_PFX)
                    
            # Unexpected pin, this should not be occured
            else:
                syslog.syslog(syslog.LOG_NOTICE, '{}: Received event from unexpected pin {}'.format(MSG_PFX, pin))
                
        # Child finished, join it
        elif event == EVENT_CHILD_END:
            if MAIL_IN_PARALLEL:
                cpid = param
                if cpid in children.keys():
                    children[cpid].join()
                    del children[cpid]
                
        # Unknown event, this should not be occured
        else:
            syslog.syslog(syslog.LOG_NOTICE, '{}: Received unknown event {}'.format(MSG_PFX, event))
    
    # Unreachable point
    exit(0)
