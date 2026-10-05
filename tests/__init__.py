import logging

# Jobs log their failures with tracebacks; the tests provoke failures on purpose.
logging.getLogger("growth").setLevel(logging.CRITICAL)
