#!/usr/bin/env python

###############
##
##  Dependencies:
##
##  python3 -m pip install aprs3 maidenhead
##
###############

import aprslib
import re
import logging
import maidenhead as mh

from datetime import datetime, timezone
from math import modf
from ka9q_js8Utils import writeStringToFile

CALLSIGN_SUFFIX_REX = r"(?P<prefix>[\d\w]{,3}[/])?(?P<callsign>[\d\w]+)[/]?(?P<suffix>[\d\w]+)?"

DEFAULT_APRS_HOST="asia.aprs2.net"
DEFAULT_APRS_PORT=14580
DEFAULT_LOG_FN="./aprs_frames.log"

glogger = logging.getLogger(__name__)

class APRSReporter:

    reporter: str
    aprs_reporting_enabled: bool
    aprs_user: str
    aprs_passcode: str
    aprs_host: str
    aprs_port: int

    log_fn: str;

    AIS = None

    def __init__(self, reporter:str, user:str, passcode:str, reporting_enabled:bool=True, host:str=DEFAULT_APRS_HOST, port:int=DEFAULT_APRS_PORT, log_fn: str=DEFAULT_LOG_FN):
        self.logger = logging.getLogger("%s.%s" % (__name__, self.__class__.__name__))
        self.reporter = reporter.upper()
        self.log_fn = log_fn
        self.aprs_reporting_enabled = reporting_enabled
        self.aprs_host=host
        self.aprs_port=port
        self.aprs_user=user
        self.aprs_passcode=passcode
        self.AIS = aprslib.IS(callsign=user, passwd=passcode, host=host, port=port) 


    def grid2aprs(self, grid_locator: str):
        # Get the top-left coordinates (latitude, longitude)
        lat, lon = mh.to_location(grid_locator)
        #print(f"Top-left corner: Lat={lat}, Lon={lon}")

        # Get the center coordinates (latitude, longitude)
        center_lat, center_lon = mh.to_location(grid_locator, center=True)
        #print(f"Center of square: Lat={center_lat}, Lon={center_lon}")

        latDir = "N"
        if (lat < 0):
            lat *= -1
            latDir = "S"
        
        lonDir = "E"
        if (lon < 0):
            lon *= -1
            lonDir = "W"

        fLat,iLat = modf(lat)
        fLon,iLon = modf(lon)

        fLatMin,iLatMin = modf(fLat * 60)
        fLonMin,iLonMin = modf(fLon * 60)

        iLatSec = round(fLatMin * 60)
        iLonSec = round(fLonMin * 60)

        if (iLatSec == 60):
            iLatMin += 1
            iLatSec = 0

        if (iLonSec == 60):
            iLonMin += 1
            iLonSec = 0
        
        if (iLatMin == 60):
            iLat += 1
            iLatMin = 0

        if (iLonMin == 60):
            iLon += 1
            iLonMin = 0

        aprsLat = iLat * 100 + iLatMin + (iLatSec / 60.0)
        aprsLon = iLon * 100 + iLonMin + (iLonSec / 60.0)

        return f"{aprsLat:07.2f}{latDir}", f"{aprsLon:08.2f}{lonDir}"  


    ############################################################################

    def removeCallsignSuffix(self, callsign: str):
        match = re.match(CALLSIGN_SUFFIX_REX, callsign)

        if match:
            return match.group('callsign')
        else:
            return callsign

    def sendFrame(self, frame):
        
        self.logger.info(f"APRS Frame: [{frame}] - APRS Reporting Enabled: [{self.aprs_reporting_enabled}]")
        if self.aprs_reporting_enabled:
            utc_now = datetime.now(timezone.utc)
            fmt_dt = utc_now.strftime("%Y/%m/%d-%H:%M:%S")
            writeStringToFile(self.log_fn, f"{fmt_dt}: {str(frame)}\n", True)
            # TODO - Need to review this library to see if we do an initial connect, does it "keep-alive" ? or a min retry ?
            #     I saw a msg come through and it did not make it to the APRSIS server. Maybe UDP vs TCP ?
            self.AIS.connect()
            try:
                self.AIS.sendall(frame)
            finally:
                self.AIS.close()


    def reportAprsPosition(self, callsign: str, grid_locator:str, comment: str):

        lat,lon = self.grid2aprs(grid_locator)

        # Format APRS Position report
        msg = f"={lat}/{lon}G#{comment}"
        self.reportAprsMessage(callsign, msg)

    def reportAprsMessage(self, callsign: str, msg: str):

        callsign_nosuffix = self.removeCallsignSuffix(callsign)

        # Format APRS Position report
        frame_msg = f"{callsign_nosuffix.upper()}>APJ8CL,qAS,{self.reporter}:{msg}"

        # Valid Message and send if ok
        try:
            packet = aprslib.parse(frame_msg)
            self.logger.debug(f"APRS packet Parsed: [{packet}]")

            self.sendFrame(frame_msg)
        except (aprslib.ParseError, aprslib.UnknownFormat) as exp:
            self.logger.error(f"Error parsing APRS packet msg:[{frame_msg}].  {exp}")


#########################


def testAPRSPosition():
    callsign_from = "VK4TAA"
    reporter = "VK4TMZ"
    try:
        aprsReporter=APRSReporter(reporter="VK4TMZ", reporting_enabled=True, user="VK4TMZ", passcode=23719)
        # lq, lr, ls, ms, ns ( do it slowly!! or you'll get the [Location changes too fast (adaptive limit)])
        aprsReporter.reportAprsPosition(callsign="VK4TAA/MM", grid_locator="QG62ms", comment="Testing APRSIS feed.")

    except aprslib.exceptions.LoginError as e:
        glogger.error(f"Login failed: {e}")
    except Exception as e:
        glogger.error(f"An error occurred: {e}")
    finally:
        glogger.info("Disconnected from APRS-IS.")


# Main
#testAPRSPosition()