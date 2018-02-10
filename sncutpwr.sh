#! /bin/sh

GPIO_CTRL=4

DATE=`/bin/date`
/bin/echo "Activate power cut-off by driving low GPIO ${GPIO_CTRL} at ${DATE}"
/usr/bin/raspi-gpio set $GPIO_CTRL op dl
