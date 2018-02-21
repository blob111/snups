#! /bin/sh

GPIO_CTRL=4
SLEEP=6

DATE=`/bin/date`
echo "Activate power cut-off by driving low GPIO ${GPIO_CTRL} at ${DATE}"
if [ ! -e /sys/class/gpio/gpio${GPIO_CTRL}/edge ]; then
  echo ${GPIO_CTRL} >/sys/class/gpio/export;
fi
echo none >/sys/class/gpio/gpio${GPIO_CTRL}/edge
echo low >/sys/class/gpio/gpio${GPIO_CTRL}/direction
echo "Start sleeping ${SLEEP} seconds, the power should gone..."
sleep ${SLEEP}
echo "Stop sleeping, something wrong."
