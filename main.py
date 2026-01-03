# pylint: disable=c-extension-no-member, no-member, no-name-in-module
from app import main

try:
    main()
except Exception as exc:
    import io
    import sys
    import time
    from config import config
    from mp_libs import logging
    from mp_libs.power.powerfeather import PowerFeather

    # Close down any other handlers so they aren't used here
    logging.shutdown()

    # Create logger
    logger = logging.getLogger("main")
    logger.setLevel(config.get("logging_level", logging.INFO))
    file_handler = logging.FileHandler("Exception_Log.txt", "a")
    file_handler.setLevel(config.get("logging_level", logging.INFO))
    file_handler.setFormatter(logging.Formatter("%(mono)d %(levelname)s-%(name)s:%(message)s"))
    logger.addHandler(file_handler)

    # Log message to file
    logger.critical("Caught unexpected exception:")
    buf = io.StringIO()
    sys.print_exception(exc, buf)
    print(buf.getvalue())
    logger.critical(f"{buf.getvalue()}")
    logger.critical("Looping...")

    # Close/flush file handler
    logging.shutdown()

    # NOTE: A hard reset will cause RTC memory to be lost
    if config.get("debug", False):
        pf = PowerFeather(first_boot=False, init_periphs=False)
        while True:
            pf.led_toggle()
            time.sleep_ms(250)
    else:
        import machine
        machine.reset()
