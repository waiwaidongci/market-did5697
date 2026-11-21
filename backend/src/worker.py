import logging
import time
from pathlib import Path
from src.services.execution import claim, execute, materialize_due, recover
from src.utils.wakeup import wait

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
heartbeat = Path('/tmp/analysis-heartbeat')


def main():
    # Recover anything a previous process left leased before taking new work.
    try:
        recover()
    except Exception:
        log.exception('Recovery at startup failed')
    while True:
        try:
            materialize_due()
            lease = claim()
            heartbeat.touch()
            if lease:
                execute(lease)
                heartbeat.touch()
            else:
                recover()
                wait()
        except Exception:
            log.exception('Worker cycle failed')
            time.sleep(2)


if __name__ == '__main__':
    main()
