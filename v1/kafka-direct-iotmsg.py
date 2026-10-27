
"""
 Processes direct stream from kafka, '\n' delimited text directly received
   every 2 seconds.
 Usage: kafka-direct-iotmsg.py <broker_list> <topic>

 To run this on your local machine, you need to setup Kafka and create a
   producer first, see:
 http://kafka.apache.org/documentation.html#quickstart

 and then run the example
    `$ bin/spark-submit --jars \
      external/kafka-assembly/target/scala-*/spark-streaming-kafka-assembly-*.jar \
      kafka-direct-iotmsg.py \
      localhost:9092 iotmsgs`
"""
from __future__ import print_function

import sys
import re

from pyspark import SparkContext
from pyspark.streaming import StreamingContext
from pyspark.streaming.kafka import KafkaUtils

from operator import add


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: kafka-direct-iotmsg.py <broker_list> <topic>", file=sys.stderr)
        exit(-1)

    sc = SparkContext(appName="PythonStreamingDirectKafkaWordCount")
    ssc = StreamingContext(sc, 2)

    sc.setLogLevel("WARN")

    ###############
    # Globals
    ###############
    smokeTotal = 0.0
    smokeCount = 0
    smokeAvg = 0.0


    brokers, topic = sys.argv[1:]
    kvs = KafkaUtils.createDirectStream(ssc, [topic], {"metadata.broker.list": brokers})

    # Read in the Kafka Direct Stream into a TransformedDStream
    lines = kvs.map(lambda x: x[1])
    jsonLines = lines.map(lambda x: re.sub(r"\s+", "", x, flags=re.UNICODE))

    ############
    # Processing
    ############
    # foreach function to iterate over each RDD of a DStream
    def processSmokeRDD(time, rdd):
      global smokeTotal
      global smokeCount
      global smokeAvg

      smokeList = rdd.collect()
      for smokeFloat in smokeList:
        smokeTotal += float(smokeFloat)
        smokeCount += 1
        smokeAvg = smokeTotal / smokeCount
      print("Smoke Total = " + str(smokeTotal))
      print("Smoke Count = " + str(smokeCount))
      print("Avg Smoke = " + str(smokeAvg))



    # Search for specific IoT data values (assumes jsonLines are split(','))
    smokeValues = jsonLines.filter(lambda x: re.findall(r"smoke.*", x, 0))
    smokeValues.pprint(num=10000)

    # Parse out just the value without the JSON key
    parsedSmokeValues = smokeValues.map(lambda x: re.sub(r"\"smoke\":", "", x).split(',')[0])

    # Search for specific IoT data values (assumes jsonLines are split(','))
    humidityValues = jsonLines.filter(lambda x: re.findall(r"humidity.*", x, 0))
    humidityValues.pprint(num=10000)

    # Parse out just the value without the JSON key
    parsedHumidityValues = humidityValues.map(lambda x: re.sub(r"\"humidity\":", "", x))

    tempValues = jsonLines.filter(lambda x: re.findall(r"temperature.*", x, 0))
    tempValues.pprint(num=10000)

    # Parse out just the value without the JSON key
    parsedTempValues = tempValues.map(lambda x: re.sub(r"\"temperature\":", "", x))

    COValues = jsonLines.filter(lambda x: re.findall(r"carbon-monoxide.*", x, 0))
    COValues.pprint(num=10000)

    # Parse out just the value without the JSON key
    parsedCOValues = COValues.map(lambda x: re.sub(r"\"carbon-monoxide\":", "", x))


    # Count how many values were parsed
    countMap = parsedTempValues.map(lambda x: 1).reduce(add)
    valueCount = countMap.map(lambda x: "Total Count of Msgs: " + unicode(x))
    valueCount.pprint()

    # Sort all the IoT values
    sortedValues = parsedTempValues.transform(lambda x: x.sortBy(lambda y: y))
    sortedValues.pprint(num=10000)
    sortedValues = parsedHumidityValues.transform(lambda x: x.sortBy(lambda y: y))
    sortedValues.pprint(num=10000)

    # Iterate on each RDD in parsedTempValues DStream to use w/global variables
    parsedSmokeValues.foreachRDD(processSmokeRDD)

    ssc.start()
    ssc.awaitTermination()
