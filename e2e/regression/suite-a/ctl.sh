#!/bin/zsh
# ctl.sh start | stop | reset | clean | status
#   start  start the server in the background (log: REG_WORK/server.log), wait until it answers
#   stop   stop it (only this suite's own server.py process)
#   reset  stop, restore REG_WORK/pristine.db over the copy, start
#   clean  stop and delete REG_WORK (the prod copy, screenshots, everything)
HERE=${0:A:h}
source $HERE/env.sh
PIDF=$REG_WORK/server.pid
stop() {
  if [[ -f $PIDF ]] && kill -0 $(cat $PIDF) 2>/dev/null; then
    kill $(cat $PIDF); for i in {1..40}; do kill -0 $(cat $PIDF) 2>/dev/null || break; sleep 0.25; done
  fi
  rm -f $PIDF
}
start() {
  nohup $HERE/serve.sh >> $REG_WORK/server.log 2>&1 &
  echo $! > $PIDF
  for i in {1..240}; do
    curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$REG_PORT/api/auth/me 2>/dev/null | grep -q 401 && { echo "server up on $REG_PORT"; return 0; }
    kill -0 $(cat $PIDF) 2>/dev/null || { echo "server died - see $REG_WORK/server.log"; tail -20 $REG_WORK/server.log; return 1; }
    sleep 0.5
  done
  echo "server did not answer"; return 1
}
case $1 in
  start) start ;;
  stop) stop ;;
  reset) stop; cd $REG_WORK && rm -f copy.db-wal copy.db-shm && cp pristine.db copy.db && rm -rf outbox toast_calls.jsonl && start ;;
  clean) stop; [[ $REG_WORK == */tmp/* ]] && rm -rf $REG_WORK ;;
  status) [[ -f $PIDF ]] && kill -0 $(cat $PIDF) 2>/dev/null && echo running || echo stopped ;;
  *) echo "usage: ctl.sh start|stop|reset|clean|status"; exit 2 ;;
esac
