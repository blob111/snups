#! /usr/bin/python3

import os
import os.path
import sys
import syslog
import re
from signal import signal, SIGINT, SIGHUP, SIGTERM
from configparser import ConfigParser
from subprocess import check_output
from time import sleep, time, localtime, strftime
from gpiozero import OutputDevice, Button
from multiprocessing import Process, Queue
from smtplib import SMTP, SMTPRecipientsRefused, SMTPSenderRefused, SMTPAuthenticationError
from ssl import create_default_context

CONF_FILE = 'snups.conf'

GPIO_BTN = 4
GPIO_LBO = 22
GPIO_PWR = 23
GPIO_DEBOUNCE = 0.2
SHUTDOWN_WAIT = 20

MSG_PFX = 'SN UPS'
MAIL_TO = 'yooozh@gmail.com'
MAIL_FROM = 'yooozh@gmail.com'
MAIL_SUBJ = '{} event'.format(MSG_PFX)
MAIL_IN_PARALLEL = True
SMTP_PORT = 25
SMTP_AUTH = False
SMTP_TIMEOUT = 10
SMTP_ATTEMPTS = 3
SMTP_SLEEP = 10

SIG_CATCHED = [         # catched signals
    SIGINT,
    SIGHUP,
    SIGTERM
]

EVENT_GPIO = 10
EVENT_CHILD_END = 20
EVENT_SIG = 30

DNS_TIMEOUT = 10
DNS_ATTEMPTS = 3
DNS_SLEEP = 10
DNS_MX_MAX = 3
DNS_Q_PROG = '/usr/bin/host -t mx -W {}'.format(DNS_TIMEOUT)
DNS_Q_MATCH = r' mail is handled by ([0-9]+) (.+)\.$'
DNS_Q_NOMX = r' has no MX record$'
DNS_Q_NXDOMAIN = r'^Host .* not found: 3\(NXDOMAIN\)$'

###
### Classes
###

class ModSMTP(SMTP):
    """Class SMTP with redefined quit() method."""

    def quit(self):
        """Redefined quit() method with masked exceptions."""

        try:
            SMTP.quit(self)
        except Exception:
            pass
        return

###
### Routines
###

def cleanup(co):
    """Cleanup routine."""

    while co['pins']:
        pin_obj = co['pins'].pop()
        pin_obj.close()
    while co['signals']:
        s, h = co['signals'].pop()
        signal(s, h)
    if co['syslog']:
        syslog.closelog()
        co['syslog'] = False
    return

def signal_handler(signal, frame):
    """Signal handler."""

    global q
    q.put((EVENT_SIG, signal))
    return

def gpio_handler(device):
    """Handler for gpiozero Button events."""

    global q
    q.put((EVENT_GPIO, device.pin.number))
    return

def sn_shutdown():
    """Call shutdown within SHUTDOWN_WAIT time."""

    # Warn logged users
    cmd = 'sudo wall "System shutting down in {} seconds"'.format(SHUTDOWN_WAIT)
    os.system(cmd)

    # Wait specified number of seconds before shutdown
    sleep(SHUTDOWN_WAIT)

    # Call system shutdown
    cmd = 'sudo shutdown now'
    os.system(cmd)
    exit(0)

def make_mx_list(a):
    """Return list of MX servers for domain."""

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

def sendmail(q, t, froma, toa, subj, msg, sign, cf):
    """Send email message by means of SMTP protocol."""

    pid = os.getpid()
    ts = strftime('%Y-%m-%d %H:%M:%S', localtime(t))
    fmsg = 'From: {}\r\nTo: {}\r\nSubject: {}\r\n\r\n{} at {}\r\n\r\n--\r\nWBR,\r\n{}\r\n'.format(
        froma, toa, subj, msg, ts, sign)

    # Use configured server or ...
    if cf['server']:
        nxdomain = False
        mxl = [cf['server']]

    # ... or Get MX list
    else:
        dns_attempts = DNS_ATTEMPTS
        nxdomain, mxl = make_mx_list(toa)
        while not nxdomain and not mxl and dns_attempts:
            sleep(DNS_SLEEP)
            dns_attempts -= 1
            nxdomain, mxl = make_mx_list(toa)

    # Error check
    if nxdomain:
        syslog.syslog(syslog.LOG_NOTICE, '{}: Sending mail failed: domain not existed for {}'.format(MSG_PFX, toa))
    elif not mxl:
        syslog.syslog(syslog.LOG_NOTICE, '{}: Sending mail failed: error finding mail server for {}'.format(MSG_PFX, toa))

    # Try to send mail through MX servers in MX list in order, make some attempts
    # Loop terminated when at least one MX server accept or refused mail
    else:
        smtp_attempts = cf['attempts']
        success = False
        refused = False
        while not success and not refused and smtp_attempts:
            if smtp_attempts < cf['attempts']:
                sleep(cf['sleep'])
            mxl_iter = mxl[:]
            smtp_attempts -= 1
            while not success and not refused and mxl_iter:

                # Pick first MX server from a list and open SMTP connection
                mx = mxl_iter.pop(0)
                try:
                    smtp = ModSMTP(host=mx, port=cf['port'], timeout=cf['timeout'])
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

                # Login
                if cf['auth']:
                    try:
                        smtp.login(cf['username'], cf['secret'])
                    except SMTPAuthenticationError as err:
                        syslog.syslog(syslog.LOG_NOTICE, '{}: Login to {} refused: {}'.format(MSG_PFX, mx, err))
                        refused = True
                        smtp.quit()
                        continue
                    except Exception as err:
                        syslog.syslog(syslog.LOG_NOTICE, '{}: Error during login to {}: {}'.format(MSG_PFX, mx, err))
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

    # Clean-up objects
    cleanup_objects = {
        'pins': [],         # list of catched gpiozero Button instances
        'signals': [],      # list of tuples (signal, handler) for catched signals
        'syslog': False     # syslog open flag
    }

    # Read configuration
    base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    conf_fn = '{}/{}'.format(base_dir, CONF_FILE)
    conf = ConfigParser()
    conf.read(conf_fn)
    smtp_conf = {}
    smtp_conf['server'] = conf.get(MAIL_TO, 'Server', fallback=None)
    smtp_conf['auth'] = conf.getboolean(MAIL_TO, 'Auth', fallback=SMTP_AUTH)
    if smtp_conf['auth']:
        smtp_conf['username'] = conf.get(MAIL_TO, 'Username', fallback=None)
        smtp_conf['secret'] = conf.get(MAIL_TO, 'Secret', fallback=None)
        if not smtp_conf['server'] or not smtp_conf['username'] or not smtp_conf['secret']:
            smtp_conf['auth'] = False
    smtp_conf['port'] = conf.getint(MAIL_TO, 'Port', fallback=SMTP_PORT)
    smtp_conf['timeout'] = conf.getint(MAIL_TO, 'Timeout', fallback=SMTP_TIMEOUT)
    smtp_conf['attempts'] = conf.getint(MAIL_TO, 'Attempts', fallback=SMTP_ATTEMPTS)
    smtp_conf['sleep'] = conf.getint(MAIL_TO, 'Sleep', fallback=SMTP_SLEEP)

    # Dictionary of childs (instances of 'Process' class) indexed by PID
    children = {}

    # Open logger
    syslog.openlog(logoption=syslog.LOG_PID, facility=syslog.LOG_USER)
    cleanup_objects['syslog'] = True

    # Redefine signal handlers and save old handlers for cleanup on exit
    for sig in SIG_CATCHED:
        cleanup_objects['signals'].append((sig, signal(sig, signal_handler)))

    # Set CTRL and LBO pins as input pull-up and assign callback handler
    btn = Button(GPIO_BTN, pull_up=True, bounce_time=GPIO_DEBOUNCE)
    btn.when_pressed = gpio_handler
    cleanup_objects['pins'].append(btn)
    lbo = Button(GPIO_LBO, pull_up=True, bounce_time=GPIO_DEBOUNCE)
    lbo.when_pressed = gpio_handler
    cleanup_objects['pins'].append(lbo)

    # Set PWR pin as input pull-up and assign callback handler
    pwr = Button(GPIO_PWR, pull_up=True, bounce_time=GPIO_DEBOUNCE)
    pwr.when_pressed = gpio_handler
    pwr.when_released = gpio_handler
    cleanup_objects['pins'].append(pwr)

    # Shutdown if LBO is already active
    if lbo.is_pressed:
        syslog.syslog(syslog.LOG_WARNING, '{}: Low battery (GPIO {}) detected at start, activate poweroff'.format(MSG_PFX, GPIO_LBO))
        cleanup(cleanup_objects)
        sn_shutdown()

    # Create queue
    q = Queue()

    # Send start notification by mail in separate process or inline
    msg = 'Monitor started'
    if MAIL_IN_PARALLEL:
        p = Process(target=sendmail, args=(q, time(), MAIL_FROM, MAIL_TO, MAIL_SUBJ, msg, MSG_PFX, smtp_conf,))
        p.start()
        cpid = p.pid
        children[cpid] = p
    else:
        sendmail(q, time(), MAIL_FROM, MAIL_TO, MAIL_SUBJ, msg, MSG_PFX, smtp_conf)

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
                syslog.syslog(syslog.LOG_WARNING, '{}: Activate poweroff by button press (GPIO {})'.format(MSG_PFX, GPIO_BTN))
                cleanup(cleanup_objects)
                sn_shutdown()

            # Low battery condition
            # Double check for LBO is active and initiate shutdown if so
            elif pin == GPIO_LBO:
                if lbo.is_pressed:
                    syslog.syslog(syslog.LOG_WARNING, '{}: Activate poweroff due to low battery (GPIO {})'.format(MSG_PFX, GPIO_LBO))
                    cleanup(cleanup_objects)
                    sn_shutdown()

            # Main power state changed (fail or restore)
            elif pin == GPIO_PWR:
                if pwr.is_pressed:
                    msg = 'Power failure'
                else:
                    msg = 'Power restored'
                syslog.syslog(syslog.LOG_WARNING, '{}: {}'.format(MSG_PFX, msg))

                # Send notification by mail in separate process or inline
                if MAIL_IN_PARALLEL:
                    p = Process(target=sendmail, args=(q, time(), MAIL_FROM, MAIL_TO, MAIL_SUBJ, msg, MSG_PFX, smtp_conf,))
                    p.start()
                    cpid = p.pid
                    children[cpid] = p
                else:
                    sendmail(q, time(), MAIL_FROM, MAIL_TO, MAIL_SUBJ, msg, MSG_PFX, smtp_conf)

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

        # Signal catched
        elif event == EVENT_SIG:
            sig = param
            if sig == SIGINT:
                syslog.syslog(syslog.LOG_NOTICE, '{}: SIGINT catched, exiting'.format(MSG_PFX))
                break
            elif sig == SIGTERM:
                syslog.syslog(syslog.LOG_NOTICE, '{}: SIGTERM catched, exiting'.format(MSG_PFX))
                break
            elif sig == SIGHUP:
                syslog.syslog(syslog.LOG_NOTICE, '{}: SIGHUP catched, ignoring'.format(MSG_PFX))
            else:
                syslog.syslog(syslog.LOG_NOTICE, '{}: Unknown signal catched'.format(MSG_PFX))

        # Unknown event, this should not be occured
        else:
            syslog.syslog(syslog.LOG_NOTICE, '{}: Received unknown event {}'.format(MSG_PFX, event))

    # Exit point
    cleanup(cleanup_objects)
    sys.exit(0)
