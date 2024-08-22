#!/bin/bash

#
# Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
#


#!/bin/bash

echo "Killing all Selenium drivers started by the current user..."
ps aux | grep -E 'chrome|chromedriver' | grep $(whoami) | awk '{print $2}' | xargs kill -9
echo "All Selenium drivers started by the current user killed."