#!/usr/bin/python

import sys
import datetime
import random
import string

# Set number of simulated messages to generate
if len(sys.argv) > 1:
  numMsgs = int(sys.argv[1])
else:
  numMsgs = 1

# Fixed values
guidStr = "0-ZZZ12345678"
destinationStr = "0-AAA12345678"
formatStr = "urn:example:sensor:Smoke"

# Choice for random letter
letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

iotmsg_header = """\
{
  "guid": "%s",
  "destination": "%s", """

iotmsg_eventTime = """\
  "eventTime": "%sZ", """

iotmsg_payload ="""\
  "payload": {
     "format": "%s", """

iotmsg_data ="""\
     "data": {
       "smoke": %d,
       "humidity": %.5f,
       "temperature": %.1f,
       "carbon-monoxide": %.1f
     }
   }
}"""

##### Generate JSON output:

print "["

dataElementDelimiter = ","
for counter in range(0, numMsgs):

  randInt = random.randrange(0, 9)
  randLetter = random.choice(letters)
  print iotmsg_header % (guidStr+str(randInt)+randLetter, destinationStr)

  today = datetime.datetime.today()
  datestr = today.isoformat()
  print iotmsg_eventTime % (datestr)

  print iotmsg_payload % (formatStr)

  # Generate a random floating point number
  randSmok = random.uniform(0, 800)
  randHum = random.uniform(0.0001, 0.2)
  randTemp = random.uniform(60.0, 90.0)
  randCO = random.uniform(0.80, 2.80)
  if counter == numMsgs - 1:
    dataElementDelimiter = ""
  print iotmsg_data % (randSmok,randHum,randTemp,randCO) + dataElementDelimiter

print "]"

