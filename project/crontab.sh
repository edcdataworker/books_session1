#minute         0-59
#hour           0-23
#day of month   1-31
#month          1-12
#day of week    0-7 (0 or 7)

#Run a command every minute

* * * * * LOG_DIR=/data LOG_TO_FILE=true LOG_LEVEL=INFO /usr/bin/python3 /app/main.py
