# om_logger.py - OpenMuscle shared logging utility

DEBUG = True

def _prefix(level):
    return "[{}]".format(level)

def debug(msg):
    if DEBUG:
        print("{} {}".format(_prefix("DBG"), msg))

def info(msg):
    print("{} {}".format(_prefix("INF"), msg))

def warn(msg):
    print("{} {}".format(_prefix("WRN"), msg))

def error(msg):
    print("{} {}".format(_prefix("ERR"), msg))
