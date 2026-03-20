#!/bin/bash
# OpenSERP start/stop/status script
# Usage: ./openserp.sh start|stop|status

PIDFILE=".openserp.pid"
PORT=7001
BINARY="tools/openserp/openserp"

# Fall back to docker if no binary
if [ ! -f "$BINARY" ]; then
    BINARY="openserp/openserp"
fi

start() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "OpenSERP already running (PID: $(cat "$PIDFILE"))"
        return 0
    fi

    if [ ! -f "$BINARY" ]; then
        echo "❌ OpenSERP binary not found at $BINARY"
        echo "   Run: cd tools && bash setup_openserp.sh"
        return 1
    fi

    echo "Starting OpenSERP on port $PORT..."
    nohup "$BINARY" serve -p "$PORT" > /dev/null 2>&1 &
    echo $! > "$PIDFILE"
    sleep 2

    if curl -s "http://localhost:$PORT/mega/search?text=test&limit=1" > /dev/null 2>&1; then
        echo "✅ OpenSERP running (PID: $(cat "$PIDFILE"), port: $PORT)"
    else
        echo "❌ OpenSERP failed to start"
        rm -f "$PIDFILE"
        return 1
    fi
}

stop() {
    if [ ! -f "$PIDFILE" ]; then
        echo "OpenSERP not running (no PID file)"
        return 0
    fi

    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        rm -f "$PIDFILE"
        echo "✅ OpenSERP stopped (PID: $PID)"
    else
        rm -f "$PIDFILE"
        echo "OpenSERP was not running (stale PID file removed)"
    fi
}

status() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "OpenSERP running (PID: $(cat "$PIDFILE"), port: $PORT)"
        curl -s "http://localhost:$PORT/mega/search?text=test&limit=1" > /dev/null 2>&1 \
            && echo "  Health: ✅ responding" \
            || echo "  Health: ❌ not responding"
    else
        echo "OpenSERP not running"
    fi
}

case "${1:-status}" in
    start)  start ;;
    stop)   stop ;;
    status) status ;;
    restart) stop; sleep 1; start ;;
    *)
        echo "Usage: $0 {start|stop|status|restart}"
        exit 1
        ;;
esac
