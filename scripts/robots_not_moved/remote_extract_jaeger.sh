#!/bin/bash
# Extract the robot-state lines of the jaeger actor log for a list of nights.
# Read-only; run where the operations-software (tron) logs live (Utah):
#
#   NIGHTS='apo:60285 lco:61210' bash remote_extract_jaeger.sh > jaeger_extract.txt
#
# For each night (observatory:MJD) it reads the jaeger log files whose names
# start with the UT date of MJD-1 or MJD, and prints a header line
#   #### <obs> <mjd> jaeger <log file name>
# followed by the configuration_loaded / "Starting trajectory" / folded=T
# lines of that file. Only jaeger keyword traffic is kept (no hub/hal
# command lines, which carry user names).
L=${TRON_LOGS:-/uufs/chpc.utah.edu/common/home/sdss51/sdsswork/data/logs}
for nm in $NIGHTS; do
  obs=${nm%%:*}; mjd=${nm##*:}
  d1=$(date -u -d "1858-11-17 + $((mjd-1)) days" +%F); d0=$(date -u -d "1858-11-17 + $mjd days" +%F)
  for f in $(ls "$L/$obs/tron/jaeger/" 2>/dev/null | grep -E "^($d1|$d0)T"); do
    p=$L/$obs/tron/jaeger/$f; case $f in *.gz) C="zcat";; *) C="cat";; esac
    echo "#### $obs $mjd jaeger $f"
    timeout 60 nice $C "$p" | grep -E 'configuration_loaded=|text="Starting trajectory|folded=T'
  done
done
